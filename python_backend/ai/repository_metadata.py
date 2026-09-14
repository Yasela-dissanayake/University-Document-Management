"""
Repository metadata registry.

Phase 2 / Phase 3 responsibility:
    - Store and retrieve document metadata only.
    - Support repository-centric metadata retrieval.
    - Provide metadata for RAG-based document discovery.

IMPORTANT:
    This module must NEVER store or retrieve confidential document plaintext.

The repository metadata is used for discovery. Once a document is identified,
the blockchain becomes authoritative for version, provenance, integrity,
approval status, and the current approved IPFS CID.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Supported logical repositories
# ---------------------------------------------------------------------------

REPOSITORIES = (
    "academic",
    "registrar",
    "examination",
    "student_services",
)


REPOSITORY_DISPLAY_NAMES = {
    "academic": "Academic Repository",
    "registrar": "Registrar Repository",
    "examination": "Examination Repository",
    "student_services": "Student Services Repository",
}


# ---------------------------------------------------------------------------
# Development-only metadata references
#
# IMPORTANT:
# These records contain metadata only.
# They must NOT contain confidential document plaintext.
#
# latest_version / latest_approved_cid are metadata references used for
# discovery and demonstration. Phase 3 must resolve the authoritative
# version and CID from the blockchain before retrieving the document.
# ---------------------------------------------------------------------------

DEMO_METADATA: List[Dict[str, Any]] = [
    {
        "document_id": "TX-98765",
        "student_id": "S19357",
        "document_type": "Transcript",
        "semester": "Semester 5",
        "course_codes": [
            "CO521",
            "CO523",
            "CO525",
        ],
        "keywords": [
            "transcript",
            "semester 5",
            "computer engineering",
            "academic",
        ],
        "repository": "academic",
        "latest_version": 3,
        "latest_approved_cid": "bafy-demo-academic-s19357-v3",
    },
    {
        "document_id": "REG-19357-01",
        "student_id": "S19357",
        "document_type": "Student Registration Record",
        "semester": None,
        "course_codes": [],
        "keywords": [
            "registration",
            "registered",
            "enrollment",
            "student",
        ],
        "repository": "registrar",
        "latest_version": 1,
        "latest_approved_cid": "bafy-demo-registrar-s19357-v1",
    },
    {
        "document_id": "EX-19357-05",
        "student_id": "S19357",
        "document_type": "Examination Record",
        "semester": "Semester 5",
        "course_codes": [
            "CO521",
            "CO523",
            "CO525",
        ],
        "keywords": [
            "examination",
            "exam",
            "results",
            "mark sheet",
            "semester 5",
        ],
        "repository": "examination",
        "latest_version": 2,
        "latest_approved_cid": "bafy-demo-examination-s19357-v2",
    },
    {
        "document_id": "SS-19357-01",
        "student_id": "S19357",
        "document_type": "Student Services Record",
        "semester": None,
        "course_codes": [],
        "keywords": [
            "student services",
            "scholarship",
            "welfare",
            "request",
        ],
        "repository": "student_services",
        "latest_version": 1,
        "latest_approved_cid": "bafy-demo-services-s19357-v1",
    },
]


# ---------------------------------------------------------------------------
# Metadata schema
#
# Only these fields are allowed in repository metadata.
#
# Blockchain-specific authoritative fields such as contentHash,
# approvalStatus, previousVersion, and timestamps do NOT belong here.
# ---------------------------------------------------------------------------

_ALLOWED = {
    "document_id",
    "student_id",
    "document_type",
    "semester",
    "course_codes",
    "keywords",
    "repository",
    "latest_version",
    "latest_approved_cid",
}


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _clean(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a metadata record.

    Only fields explicitly allowed by _ALLOWED are retained.
    No confidential document content is accepted.
    """

    cleaned = {
        key: record.get(key)
        for key in _ALLOWED
        if key in record
    }

    # Document identifier
    cleaned["document_id"] = str(
        cleaned.get("document_id", "")
    ).strip()

    # Student identifier
    cleaned["student_id"] = str(
        cleaned.get("student_id", "")
    ).strip().upper()

    # Document type
    cleaned["document_type"] = str(
        cleaned.get("document_type", "")
    ).strip()

    # Semester
    semester = cleaned.get("semester")

    if semester is None:
        cleaned["semester"] = None
    else:
        semester_text = str(semester).strip()
        cleaned["semester"] = semester_text or None

    # Repository
    cleaned["repository"] = str(
        cleaned.get("repository", "")
    ).strip().lower()

    # Course codes
    course_codes = cleaned.get("course_codes") or []

    if not isinstance(course_codes, (list, tuple, set)):
        course_codes = [course_codes]

    cleaned["course_codes"] = [
        str(course).strip().upper()
        for course in course_codes
        if str(course).strip()
    ]

    # Keywords
    keywords = cleaned.get("keywords") or []

    if not isinstance(keywords, (list, tuple, set)):
        keywords = [keywords]

    cleaned["keywords"] = [
        str(keyword).strip().lower()
        for keyword in keywords
        if str(keyword).strip()
    ]

    # Latest version
    try:
        cleaned["latest_version"] = int(
            cleaned.get("latest_version", 0)
        )
    except (TypeError, ValueError):
        cleaned["latest_version"] = 0

    # CID is only a metadata reference.
    cid = cleaned.get("latest_approved_cid")

    if cid is None:
        cleaned["latest_approved_cid"] = ""
    else:
        cleaned["latest_approved_cid"] = str(cid).strip()

    return cleaned


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_metadata_record(record: Dict[str, Any]) -> bool:
    """
    Validate a repository metadata record.

    Validation is intentionally limited to metadata structure.
    Confidential document content is never accepted or inspected.
    """

    if not isinstance(record, dict):
        return False

    cleaned = _clean(record)

    if not cleaned["document_id"]:
        return False

    if not cleaned["student_id"]:
        return False

    if not cleaned["document_type"]:
        return False

    if cleaned["repository"] not in REPOSITORIES:
        return False

    if cleaned["latest_version"] < 0:
        return False

    return True


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_metadata_records(
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Load repository metadata records.

    Priority:
        1. Explicitly supplied records
        2. METADATA_REGISTRY_PATH environment variable
        3. Development DEMO_METADATA

    The registry file may contain either:

        [
            {...},
            {...}
        ]

    or:

        {
            "documents": [
                {...},
                {...}
            ]
        }

    Invalid records are ignored.
    """

    if records is not None:
        source = list(records)

    else:
        configured = os.getenv(
            "METADATA_REGISTRY_PATH",
            "",
        ).strip()

        if configured:
            path = Path(configured)

            if not path.is_absolute():
                path = (
                    Path(__file__).resolve().parent
                    / path
                )

            with path.open(
                "r",
                encoding="utf-8",
            ) as file:
                loaded = json.load(file)

            if isinstance(loaded, dict):
                source = loaded.get(
                    "documents",
                    [],
                )
            else:
                source = loaded

        else:
            source = DEMO_METADATA

    if not isinstance(source, list):
        return []

    valid_records: List[Dict[str, Any]] = []

    for record in source:
        if not isinstance(record, dict):
            continue

        if not validate_metadata_record(record):
            continue

        valid_records.append(
            _clean(record)
        )

    return valid_records


# ---------------------------------------------------------------------------
# Repository-specific retrieval
# ---------------------------------------------------------------------------

def get_repository_metadata(
    repository: str,
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Return metadata records belonging to one repository.
    """

    repository_name = str(
        repository or ""
    ).strip().lower()

    if repository_name not in REPOSITORIES:
        return []

    return [
        record
        for record in load_metadata_records(records)
        if record["repository"] == repository_name
    ]


# ---------------------------------------------------------------------------
# All repositories
# ---------------------------------------------------------------------------

def get_all_metadata(
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Return metadata from all supported repositories.
    """

    return load_metadata_records(records)


# ---------------------------------------------------------------------------
# Document lookup
# ---------------------------------------------------------------------------

def get_document_metadata(
    document_id: str,
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Find one metadata record by document ID.

    This is still metadata-only. It does not retrieve the document itself.
    """

    target = str(
        document_id or ""
    ).strip()

    if not target:
        return None

    for record in load_metadata_records(records):
        if record["document_id"] == target:
            return record

    return None


# ---------------------------------------------------------------------------
# Student-specific metadata
# ---------------------------------------------------------------------------

def get_student_metadata(
    student_id: str,
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Return metadata references associated with a student.
    """

    target = str(
        student_id or ""
    ).strip().upper()

    if not target:
        return []

    return [
        record
        for record in load_metadata_records(records)
        if record["student_id"] == target
    ]


# ---------------------------------------------------------------------------
# Repository + student lookup
# ---------------------------------------------------------------------------

def get_student_repository_metadata(
    student_id: str,
    repository: str,
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Return metadata for a student within a specific repository.
    """

    target_student = str(
        student_id or ""
    ).strip().upper()

    target_repository = str(
        repository or ""
    ).strip().lower()

    if (
        not target_student
        or target_repository not in REPOSITORIES
    ):
        return []

    return [
        record
        for record in load_metadata_records(records)
        if (
            record["student_id"] == target_student
            and record["repository"] == target_repository
        )
    ]