"""
Metadata-only Repository RAG.

Phase 2 / Phase 3 responsibility:
    - Search repository metadata.
    - Identify candidate documents.
    - Return document references and metadata.
    - Never retrieve confidential document plaintext.
    - Never access IPFS.
    - Never perform document decryption.

The Phase 3 workflow uses the result of this module as a discovery step:

    User Query
        |
        v
    Authorization
        |
        v
    Repository Metadata RAG
        |
        v
    Candidate Document
        |
        v
    Blockchain Authoritative Resolution
        |
        v
    Authorized IPFS Retrieval / Decryption
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from python_backend.ai.repository_metadata import (
    REPOSITORIES,
    REPOSITORY_DISPLAY_NAMES,
    load_metadata_records,
)


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    """
    Normalize arbitrary values into a lowercase whitespace-normalized string.
    """

    return " ".join(
        str(value or "")
        .strip()
        .lower()
        .split()
    )


def _tokens(value: Any) -> set[str]:
    """
    Convert text into a lightweight set of searchable tokens.

    This is intentionally a simple metadata retrieval mechanism.
    Confidential document content is never passed here.
    """

    return set(
        re.findall(
            r"[a-z0-9_]+",
            _norm(value),
        )
    )


# ---------------------------------------------------------------------------
# Metadata representation
# ---------------------------------------------------------------------------

def _record_text(record: Dict[str, Any]) -> str:
    """
    Build the searchable text representation of a metadata record.

    Only metadata fields are included.
    """

    values = [
        record.get("document_id"),
        record.get("student_id"),
        record.get("document_type"),
        record.get("semester"),
        record.get("repository"),
    ]

    values.extend(
        record.get("course_codes") or []
    )

    values.extend(
        record.get("keywords") or []
    )

    return _norm(
        " ".join(
            str(value or "")
            for value in values
        )
    )


# ---------------------------------------------------------------------------
# Metadata scoring
# ---------------------------------------------------------------------------

def _score(
    question: str,
    record: Dict[str, Any],
    student_id: Optional[str] = None,
    course_code: Optional[str] = None,
) -> Tuple[int, List[str]]:
    """
    Calculate a lightweight relevance score for one metadata record.

    Scoring priorities:
        - Student ID match
        - Course code match
        - Document type match
        - Semester match
        - General keyword overlap

    This function operates exclusively on metadata.
    """

    normalized_question = _norm(question)

    question_tokens = _tokens(
        normalized_question
    )

    record_tokens = _tokens(
        _record_text(record)
    )

    score = len(
        question_tokens & record_tokens
    )

    reasons: List[str] = []

    # ------------------------------------------------------------------
    # Student ID
    # ------------------------------------------------------------------

    normalized_student_id = _norm(
        student_id
    )

    record_student_id = _norm(
        record.get("student_id")
    )

    if (
        normalized_student_id
        and record_student_id
        == normalized_student_id
    ):
        score += 10
        reasons.append("student_id_match")

    # ------------------------------------------------------------------
    # Course code
    # ------------------------------------------------------------------

    normalized_course_code = _norm(
        course_code
    )

    record_course_codes = {
        _norm(code)
        for code in (
            record.get("course_codes")
            or []
        )
    }

    if (
        normalized_course_code
        and normalized_course_code
        in record_course_codes
    ):
        score += 8
        reasons.append("course_code_match")

    # ------------------------------------------------------------------
    # Document type
    # ------------------------------------------------------------------

    document_type = _norm(
        record.get("document_type")
    )

    if (
        document_type
        and document_type in normalized_question
    ):
        score += 4
        reasons.append("document_type_match")

    # ------------------------------------------------------------------
    # Semester
    # ------------------------------------------------------------------

    semester = _norm(
        record.get("semester")
    )

    if (
        semester
        and semester in normalized_question
    ):
        score += 3
        reasons.append("semester_match")

    # ------------------------------------------------------------------
    # General keyword overlap
    # ------------------------------------------------------------------

    if score > 0:
        reasons.append("keyword_overlap")

    return score, reasons


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def _format_result(
    record: Dict[str, Any],
    score: int,
    reasons: List[str],
) -> Dict[str, Any]:
    """
    Convert an internal metadata record into the public RAG result format.

    No document plaintext is included.
    """

    repository = str(
        record.get("repository", "")
    ).strip().lower()

    return {
        "document_id": record.get(
            "document_id"
        ),
        "student_id": record.get(
            "student_id"
        ),
        "document_type": record.get(
            "document_type"
        ),
        "semester": record.get(
            "semester"
        ),
        "course_codes": list(
            record.get("course_codes")
            or []
        ),
        "keywords": list(
            record.get("keywords")
            or []
        ),
        "repository": repository,
        "repository_name": (
            REPOSITORY_DISPLAY_NAMES.get(
                repository,
                repository.title(),
            )
        ),
        "latest_version": record.get(
            "latest_version"
        ),
        "latest_approved_cid": record.get(
            "latest_approved_cid"
        ),
        "relevance_score": score,
        "match_reasons": list(reasons),

        # Explicitly indicate that this RAG did not retrieve content.
        "content_retrieved": False,
    }


# ---------------------------------------------------------------------------
# Single repository search
# ---------------------------------------------------------------------------

def search_repository_metadata(
    question: str,
    repository: str,
    *,
    student_id: Optional[str] = None,
    course_code: Optional[str] = None,
    records: Optional[
        Iterable[Dict[str, Any]]
    ] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Search metadata within one logical repository.

    Returns metadata references only.

    It does NOT:
        - access IPFS;
        - decrypt documents;
        - retrieve document plaintext;
        - query confidential document content.
    """

    repository_name = str(
        repository or ""
    ).strip().lower()

    if repository_name not in REPOSITORIES:
        return []

    try:
        limit = max(
            1,
            int(top_k),
        )
    except (TypeError, ValueError):
        limit = 5

    ranked: List[
        Tuple[
            int,
            Dict[str, Any],
            List[str],
        ]
    ] = []

    for record in load_metadata_records(
        records
    ):
        if (
            record.get("repository")
            != repository_name
        ):
            continue

        score, reasons = _score(
            question,
            record,
            student_id,
            course_code,
        )

        if score <= 0:
            continue

        ranked.append(
            (
                score,
                record,
                reasons,
            )
        )

    # Highest relevance first.
    #
    # latest_version is used only as a deterministic tie-breaker.
    ranked.sort(
        key=lambda item: (
            item[0],
            int(
                item[1].get(
                    "latest_version",
                    0,
                )
                or 0
            ),
            str(
                item[1].get(
                    "document_id",
                    "",
                )
            ),
        ),
        reverse=True,
    )

    return [
        _format_result(
            record=record,
            score=score,
            reasons=reasons,
        )
        for score, record, reasons
        in ranked[:limit]
    ]


