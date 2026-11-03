#!/usr/bin/env python3
"""Evaluate live confidential-document QA through the public LangGraph entry point.

Run from the repository root or python_backend directory:
    python -m python_backend.evaluation.evaluate_live_document_qa

Or run this file directly:
    python python_backend/evaluation/evaluate_live_document_qa.py

The evaluator deliberately does NOT call blockchain/IPFS retrieval functions directly.
It authenticates through the project's RBAC module and calls
answer_question(question, user_context=authenticated_user), matching app.py.

Privacy:
- Passwords and password hashes are never written to the report.
- Raw answers and decrypted document contents are not written to the report.
- Optional LLM judging sends answers and verified reference facts only to the
  configured local Ollama server, in memory; judge explanations are not saved.
- Reports contain operational flags, latency, answer length/hash, and scores.
  Store reports in a protected local directory.
- No ground-truth facts are invented. Supply manually verified facts through
  --cases-json if you want deterministic answer-content scoring.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Repository imports: support both `python -m ...` and direct script execution.
# ---------------------------------------------------------------------------

def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "python_backend" / "ai" / "agent.py").is_file():
            return candidate
        if (candidate / "ai" / "agent.py").is_file() and candidate.name == "python_backend":
            return candidate.parent
    raise RuntimeError(
        "Could not locate the repository root. Run this script from the project "
        "repository or python_backend directory."
    )


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = _find_repo_root(SCRIPT_PATH.parent)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from python_backend import rbac
    from python_backend.ai.agent import answer_question
    from python_backend.evaluation.llm_judge import LLMJudge, JudgeConfig, LLMJudgeError
except Exception as exc:  # pragma: no cover - depends on local project environment
    raise RuntimeError(
        "Unable to import the project's RBAC module or LangGraph agent. "
        "Activate university_venv and run from the repository. Original error: "
        f"{type(exc).__name__}: {exc}"
    ) from exc


# These are the four live document IDs selected from the user's existing inventory.
# Do not add the SS-* Student Service Record until agent.py's ID parser supports SS.
DEFAULT_CASES: List[Dict[str, Any]] = [
    {
        "case_id": "QA-EX-STD001-001-SUMMARY",
        "document_id": "EX-STD001-001",
        "student_id": "STD001",
        "category": "examination_result_sheet",
        "question": (
            "Retrieve document EX-STD001-001 for student STD001 and summarize "
            "the examination results and other key information explicitly stated "
            "in the document."
        ),
        "expected_facts": [],
    },
    {
        "case_id": "QA-REG-STD001-001-SUMMARY",
        "document_id": "REG-STD001-001",
        "student_id": "STD001",
        "category": "registration_certificate",
        "question": (
            "Retrieve document REG-STD001-001 for student STD001 and summarize "
            "the registration details explicitly stated in the document."
        ),
        "expected_facts": [],
    },
    {
        "case_id": "QA-TX-STD001-001-SUMMARY",
        "document_id": "TX-STD001-001",
        "student_id": "STD001",
        "category": "transcript",
        "question": (
            "Retrieve document TX-STD001-001 for student STD001 and summarize "
            "the academic information explicitly stated in the transcript."
        ),
        "expected_facts": [],
    },
    {
        "case_id": "QA-TX-STD002-001-SUMMARY",
        "document_id": "TX-STD002-001",
        "student_id": "STD002",
        "category": "transcript",
        "question": (
            "Retrieve document TX-STD002-001 for student STD002 and summarize "
            "the academic information explicitly stated in the transcript."
        ),
        "expected_facts": [],
    },
]


# ---------------------------------------------------------------------------
# Authentication and case loading
# ---------------------------------------------------------------------------

def authenticate_user(username: str) -> Dict[str, Any]:
    """Authenticate exactly as the Flask login route does; omit password hash."""
    password = getpass.getpass(f"Password for {username}: ")
    authenticated = rbac.authenticate(username, password)
    # Drop the local password reference as soon as authentication has completed.
    password = ""
    if not authenticated:
        raise SystemExit("Authentication failed. No evaluation was started.")

    # Match app.py's `_current_user()` context, while excluding credential material.
    user_context = dict(authenticated)
    user_context.pop("password_hash", None)
    if not user_context.get("role"):
        raise SystemExit("Authenticated account has no role. Evaluation aborted.")
    return user_context


def load_cases(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path:
        cases = [dict(case) for case in DEFAULT_CASES]
    else:
        case_path = Path(path).expanduser().resolve()
        if not case_path.is_file():
            raise SystemExit(f"Cases JSON file not found: {case_path}")
        with case_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        cases = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(cases, list) or not cases:
            raise SystemExit("Cases JSON must contain a non-empty list (or {'cases': [...]}).")
        cases = [dict(case) for case in cases]

    required = ("case_id", "document_id", "student_id", "question")
    seen = set()
    for index, case in enumerate(cases, start=1):
        missing = [key for key in required if not str(case.get(key, "")).strip()]
        if missing:
            raise SystemExit(f"Case {index} is missing required fields: {', '.join(missing)}")
        case["case_id"] = str(case["case_id"]).strip()
        case["document_id"] = str(case["document_id"]).strip().upper()
        case["student_id"] = str(case["student_id"]).strip().upper()
        case["question"] = str(case["question"]).strip()
        case["expected_facts"] = case.get("expected_facts") or []
        if not isinstance(case["expected_facts"], list):
            raise SystemExit(f"expected_facts must be a list in case {case['case_id']}")
        if case["case_id"] in seen:
            raise SystemExit(f"Duplicate case_id: {case['case_id']}")
        seen.add(case["case_id"])
        # Ensure the prompt explicitly identifies the requested document and student.
        if case["document_id"].lower() not in case["question"].lower():
            raise SystemExit(f"Question for {case['case_id']} must include its document ID.")
        if case["student_id"].lower() not in case["question"].lower():
            raise SystemExit(f"Question for {case['case_id']} must include its student ID.")
    return cases


# ---------------------------------------------------------------------------
# Trace inspection. Unknown values remain None; they are never assumed successful.
# ---------------------------------------------------------------------------

def _trace_entries(trace: Any) -> List[Dict[str, Any]]:
    if not isinstance(trace, dict):
        return []
    nodes = trace.get("nodes", [])
    return [item for item in nodes if isinstance(item, dict)] if isinstance(nodes, list) else []


def _details_by_node(trace: Any, node_name: str) -> List[Dict[str, Any]]:
    matches = []
    for entry in _trace_entries(trace):
        if entry.get("node") == node_name and isinstance(entry.get("details"), dict):
            matches.append(entry["details"])
    return matches


def _last_detail(trace: Any, node_name: str) -> Dict[str, Any]:
    entries = _details_by_node(trace, node_name)
    return entries[-1] if entries else {}


def _bool_or_none(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def inspect_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "authorized": None,
            "authorization_allowed": None,
            "integrity_verified": None,
            "document_found": None,
            "ipfs_retrieval_started": None,
            "llm_invoked": None,
            "llm_context_authorized": None,
            "grounded_answer_generated": None,
            "authorization_reason": None,
            "retrieval_error": "non_dict_result",
        }

    trace = result.get("trace") or {}
    auth_detail = _last_detail(trace, "authorize_query")
    retrieval_detail = _last_detail(trace, "retrieve_authorized_document")
    qa_detail = _last_detail(trace, "document_qa")
    response_detail = _last_detail(trace, "phase4_response")
    denied_detail = _last_detail(trace, "denied_response")

    # The graph's node trace is the source of truth for node-level observability.
    # Some versions may also return selected state fields at top-level.
    authorization = result.get("authorization")
    if not isinstance(authorization, dict):
        authorization = {}

    allowed = auth_detail.get("allowed")
    if allowed is None:
        allowed = authorization.get("allowed")

    authorized = result.get("authorized")
    if authorized is None:
        authorized = retrieval_detail.get("authorized")

    integrity = result.get("integrity_verified")
    if integrity is None:
        integrity = retrieval_detail.get("integrity_verified")
    if integrity is None:
        integrity = qa_detail.get("integrity_verified")

    found = result.get("document")
    if found is not None:
        found = isinstance(found, dict) and bool(found)
    else:
        found = retrieval_detail.get("found")

    ipfs_started = retrieval_detail.get("ipfs_retrieval_started")
    if ipfs_started is None:
        ipfs = result.get("ipfs_retrieval")
        if isinstance(ipfs, dict):
            ipfs_started = ipfs.get("started")

    llm_invoked = qa_detail.get("llm_invoked")
    if llm_invoked is None:
        llm_invoked = response_detail.get("llm_invoked")
        if llm_invoked is None:
            llm_invoked = result.get("llm_invoked")

    llm_context_authorized = qa_detail.get("llm_context_authorized")
    if llm_context_authorized is None:
        llm_context_authorized = response_detail.get("llm_context_authorized")
        if llm_context_authorized is None:
            llm_context_authorized = result.get("llm_context_authorized")

    grounded = response_detail.get("grounded_answer_generated")
    if grounded is None:
        grounded = result.get("grounded_answer_generated")

    reason = auth_detail.get("reason") or authorization.get("reason") or denied_detail.get("reason")
    error = result.get("error")

    return {
        "authorized": _bool_or_none(authorized),
        "authorization_allowed": _bool_or_none(allowed),
        "integrity_verified": _bool_or_none(integrity),
        "document_found": _bool_or_none(found),
        "ipfs_retrieval_started": _bool_or_none(ipfs_started),
        "llm_invoked": _bool_or_none(llm_invoked),
        "llm_context_authorized": _bool_or_none(llm_context_authorized),
        "grounded_answer_generated": _bool_or_none(grounded),
        "authorization_reason": reason,
        "retrieval_error": str(error) if error else None,
    }


# ---------------------------------------------------------------------------
# Content scoring without storing confidential answer or ground-truth values.
# ---------------------------------------------------------------------------

def _normalize_fact(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def score_expected_facts(answer: str, expected_facts: Iterable[str]) -> Dict[str, Any]:
    facts = [str(fact).strip() for fact in expected_facts if str(fact).strip()]
    if not facts:
        return {
            "scoring_status": "not_scored_no_verified_ground_truth",
            "expected_fact_count": 0,
            "matched_fact_count": None,
            "fact_coverage": None,
        }
    normalized_answer = _normalize_fact(answer)
    matches = sum(1 for fact in facts if _normalize_fact(fact) in normalized_answer)
    return {
        "scoring_status": "lexical_fact_coverage",
        "expected_fact_count": len(facts),
        "matched_fact_count": matches,
        "fact_coverage": round(matches / len(facts), 4),
    }


def _safe_answer_metadata(answer: str) -> Dict[str, Any]:
    # The answer itself may contain confidential student data; never persist it.
    return {
        "answer_present": bool(answer.strip()),
        "answer_characters": len(answer),
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest() if answer else None,
    }


# ---------------------------------------------------------------------------
# Evaluation execution
# ---------------------------------------------------------------------------

def run_authorized_cases(
    cases: List[Dict[str, Any]],
    user_context: Dict[str, Any],
    judge: Optional[LLMJudge] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['case_id']} ({case['document_id']}) ...", flush=True)
        started = time.perf_counter()
        try:
            # Same public entry point and user_context pattern used by app.py.
            result = answer_question(case["question"], user_context=user_context)
            latency_ms = (time.perf_counter() - started) * 1000.0
            answer = str(result.get("answer", "")) if isinstance(result, dict) else str(result)
            checks = inspect_result(result)
            content_score = score_expected_facts(answer, case.get("expected_facts", []))
            row = {
                "case_id": case["case_id"],
                "document_id": case["document_id"],
                "student_id": case["student_id"],
                "category": case.get("category"),
                "latency_ms": round(latency_ms, 2),
                **checks,
                **content_score,
                **_safe_answer_metadata(answer),
            }
            # QA is eligible for answer-quality analysis only if all security gates pass.
            row["qa_eligible"] = (
                checks["authorization_allowed"] is True
                and checks["authorized"] is True
                and checks["document_found"] is True
                and checks["integrity_verified"] is True
                and checks["llm_invoked"] is True
                and checks["llm_context_authorized"] is True
            )
            if row["qa_eligible"] and content_score["scoring_status"] == "not_scored_no_verified_ground_truth":
                row["qa_quality_status"] = "retrieval_and_llm_passed_content_not_scored"
            elif row["qa_eligible"]:
                row["qa_quality_status"] = "scored_against_supplied_facts"
            else:
                row["qa_quality_status"] = "not_scored_security_or_retrieval_gate_failed"

            # Judge only eligible answers. The answer and reference facts remain
            # in memory and are never written to the evaluation report.
            if judge is not None:
                if row["qa_eligible"] and case.get("expected_facts") and answer.strip():
                    try:
                        judged = judge.evaluate(
                            question=case["question"],
                            expected_facts=case["expected_facts"],
                            generated_answer=answer,
                        )
                        row["llm_judge"] = {
                            "status": "scored",
                            "correctness": judged["correctness"],
                            "completeness": judged["completeness"],
                            "reference_alignment": judged["reference_alignment"],
                            "hallucination": judged["hallucination"],
                            "verdict": judged["verdict"],
                        }
                        # Deliberately omit judge explanation: it may quote
                        # confidential student information.
                    except Exception as judge_exc:
                        row["llm_judge"] = {
                            "status": "judge_error",
                            "error_type": type(judge_exc).__name__,
                        }
                elif not row["qa_eligible"]:
                    row["llm_judge"] = {"status": "not_scored_security_or_retrieval_gate_failed"}
                elif not case.get("expected_facts"):
                    row["llm_judge"] = {"status": "not_scored_no_verified_ground_truth"}
                else:
                    row["llm_judge"] = {"status": "not_scored_empty_answer"}

            rows.append(row)
            print(
                "    retrieval={retrieval} integrity={integrity} llm={llm} latency={latency:.0f}ms".format(
                    retrieval="PASS" if row["document_found"] is True else "NO/UNKNOWN",
                    integrity="PASS" if row["integrity_verified"] is True else "NO/UNKNOWN",
                    llm="YES" if row["llm_invoked"] is True else "NO/UNKNOWN",
                    latency=latency_ms,
                ),
                flush=True,
            )
        except Exception as exc:  # keep the batch moving; never print document content
            latency_ms = (time.perf_counter() - started) * 1000.0
            rows.append({
                "case_id": case["case_id"],
                "document_id": case["document_id"],
                "student_id": case["student_id"],
                "category": case.get("category"),
                "latency_ms": round(latency_ms, 2),
                "execution_error_type": type(exc).__name__,
                "qa_eligible": False,
                "qa_quality_status": "not_scored_execution_error",
            })
            print(f"    ERROR: {type(exc).__name__}", flush=True)
    return rows


def run_unauthenticated_security_cases(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Verify confidential retrieval is denied when user_context is absent."""
    rows: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        question = case["question"]
        print(f"[SEC {index}/{len(cases)}] unauthenticated {case['document_id']} ...", flush=True)
        started = time.perf_counter()
        try:
            result = answer_question(question, user_context=None)
            latency_ms = (time.perf_counter() - started) * 1000.0
            checks = inspect_result(result)
            # Expected security behaviour: deny before confidential IPFS retrieval and LLM.
            passed = (
                checks["authorization_allowed"] is False
                and checks["ipfs_retrieval_started"] is not True
                and checks["llm_invoked"] is False
                and checks["llm_context_authorized"] is not True
            )
            rows.append({
                "case_id": f"SEC-UNAUTH-{case['case_id']}",
                "document_id": case["document_id"],
                "student_id": case["student_id"],
                "latency_ms": round(latency_ms, 2),
                **checks,
                "security_test": "unauthenticated_document_access",
                "security_test_passed": passed,
            })
            print(f"    {'PASS' if passed else 'FAIL/UNKNOWN'} ({latency_ms:.0f}ms)", flush=True)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            rows.append({
                "case_id": f"SEC-UNAUTH-{case['case_id']}",
                "document_id": case["document_id"],
                "student_id": case["student_id"],
                "latency_ms": round(latency_ms, 2),
                "security_test": "unauthenticated_document_access",
                "security_test_passed": False,
                "execution_error_type": type(exc).__name__,
            })
            print(f"    ERROR: {type(exc).__name__}", flush=True)
    return rows


