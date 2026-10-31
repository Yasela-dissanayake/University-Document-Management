#!/usr/bin/env python3
"""Build a reproducible metadata-only test fixture and varied RAG benchmark.

The generated records are test fixtures, not records from a real university,
blockchain deployment, or IPFS repository. No blockchain writes or IPFS uploads
are performed. Relevance labels are provisional and require independent review.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

REPOSITORIES = {
    "academic": {
        "document_type": "Transcript",
        "prefix": "TX",
        "keywords": ["transcript", "academic record", "grades", "course results", "academic performance"],
    },
    "registrar": {
        "document_type": "Registration Certificate",
        "prefix": "REG",
        "keywords": ["registration", "enrolment", "student status", "registrar", "enrolled"],
    },
    "examination": {
        "document_type": "Examination Result Sheet",
        "prefix": "EX",
        "keywords": ["examination", "exam results", "marks", "assessment", "semester results"],
    },
    "student_services": {
        "document_type": "Student Service Record",
        "prefix": "SS",
        "keywords": ["student services", "welfare", "scholarship", "student support", "financial assistance"],
    },
}
COURSE_CODES = [f"ICT{n:03d}" for n in (101, 102, 201, 202, 301, 302, 401, 402, 501, 502)]
SEMESTERS = [f"Semester {n}" for n in range(1, 9)]
DEPARTMENTS = [
    "Information Technology", "Computer Science", "Software Engineering",
    "Data Science", "Information Systems",
]


def build_dataset(record_count: int, seed: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if record_count < 20:
        raise ValueError("Use at least 20 records for a useful test fixture.")
    rng = random.Random(seed)
    repo_names = list(REPOSITORIES)
    records: list[dict[str, Any]] = []

    # One record per student per repository, with multiple metadata attributes.
    student_count = (record_count + len(repo_names) - 1) // len(repo_names)
    for index in range(record_count):
        student_number = index // len(repo_names) + 1
        repo = repo_names[index % len(repo_names)]
        config = REPOSITORIES[repo]
        student_id = f"STU{student_number:04d}"
        doc_id = f"{config['prefix']}-{student_id}-{index + 1:04d}"
        courses = rng.sample(COURSE_CODES, k=2)
        semester = rng.choice(SEMESTERS) if repo in {"academic", "examination"} else None
        keywords = list(config["keywords"]) + ["record", "records", "university document", "student record"]
        if semester:
            keywords.append(semester.lower())
        keywords.extend(code.lower() for code in courses)
        keywords.append(rng.choice(DEPARTMENTS).lower())
        records.append({
            "document_id": doc_id,
            "student_id": student_id,
            "document_type": config["document_type"],
            "semester": semester,
            "course_codes": courses if repo in {"academic", "examination"} else [],
            "keywords": sorted(set(keywords)),
            "repository": repo,
            "latest_version": rng.randint(1, 4),
            "latest_approved_cid": f"test-cid-{doc_id.lower()}",
            "metadata_origin": "generated_test_fixture",
            "approval_status": "TEST_FIXTURE",
        })

    by_student_repo = {(r["student_id"], r["repository"]): r for r in records}
    queries: list[dict[str, Any]] = []

    def add(qid: int, category: str, query: str, repositories: list[str],
            relevant: list[str], student_id: str | None = None,
            course_code: str | None = None, notes: str = "") -> None:
        queries.append({
            "id": f"Q-{qid:04d}",
            "category": category,
            "query": query,
            "repositories": repositories,
            "student_id": student_id,
            "course_code": course_code,
            "relevant_document_ids": relevant,
            "notes": notes or "Provisional label derived from the fixture design; independently review before reporting final results.",
        })

    # 40 identity + repository scoped queries, with varied phrasing.
    natural_templates = [
        "I need proof of my academic performance for student {sid}",
        "Can you locate the enrolment confirmation for {sid}?",
        "Please find the exam marks document for student {sid}",
        "Look up student support or welfare information for {sid}",
        "I need the official record showing that {sid} is registered",
        "Find the semester grades record belonging to {sid}",
        "Locate the examination results for {sid}",
        "Show the student services file for {sid}",
    ]
    for i in range(min(40, record_count)):
        record = records[i]
        sid, repo = record["student_id"], record["repository"]
        phrase = natural_templates[i % len(natural_templates)].format(sid=sid)
        add(len(queries) + 1, "scoped_natural_language", phrase, [repo],
            [record["document_id"]], sid,
            notes="Single intended target with an explicit student scope and repository.")

    # 20 broad, multi-document requests: every repository record for a student is relevant.
    for student_num in range(1, min(student_count, 21)):
        sid = f"STU{student_num:04d}"
        relevant_records = [by_student_repo[(sid, repo)]["document_id"]
                            for repo in repo_names if (sid, repo) in by_student_repo]
        add(len(queries) + 1, "broad_student_records",
            f"Show me the available university records for {sid}",
            repo_names, relevant_records, sid,
            notes="Multiple records are relevant; tests recall and ranking across repositories.")

    # 20 attribute-driven queries without an identity filter.
    for i in range(20):
        semester = SEMESTERS[i % len(SEMESTERS)]
        repo = "examination" if i % 2 == 0 else "academic"
        matching = [r["document_id"] for r in records
                    if r["repository"] == repo and r["semester"] == semester]
        add(len(queries) + 1, "attribute_search",
            f"Find {semester.lower()} results" if repo == "examination"
            else f"Find academic performance records for {semester.lower()}",
            [repo], matching, notes="Attribute query; expected set is based on matching metadata attributes.")

    # 20 no-answer / out-of-domain queries.
    no_answer_queries = [
        ("Find a veterinary license for {sid}", ["academic"]),
        ("Retrieve a parking permit for {sid}", ["registrar"]),
        ("Find a library fine receipt for {sid}", ["student_services"]),
        ("Show a laboratory equipment calibration certificate for {sid}", ["examination"]),
        ("Find a dormitory meal voucher for {sid}", ["student_services"]),
    ]
    for i in range(20):
        template, repos = no_answer_queries[i % len(no_answer_queries)]
        sid = f"STU{(i % max(student_count, 1)) + 1:04d}"
        add(len(queries) + 1, "no_answer", template.format(sid=sid), repos, [], sid,
            notes="Expected no result because this document type is absent from the metadata fixture.")

    # Keep exactly 100 queries by deterministic truncation or fill if the record count is unusual.
    queries = queries[:100]
    while len(queries) < 100:
        i = len(queries)
        sid = f"STU{(i % max(student_count, 1)) + 1:04d}"
        add(len(queries) + 1, "scoped_natural_language",
            f"Find academic performance details for {sid}", ["academic"],
            [by_student_repo[(sid, "academic")]["document_id"]]
            if (sid, "academic") in by_student_repo else [], sid)

    registry = {
        "description": "Generated metadata-only evaluation test fixture; not real university records.",
        "dataset_type": "generated_test_fixture",
        "document_count": len(records),
        "documents": records,
    }
    benchmark = {
        "description": "Mixed-difficulty metadata retrieval benchmark with scoped, broad, attribute, and no-answer queries.",
        "dataset_type": "generated_evaluation_benchmark",
        "ground_truth_status": "generated_unverified",
        "metadata_record_count_at_generation": len(records),
        "k_values": [1, 3, 5],
        "queries": queries,
        "notes": [
            "Records and identifiers are generated test fixtures, not real institutional records.",
            "Relevance labels are provisional and require independent review.",
            "The benchmark evaluates metadata retrieval only; it does not evaluate authorization, blockchain consistency, IPFS retrieval, decryption, or answer generation."
        ],
    }
    counts = dict(Counter(r["repository"] for r in records))
    manifest = {
        "dataset_type": "generated_test_fixture",
        "seed": seed,
        "document_count": len(records),
        "query_count": len(queries),
        "query_category_counts": dict(Counter(q["category"] for q in queries)),
        "repository_counts": counts,
        "student_count": len({r["student_id"] for r in records}),
        "source": "locally generated test data",
        "real_blockchain_records": 0,
        "real_ipfs_objects": 0,
        "blockchain_writes": 0,
        "ipfs_uploads": 0,
    }
    return registry, benchmark, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--output-dir", type=Path, default=Path("python_backend/ai/rag_evaluation_data"))
    args = parser.parse_args()
    try:
        registry, benchmark, manifest = build_dataset(args.records, args.seed)
    except ValueError as exc:
        parser.error(str(exc))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "metadata_registry.json": registry,
        "rag_benchmark.json": benchmark,
        "dataset_manifest.json": manifest,
    }
    for name, payload in outputs.items():
        path = args.output_dir / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote: {path.resolve()}")
    print("\nEvaluation fixture summary")
    print(f"Records: {manifest['document_count']}")
    print(f"Queries: {manifest['query_count']}")
    print(f"Query categories: {manifest['query_category_counts']}")
    print("Blockchain writes: 0")
    print("IPFS uploads: 0")
    print("Note: generated test fixtures; review relevance labels before reporting metrics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
