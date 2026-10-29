"""Evaluate metadata-only Repository RAG against manually verified ground truth.

Run from the repository root:
    python -m python_backend.ai.evaluate_repository_rag --prepare-benchmark
Review the generated candidate benchmark, anonymize identifiers if necessary, and
independently verify every relevant_document_ids label. Set
"ground_truth_status": "verified" only after that review, then run:
    python -m python_backend.ai.evaluate_repository_rag

The default metadata source is the live blockchain adapter; demo fixtures are
available only with METADATA_SOURCE=demo. A run with unverified labels is
refused unless --allow-unverified-ground-truth is explicitly passed, and the
resulting report is marked diagnostic_only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

from python_backend.ai.repository_metadata import load_metadata_records
from python_backend.ai.repository_rag import RepositoryMetadataRAG

DEFAULT_BENCHMARK = Path(__file__).resolve().parent / "rag_benchmark.json"


def precision_at_k(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    return sum(doc_id in relevant for doc_id in retrieved[:k]) / k if k > 0 else 0.0


def recall_at_k(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    if not relevant:
        return 1.0 if not retrieved[:k] else 0.0
    return sum(doc_id in relevant for doc_id in retrieved[:k]) / len(relevant)


def reciprocal_rank(retrieved: Sequence[str], relevant: Set[str]) -> float:
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 1.0 if not relevant and not retrieved else 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    dcg = sum(
        (1.0 if doc_id in relevant else 0.0) / math.log2(rank + 1)
        for rank, doc_id in enumerate(retrieved[:k], start=1)
    )
    ideal_count = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / idcg if idcg else (1.0 if not retrieved[:k] else 0.0)


def _prepare_candidate_benchmark(records: List[Dict[str, Any]], output: Path) -> None:
    """Write candidate cases from real metadata; labels are explicitly unverified."""
    queries: List[Dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        student_id = str(record.get("student_id") or "").strip()
        document_type = str(record.get("document_type") or "").strip()
        repository = str(record.get("repository") or "").strip()
        document_id = str(record.get("document_id") or "").strip()
        if not all((student_id, document_type, repository, document_id)):
            continue
        queries.append({
            "id": f"CANDIDATE-{index:04d}",
            "query": f"Find {document_type} metadata for student {student_id}",
            "repositories": [repository],
            "student_id": student_id,
            "course_code": None,
            "relevant_document_ids": [document_id],
            "notes": (
                "PROVISIONAL label generated from the source record. Independently verify "
                "whether this is the complete relevant set; edit query and labels as needed."
            ),
        })

    benchmark = {
        "description": (
            "Candidate metadata-only RAG benchmark generated from the configured real metadata source. "
            "Labels are provisional and must be independently verified before reporting final metrics."
        ),
        "ground_truth_status": "needs_manual_review",
        "metadata_record_count_at_generation": len(records),
        "k_values": [1, 3, 5],
        "queries": queries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(benchmark, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Candidate benchmark written to: {output.resolve()}")
    print(f"Candidate queries: {len(queries)}")
    print('Review the labels and set "ground_truth_status" to "verified" only after independent validation.')


def evaluate_case(rag: RepositoryMetadataRAG, case: Dict[str, Any],
                  k_values: Sequence[int], top_k: int) -> Dict[str, Any]:
    start = time.perf_counter()
    results = rag.search(
        case["query"],
        case.get("repositories", []),
        student_id=case.get("student_id"),
        course_code=case.get("course_code"),
        top_k_per_repository=top_k,
    )
    latency_ms = (time.perf_counter() - start) * 1000.0
    retrieved = [str(item.get("document_id") or "").strip() for item in results]
    relevant = {str(value).strip() for value in case.get("relevant_document_ids", []) if str(value).strip()}
    requested_student = str(case.get("student_id") or "").strip().upper()
    wrong_student = [
        str(item.get("document_id") or "")
        for item in results
        if requested_student and str(item.get("student_id") or "").strip().upper() != requested_student
    ]
    unexpected = [doc_id for doc_id in retrieved if doc_id not in relevant]

    row: Dict[str, Any] = {
        "id": case.get("id"),
        "query": case["query"],
        "repositories": case.get("repositories", []),
        "expected_document_ids": sorted(relevant),
        "retrieved_document_ids": retrieved,
        "result_count": len(results),
        "latency_ms": round(latency_ms, 3),
        "empty_result_expected": not relevant,
        "empty_result_correct": len(results) == 0 if not relevant else None,
        "wrong_student_document_ids": wrong_student,
        "unexpected_document_ids": unexpected,
        "metrics": {},
        "metadata_only_contract_pass": all(
            item.get("content_retrieved") is False
            and not any(key in item for key in (
                "content", "plaintext", "document_content", "raw_document",
                "encrypted_content", "document_data"
            ))
            for item in results
        ),
    }
    for k in k_values:
        row["metrics"][f"precision@{k}"] = round(precision_at_k(retrieved, relevant, k), 4)
        row["metrics"][f"recall@{k}"] = round(recall_at_k(retrieved, relevant, k), 4)
        row["metrics"][f"ndcg@{k}"] = round(ndcg_at_k(retrieved, relevant, k), 4)
    row["metrics"]["mrr"] = round(reciprocal_rank(retrieved, relevant), 4)
    return row


def mean_metric(rows: Sequence[Dict[str, Any]], name: str) -> float:
    values = [float(row["metrics"][name]) for row in rows]
    return round(sum(values) / len(values), 4) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("rag_evaluation_report.json"))
    parser.add_argument(
        "--prepare-benchmark",
        action="store_true",
        help="Create provisional candidate queries from the configured metadata source, then stop.",
    )
    parser.add_argument(
        "--allow-unverified-ground-truth",
        action="store_true",
        help="Run diagnostic metrics with unverified labels; report is explicitly marked diagnostic_only.",
    )
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")

    try:
        records = load_metadata_records()
        if args.prepare_benchmark:
            _prepare_candidate_benchmark(records, args.benchmark)
            return 0
        benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
        cases = benchmark["queries"]
        ground_truth_status = str(benchmark.get("ground_truth_status", "unverified")).lower()
        if ground_truth_status != "verified" and not args.allow_unverified_ground_truth:
            print(
                "Benchmark ground truth is not verified. Review relevant_document_ids "
                "independently, set ground_truth_status to 'verified', or pass "
                "--allow-unverified-ground-truth for a diagnostic-only run.",
                file=sys.stderr,
            )
            return 2
        k_values = sorted({int(k) for k in benchmark.get("k_values", [1, 3, 5]) if int(k) > 0}) or [1, 3, 5]
        rag = RepositoryMetadataRAG(records=records)
    except Exception as exc:
        print(f"Could not initialize evaluation: {exc}", file=sys.stderr)
        return 2

    if not isinstance(cases, list) or not cases:
        print("Benchmark JSON must contain a non-empty queries list.", file=sys.stderr)
        return 2

    rows = [evaluate_case(rag, case, k_values, args.top_k) for case in cases]
    metric_names = [*(f"precision@{k}" for k in k_values),
                    *(f"recall@{k}" for k in k_values),
                    *(f"ndcg@{k}" for k in k_values), "mrr"]
    empty_rows = [row for row in rows if row["empty_result_expected"]]
    summary = {
        "query_count": len(rows),
        "metadata_record_count": len(records),
        "mean_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows), 3),
        "metrics": {name: mean_metric(rows, name) for name in metric_names},
        "correct_empty_result_rate": round(
            sum(row["empty_result_correct"] is True for row in empty_rows) / len(empty_rows), 4
        ) if empty_rows else None,
        "queries_with_wrong_student_results": sum(bool(row["wrong_student_document_ids"]) for row in rows),
        "total_wrong_student_results": sum(len(row["wrong_student_document_ids"]) for row in rows),
        "queries_with_unexpected_results": sum(bool(row["unexpected_document_ids"]) for row in rows),
        "metadata_only_contract_pass_rate": round(
            sum(row["metadata_only_contract_pass"] for row in rows) / len(rows), 4
        ),
    }
    report = {
        "evaluation_type": "metadata_only_repository_rag",
        "metadata_source": (
            "METADATA_REGISTRY_PATH" if __import__("os").getenv("METADATA_REGISTRY_PATH")
            else __import__("os").getenv("METADATA_SOURCE", "blockchain")
        ),
        "ground_truth_status": ground_truth_status,
        "diagnostic_only": ground_truth_status != "verified",
        "benchmark_path": str(args.benchmark),
        "summary": summary,
        "per_query_results": rows,
        "limitations": [
            "UniversityRegistry can enumerate student registration records but cannot enumerate logical document IDs stored only in its documents mapping; a maintained metadata snapshot/indexer is required for complete versioned-document coverage.",
            "Candidate labels generated from metadata are not independent ground truth. Independently review all labels before setting ground_truth_status to verified.",
            "This evaluates metadata retrieval only; it does not evaluate authorization, authoritative version resolution, IPFS retrieval, decryption, or LLM answer quality.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 68)
    print("METADATA-ONLY REPOSITORY RAG EVALUATION")
    print("=" * 68)
    print(f"Metadata records: {summary['metadata_record_count']}")
    print(f"Queries:          {summary['query_count']}")
    print(f"Ground truth:     {ground_truth_status}")
    print(f"Diagnostic only:  {report['diagnostic_only']}")
    print(f"Mean latency:     {summary['mean_latency_ms']} ms")
    for name, value in summary["metrics"].items():
        print(f"{name:12s} {value:.4f}")
    print(f"Correct empty-result rate: {summary['correct_empty_result_rate']}")
    print(f"Queries with wrong-student results: {summary['queries_with_wrong_student_results']}")
    print(f"Total wrong-student results: {summary['total_wrong_student_results']}")
    print(f"Queries with unexpected results: {summary['queries_with_unexpected_results']}")
    print(f"Metadata-only contract pass rate: {summary['metadata_only_contract_pass_rate']}")
    print(f"Report written to: {args.output.resolve()}")
    for row in rows:
        print(f"{row['id']} | retrieved={row['retrieved_document_ids']} | expected={row['expected_document_ids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