# ---------------------------------------------------------------------------
# Multi-repository search
# ---------------------------------------------------------------------------

def search_multiple_repositories(
    question: str,
    repositories: Sequence[str],
    *,
    student_id: Optional[str] = None,
    course_code: Optional[str] = None,
    records: Optional[
        Iterable[Dict[str, Any]]
    ] = None,
    top_k_per_repository: int = 3,
) -> List[Dict[str, Any]]:
    """
    Search multiple permitted repositories.

    The repository list is expected to have already been determined by
    the authorization / routing layer.

    This function does not expand permissions and does not retrieve
    confidential content.
    """

    normalized_repositories: List[str] = []

    for repository in repositories:
        repository_name = str(
            repository or ""
        ).strip().lower()

        if (
            repository_name not in REPOSITORIES
        ):
            continue

        if (
            repository_name
            not in normalized_repositories
        ):
            normalized_repositories.append(
                repository_name
            )

    results: List[Dict[str, Any]] = []

    for repository_name in normalized_repositories:
        results.extend(
            search_repository_metadata(
                question,
                repository_name,
                student_id=student_id,
                course_code=course_code,
                records=records,
                top_k=top_k_per_repository,
            )
        )

    # Global ranking across the repositories.
    results.sort(
        key=lambda result: (
            result.get(
                "relevance_score",
                0,
            ),
            int(
                result.get(
                    "latest_version",
                    0,
                )
                or 0
            ),
            str(
                result.get(
                    "document_id",
                    "",
                )
            ),
        ),
        reverse=True,
    )

    return results


# ---------------------------------------------------------------------------
# Repository Metadata RAG facade
# ---------------------------------------------------------------------------

class RepositoryMetadataRAG:
    """
    Metadata-only Repository RAG facade.

    The class provides the interface consumed by the LangGraph agent.

    No IPFS client, blockchain client, encryption key, or confidential
    document content is used here.
    """

    def __init__(
        self,
        records: Optional[
            Iterable[Dict[str, Any]]
        ] = None,
    ) -> None:
        """
        Initialize the metadata RAG.

        If records are supplied, they are materialized once so that
        repeated searches use the same metadata snapshot.
        """

        self._records = (
            list(records)
            if records is not None
            else None
        )

    def search(
        self,
        question: str,
        repositories: Sequence[str],
        *,
        student_id: Optional[str] = None,
        course_code: Optional[str] = None,
        top_k_per_repository: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Search metadata across the supplied repositories.

        The supplied repositories are treated as the permitted search
        scope. This method does not grant additional access.
        """

        return search_multiple_repositories(
            question,
            repositories,
            student_id=student_id,
            course_code=course_code,
            records=self._records,
            top_k_per_repository=top_k_per_repository,
        )