# ---------------------------------------------------------------------------
# Summary and report
# ---------------------------------------------------------------------------

def _mean(values: List[float]) -> Optional[float]:
    return round(statistics.mean(values), 2) if values else None


def build_summary(qa_rows: List[Dict[str, Any]], security_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in qa_rows if isinstance(row.get("latency_ms"), (int, float))]
    eligible = [row for row in qa_rows if row.get("qa_eligible") is True]
    scored = [row for row in eligible if isinstance(row.get("fact_coverage"), (int, float))]
    verified = [row for row in qa_rows if row.get("integrity_verified") is True and row.get("document_found") is True]
    judged = [row["llm_judge"] for row in qa_rows if isinstance(row.get("llm_judge"), dict) and row["llm_judge"].get("status") == "scored"]
    judge_summary = {
        "enabled": any("llm_judge" in row for row in qa_rows),
        "scored_cases": len(judged),
        "judge_errors": sum(1 for row in qa_rows if isinstance(row.get("llm_judge"), dict) and row["llm_judge"].get("status") == "judge_error"),
        "mean_correctness": round(statistics.mean([item["correctness"] for item in judged]), 3) if judged else None,
        "mean_completeness": round(statistics.mean([item["completeness"] for item in judged]), 3) if judged else None,
        "mean_reference_alignment": round(statistics.mean([item["reference_alignment"] for item in judged]), 3) if judged else None,
        "mean_hallucination": round(statistics.mean([item["hallucination"] for item in judged]), 3) if judged else None,
        "verdict_counts": {verdict: sum(1 for item in judged if item["verdict"] == verdict) for verdict in ("correct", "partially_correct", "incorrect")},
    }
    return {
        "qa_cases": len(qa_rows),
        "retrieval_and_integrity_verified": len(verified),
        "qa_eligible_after_security_gates": len(eligible),
        "answer_quality_scored_cases": len(scored),
        "mean_latency_ms": _mean(latencies),
        "mean_fact_coverage": round(statistics.mean([row["fact_coverage"] for row in scored]), 4) if scored else None,
        "llm_judge": judge_summary,
        "unauthenticated_security_cases": len(security_rows),
        "unauthenticated_security_passed": sum(1 for row in security_rows if row.get("security_test_passed") is True),
        "unauthenticated_security_failed_or_unknown": sum(1 for row in security_rows if row.get("security_test_passed") is not True),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate live university document QA via LangGraph + RBAC.")
    parser.add_argument("--username", help="Existing account username. If omitted, you will be prompted.")
    parser.add_argument("--cases-json", help="Optional JSON file with cases and manually verified expected_facts.")
    parser.add_argument("--output", default=None, help="Report path (default: evaluation_reports/live_document_qa_<timestamp>.json).")
    parser.add_argument("--skip-security-tests", action="store_true", help="Skip unauthenticated-access negative tests.")
    parser.add_argument("--security-sample", type=int, default=4, help="Number of documents used for unauthenticated tests (default: 4).")
    parser.add_argument("--llm-judge", action="store_true", help="Enable local Ollama LLM-as-a-Judge for eligible QA answers.")
    parser.add_argument("--judge-model", default="llama3", help="Local Ollama model used as judge (default: llama3).")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434", help="Local Ollama base URL.")
    args = parser.parse_args()

    cases = load_cases(args.cases_json)
    if args.security_sample < 0:
        parser.error("--security-sample must be zero or greater")

    username = (args.username or input("Existing account username: ")).strip()
    if not username:
        parser.error("A username is required")
    user_context = authenticate_user(username)

    role = str(user_context.get("role", "UNKNOWN")).upper()
    print(f"\nAuthenticated evaluation account: {username} (role={role})")
    print(f"Repository: {REPO_ROOT}")
    print(f"QA cases: {len(cases)}")
    print("Note: document contents and generated answers will not be saved to the report.\n")

    judge: Optional[LLMJudge] = None
    if args.llm_judge:
        print(f"Initializing local LLM judge: {args.judge_model} at {args.ollama_base_url}")
        try:
            judge = LLMJudge(JudgeConfig(model=args.judge_model, base_url=args.ollama_base_url, temperature=0.0))
        except LLMJudgeError as exc:
            raise SystemExit(f"Could not initialize LLM judge: {exc}") from exc

    qa_rows = run_authorized_cases(cases, user_context, judge=judge)
    security_rows: List[Dict[str, Any]] = []
    if not args.skip_security_tests and args.security_sample:
        security_rows = run_unauthenticated_security_cases(cases[: args.security_sample])

    report = {
        "evaluation": "live_confidential_document_qa",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository_root": str(REPO_ROOT),
        "agent_entry_point": "python_backend.ai.agent.answer_question",
        "authentication_method": "python_backend.rbac.authenticate",
        "user": {
            "username": username,
            "role": role,
            "student_id_present": bool(user_context.get("student_id")),
            # Never persist uid, wallet address, password, or password hash.
        },
        "configuration": {
            "case_source": str(Path(args.cases_json).expanduser().resolve()) if args.cases_json else "built_in_live_document_sample",
            "security_tests_enabled": not args.skip_security_tests,
            "security_sample_size": len(security_rows),
            "answer_text_persisted": False,
            "document_plaintext_persisted": False,
            "ground_truth_source": "manually_verified_expected_facts_only_when_supplied",
            "fact_metric": "normalized lexical fact coverage; not semantic equivalence",
            "llm_judge_enabled": bool(args.llm_judge),
            "llm_judge_model": args.judge_model if args.llm_judge else None,
            "llm_judge_base_url": args.ollama_base_url if args.llm_judge else None,
            "llm_judge_method": "local reference-based LLM-as-a-Judge; scores correctness, completeness, reference alignment, and hallucination",
            "llm_judge_persists_answer_or_explanation": False,
        },
        "summary": build_summary(qa_rows, security_rows),
        "qa_results": qa_rows,
        "security_results": security_rows,
        "interpretation_notes": [
            "Retrieval success and integrity verification are not measures of answer correctness.",
            "Answer-quality scoring is performed only after authorization, retrieval, integrity, and LLM-context gates pass.",
            "Cases without manually verified expected_facts are reported as not scored.",
            "Lexical fact coverage is a simple deterministic measure and may miss semantically correct paraphrases.",
            "LLM-as-a-Judge uses the same local model when enabled; self-evaluation bias is possible and should be checked through manual review.",
            "The judge compares answers with manually verified expected facts; it does not independently verify the full source document or establish document-level faithfulness.",
            "Judge explanations and generated answers are not persisted because they may contain confidential student information.",
            "Unauthenticated tests check that access is denied before IPFS retrieval and LLM invocation.",
            "This is a sequential functional evaluation, not a concurrent-load benchmark.",
        ],
    }

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_dir = REPO_ROOT / "evaluation_reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"live_document_qa_{timestamp}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Restrict permissions where supported: reports may reveal document identifiers.
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    try:
        os.chmod(output_path, 0o600)
    except OSError:
        pass

    summary = report["summary"]
    print("\n================ LIVE DOCUMENT QA SUMMARY ================")
    print(f"QA cases:                         {summary['qa_cases']}")
    print(f"Retrieved + integrity verified:   {summary['retrieval_and_integrity_verified']}")
    print(f"Passed all QA security gates:     {summary['qa_eligible_after_security_gates']}")
    print(f"Cases with answer-quality score:  {summary['answer_quality_scored_cases']}")
    print(f"Mean latency:                     {summary['mean_latency_ms']} ms")
    print(f"Mean lexical fact coverage:       {summary['mean_fact_coverage']}")
    if args.llm_judge:
        judge_summary = summary["llm_judge"]
        print(f"LLM judge scored cases:            {judge_summary['scored_cases']}")
        print(f"Mean judge correctness:            {judge_summary['mean_correctness']}/5")
        print(f"Mean judge completeness:           {judge_summary['mean_completeness']}/5")
        print(f"Mean judge reference alignment:    {judge_summary['mean_reference_alignment']}/5")
        print(f"Mean judge hallucination:           {judge_summary['mean_hallucination']}/5 (lower is better)")
    print(f"Unauthenticated security passed:  {summary['unauthenticated_security_passed']}/{summary['unauthenticated_security_cases']}")
    print(f"\nPrivacy-safe report saved to: {output_path}")
    if summary["answer_quality_scored_cases"] == 0:
        print("\nNOTE: No answer-quality score was calculated. Add manually verified expected_facts to a cases JSON file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
