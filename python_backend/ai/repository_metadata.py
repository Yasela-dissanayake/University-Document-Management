"""
Metadata-only source registry for Repository RAG.

Source precedence:
1. Explicit records passed by the caller (useful for isolated unit tests).
2. METADATA_REGISTRY_PATH (a JSON snapshot maintained by an indexer/admin).
3. METADATA_SOURCE=blockchain (default): read enumerable student-registration
   metadata from the deployed UniversityRegistry.

DEMO_METADATA is available only when METADATA_SOURCE=demo is explicitly set.
The loader never silently falls back to demo records.

Important contract limitation:
UniversityRegistry exposes studentIds/studentCount, so student registration
records can be enumerated. Its versioned documents mapping has no document-ID
enumeration method. DocumentVersionCreated indexes string identifiers as topic
hashes, which cannot be decoded from event topics. Consequently this module
cannot discover every createDocumentVersion record from the current contract
without an off-chain indexer/snapshot. It does not fabricate those records.

This module never retrieves IPFS content or decrypts documents.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from python_backend.ai.blockchain_metadata_source import load_blockchain_metadata_records

REPOSITORIES = ("academic", "registrar", "examination", "student_services")

REPOSITORY_DISPLAY_NAMES = {
    "academic": "Academic Repository",
    "registrar": "Registrar Repository",
    "examination": "Examination Repository",
    "student_services": "Student Services Repository",
}

# Development-only fixtures. They are intentionally unreachable unless the
# operator explicitly selects METADATA_SOURCE=demo.
DEMO_METADATA: List[Dict[str, Any]] = [
    {
        "document_id": "TX-98765", "student_id": "STD001",
        "document_type": "Transcript", "semester": "Semester 5",
        "course_codes": ["CO521", "CO523"],
        "keywords": ["transcript", "semester 5", "computer engineering", "academic"],
        "repository": "academic", "latest_version": 4,
        "latest_approved_cid": "QmP2pwTUWYqzVEp3HQoBWHsZ99xUmgpWdJMYZDNKe9WUiF",
        "metadata_origin": "demo_fixture", "approval_status": "APPROVED",
    },
    {
        "document_id": "REG-19357-01", "student_id": "S19357",
        "document_type": "Student Registration Record", "semester": None,
        "course_codes": [], "keywords": ["registration", "registered", "enrollment", "student"],
        "repository": "registrar", "latest_version": 1,
        "latest_approved_cid": "", "metadata_origin": "demo_fixture",
        "approval_status": "NOT_TRACKED",
    },
    {
        "document_id": "EX-19357-05", "student_id": "S19357",
        "document_type": "Examination Record", "semester": "Semester 5",
        "course_codes": ["CO521", "CO523", "CO525"],
        "keywords": ["examination", "exam", "results", "mark sheet", "semester 5"],
        "repository": "examination", "latest_version": 2,
        "latest_approved_cid": "", "metadata_origin": "demo_fixture",
        "approval_status": "NOT_TRACKED",
    },
    {
        "document_id": "SS-19357-01", "student_id": "S19357",
        "document_type": "Student Services Record", "semester": None,
        "course_codes": [], "keywords": ["student services", "scholarship", "welfare", "request"],
        "repository": "student_services", "latest_version": 1,
        "latest_approved_cid": "", "metadata_origin": "demo_fixture",
        "approval_status": "NOT_TRACKED",
    },
]

_ALLOWED = {
    "document_id", "student_id", "document_type", "semester",
    "course_codes", "keywords", "repository", "latest_version",
    "latest_approved_cid", "metadata_origin", "approval_status",
}


def _clean(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a record and retain only approved metadata fields."""
    cleaned = {key: record.get(key) for key in _ALLOWED if key in record}
    cleaned["document_id"] = str(cleaned.get("document_id") or "").strip()
    cleaned["student_id"] = str(cleaned.get("student_id") or "").strip().upper()
    cleaned["document_type"] = str(cleaned.get("document_type") or "").strip()

    semester = cleaned.get("semester")
    cleaned["semester"] = str(semester).strip() or None if semester is not None else None
    cleaned["repository"] = str(cleaned.get("repository") or "").strip().lower()

    course_codes = cleaned.get("course_codes") or []
    if not isinstance(course_codes, (list, tuple, set)):
        course_codes = [course_codes]
    cleaned["course_codes"] = [
        str(value).strip().upper() for value in course_codes if str(value).strip()
    ]

    keywords = cleaned.get("keywords") or []
    if not isinstance(keywords, (list, tuple, set)):
        keywords = [keywords]
    cleaned["keywords"] = [
        str(value).strip().lower() for value in keywords if str(value).strip()
    ]

    try:
        cleaned["latest_version"] = max(0, int(cleaned.get("latest_version", 0) or 0))
    except (TypeError, ValueError):
        cleaned["latest_version"] = 0

    cleaned["latest_approved_cid"] = str(cleaned.get("latest_approved_cid") or "").strip()
    cleaned["metadata_origin"] = str(cleaned.get("metadata_origin") or "json_snapshot").strip()
    cleaned["approval_status"] = str(cleaned.get("approval_status") or "UNKNOWN").strip().upper()
    return cleaned


