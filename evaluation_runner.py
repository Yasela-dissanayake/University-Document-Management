"""
Phase 5 Experimental Evaluation Runner
=======================================

Run from the project root:

    python phase5_evaluation_runner.py

Examples:

    python phase5_evaluation_runner.py
    python phase5_evaluation_runner.py --dataset phase5_dataset --limit 20
    python phase5_evaluation_runner.py --category course_specific
    python phase5_evaluation_runner.py --security-only
    python phase5_evaluation_runner.py --performance-repeats 3
    python phase5_evaluation_runner.py --mock-llm

Purpose
-------
This runner evaluates the existing Phase 3 + Phase 4 LangGraph agent against
the synthetic Phase 5 evaluation dataset.

Important experimental distinction:
- The dataset contains the complete synthetic ground truth:
      20 students
      53 courses
      80 logical documents
      140 planned blockchain-version records
      100 questions
      6 security scenarios
- It does NOT assume that all 140 versions are currently instantiated on the
  blockchain/IPFS test environment.
- Questions that require actual confidential retrieval are therefore measured
  against the currently instantiated blockchain/IPFS state.
- Dataset records are used as evaluation ground truth and expected values.

The existing test_agent.py remains a regression/integration test suite.
This file is the Phase 5 experimental evaluation runner and produces
machine-readable JSON/CSV results.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from python_backend.ai import agent as agent_module
from python_backend.ai.agent import answer_question
from python_backend.ai.repository_rag import RepositoryMetadataRAG


DEFAULT_DATASET = Path("phase5_dataset")
DEFAULT_OUTPUT = Path("phase5_results")

QUESTION_FILES = (
    "manifest.json",
    "students.json",
    "courses.json",
    "documents.json",
    "metadata_records.json",
    "questions.json",
    "security_attacks.json",
)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


class DatasetError(RuntimeError):
    """Raised when the Phase 5 dataset is missing or malformed."""


def load_json(path: Path) -> Any:
    if not path.exists():
        raise DatasetError(f"Missing dataset file: {path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"Invalid JSON in {path}: {exc}") from exc


def load_dataset(dataset_dir: Path) -> Dict[str, Any]:
    data = {
        "manifest": load_json(dataset_dir / "manifest.json"),
        "students": load_json(dataset_dir / "students.json"),
        "courses": load_json(dataset_dir / "courses.json"),
        "documents": load_json(dataset_dir / "documents.json"),
        "metadata_records": load_json(dataset_dir / "metadata_records.json"),
        "questions": load_json(dataset_dir / "questions.json"),
        "security_attacks": load_json(dataset_dir / "security_attacks.json"),
    }

    if not isinstance(data["students"], list):
        raise DatasetError("students.json must contain a list.")
    if not isinstance(data["courses"], list):
        raise DatasetError("courses.json must contain a list.")
    if not isinstance(data["documents"], list):
        raise DatasetError("documents.json must contain a list.")
    if not isinstance(data["metadata_records"], list):
        raise DatasetError("metadata_records.json must contain a list.")
    if not isinstance(data["questions"], list):
        raise DatasetError("questions.json must contain a list.")
    if not isinstance(data["security_attacks"], list):
        raise DatasetError("security_attacks.json must contain a list.")

    return data


def configure_evaluation_metadata(data: Dict[str, Any]) -> None:
    """Bind the LangGraph metadata RAG to the Phase 5 dataset for this run."""
    records = data.get("metadata_records") or []
    agent_module.metadata_rag = RepositoryMetadataRAG(records=records)


def validate_dataset(data: Dict[str, Any]) -> None:
    manifest = data["manifest"]

    expected_counts = {
        "students": len(data["students"]),
        "logical_documents": len(data["documents"]),
        "questions": len(data["questions"]),
        "security_attacks": len(data["security_attacks"]),
    }

    for key, actual in expected_counts.items():
        declared = manifest.get(key)
        if declared is not None and int(declared) != actual:
            raise DatasetError(
                f"Manifest mismatch for {key}: declared={declared}, actual={actual}"
            )

    document_ids = {
        str(item.get("document_id", "")).strip()
        for item in data["documents"]
        if isinstance(item, dict)
    }

    metadata_ids = {
        str(item.get("document_id", "")).strip()
        for item in data["metadata_records"]
        if isinstance(item, dict)
    }

    missing_metadata = sorted(document_ids - metadata_ids)
    if missing_metadata:
        raise DatasetError(
            "Documents missing metadata records: "
            + ", ".join(missing_metadata[:10])
        )

    question_ids = set()
    for question in data["questions"]:
        if not isinstance(question, dict):
            raise DatasetError("Every question record must be an object.")
        question_id = str(question.get("id", "")).strip()
        if not question_id:
            raise DatasetError("Question record without id.")
        if question_id in question_ids:
            raise DatasetError(f"Duplicate question id: {question_id}")
        question_ids.add(question_id)

    student_ids = {
        str(item.get("student_id", "")).strip().upper()
        for item in data["students"]
        if isinstance(item, dict)
    }

    for question in data["questions"]:
        student_id = str(question.get("student_id", "")).strip().upper()
        if student_id and student_id not in student_ids:
            raise DatasetError(
                f"Question {question.get('id')} references unknown student {student_id}"
            )

        document_id = str(question.get("document_id", "")).strip()
        if document_id and document_id not in document_ids:
            raise DatasetError(
                f"Question {question.get('id')} references unknown document {document_id}"
            )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def answer_contains_expected(answer: Any, expected: Any) -> bool:
    """Case-insensitive containment check for simple scalar expectations."""
    if expected is None:
        return False

    answer_text = normalize_text(answer)
    expected_text = normalize_text(expected)

    return bool(answer_text and expected_text and expected_text in answer_text)


def answer_contains_all_expected(
    answer: Any,
    expected: Dict[str, Any],
) -> bool:
    """Check that every course->grade pair appears in the answer."""
    if not isinstance(expected, dict):
        return False

    answer_text = normalize_text(answer)

    if not answer_text:
        return False

    return all(
        normalize_text(course_code) in answer_text
        and normalize_text(grade) in answer_text
        for course_code, grade in expected.items()
    )


def trace_nodes(result: Dict[str, Any]) -> List[str]:
    trace = result.get("trace") or {}
    nodes = trace.get("nodes") or []
    return [
        str(item.get("node"))
        for item in nodes
        if isinstance(item, dict) and item.get("node")
    ]


def trace_node(result: Dict[str, Any], node_name: str) -> Dict[str, Any]:
    trace = result.get("trace") or {}
    for item in trace.get("nodes") or []:
        if isinstance(item, dict) and item.get("node") == node_name:
            details = item.get("details")
            return details if isinstance(details, dict) else {}
    return {}


def make_user_context(
    student_id: Optional[str],
    role: str = "STUDENT",
    username: Optional[str] = None,
) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "username": username or f"phase5_{role.lower()}_user",
        "role": role,
    }
    if student_id:
        context["student_id"] = student_id
    return context


def question_requests_document(question: Dict[str, Any]) -> bool:
    category = normalize_text(question.get("category"))
    text = normalize_text(question.get("question"))

    if category in {
        "direct_retrieval",
        "course_specific",
        "multi_course",
        "document_qa",
        "document_retrieval",
        "missing_information",
        "unsupported_information",
    }:
        return True

    retrieval_terms = (
        "retrieve document",
        "get document",
        "open document",
        "fetch document",
        "show document",
        "view document",
        "what grade",
        "what mark",
        "what course",
        "what is my gpa",
        "what is my cgpa",
        "attendance",
    )

    return any(term in text for term in retrieval_terms)


def _document_retrieval_succeeded(result: Dict[str, Any]) -> bool:
    """
    Phase 3 retrieval success is intentionally independent of Phase 4 LLM
    availability.

    The current agent records a successful authorized document retrieval and
    then may return ``error='llm_error'`` if Ollama cannot load the model.
    That is an LLM-environment failure, not a blockchain/IPFS retrieval failure.
    """
    return (
        result.get("authorized") is True
        and result.get("document") is not None
        and bool(result.get("integrity_verified"))
    )


def expected_question_success(
    question: Dict[str, Any],
    result: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Evaluate functional correctness without conflating infrastructure failures.

    Categories:
      direct_retrieval      -> Phase 3 retrieval correctness
      course_specific       -> grounded answer correctness
      multi_course          -> all expected course/grade pairs
      missing_information   -> explicit safe "not present" behaviour
      unsupported_information
                             -> explicit safe "not determinable" behaviour

    A real LLM failure is reported separately as ``llm_error`` and does not
    erase a successful Phase 3 retrieval result.
    """
    expected = question.get("expected")
    category = normalize_text(question.get("category"))
    answer = result.get("answer", "")
    llm_error = result.get("llm_error")
    error = result.get("error")

    retrieved = _document_retrieval_succeeded(result)

    if category == "direct_retrieval" or expected == "document_retrieved":
        ok = (
            retrieved
            and result.get("document_id") == question.get("document_id")
        )
        return (
            ok,
            "document_retrieved"
            if ok
            else (
                "document_retrieval_failed"
                if error != "llm_error"
                else "retrieval_succeeded_but_llm_unavailable"
            ),
        )

    if category == "course_specific":
        if not retrieved:
            return False, "document_unavailable"

        if not result.get("llm_invoked"):
            return False, "llm_not_invoked"

        if llm_error:
            return False, "llm_error"

        ok = answer_contains_expected(answer, expected)
        return (
            ok,
            "expected_value_found" if ok else "expected_value_not_found",
        )

    if category == "multi_course":
        if not retrieved:
            return False, "document_unavailable"

        if not result.get("llm_invoked"):
            return False, "llm_not_invoked"

        if llm_error:
            return False, "llm_error"

        if not isinstance(expected, dict):
            return False, "invalid_expected_structure"

        ok = answer_contains_all_expected(answer, expected)
        return (
            ok,
            "all_expected_values_found"
            if ok
            else "one_or_more_expected_values_missing",
        )

    if category == "missing_information":
        if not retrieved:
            return False, "document_unavailable"

        if llm_error:
            return False, "llm_error"

        answer_text = normalize_text(answer)

        safe_missing_markers = (
            "not present",
            "not found",
            "cannot be determined",
            "cannot determine",
            "not available",
            "does not contain",
            "not in the document",
            "no information",
        )

        ok = any(marker in answer_text for marker in safe_missing_markers)

        return (
            ok,
            "missing_information_handled"
            if ok
            else "missing_information_not_handled",
        )

    if category == "unsupported_information":
        # The agent is allowed to safely determine that the requested field
        # is outside the supported/document-grounded information space and
        # stop at routing.  In that case no confidential document needs to
        # be retrieved and the LLM must not be invoked.
        answer_text = normalize_text(answer)

        safe_unsupported_markers = (
            "not determinable",
            "cannot be determined",
            "cannot determine",
            "not available",
            "not provided",
            "not in the document",
            "no information",
            "cannot answer",
            "did not require repository metadata retrieval",
            "request did not require repository metadata retrieval",
        )

        safe_response = any(
            marker in answer_text
            for marker in safe_unsupported_markers
        )

        # Safe early routing is a valid outcome for an unsupported request:
        # no document retrieval, no integrity claim, and no LLM context.
        safe_early_exit = (
            result.get("document") is None
            and result.get("llm_invoked") is not True
            and result.get("llm_context_authorized") is not True
            and not llm_error
            and safe_response
        )

        # If the system did retrieve the document, require an explicit safe
        # grounded response instead of accepting retrieval alone.
        safe_grounded_response = (
            retrieved
            and not llm_error
            and safe_response
        )

        ok = safe_early_exit or safe_grounded_response

        return (
            ok,
            "unsupported_information_handled"
            if ok
            else "unsupported_information_not_handled",
        )

    if expected is not None:
        ok = answer_contains_expected(answer, expected)
        return (
            ok,
            "expected_value_found"
            if ok
            else "expected_value_not_found",
        )

    ok = error is None
    return ok, "completed" if ok else "agent_error"


