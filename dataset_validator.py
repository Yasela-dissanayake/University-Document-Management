#!/usr/bin/env python3
"""Validate the Phase 5 synthetic evaluation dataset.

Run from the project root:
    python dataset_validator.py

Optional:
    python dataset_validator.py --dataset phase5_dataset
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_FILES = [
    "students.json",
    "courses.json",
    "documents.json",
    "metadata_records.json",
    "questions.json",
    "security_attacks.json",
    "manifest.json",
]

REPOSITORIES = {"academic", "registrar", "examination", "student_services"}

DOCUMENT_TYPES = {
    "Transcript": "academic",
    "Registration Certificate": "registrar",
    "Examination Result Sheet": "examination",
    "Student Service Record": "student_services",
}

APPROVAL_STATUSES = {"APPROVED", "PENDING"}

QUESTION_CATEGORIES = {
    "direct_retrieval",
    "course_specific",
    "multi_course",
    "missing_information",
    "unsupported_information",
}

ATTACK_TYPES = {
    "unauthorized_student",
    "rag_wrong_student",
    "rag_wrong_document_type",
    "ipfs_content_tampering",
    "pending_latest_version",
    "rag_wrong_version",
}


class Validator:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def passed(self) -> bool:
        return not self.errors


def load_json(path: Path, v: Validator) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        v.error(f"Missing file: {path.name}")
    except json.JSONDecodeError as exc:
        v.error(
            f"{path.name}: invalid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        )
    except OSError as exc:
        v.error(f"{path.name}: cannot read file: {exc}")
    return None


def as_list(data: Any, name: str, v: Validator) -> list[dict]:
    if not isinstance(data, list):
        v.error(f"{name}: expected a JSON list.")
        return []
    bad = [i for i, x in enumerate(data) if not isinstance(x, dict)]
    if bad:
        v.error(f"{name}: non-object records at indexes {bad[:10]}.")
    return [x for x in data if isinstance(x, dict)]


def as_dict(data: Any, name: str, v: Validator) -> dict:
    if not isinstance(data, dict):
        v.error(f"{name}: expected a JSON object.")
        return {}
    return data


def required(record: dict, fields: set[str], context: str, v: Validator) -> bool:
    missing = sorted(fields - set(record))
    if missing:
        v.error(f"{context}: missing fields: {', '.join(missing)}")
        return False
    return True


def unique(records: list[dict], field: str, name: str, v: Validator) -> None:
    seen: dict[Any, int] = {}
    for i, record in enumerate(records):
        value = record.get(field)
        if value is None:
            continue
        if value in seen:
            v.error(
                f"{name}: duplicate {field}={value!r} "
                f"at indexes {seen[value]} and {i}."
            )
        else:
            seen[value] = i


def validate_students(students: list[dict], v: Validator) -> set[str]:
    ids: set[str] = set()
    for i, s in enumerate(students):
        c = f"students.json[{i}]"
        if not required(s, {"student_id", "name", "program", "year", "active"}, c, v):
            continue
        sid = s["student_id"]
        if not isinstance(sid, str) or not sid:
            v.error(f"{c}: student_id must be a non-empty string.")
        else:
            ids.add(sid)
        if not isinstance(s["name"], str) or not s["name"]:
            v.error(f"{c}: name must be a non-empty string.")
        if not isinstance(s["program"], str) or not s["program"]:
            v.error(f"{c}: program must be a non-empty string.")
        if not isinstance(s["year"], int) or isinstance(s["year"], bool) or s["year"] < 1:
            v.error(f"{c}: year must be a positive integer.")
        if not isinstance(s["active"], bool):
            v.error(f"{c}: active must be boolean.")
    unique(students, "student_id", "students.json", v)
    return ids


def validate_courses(courses: list[dict], v: Validator) -> set[str]:
    codes: set[str] = set()
    for i, course in enumerate(courses):
        c = f"courses.json[{i}]"
        if not required(course, {"code", "name", "credits", "level"}, c, v):
            continue
        code = course["code"]
        if not isinstance(code, str) or not code:
            v.error(f"{c}: code must be a non-empty string.")
        else:
            codes.add(code)
        if not isinstance(course["name"], str) or not course["name"]:
            v.error(f"{c}: name must be a non-empty string.")
        if not isinstance(course["credits"], int) or isinstance(course["credits"], bool) or course["credits"] <= 0:
            v.error(f"{c}: credits must be a positive integer.")
        if not isinstance(course["level"], int) or course["level"] not in {1000, 2000, 3000, 4000}:
            v.error(f"{c}: invalid level {course.get('level')!r}.")
    unique(courses, "code", "courses.json", v)
    return codes


def validate_version_plan(document: dict, context: str, v: Validator) -> tuple[int | None, int | None]:
    plan = document.get("version_plan")
    if not isinstance(plan, list) or not plan:
        v.error(f"{context}: version_plan must be a non-empty list.")
        return None, None

    versions = []
    for i, record in enumerate(plan):
        c = f"{context}.version_plan[{i}]"
        if not isinstance(record, dict):
            v.error(f"{c}: expected object.")
            continue
        if not required(record, {"version", "approval_status"}, c, v):
            continue
        version = record["version"]
        status = record["approval_status"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            v.error(f"{c}: version must be a positive integer.")
        else:
            versions.append(version)
        if status not in APPROVAL_STATUSES:
            v.error(f"{c}: invalid approval_status={status!r}.")

    if versions:
        expected = list(range(1, len(versions) + 1))
        if versions != expected:
            v.error(f"{context}: versions {versions} are not sequential; expected {expected}.")

    latest = max(versions) if versions else None
    approved = [
        r["version"] for r in plan
        if isinstance(r, dict)
        and isinstance(r.get("version"), int)
        and r.get("approval_status") == "APPROVED"
    ]
    return latest, (max(approved) if approved else None)


def validate_documents(
    documents: list[dict],
    student_ids: set[str],
    course_codes: set[str],
    v: Validator,
) -> tuple[dict[str, dict], int]:
    docs: dict[str, dict] = {}
    total_versions = 0

    for i, d in enumerate(documents):
        c = f"documents.json[{i}]"
        fields = {"document_id", "student_id", "document_type", "repository", "content", "version_plan"}
        if not required(d, fields, c, v):
            continue

        did = d["document_id"]
        sid = d["student_id"]
        dtype = d["document_type"]
        repo = d["repository"]

        if not isinstance(did, str) or not did:
            v.error(f"{c}: document_id must be a non-empty string.")
        elif did in docs:
            v.error(f"{c}: duplicate document_id={did!r}.")
        else:
            docs[did] = d

        if sid not in student_ids:
            v.error(f"{c}: unknown student_id={sid!r}.")

        if dtype not in DOCUMENT_TYPES:
            v.error(f"{c}: unknown document_type={dtype!r}.")
        elif repo != DOCUMENT_TYPES[dtype]:
            v.error(
                f"{c}: document_type {dtype!r} should use repository "
                f"{DOCUMENT_TYPES[dtype]!r}, found {repo!r}."
            )

        if repo not in REPOSITORIES:
            v.error(f"{c}: unknown repository={repo!r}.")

        content = d["content"]
        if not isinstance(content, dict):
            v.error(f"{c}: content must be an object.")
        else:
            for field in ("document_id", "student_id", "document_type"):
                if content.get(field) != d.get(field):
                    v.error(f"{c}: content.{field} does not match document metadata.")

            if repo == "academic":
                courses = content.get("courses")
                if not isinstance(courses, list) or not courses:
                    v.error(f"{c}: academic document must have a non-empty courses list.")
                else:
                    for j, course in enumerate(courses):
                        cc = f"{c}.content.courses[{j}]"
                        if not isinstance(course, dict):
                            v.error(f"{cc}: expected object.")
                            continue
                        if not required(course, {"code", "name", "credits", "grade"}, cc, v):
                            continue
                        if course["code"] not in course_codes:
                            v.error(f"{cc}: unknown course code {course['code']!r}.")

        latest, _ = validate_version_plan(d, c, v)
        if latest is not None:
            total_versions += latest

    unique(documents, "document_id", "documents.json", v)
    return docs, total_versions


def validate_metadata(metadata: list[dict], docs: dict[str, dict], v: Validator) -> None:
    seen: set[str] = set()

    for i, m in enumerate(metadata):
        c = f"metadata_records.json[{i}]"
        fields = {
            "document_id", "student_id", "document_type", "repository",
            "latest_version", "latest_approved_version",
            "latest_approved_cid", "keywords",
        }
        if not required(m, fields, c, v):
            continue

        did = m["document_id"]
        if did in seen:
            v.error(f"{c}: duplicate document_id={did!r}.")
        seen.add(did)

        if did not in docs:
            v.error(f"{c}: unknown document_id={did!r}.")
            continue

        d = docs[did]
        for field in ("student_id", "document_type", "repository"):
            if m[field] != d[field]:
                v.error(f"{c}: {field} does not match documents.json.")

        plan = d["version_plan"]
        latest = max(x["version"] for x in plan)
        approved = [x["version"] for x in plan if x["approval_status"] == "APPROVED"]
        latest_approved = max(approved) if approved else None

        if m["latest_version"] != latest:
            v.error(f"{c}: latest_version={m['latest_version']!r}, expected {latest!r}.")
        if m["latest_approved_version"] != latest_approved:
            v.error(
                f"{c}: latest_approved_version={m['latest_approved_version']!r}, "
                f"expected {latest_approved!r}."
            )

        expected_cid = (
            f"bafy-phase5-{did.lower()}-v{latest_approved}"
            if latest_approved is not None else None
        )
        if m["latest_approved_cid"] != expected_cid:
            v.error(
                f"{c}: latest_approved_cid={m['latest_approved_cid']!r}, "
                f"expected {expected_cid!r}."
            )

        if not isinstance(m["keywords"], list) or not all(
            isinstance(x, str) and x for x in m["keywords"]
        ):
            v.error(f"{c}: keywords must be a list of non-empty strings.")

    if len(seen) != len(docs):
        missing = sorted(set(docs) - seen)
        v.error(
            f"metadata_records.json: {len(missing)} documents have no metadata "
            f"record: {', '.join(missing[:20])}"
        )


def validate_questions(
    questions: list[dict],
    docs: dict[str, dict],
    student_ids: set[str],
    course_codes: set[str],
    v: Validator,
) -> None:
    seen: set[str] = set()

    for i, q in enumerate(questions):
        c = f"questions.json[{i}]"
        fields = {"id", "category", "student_id", "document_id", "question", "expected"}
        if not required(q, fields, c, v):
            continue

        qid, category, sid, did = q["id"], q["category"], q["student_id"], q["document_id"]

        if qid in seen:
            v.error(f"{c}: duplicate question id={qid!r}.")
        seen.add(qid)

        if category not in QUESTION_CATEGORIES:
            v.error(f"{c}: unknown category={category!r}.")
        if sid not in student_ids:
            v.error(f"{c}: unknown student_id={sid!r}.")
        if did not in docs:
            v.error(f"{c}: unknown document_id={did!r}.")
            continue

        d = docs[did]
        if d["student_id"] != sid:
            v.error(f"{c}: student_id does not match the target document.")
        if not isinstance(q["question"], str) or not q["question"]:
            v.error(f"{c}: question must be a non-empty string.")

        if d["repository"] != "academic":
            v.error(f"{c}: expected an academic document, found {d['repository']!r}.")
            continue

        courses = {
            x["code"]: x
            for x in d["content"].get("courses", [])
            if isinstance(x, dict) and "code" in x
        }
        referenced = [code for code in course_codes if code in q["question"]]

        if category == "course_specific":
            if len(referenced) != 1:
                v.error(f"{c}: course_specific question must contain exactly one course code.")
            elif q["expected"] != courses.get(referenced[0], {}).get("grade"):
                v.error(f"{c}: expected grade does not match document content.")

        elif category == "multi_course":
            if len(referenced) != 2:
                v.error(f"{c}: multi_course question must contain exactly two course codes.")
            elif not isinstance(q["expected"], dict):
                v.error(f"{c}: expected must be an object.")
            else:
                for code in referenced:
                    if q["expected"].get(code) != courses.get(code, {}).get("grade"):
                        v.error(f"{c}: expected grade for {code} does not match document content.")

        elif category == "missing_information" and q["expected"] != "not_present":
            v.error(f"{c}: expected must be 'not_present'.")

        elif category == "unsupported_information" and q["expected"] != "not_determinable":
            v.error(f"{c}: expected must be 'not_determinable'.")


def validate_attacks(
    attacks: list[dict],
    docs: dict[str, dict],
    student_ids: set[str],
    v: Validator,
) -> None:
    seen: set[str] = set()

    for i, attack in enumerate(attacks):
        c = f"security_attacks.json[{i}]"
        if not required(attack, {"id", "type", "document_id", "expected"}, c, v):
            continue

        aid, atype, did = attack["id"], attack["type"], attack["document_id"]

        if aid in seen:
            v.error(f"{c}: duplicate attack id={aid!r}.")
        seen.add(aid)

        if atype not in ATTACK_TYPES:
            v.error(f"{c}: unknown attack type={atype!r}.")
        if did not in docs:
            v.error(f"{c}: unknown document_id={did!r}.")
            continue

        if atype == "unauthorized_student":
            user = attack.get("user")
            if not isinstance(user, dict):
                v.error(f"{c}: unauthorized_student requires a user object.")
            else:
                required(user, {"username", "role", "student_id"}, f"{c}.user", v)
                if user.get("student_id") == attack.get("target_student_id"):
                    v.error(f"{c}: attacker and target student must differ.")
            if attack.get("target_student_id") not in student_ids:
                v.error(f"{c}: unknown target_student_id.")

        elif atype in {"rag_wrong_student", "rag_wrong_document_type", "rag_wrong_version"}:
            if not isinstance(attack.get("rag_mutation"), dict) or not attack["rag_mutation"]:
                v.error(f"{c}: requires a non-empty rag_mutation object.")

        elif atype == "ipfs_content_tampering":
            if not isinstance(attack.get("mutation"), str):
                v.error(f"{c}: requires a mutation description.")


def validate_manifest(
    manifest: dict,
    students: list[dict],
    documents: list[dict],
    total_versions: int,
    questions: list[dict],
    attacks: list[dict],
    v: Validator,
) -> None:
    fields = {
        "dataset_name", "dataset_version", "synthetic", "students",
        "logical_documents", "blockchain_version_records", "repositories",
        "questions", "security_attacks", "integration_note",
    }
    required(manifest, fields, "manifest.json", v)

    if manifest.get("synthetic") is not True:
        v.error("manifest.json: synthetic must be true.")

    expected = {
        "students": len(students),
        "logical_documents": len(documents),
        "blockchain_version_records": total_versions,
        "questions": len(questions),
        "security_attacks": len(attacks),
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            v.error(
                f"manifest.json: {field}={manifest.get(field)!r}, "
                f"expected {value!r}."
            )

    if set(manifest.get("repositories", [])) != REPOSITORIES:
        v.error("manifest.json: repository list does not match the dataset.")

    if "TX-98765" not in str(manifest.get("integration_note", "")):
        v.warning("manifest.json integration_note does not mention TX-98765.")


def validate(dataset: Path) -> Validator:
    v = Validator()

    print("=" * 64)
    print("PHASE 5 DATASET VALIDATION")
    print("=" * 64)
    print(f"Dataset: {dataset.resolve()}\n")

    print("[1/7] Required files")
    missing = [x for x in EXPECTED_FILES if not (dataset / x).is_file()]
    if missing:
        for x in missing:
            v.error(f"Missing file: {x}")
        print(f"  FAIL - {len(EXPECTED_FILES)-len(missing)}/{len(EXPECTED_FILES)} found")
    else:
        print(f"  PASS - {len(EXPECTED_FILES)}/{len(EXPECTED_FILES)} found")

    print("\n[2/7] JSON syntax")
    raw = {}
    for filename in EXPECTED_FILES:
        path = dataset / filename
        if path.is_file():
            data = load_json(path, v)
            if data is not None:
                raw[filename] = data
    print(
        f"  {'PASS' if len(raw) == len(EXPECTED_FILES) else 'FAIL'} - "
        f"{len(raw)}/{len(EXPECTED_FILES)} valid JSON files"
    )

    students = as_list(raw.get("students.json"), "students.json", v)
    courses = as_list(raw.get("courses.json"), "courses.json", v)
    documents = as_list(raw.get("documents.json"), "documents.json", v)
    metadata = as_list(raw.get("metadata_records.json"), "metadata_records.json", v)
    questions = as_list(raw.get("questions.json"), "questions.json", v)
    attacks = as_list(raw.get("security_attacks.json"), "security_attacks.json", v)
    manifest = as_dict(raw.get("manifest.json"), "manifest.json", v)

    print("\n[3/7] Students and courses")
    student_ids = validate_students(students, v)
    course_codes = validate_courses(courses, v)
    before = len(v.errors)
    print(
        f"  {'PASS' if len(v.errors) == before else 'FAIL'} - "
        f"{len(student_ids)} students, {len(course_codes)} courses"
    )

    print("\n[4/7] Documents and immutable versions")
    before = len(v.errors)
    docs, total_versions = validate_documents(documents, student_ids, course_codes, v)
    print(
        f"  {'PASS' if len(v.errors) == before else 'FAIL'} - "
        f"{len(docs)} documents, {total_versions} version records"
    )

    print("\n[5/7] Metadata, questions, and attacks")
    before = len(v.errors)
    validate_metadata(metadata, docs, v)
    validate_questions(questions, docs, student_ids, course_codes, v)
    validate_attacks(attacks, docs, student_ids, v)
    print(
        f"  {'PASS' if len(v.errors) == before else 'FAIL'} - "
        f"{len(v.errors)-before} validation error(s) in this stage"
    )

    print("\n[6/7] Manifest")
    before = len(v.errors)
    validate_manifest(manifest, students, documents, total_versions, questions, attacks, v)
    print(f"  {'PASS' if len(v.errors) == before else 'FAIL'}")

    print("\n[7/7] Final result")
    print(f"  {'PASS' if v.passed else 'FAIL'}")

    return v


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Phase 5 dataset.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("phase5_dataset"),
        help="Path to the Phase 5 dataset directory.",
    )
    args = parser.parse_args()

    if not args.dataset.is_dir():
        print(f"ERROR: dataset directory not found: {args.dataset}", file=sys.stderr)
        return 1

    v = validate(args.dataset)

    print("\n" + "=" * 64)
    if v.passed:
        print("RESULT: DATASET VALIDATION PASSED")
    else:
        print("RESULT: DATASET VALIDATION FAILED")
        print("\nERRORS:")
        for i, error in enumerate(v.errors, 1):
            print(f"  {i:03d}. {error}")

    if v.warnings:
        print("\nWARNINGS:")
        for i, warning in enumerate(v.warnings, 1):
            print(f"  {i:03d}. {warning}")

    print("=" * 64)
    return 0 if v.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
