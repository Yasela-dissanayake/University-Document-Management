"""Standalone metadata-only RepositoryMetadataRAG evaluator.

Copy this file to python_backend/ai/evaluate_repository_rag.py and the
benchmark JSON to python_backend/ai/rag_benchmark.json.

Run from project root:
    python -m python_backend.ai.evaluate_repository_rag
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

from python_backend.ai.repository_metadata import load_metadata_records
from python_backend.ai.repository_rag import RepositoryMetadataRAG

DEFAULT_BENCHMARK = Path(__file__).resolve().parent / "rag_benchmark.json"


def precision_at_k(retrieved: Sequence[str], relevant: Set[str], k: int) -> float:
    top = list(retrieved[:k])
    return sum(doc_id in relevant for doc_id in top) / k if k > 0 else 0.0


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
        row["metrics"][f"precision@{k}"] = round(
            sum(doc_id in relevant for doc_id in retrieved[:k]) / k, 4
        )
        row["metrics"][f"recall@{k}"] = round(
            (sum(doc_id in relevant for doc_id in retrieved[:k]) / len(relevant))
            if relevant else (1.0 if not retrieved[:k] else 0.0), 4
        )
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
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")

    try:
        benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
        cases = benchmark["queries"]
        k_values = sorted({int(k) for k in benchmark.get("k_values", [1, 3, 5]) if int(k) > 0}) or [1, 3, 5]
        records = load_metadata_records()
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
        "benchmark_path": str(args.benchmark),
        "metadata_source": "METADATA_REGISTRY_PATH if configured, otherwise DEMO_METADATA",
        "summary": summary,
        "per_query_results": rows,
        "limitations": [
            "Initial labels are based on the current development DEMO_METADATA fixture, not independently verified production data.",
            "Ten queries are only an initial smoke benchmark, not a statistically representative evaluation.",
            "This evaluates metadata retrieval only; it does not evaluate blockchain resolution, authorization, IPFS, decryption, or LLM answers."
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 68)
    print("METADATA-ONLY REPOSITORY RAG EVALUATION")
    print("=" * 68)
    print(f"Metadata records: {summary['metadata_record_count']}")
    print(f"Queries:          {summary['query_count']}")
    print(f"Mean latency:     {summary['mean_latency_ms']} ms")
    for name, value in summary["metrics"].items():
        print(f"{name:12s} {value:.4f}")
    print(f"Correct empty-result rate: {summary['correct_empty_result_rate']}")
    print(f"Queries with wrong-student results: {summary['queries_with_wrong_student_results']}")
    print(f"Total wrong-student results: {summary['total_wrong_student_results']}")
    print(f"Queries with unexpected results: {summary['queries_with_unexpected_results']}")
    print(f"Metadata-only contract pass rate: {summary['metadata_only_contract_pass_rate']}")
    print(f"Report written to: {args.output.resolve()}")
    print("\nPer-query results:")
    for row in rows:
        print(f"{row['id']} | retrieved={row['retrieved_document_ids']} | expected={row['expected_document_ids']} | wrong_student={row['wrong_student_document_ids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