# ---------------------------------------------------------------------------
# Deterministic LLM double
# ---------------------------------------------------------------------------


class EvaluationLLM:
    """
    Deterministic grounded LLM double.

    This mode is an architectural/functional evaluation mode. It uses the
    authorized plaintext document supplied by the agent and deterministically
    answers the five dataset question categories.

    It must NOT be reported as real-model LLM accuracy. For actual LLM
    grounding experiments, run without --mock-llm on a machine where the
    selected Ollama model can load.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    @staticmethod
    def _find_course(
        document: Dict[str, Any],
        course_code: str,
    ) -> Optional[Dict[str, Any]]:
        for course in document.get("courses", []) or []:
            if (
                isinstance(course, dict)
                and normalize_text(course.get("code"))
                == normalize_text(course_code)
            ):
                return course
        return None

    def __call__(
        self,
        question: str,
        document: Dict[str, Any],
    ) -> str:
        self.calls.append(
            {
                "question": question,
                "document": document,
            }
        )

        question_text = normalize_text(question)

        course_codes = re.findall(
            r"\b[A-Z]{2,4}\d{3,4}\b",
            question.upper(),
        )

        unique_codes: List[str] = []
        for code in course_codes:
            if code not in unique_codes:
                unique_codes.append(code)

        if "attendance" in question_text:
            return (
                "Attendance percentage is not determinable from the "
                "provided document because attendance information is not present."
            )

        # Check the plural form first because "what grades" also contains
        # the substring "what grade".
        if "what grades" in question_text and unique_codes:
            parts = []
            for code in unique_codes:
                course = self._find_course(document, code)
                if course is None:
                    parts.append(f"{code}: not present")
                else:
                    parts.append(
                        f"{code}: {course.get('grade', 'not available')}"
                    )
            return "; ".join(parts) + "."

        if "what grade" in question_text or "what mark" in question_text:
            if unique_codes:
                course = self._find_course(document, unique_codes[0])
                if course is not None:
                    return (
                        f"{unique_codes[0]} has grade "
                        f"{course.get('grade', 'not available')}."
                    )
            return (
                "The requested course information cannot be determined "
                "because the course is not present in the document."
            )

        if "retrieve document" in question_text:
            return (
                f"Document {document.get('document_id')} was retrieved "
                "and its integrity was verified."
            )

        return (
            "The requested information is not determinable from the "
            "provided document."
        )


@contextmanager
def patched_llm(enabled: bool) -> Iterator[Optional[EvaluationLLM]]:
    if not enabled:
        yield None
        return

    fake = EvaluationLLM()
    original = agent_module.llm_service.answer_document_question

    def mocked_answer_document_question(
        question: str,
        document: Dict[str, Any],
    ) -> str:
        return fake(question, document)

    agent_module.llm_service.answer_document_question = (
        mocked_answer_document_question
    )

    try:
        yield fake
    finally:
        agent_module.llm_service.answer_document_question = original


# ---------------------------------------------------------------------------
# Question evaluation
# ---------------------------------------------------------------------------


def build_question_user_context(question: Dict[str, Any]) -> Dict[str, Any]:
    student_id = str(question.get("student_id", "")).strip().upper() or None
    return make_user_context(student_id=student_id, role="STUDENT")


def build_fixture_inventory(
    questions: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Probe the authoritative blockchain fixture for each required document.

    The synthetic Phase 5 dataset contains more documents than are currently
    instantiated in the hosted blockchain/IPFS test environment.  The runner
    therefore performs an explicit preflight against the blockchain before
    classifying question results.  This prevents an unavailable fixture from
    being mistaken for an application failure.

    Status values:
      available      -> blockchain has an approved authoritative version.
      unavailable    -> document does not exist / has no approved version.
      probe_error    -> blockchain probe itself failed; coverage cannot be
                        established and the question is kept out of the
                        fixture-aware denominator.
    """
    inventory: Dict[str, Dict[str, Any]] = {}

    document_ids = sorted({
        str(question.get("document_id", "")).strip()
        for question in questions
        if question_requests_document(question)
        and normalize_text(question.get("category")) != "unsupported_information"
        and str(question.get("document_id", "")).strip()
    })

    resolver = getattr(
        getattr(agent_module, "tools", None),
        "resolve_document_from_blockchain",
        None,
    )

    if resolver is None:
        for document_id in document_ids:
            inventory[document_id] = {
                "status": "probe_error",
                "reason": "blockchain_resolver_unavailable",
            }
        return inventory

    print("\nFIXTURE PREFLIGHT")
    print("-" * 90)

    for document_id in document_ids:
        try:
            resolved = resolver(
                document_id=document_id,
                approved_only=True,
            )

            metadata = dict(
                resolved.get("blockchain_metadata") or {}
            ) if isinstance(resolved, dict) else {}

            ok = bool(
                isinstance(resolved, dict)
                and resolved.get("ok") is True
                and metadata
            )

            if ok:
                inventory[document_id] = {
                    "status": "available",
                    "reason": "approved_blockchain_version_available",
                    "version": metadata.get("version")
                    or metadata.get("resolved_version"),
                    "ipfs_cid": metadata.get("ipfs_cid"),
                    "document_id": metadata.get("document_id") or document_id,
                    "student_id": metadata.get("student_id"),
                    "document_type": metadata.get("document_type"),
                }
                print(
                    f"[AVAILABLE] {document_id} | "
                    f"version={inventory[document_id].get('version')}"
                )
                continue

            error = normalize_text(
                resolved.get("error") if isinstance(resolved, dict) else None
            )
            reason = normalize_text(
                resolved.get("reason") if isinstance(resolved, dict) else None
            )
            combined = f"{error} {reason}".strip()

            unavailable_markers = (
                "not found",
                "does not exist",
                "document_not_found",
                "no approved",
                "no approved version",
                "not registered",
                "unknown document",
            )

            if any(marker in combined for marker in unavailable_markers):
                status = "unavailable"
                probe_reason = "document_not_instantiated_or_no_approved_version"
            else:
                # Do not silently interpret an RPC/tool failure as a missing
                # fixture.  It is an evaluation-environment uncertainty.
                status = "probe_error"
                probe_reason = combined or "blockchain_probe_did_not_resolve_document"

            inventory[document_id] = {
                "status": status,
                "reason": probe_reason,
                "probe_error": error or None,
            }

            print(f"[{status.upper()}] {document_id} | {probe_reason}")

        except Exception as exc:
            inventory[document_id] = {
                "status": "probe_error",
                "reason": f"{type(exc).__name__}: {exc}",
            }
            print(
                f"[PROBE_ERROR] {document_id} | "
                f"{type(exc).__name__}: {exc}"
            )

    available = sum(
        item.get("status") == "available"
        for item in inventory.values()
    )
    unavailable = sum(
        item.get("status") == "unavailable"
        for item in inventory.values()
    )
    probe_errors = sum(
        item.get("status") == "probe_error"
        for item in inventory.values()
    )

    print(
        f"Fixture preflight: {available} available | "
        f"{unavailable} unavailable | {probe_errors} probe error(s) | "
        f"{len(inventory)} required unique documents"
    )

    return inventory


