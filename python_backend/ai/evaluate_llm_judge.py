"""
Evaluate the complete LangGraph entry point with a local Ollama judge.

Run from the project root:
  python -m python_backend.ai.evaluate_llm_judge

Privacy-safe initial mode:
- invokes answer_question(question, user_context=None)
- dataset uses synthetic identifiers
- never sends agent traces or retrieved metadata/documents to Ollama
- skips judging if the graph trace indicates the document-retrieval node ran
"""
from __future__ import annotations
import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List
from python_backend.ai.agent import answer_question
from python_backend.ai.llm_judge import judge_answer, sanitize_text

ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = ROOT / "llm_judge_dataset.json"
DEFAULT_REPORT = ROOT / "llm_judge_report.json"

def load_cases(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", data) if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError("Dataset must be a list or an object with 'cases'.")
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not case.get("id") or not case.get("question"):
            raise ValueError(f"Invalid dataset case at index {index}.")
        if case.get("user_context"):
            raise ValueError("Privacy-safe evaluator does not accept user_context.")
    return cases

def mean_scores(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    fields = ("answer_correctness", "answer_relevance", "faithfulness", "completeness", "safety")
    judged = [r for r in rows if isinstance(r.get("judge"), dict)]
    return {
        field: round(statistics.mean(r["judge"][field] for r in judged), 3) if judged else None
        for field in fields
    }

def run_evaluation(dataset_path: Path, report_path: Path) -> Dict[str, Any]:
    cases = load_cases(dataset_path)
    rows: List[Dict[str, Any]] = []
    latencies: List[float] = []

    for case in cases:
        question = str(case["question"])
        start = time.perf_counter()
        # No user identity is passed: protected queries should fail closed.
        result = answer_question(question, user_context=None)
        latency_ms = (time.perf_counter() - start) * 1000
        latencies.append(latency_ms)

        if isinstance(result, dict):
            answer = str(result.get("answer", ""))
            error = result.get("error")
            trace = result.get("trace")
            nodes = trace.get("nodes", []) if isinstance(trace, dict) else []
            node_names = [
                str(node.get("node")) for node in nodes
                if isinstance(node, dict) and node.get("node")
            ]
            denied = "denied_response" in node_names or error == "forbidden"
        else:
            answer, error, node_names, denied = str(result), None, [], False

        row: Dict[str, Any] = {
            "id": str(case["id"]),
            "question": sanitize_text(question),
            "answer": sanitize_text(answer),
            "latency_ms": round(latency_ms, 3),
            "authorization_denied": denied,
            "error_present": bool(error),
            "judge": None,
            "judge_skipped_reason": None,
        }

        # If document retrieval may have occurred, do not send the answer to
        # the judge or save it verbatim in the report.
        retrieval_nodes = {"retrieve_authorized_document"}
        if retrieval_nodes.intersection(node_names):
            row["answer"] = "[REDACTED: document-retrieval path detected]"
            row["judge_skipped_reason"] = "document_retrieval_path_not_sent_to_judge"
        else:
            try:
                row["judge"] = judge_answer(
                    question=question,
                    answer=row["answer"],
                    expected_criteria=str(case.get("expected_criteria", "")),
                )
            except Exception as exc:
                row["judge_skipped_reason"] = f"judge_error:{type(exc).__name__}"

        rows.append(row)
        print(
            f'{row["id"]}: latency={row["latency_ms"]:.1f}ms, '
            f'authorization_denied={denied}, judged={row["judge"] is not None}'
        )

    report = {
        "evaluation": "ollama_llm_as_a_judge",
        "judge_provider": "Ollama",
        "judge_model": "llama3",
        "agent_entry_point": "python_backend.ai.agent.answer_question",
        "dataset": dataset_path.name,
        "case_count": len(cases),
        "mean_agent_latency_ms": round(statistics.mean(latencies), 3) if latencies else None,
        "mean_judge_scores": mean_scores(rows),
        "judged_case_count": sum(r["judge"] is not None for r in rows),
        "skipped_case_count": sum(r["judge"] is None for r in rows),
        "authorization_denied_count": sum(r["authorization_denied"] for r in rows),
        "privacy_note": (
            "Only synthetic questions and sanitized answer text are recorded. "
            "Agent traces, raw blockchain metadata, CIDs, hashes, and document contents "
            "are not included in the report."
        ),
        "limitations": [
            "Initial dataset uses synthetic IDs and unauthenticated requests.",
            "This evaluates safe routing/refusal and non-sensitive responses, not authorized private-document answer quality.",
            "LLM-as-a-Judge is approximate; manually review a sample."
        ],
        "results": rows,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nMean judge scores (1-5):")
    for key, value in report["mean_judge_scores"].items():
        print(f"  {key}: {value if value is not None else 'n/a'}")
    print(f"Report written to: {report_path}")
    return report

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the LangGraph agent with local Ollama LLM-as-a-Judge.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    run_evaluation(args.dataset, args.report)

if __name__ == "__main__":
    main()