def validate_metadata_record(record: Dict[str, Any]) -> bool:
    """Validate the metadata schema; document plaintext is never accepted."""
    if not isinstance(record, dict):
        return False
    cleaned = _clean(record)
    return bool(
        cleaned["document_id"]
        and cleaned["student_id"]
        and cleaned["document_type"]
        and cleaned["repository"] in REPOSITORIES
        and cleaned["latest_version"] >= 0
    )


def _load_json_snapshot(configured: str) -> List[Dict[str, Any]]:
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if not path.is_file():
        raise FileNotFoundError(f"METADATA_REGISTRY_PATH does not exist: {path}")
    with path.open("r", encoding="utf-8") as file:
        loaded = json.load(file)
    source = loaded.get("documents") if isinstance(loaded, dict) else loaded
    if not isinstance(source, list):
        raise ValueError("Metadata snapshot must be a JSON array or an object with a 'documents' array.")
    return source


def load_metadata_records(
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Load metadata without silently switching to synthetic fixtures.

    METADATA_SOURCE values:
      - blockchain (default): enumerable metadata from UniversityRegistry
      - json: requires METADATA_REGISTRY_PATH
      - demo: explicitly opt into development fixtures
    A configured METADATA_REGISTRY_PATH takes precedence over METADATA_SOURCE.
    """
    if records is not None:
        source = list(records)
    else:
        configured_path = os.getenv("METADATA_REGISTRY_PATH", "").strip()
        if configured_path:
            source = _load_json_snapshot(configured_path)
        else:
            source_name = os.getenv("METADATA_SOURCE", "blockchain").strip().lower()
            if source_name == "blockchain":
                source = load_blockchain_metadata_records()
            elif source_name == "json":
                raise RuntimeError(
                    "METADATA_SOURCE=json requires METADATA_REGISTRY_PATH to point to a metadata snapshot."
                )
            elif source_name == "demo":
                source = DEMO_METADATA
            else:
                raise ValueError(
                    "Unsupported METADATA_SOURCE. Choose 'blockchain', 'json', or 'demo'."
                )

    if not isinstance(source, list):
        raise ValueError("Metadata source must return a list of records.")

    valid_records: List[Dict[str, Any]] = []
    seen_ids = set()
    for record in source:
        if not isinstance(record, dict) or not validate_metadata_record(record):
            continue
        cleaned = _clean(record)
        # Keep the first occurrence, avoiding duplicate document IDs from snapshots.
        if cleaned["document_id"] in seen_ids:
            continue
        seen_ids.add(cleaned["document_id"])
        valid_records.append(cleaned)
    return valid_records


def get_repository_metadata(repository: str, records: Optional[Iterable[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    repository_name = str(repository or "").strip().lower()
    if repository_name not in REPOSITORIES:
        return []
    return [record for record in load_metadata_records(records) if record["repository"] == repository_name]


def get_all_metadata(records: Optional[Iterable[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    return load_metadata_records(records)


def get_document_metadata(document_id: str, records: Optional[Iterable[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    target = str(document_id or "").strip()
    if not target:
        return None
    return next((record for record in load_metadata_records(records) if record["document_id"] == target), None)


def get_student_metadata(student_id: str, records: Optional[Iterable[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    target = str(student_id or "").strip().upper()
    if not target:
        return []
    return [record for record in load_metadata_records(records) if record["student_id"] == target]


def get_student_repository_metadata(
    student_id: str,
    repository: str,
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    target_student = str(student_id or "").strip().upper()
    target_repository = str(repository or "").strip().lower()
    if not target_student or target_repository not in REPOSITORIES:
        return []
    return [
        record for record in load_metadata_records(records)
        if record["student_id"] == target_student and record["repository"] == target_repository
    ]