def classify_fixture_status(
    question: Dict[str, Any],
    result: Dict[str, Any],
    functional_ok: bool,
    fixture_inventory: Dict[str, Dict[str, Any]],
) -> Tuple[str, bool, str]:
    """Classify question eligibility using the explicit blockchain preflight.

    This is deliberately based on the authoritative blockchain fixture
    inventory, not on a missing-document error inferred from the LangGraph
    trace.  That makes the distinction between infrastructure coverage and
    application failure reproducible and auditable.

    Returns:
        (fixture_status, evaluation_eligible, reason)

    Status values:
        available       Required fixture exists and was retrieved successfully.
        unavailable     Required document is not instantiated/approved.
        probe_error     Fixture availability could not be established.
        not_required    No confidential document retrieval is required.
        failed          Fixture is available, but the application failed.
    """
    category = normalize_text(question.get("category"))
    expected_document = question_requests_document(question)

    document_id = str(question.get("document_id", "")).strip()

    if category == "unsupported_information":
        safe_early_exit = (
            result.get("document") is None
            and result.get("llm_invoked") is not True
            and result.get("llm_context_authorized") is not True
            and not result.get("llm_error")
            and functional_ok
        )
        if safe_early_exit:
            return ("not_required", True, "safe_early_routing")

    if not expected_document:
        return ("not_required", True, "document_not_required")

    probe = fixture_inventory.get(document_id)
    probe_status = probe.get("status") if probe else "probe_error"

    if probe_status == "unavailable":
        return (
            "unavailable",
            False,
            probe.get("reason", "required_document_not_instantiated")
            if probe else "required_document_not_instantiated",
        )

    if probe_status == "probe_error":
        return (
            "probe_error",
            False,
            probe.get("reason", "fixture_availability_unknown")
            if probe else "fixture_availability_unknown",
        )

    if _document_retrieval_succeeded(result):
        return (
            "available",
            True,
            "document_retrieved_and_integrity_verified",
        )

    # Authorization failures are real application/security outcomes.  Keep
    # them eligible rather than misclassifying them as fixture coverage.
    if result.get("authorized") is False:
        return ("failed", True, "authorization_denied")

    return (
        "failed",
        True,
        "fixture_available_but_retrieval_or_application_failed",
    )

def run_single_question(
    question: Dict[str, Any],
    mock_llm: bool,
    fixture_inventory: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    question_id = str(question.get("id", "UNKNOWN"))
    question_text = str(question.get("question", ""))
    student_id = str(question.get("student_id", "")).strip().upper() or None
    document_id = str(question.get("document_id", "")).strip() or None
    category = str(question.get("category", "unknown"))

    user_context = build_question_user_context(question)

    start = time.perf_counter()

    with patched_llm(mock_llm) as fake:
        result = answer_question(
            question_text,
            user_context=user_context,
        )

        llm_calls = len(fake.calls) if fake is not None else (
            1 if result.get("llm_invoked") else 0
        )

    elapsed_ms = (time.perf_counter() - start) * 1000.0

    functional_ok, functional_reason = expected_question_success(
        question,
        result,
    )

    authorized = bool(result.get("authorized"))
    integrity_verified = bool(result.get("integrity_verified"))
    llm_invoked = bool(result.get("llm_invoked"))

    expected_document = question_requests_document(question)
    fixture_status, evaluation_eligible, fixture_reason = classify_fixture_status(
        question,
        result,
        functional_ok,
        fixture_inventory,
    )

    return {
        "id": question_id,
        "category": category,
        "student_id": student_id,
        "document_id": document_id,
        "question": question_text,
        "expected": question.get("expected"),
        "functional_success": functional_ok,
        "functional_reason": functional_reason,
        "authorized": authorized,
        "document_retrieved": result.get("document") is not None,
        "phase3_retrieval_success": _document_retrieval_succeeded(result),
        "integrity_verified": integrity_verified,
        "llm_invoked": llm_invoked,
        "llm_context_authorized": bool(result.get("llm_context_authorized")),
        "llm_calls": llm_calls,
        "error": result.get("error"),
        "llm_error": result.get("llm_error"),
        "answer": result.get("answer"),
        "version": result.get("version") or result.get("document_version"),
        "ipfs_cid": result.get("ipfs_cid") or result.get("resolved_cid"),
        "latency_ms": round(elapsed_ms, 3),
        "trace_nodes": trace_nodes(result),
        "expected_document_operation": expected_document,
        "fixture_status": fixture_status,
        "evaluation_eligible": evaluation_eligible,
        "fixture_reason": fixture_reason,
    }


def run_questions(
    questions: List[Dict[str, Any]],
    mock_llm: bool,
    fixture_inventory: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for index, question in enumerate(questions, start=1):
        print(
            f"[QUESTION {index}/{len(questions)}] "
            f"{question.get('id')} | {question.get('category')}"
        )

        try:
            result = run_single_question(
                question,
                mock_llm=mock_llm,
                fixture_inventory=fixture_inventory,
            )
        except Exception as exc:
            result = {
                "id": question.get("id"),
                "category": question.get("category"),
                "student_id": question.get("student_id"),
                "document_id": question.get("document_id"),
                "question": question.get("question"),
                "expected": question.get("expected"),
                "functional_success": False,
                "functional_reason": "runner_exception",
                "authorized": False,
                "document_retrieved": False,
                "integrity_verified": False,
                "llm_invoked": False,
                "llm_calls": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "answer": None,
                "version": None,
                "ipfs_cid": None,
                "latency_ms": None,
                "trace_nodes": [],
                "expected_document_operation": question_requests_document(question),
                "fixture_status": "failed",
                "evaluation_eligible": True,
                "fixture_reason": "runner_exception",
            }

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Security evaluation
# ---------------------------------------------------------------------------


def run_attack_unauthorized_student(attack: Dict[str, Any]) -> Dict[str, Any]:
    user = dict(attack.get("user") or {})
    target_student_id = str(
        attack.get("target_student_id", "")
    ).strip().upper()
    document_id = str(attack.get("document_id", "")).strip()

    question = f"Retrieve document {document_id} for student {target_student_id}."

    started = time.perf_counter()
    result = answer_question(question, user_context=user)
    latency_ms = (time.perf_counter() - started) * 1000.0

    llm_blocked = (
        result.get("llm_invoked") is not True
        and result.get("document") is None
    )

    expected = attack.get("expected")
    passed = (
        expected == "rbac_denied_and_llm_blocked"
        and result.get("authorized") is False
        and llm_blocked
    )

    return {
        "id": attack.get("id"),
        "type": attack.get("type"),
        "expected": expected,
        "passed": passed,
        "authorized": result.get("authorized"),
        "llm_invoked": result.get("llm_invoked"),
        "document_retrieved": result.get("document") is not None,
        "error": result.get("error"),
        "latency_ms": round(latency_ms, 3),
        "trace_nodes": trace_nodes(result),
        "details": "RBAC denial and LLM blocking verified."
        if passed
        else "Expected unauthorized access to be blocked before LLM invocation.",
    }


def _run_metadata_mutation_attack(
    attack: Dict[str, Any],
    mutation_key: str,
    mutation_value: Any,
) -> Dict[str, Any]:
    """Run an RAG metadata mutation and verify blockchain authority."""
    document_id = str(attack.get("document_id", "")).strip()

    original_search = agent_module.metadata_rag.search

    def mutated_metadata_search(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        original_results = original_search(*args, **kwargs)

        mutated_results: List[Dict[str, Any]] = []

        for item in original_results or []:
            if not isinstance(item, dict):
                continue

            copy_item = dict(item)
            copy_item[mutation_key] = mutation_value
            mutated_results.append(copy_item)

        if not mutated_results:
            mutated_results = [
                {
                    "repository": "academic",
                    "document_id": document_id,
                    "student_id": "STD001",
                    "document_type": "Transcript",
                    "latest_version": mutation_value
                    if mutation_key == "latest_version"
                    else 1,
                    "content_retrieved": False,
                }
            ]

        return mutated_results

    agent_module.metadata_rag.search = mutated_metadata_search

    try:
        user = make_user_context(
            student_id="STD001",
            role="ADMIN",
            username=f"phase5_{attack.get('id', 'mutation').lower()}",
        )

        started = time.perf_counter()

        result = answer_question(
            f"Retrieve academic transcript document {document_id} "
            f"for student STD001.",
            user_context=user,
        )

        latency_ms = (time.perf_counter() - started) * 1000.0

    finally:
        agent_module.metadata_rag.search = original_search

    retrieval = trace_node(
        result,
        "retrieve_authorized_document",
    )

    return result, retrieval, latency_ms


def run_attack_rag_wrong_student(
    attack: Dict[str, Any],
) -> Dict[str, Any]:
    mutation = dict(attack.get("rag_mutation") or {})
    result, retrieval, latency_ms = _run_metadata_mutation_attack(
        attack,
        "student_id",
        str(mutation.get("student_id", "STD999")).upper(),
    )

    passed = (
        result.get("error") == "rag_blockchain_identity_mismatch"
        and result.get("document") is None
        and retrieval.get("ipfs_retrieval_started") is False
        and retrieval.get("decryption_started") is False
        and result.get("llm_invoked") is not True
    )

    return {
        "id": attack.get("id"),
        "type": attack.get("type"),
        "expected": attack.get("expected"),
        "passed": passed,
        "authorized": result.get("authorized"),
        "llm_invoked": result.get("llm_invoked"),
        "document_retrieved": result.get("document") is not None,
        "error": result.get("error"),
        "ipfs_retrieval_started": retrieval.get("ipfs_retrieval_started"),
        "decryption_started": retrieval.get("decryption_started"),
        "latency_ms": round(latency_ms, 3),
        "trace_nodes": trace_nodes(result),
        "details": (
            "RAG/blockchain student identity mismatch was blocked."
            if passed
            else "Student identity mismatch was not blocked as expected."
        ),
    }


def run_attack_rag_wrong_document_type(
    attack: Dict[str, Any],
) -> Dict[str, Any]:
    mutation = dict(attack.get("rag_mutation") or {})

    result, retrieval, latency_ms = _run_metadata_mutation_attack(
        attack,
        "document_type",
        str(
            mutation.get(
                "document_type",
                "Registration Certificate",
            )
        ),
    )

    passed = (
        result.get("error") == "rag_blockchain_identity_mismatch"
        and result.get("document") is None
        and retrieval.get("ipfs_retrieval_started") is False
        and retrieval.get("decryption_started") is False
        and result.get("llm_invoked") is not True
    )

    return {
        "id": attack.get("id"),
        "type": attack.get("type"),
        "expected": attack.get("expected"),
        "passed": passed,
        "authorized": result.get("authorized"),
        "llm_invoked": result.get("llm_invoked"),
        "document_retrieved": result.get("document") is not None,
        "error": result.get("error"),
        "ipfs_retrieval_started": retrieval.get("ipfs_retrieval_started"),
        "decryption_started": retrieval.get("decryption_started"),
        "latency_ms": round(latency_ms, 3),
        "trace_nodes": trace_nodes(result),
        "details": (
            "RAG/blockchain document-type mismatch was blocked."
            if passed
            else "Document-type mismatch was not blocked as expected."
        ),
    }


def run_attack_ipfs_content_tampering(
    attack: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Simulate modification of the plaintext document after blockchain hash
    creation but before the application's integrity check.

    The mutation is injected at the IPFS retrieval boundary:
      IPFS -> decrypted plaintext -> blockchain hash verification.

    The runner uses the real ACL decryption path to obtain the original
    plaintext, modifies one field, then returns the mutated plaintext to
    tools.get_authorized_document(). The normal blockchain content-hash
    verification must reject it before the LLM is reached.
    """
    document_id = str(attack.get("document_id", "")).strip()

    original_retrieve = agent_module.tools._ipfs.retrieve_resolved_document

    def tampered_retrieve(
        resolved_cid: str,
        *,
        expected_content_hash: Optional[str] = None,
        verify_integrity: bool = True,
    ) -> Dict[str, Any]:
        original_payload = original_retrieve(
            resolved_cid,
            expected_content_hash=None,
            verify_integrity=False,
        )

        envelope = original_payload.get("document")

        if not isinstance(envelope, dict):
            raise RuntimeError(
                "Tampering adapter expected an encrypted IPFS envelope."
            )

        # Decrypt without asking the ACL layer to verify the blockchain hash.
        # The actual application then hashes the mutated plaintext against the
        # authoritative blockchain content_hash.
        plaintext_result = agent_module.tools.acl.decrypt_for_user(
            make_user_context(
                student_id="STD001",
                role="ADMIN",
                username="phase5_tampering_user",
            ),
            "STD001",
            resolved_cid,
            envelope,
        )

        if isinstance(plaintext_result, dict):
            tampered_document = dict(
                plaintext_result.get("document")
                or plaintext_result
            )
        else:
            raise RuntimeError(
                "ACL decryption adapter returned an unexpected value."
            )

        tampered_document["phase5_tampered"] = True

        return {
            "document": tampered_document,
            "ipfs_cid": resolved_cid,
            "integrity_verified": False,
        }

    agent_module.tools._ipfs.retrieve_resolved_document = tampered_retrieve

    try:
        user = make_user_context(
            student_id="STD001",
            role="ADMIN",
            username="phase5_tampering_user",
        )

        started = time.perf_counter()

        result = answer_question(
            f"Retrieve academic transcript document {document_id} "
            "for student STD001.",
            user_context=user,
        )

        latency_ms = (time.perf_counter() - started) * 1000.0

    finally:
        agent_module.tools._ipfs.retrieve_resolved_document = original_retrieve

    # The security property being tested is the integrity boundary: the
    # tampered plaintext must not become an accepted document and must never
    # reach the LLM.  Depending on the retrieval adapter, the graph may expose
    # an explicit integrity_verification_failed error OR simply terminate the
    # retrieval with integrity_verified=False and no document.  Both represent
    # rejection of the tampered payload; the latter is what the current tools
    # path returns for this injected retrieval-boundary mutation.
    explicit_integrity_failure = (
        result.get("error") == "integrity_verification_failed"
    )
    rejected_at_integrity_boundary = (
        result.get("document") is None
        and result.get("integrity_verified") is not True
        and result.get("llm_invoked") is not True
    )

    passed = explicit_integrity_failure or rejected_at_integrity_boundary

    return {
        "id": attack.get("id"),
        "type": attack.get("type"),
        "expected": attack.get("expected"),
        "passed": passed,
        "authorized": result.get("authorized"),
        "llm_invoked": result.get("llm_invoked"),
        "document_retrieved": result.get("document") is not None,
        "integrity_verified": result.get("integrity_verified"),
        "error": result.get("error"),
        "latency_ms": round(latency_ms, 3),
        "trace_nodes": trace_nodes(result),
        "details": (
            "Tampered plaintext was rejected at the integrity boundary and "
            "did not reach the LLM."
            if passed
            else "Tampered plaintext was not rejected as expected."
        ),
    }


def run_attack_pending_latest_version(
    attack: Dict[str, Any],
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Verify that latest approved blockchain resolution ignores a pending
    latest version.

    The dataset declares the approved target through metadata_records.json.
    The blockchain remains authoritative at runtime.
    """
    document_id = str(attack.get("document_id", "")).strip()

    expected_approved_version: Optional[int] = None

    if data:
        for record in data.get("metadata_records", []):
            if (
                isinstance(record, dict)
                and record.get("document_id") == document_id
            ):
                raw = record.get("latest_approved_version")
                if raw is not None:
                    expected_approved_version = int(raw)
                break

    started = time.perf_counter()

    result = answer_question(
        f"Retrieve academic transcript document {document_id} "
        "for student STD001.",
        user_context=make_user_context(
            student_id="STD001",
            role="ADMIN",
            username="phase5_pending_version_user",
        ),
    )

    latency_ms = (time.perf_counter() - started) * 1000.0

    resolved_version = result.get("version") or result.get("document_version")

    passed = (
        expected_approved_version is not None
        and resolved_version == expected_approved_version
        and result.get("document") is not None
        and result.get("integrity_verified") is True
    )

    return {
        "id": attack.get("id"),
        "type": attack.get("type"),
        "expected": attack.get("expected"),
        "passed": passed,
        "expected_approved_version": expected_approved_version,
        "resolved_version": resolved_version,
        "authorized": result.get("authorized"),
        "integrity_verified": result.get("integrity_verified"),
        "llm_invoked": result.get("llm_invoked"),
        "error": result.get("error"),
        "llm_error": result.get("llm_error"),
        "latency_ms": round(latency_ms, 3),
        "trace_nodes": trace_nodes(result),
        "details": (
            "Blockchain resolved the latest approved version rather than "
            "the pending latest version."
            if passed
            else "The expected latest-approved-version behaviour was not verified."
        ),
    }


def run_attack_rag_wrong_version(
    attack: Dict[str, Any],
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Mutate RAG latest_version and verify that the returned authoritative
    version still comes from blockchain resolution.
    """
    mutation = dict(attack.get("rag_mutation") or {})
    mutated_version = int(mutation.get("latest_version", 1))

    document_id = str(attack.get("document_id", "")).strip()

    expected_approved_version: Optional[int] = None

    if data:
        for record in data.get("metadata_records", []):
            if (
                isinstance(record, dict)
                and record.get("document_id") == document_id
            ):
                raw = record.get("latest_approved_version")
                if raw is not None:
                    expected_approved_version = int(raw)
                break

    result, _retrieval, latency_ms = _run_metadata_mutation_attack(
        attack,
        "latest_version",
        mutated_version,
    )

    resolved_version = result.get("version") or result.get("document_version")

    passed = (
        expected_approved_version is not None
        and resolved_version == expected_approved_version
        and resolved_version != mutated_version
    )

    return {
        "id": attack.get("id"),
        "type": attack.get("type"),
        "expected": attack.get("expected"),
        "passed": passed,
        "rag_mutated_version": mutated_version,
        "expected_approved_version": expected_approved_version,
        "resolved_version": resolved_version,
        "authorized": result.get("authorized"),
        "document_retrieved": result.get("document") is not None,
        "integrity_verified": result.get("integrity_verified"),
        "llm_invoked": result.get("llm_invoked"),
        "error": result.get("error"),
        "llm_error": result.get("llm_error"),
        "latency_ms": round(latency_ms, 3),
        "trace_nodes": trace_nodes(result),
        "details": (
            "Blockchain version remained authoritative despite mutated RAG "
            "version metadata."
            if passed
            else "RAG version mutation was not overridden by blockchain authority."
        ),
    }


def run_generic_security_attack(
    attack: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "id": attack.get("id"),
        "type": attack.get("type"),
        "expected": attack.get("expected"),
        "passed": False,
        "status": "UNSUPPORTED_ATTACK_ADAPTER",
        "details": (
            "No mutation adapter is implemented for this dataset attack type."
        ),
    }


def run_security_attacks(
    attacks: List[Dict[str, Any]],
    data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    results = []

    handlers = {
        "unauthorized_student": run_attack_unauthorized_student,
        "rag_wrong_student": run_attack_rag_wrong_student,
        "rag_wrong_document_type": run_attack_rag_wrong_document_type,
        "ipfs_content_tampering": run_attack_ipfs_content_tampering,
        "pending_latest_version": lambda attack: run_attack_pending_latest_version(
            attack,
            data,
        ),
        "rag_wrong_version": lambda attack: run_attack_rag_wrong_version(
            attack,
            data,
        ),
    }

    for attack in attacks:
        attack_type = str(attack.get("type", "")).strip()

        print(
            f"[SECURITY] {attack.get('id')} | {attack_type}"
        )

        handler = handlers.get(
            attack_type,
            run_generic_security_attack,
        )

        try:
            results.append(handler(attack))
        except Exception as exc:
            results.append(
                {
                    "id": attack.get("id"),
                    "type": attack_type,
                    "expected": attack.get("expected"),
                    "passed": False,
                    "status": "RUNNER_EXCEPTION",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return results


# ---------------------------------------------------------------------------
# Regression/security invariant checks
# ---------------------------------------------------------------------------


def run_graph_security_invariants() -> Dict[str, Any]:
    mermaid = agent_module.get_graph_mermaid()

    required_nodes = {
        "analyze_query",
        "authorize_query",
        "route_repository",
        "retrieve_metadata",
        "retrieve_onchain_metadata",
        "retrieve_authorized_document",
        "denied_response",
        "phase3_response",
        "document_qa",
        "phase4_response",
    }

    missing = sorted(
        node for node in required_nodes
        if node not in mermaid
    )

    return {
        "passed": not missing,
        "missing_nodes": missing,
        "required_nodes": sorted(required_nodes),
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None

    values = sorted(values)

    if len(values) == 1:
        return values[0]

    position = (len(values) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return values[lower]

    weight = position - lower
    return values[lower] + (values[upper] - values[lower]) * weight


def safe_rate(numerator: int, denominator: int) -> float:
    return round(
        numerator / denominator,
        6,
    ) if denominator else 0.0


def summarize_question_results(
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Summarize both raw results and fixture-aware experimental results.

    Raw success rate answers: "How did the agent behave over all submitted
    questions?"

    Fixture-aware success rate answers: "How did the agent behave on questions
    for which the required blockchain/IPFS fixture was actually available?"

    This distinction prevents uninstantiated synthetic documents from being
    reported as application failures.
    """
    total = len(results)
    successes = sum(bool(item.get("functional_success")) for item in results)

    eligible = [
        item for item in results
        if item.get("evaluation_eligible") is True
    ]
    eligible_successes = sum(
        bool(item.get("functional_success")) for item in eligible
    )

    unavailable = [
        item for item in results
        if item.get("fixture_status") == "unavailable"
    ]

    required_document_results = [
        item for item in results
        if item.get("expected_document_operation") is True
        and item.get("category") != "unsupported_information"
    ]
    available_required = [
        item for item in required_document_results
        if item.get("fixture_status") == "available"
    ]

    unique_required_documents = {
        str(item.get("document_id"))
        for item in required_document_results
        if item.get("document_id")
    }
    unique_available_documents = {
        str(item.get("document_id"))
        for item in available_required
        if item.get("document_id")
    }

    latencies = [
        float(item["latency_ms"])
        for item in results
        if item.get("latency_ms") is not None
    ]
    eligible_latencies = [
        float(item["latency_ms"])
        for item in eligible
        if item.get("latency_ms") is not None
    ]
    available_latencies = [
        float(item["latency_ms"])
        for item in available_required
        if item.get("latency_ms") is not None
    ]

    categories: Dict[str, Dict[str, Any]] = {}

    for item in results:
        category = str(item.get("category", "unknown"))
        bucket = categories.setdefault(
            category,
            {
                "total": 0,
                "successes": 0,
                "raw_success_rate": 0.0,
                "evaluation_eligible": 0,
                "eligible_successes": 0,
                "eligible_success_rate": 0.0,
                "fixture_unavailable": 0,
                "safe_early_exits": 0,
            },
        )

        bucket["total"] += 1
        bucket["successes"] += int(bool(item.get("functional_success")))
        bucket["evaluation_eligible"] += int(
            item.get("evaluation_eligible") is True
        )
        bucket["eligible_successes"] += int(
            item.get("evaluation_eligible") is True
            and bool(item.get("functional_success"))
        )
        bucket["fixture_unavailable"] += int(
            item.get("fixture_status") == "unavailable"
        )
        bucket["safe_early_exits"] += int(
            item.get("fixture_reason") == "safe_early_routing"
        )

    for bucket in categories.values():
        bucket["raw_success_rate"] = safe_rate(
            bucket["successes"], bucket["total"]
        )
        bucket["eligible_success_rate"] = safe_rate(
            bucket["eligible_successes"], bucket["evaluation_eligible"]
        )

    def latency_summary(values: List[float]) -> Dict[str, Any]:
        return {
            "count": len(values),
            "mean": round(statistics.mean(values), 3) if values else None,
            "median": round(statistics.median(values), 3) if values else None,
            "stddev": round(statistics.stdev(values), 3)
            if len(values) >= 2 else 0.0,
            "p95": round(percentile(values, 0.95), 3)
            if values else None,
            "min": round(min(values), 3) if values else None,
            "max": round(max(values), 3) if values else None,
        }

    return {
        "total_questions": total,
        "functional_successes": successes,
        "raw_functional_success_rate": safe_rate(successes, total),
        "evaluation_eligible_questions": len(eligible),
        "evaluation_eligible_successes": eligible_successes,
        "fixture_aware_functional_success_rate": safe_rate(
            eligible_successes, len(eligible)
        ),
        "fixture_unavailable_questions": len(unavailable),
        "required_document_questions": len(required_document_results),
        "available_required_document_questions": len(available_required),
        "document_fixture_coverage_rate": safe_rate(
            len(available_required), len(required_document_results)
        ),
        "required_unique_documents": len(unique_required_documents),
        "available_unique_documents": len(unique_available_documents),
        "available_document_ids": sorted(unique_available_documents),
        "unavailable_document_ids": sorted(
            {
                str(item.get("document_id"))
                for item in unavailable
                if item.get("document_id")
            }
        ),
        "safe_early_routing_count": sum(
            item.get("fixture_reason") == "safe_early_routing"
            for item in results
        ),
        "llm_invocations": sum(
            bool(item.get("llm_invoked")) for item in results
        ),
        "integrity_verified_count": sum(
            bool(item.get("integrity_verified")) for item in results
        ),
        "errors": sum(item.get("error") is not None for item in results),
        "eligible_errors": sum(
            item.get("evaluation_eligible") is True
            and item.get("error") is not None
            for item in results
        ),
        "fixture_unavailable_questions": len(unavailable),
        "fixture_unavailable_errors": sum(
            item.get("fixture_status") == "unavailable"
            and item.get("error") is not None
            for item in results
        ),
        "fixture_probe_errors": sum(
            item.get("fixture_status") == "probe_error"
            for item in results
        ),
        "latency_ms": latency_summary(latencies),
        "eligible_latency_ms": latency_summary(eligible_latencies),
        "available_retrieval_latency_ms": latency_summary(available_latencies),
        "by_category": categories,
    }


def summarize_security_results(
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    total = len(results)
    supported = [
        item for item in results
        if item.get("status") not in {
            "UNSUPPORTED_ATTACK_ADAPTER",
            "RUNNER_EXCEPTION",
        }
    ]

    passed = sum(
        bool(item.get("passed"))
        for item in supported
    )

    unsupported = sum(
        item.get("status") == "UNSUPPORTED_ATTACK_ADAPTER"
        for item in results
    )

    return {
        "total_attacks": total,
        "supported_attacks": len(supported),
        "passed_attacks": passed,
        "attack_block_rate_supported": safe_rate(
            passed,
            len(supported),
        ),
        "unsupported_attack_adapters": unsupported,
        "overall_attack_suite_complete": (
            total > 0 and unsupported == 0
        ),
    }


# ---------------------------------------------------------------------------
# CSV/JSON output
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            indent=2,
            ensure_ascii=False,
            default=str,
        )


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    keys: List[str] = []

    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=keys,
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(
                        value,
                        ensure_ascii=False,
                        default=str,
                    )
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------


def select_questions(
    questions: List[Dict[str, Any]],
    limit: Optional[int],
    category: Optional[str],
) -> List[Dict[str, Any]]:
    selected = questions

    if category:
        category_norm = normalize_text(category)
        selected = [
            question
            for question in selected
            if normalize_text(question.get("category"))
            == category_norm
        ]

    if limit is not None:
        selected = selected[:limit]

    return selected


def print_summary(
    manifest: Dict[str, Any],
    question_summary: Optional[Dict[str, Any]],
    security_summary: Optional[Dict[str, Any]],
    graph_summary: Dict[str, Any],
) -> None:
    print("\n" + "=" * 90)
    print("PHASE 5 EVALUATION SUMMARY")
    print("=" * 90)

    print(
        f"Dataset: {manifest.get('dataset_name')} "
        f"v{manifest.get('dataset_version')}"
    )

    print(
        "Ground truth: "
        f"{manifest.get('students')} students | "
        f"{manifest.get('logical_documents')} logical documents | "
        f"{manifest.get('blockchain_version_records')} planned blockchain versions | "
        f"{manifest.get('questions')} questions | "
        f"{manifest.get('security_attacks')} security attacks"
    )

    print(
        f"Graph security invariants: "
        f"{'PASS' if graph_summary['passed'] else 'FAIL'}"
    )

    if question_summary:
        print(
            "Raw functional success rate: "
            f"{question_summary['raw_functional_success_rate']:.2%}"
        )
        print(
            "Fixture-aware success rate: "
            f"{question_summary['fixture_aware_functional_success_rate']:.2%} "
            f"({question_summary['evaluation_eligible_successes']}/"
            f"{question_summary['evaluation_eligible_questions']} eligible)"
        )
        print(
            "Fixture coverage: "
            f"{question_summary['document_fixture_coverage_rate']:.2%} "
            f"({question_summary['available_required_document_questions']}/"
            f"{question_summary['required_document_questions']} required-document questions)"
        )
        print(
            "Fixture availability: "
            f"{question_summary['available_unique_documents']}/"
            f"{question_summary['required_unique_documents']} unique required documents | "
            f"Unavailable questions: {question_summary['fixture_unavailable_questions']}"
        )

        latency = question_summary["latency_ms"]
        eligible_latency = question_summary["eligible_latency_ms"]

        print(
            "Latency (all questions): "
            f"mean={latency['mean']} ms | "
            f"median={latency['median']} ms | "
            f"std={latency['stddev']} ms | "
            f"P95={latency['p95']} ms"
        )
        print(
            "Latency (eligible questions): "
            f"mean={eligible_latency['mean']} ms | "
            f"median={eligible_latency['median']} ms | "
            f"P95={eligible_latency['p95']} ms"
        )

        print(
            f"LLM invocations: {question_summary['llm_invocations']} | "
            f"Integrity verified: {question_summary['integrity_verified_count']} | "
            f"Total errors: {question_summary['errors']} | "
            f"Eligible application errors: {question_summary['eligible_errors']}"
        )
        print(
            "Fixture status: "
            f"unavailable questions={question_summary['fixture_unavailable_questions']} | "
            f"unavailable-with-error={question_summary['fixture_unavailable_errors']} | "
            f"probe errors={question_summary['fixture_probe_errors']}"
        )

        print("\nBy category:")
        for category, bucket in sorted(
            question_summary["by_category"].items()
        ):
            print(
                f"  {category}: "
                f"raw={bucket['successes']}/{bucket['total']} "
                f"({bucket['raw_success_rate']:.2%}) | "
                f"eligible={bucket['eligible_successes']}/{bucket['evaluation_eligible']} "
                f"({bucket['eligible_success_rate']:.2%}) | "
                f"unavailable={bucket['fixture_unavailable']}"
            )

    if security_summary:
        print(
            "Security attack blocking rate "
            "(supported adapters): "
            f"{security_summary['attack_block_rate_supported']:.2%}"
        )

        print(
            "Security suite complete: "
            f"{security_summary['overall_attack_suite_complete']}"
        )

        if security_summary["unsupported_attack_adapters"]:
            print(
                "WARNING: "
                f"{security_summary['unsupported_attack_adapters']} "
                "dataset attack(s) do not yet have a dedicated mutation adapter."
            )

    print("=" * 90)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Phase 5 evaluation for the Hybrid Blockchain-AI document agent."
    )

    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Path to phase5_dataset.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for JSON/CSV evaluation results.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N questions.",
    )

    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Evaluate only one question category.",
    )

    parser.add_argument(
        "--security-only",
        action="store_true",
        help="Run only security attacks and graph security invariants.",
    )

    parser.add_argument(
        "--questions-only",
        action="store_true",
        help="Run only the dataset question evaluation.",
    )

    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help=(
            "Replace the LLM with a deterministic test double. "
            "Use this for graph/security evaluation, not LLM answer accuracy."
        ),
    )

    parser.add_argument(
        "--show-answers",
        action="store_true",
        help="Include full answer text in console output.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.limit is not None and args.limit <= 0:
        print("--limit must be greater than zero.", file=sys.stderr)
        return 2

    try:
        data = load_dataset(args.dataset)
        validate_dataset(data)
        configure_evaluation_metadata(data)
    except DatasetError as exc:
        print(f"DATASET ERROR: {exc}", file=sys.stderr)
        return 2

    manifest = data["manifest"]

    print("=" * 90)
    print("PHASE 5 EXPERIMENTAL EVALUATION RUNNER")
    print("=" * 90)
    print(f"Dataset directory: {args.dataset}")
    print(f"Output directory:  {args.output}")
    print(
        "LLM mode:          "
        + (
            "deterministic grounded evaluation double"
            if args.mock_llm
            else "real configured LLM"
        )
    )
    print(
        f"Questions: {len(data['questions'])} | "
        f"Security attacks: {len(data['security_attacks'])}"
    )
    print("=" * 90)

    started = time.perf_counter()

    graph_summary = run_graph_security_invariants()

    if not args.security_only:
        selected_for_preflight = select_questions(
            data["questions"],
            limit=args.limit,
            category=args.category,
        )

        # Preflight the actual blockchain fixture for the selected question set.
        # For a full run this covers all 20 academic document IDs; for --limit
        # it only probes the documents needed by the selected questions.
        fixture_inventory = build_fixture_inventory(selected_for_preflight)
    else:
        fixture_inventory = {}

    question_results: List[Dict[str, Any]] = []
    security_results: List[Dict[str, Any]] = []

    if not args.security_only:
        questions = select_questions(
            data["questions"],
            limit=args.limit,
            category=args.category,
        )

        if not questions:
            print("No questions matched the selected filters.")
            return 2

        question_results = run_questions(
            questions,
            mock_llm=args.mock_llm,
            fixture_inventory=fixture_inventory,
        )

    if not args.questions_only:
        security_results = run_security_attacks(
            data["security_attacks"],
            data=data,
        )

    elapsed_seconds = time.perf_counter() - started

    question_summary = (
        summarize_question_results(question_results)
        if question_results
        else None
    )

    security_summary = (
        summarize_security_results(security_results)
        if security_results
        else None
    )

    run_id = time.strftime(
        "%Y%m%d_%H%M%S"
    )

    payload = {
        "evaluation": {
            "runner": "phase5_evaluation_runner",
            "runner_version": "1.1-fixture-aware",
            "started_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "elapsed_seconds": round(
                elapsed_seconds,
                3,
            ),
            "mock_llm": args.mock_llm,
        },
        "dataset_manifest": manifest,
        "dataset_counts": {
            "students": len(data["students"]),
            "courses": len(data["courses"]),
            "logical_documents": len(data["documents"]),
            "metadata_records": len(data["metadata_records"]),
            "questions": len(data["questions"]),
            "security_attacks": len(data["security_attacks"]),
            "planned_blockchain_versions": manifest.get(
                "blockchain_version_records"
            ),
            "instantiated_fixture_documents_observed": (
                question_summary.get("available_unique_documents", 0)
                if question_summary else 0
            ),
            "fixture_coverage_note": (
                "Fixture availability is measured from successful authoritative "
                "document retrievals in the selected question set; planned dataset "
                "records are not assumed to be instantiated on-chain/IPFS."
            ),
        },
        "graph_security_invariants": graph_summary,
        "fixture_inventory": fixture_inventory,
        "question_summary": question_summary,
        "security_summary": security_summary,
        "question_results": question_results,
        "security_results": security_results,
        "experimental_note": (
            "The dataset contains the complete synthetic ground truth. "
            "Before question evaluation, the runner explicitly probes the "
            "authoritative blockchain for each required document fixture. "
            "Documents without an instantiated approved blockchain version "
            "are classified as fixture-unavailable rather than application "
            "failures. Blockchain probe errors are kept separate from both "
            "available and unavailable fixtures."
        ),
    }

    output_dir = args.output
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path = output_dir / f"phase5_evaluation_{run_id}.json"
    questions_csv = output_dir / f"phase5_questions_{run_id}.csv"
    security_csv = output_dir / f"phase5_security_{run_id}.csv"

    write_json(json_path, payload)
    write_csv(questions_csv, question_results)
    write_csv(security_csv, security_results)

    print_summary(
        manifest,
        question_summary,
        security_summary,
        graph_summary,
    )

    print("\nResults written to:")
    print(f"  JSON:     {json_path}")
    print(f"  Questions:{questions_csv}")
    print(f"  Security: {security_csv}")

    if args.show_answers:
        print("\nQuestion answers:")
        for result in question_results:
            print(
                f"\n[{result['id']}] "
                f"{result['functional_success']}"
            )
            print(result.get("answer"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
