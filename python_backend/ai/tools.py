"""
AI tools for the university document management system.

Phase 3 architecture:

    User Query
        |
        v
    LangGraph Authorization
        |
        v
    Repository Metadata RAG
        |
        v
    document_id
        |
        v
    Blockchain authoritative resolution
        |
        +--> approved version
        +--> previous version
        +--> IPFS CID
        +--> content hash
        |
        v
    IPFS retrieval
        |
        v
    Integrity verification
        |
        v
    Authorized document

IMPORTANT:
    Repository RAG is metadata-only.

    Blockchain is authoritative for document version, provenance,
    approval status, CID, and content hash.

    IPFS contains encrypted document content.

    This module does NOT grant authorization. Authorization must
    already have been established by the LangGraph authorization
    node / RBAC layer before confidential retrieval is performed.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Load .env early
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv

    load_dotenv(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            ".env",
        )
    )

except Exception:
    pass


# ---------------------------------------------------------------------------
# Project clients
# ---------------------------------------------------------------------------

from python_backend.blockchain_client import (
    UniversityBlockchainClient,
)

from python_backend.ipfs_client import (
    UniversityIPFSClient,
)

from python_backend import (
    rbac,
    acl,
)


# ---------------------------------------------------------------------------
# Shared clients
# ---------------------------------------------------------------------------

_bc = UniversityBlockchainClient()
_ipfs = UniversityIPFSClient()


# ===========================================================================


def _canon_sid(s: str) -> str:
    """
    Canonicalize a student ID.
    """

    return (
        str(s or "")
        .strip()
        .upper()
    )


# ===========================================================================


def _canon_document_id(
    document_id: str,
) -> str:
    """
    Canonicalize a document ID.
    """

    return (
        str(document_id or "")
        .strip()
    )


# ===========================================================================


def _is_allowed_offchain(
    user: Optional[Dict[str, Any]],
    student_id: str,
) -> bool:
    """
    Check whether the current user may access off-chain data
    belonging to the specified student.

    This function delegates the actual decision to the existing
    RBAC implementation.
    """

    return rbac.can_view_offchain(
        user,
        _canon_sid(student_id),
    )


# ===========================================================================


def get_onchain_student(
    student_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Public read: retrieve an on-chain student record.

    This does not access IPFS and does not retrieve confidential
    document content.
    """

    sid = _canon_sid(student_id)

    if not sid:
        return None

    try:
        return _bc.get_student_details(
            sid
        )

    except Exception:
        return None


# ===========================================================================


def list_semester_cids(
    student_id: str,
) -> List[str]:
    """
    Return historical semester IPFS CIDs for a student.

    This is retained for compatibility with the existing academic
    document workflow.
    """

    sid = _canon_sid(student_id)

    if not sid:
        return []

    try:
        cids = (
            _bc.get_all_semester_hashes(
                sid
            )
            or []
        )

        return [
            str(cid)
            for cid in cids
            if cid
        ]

    except Exception:
        return []


# ===========================================================================


def _fetch_and_decrypt(
    cid: str,
    user: Optional[Dict[str, Any]],
    student_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a legacy semester document and perform the existing
    application-level decryption path when required.

    This function is retained for backward compatibility.

    Phase 3 document retrieval should preferably use
    get_authorized_document().
    """

    try:
        payload = (
            _ipfs.retrieve_academic_document(
                cid
            )
        )

        if (
            isinstance(
                payload,
                dict,
            )
            and payload.get("enc")
            == "fernet-v1"
        ):
            return acl.decrypt_for_user(
                user,
                _canon_sid(student_id),
                cid,
                payload,
            )

        return (
            payload
            if isinstance(
                payload,
                dict,
            )
            else {
                "raw": payload
            }
        )

    except PermissionError:
        return None

    except Exception:
        return None


# ===========================================================================


def get_offchain_semesters(
    student_id: str,
    user: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Protected read of historical semester records.

    Returns:

        {
            "ok": bool,
            "semesters": [...],
            "trace": {...}
        }

    This function is preserved from the existing Phase 2 workflow.
    """

    sid = _canon_sid(
        student_id
    )

    trace: Dict[str, Any] = {
        "student_id": sid,
        "action": "get_offchain_semesters",
        "rbac_allowed": False,
        "ipfs_docs_checked": [],
        "explanation": "",
    }

    if not _is_allowed_offchain(
        user,
        sid,
    ):
        trace["explanation"] = (
            "Access denied due to RBAC."
        )

        return {
            "ok": False,
            "reason": "forbidden",
            "trace": trace,
        }

    trace["rbac_allowed"] = True

    cids = list_semester_cids(
        sid
    )

    trace["ipfs_docs_checked"] = cids

    out: List[Dict[str, Any]] = []

    for cid in cids:
        doc = _fetch_and_decrypt(
            cid,
            user,
            sid,
        )

        if not isinstance(
            doc,
            dict,
        ):
            continue

        item: Dict[str, Any] = {
            "cid": cid
        }

        for key in (
            "timestamp",
            "document_type",
            "gpa",
            "courses",
            "student_id",
        ):
            if key in doc:
                item[key] = doc[key]

        out.append(item)

    trace["explanation"] = (
        f"Fetched and decrypted "
        f"{len(out)} semester record(s)."
    )

    return {
        "ok": True,
        "semesters": out,
        "trace": trace,
    }


# ===========================================================================


def get_course_grade(
    student_id: str,
    course_code: str,
    user: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Retrieve a student's grade for a course.

    This is retained for the existing academic workflow.

    Returns either:

        {
            "ok": False,
            "reason": "forbidden",
            "trace": ...
        }

    or:

        {
            "ok": True,
            "found": True/False,
            "grade": ...,
            "cid": ...,
            "when": ...,
            "trace": ...
        }
    """

    sid = _canon_sid(
        student_id
    )

    cc = (
        str(course_code or "")
        .strip()
        .upper()
    )

    semesters_resp = (
        get_offchain_semesters(
            sid,
            user,
        )
    )

    trace: Dict[str, Any] = {
        "student_id": sid,
        "course_code": cc,
        "rbac_allowed": semesters_resp.get(
            "ok",
            False,
        ),
        "ipfs_docs_checked": [],
        "matched_doc": None,
        "explanation": "",
    }

    if not semesters_resp.get(
        "ok"
    ):
        trace["explanation"] = (
            "Access denied or off-chain "
            "semesters not available."
        )

        return {
            "ok": False,
            "reason": "forbidden",
            "trace": trace,
        }

    semesters: List[
        Dict[str, Any]
    ] = semesters_resp.get(
        "semesters",
        [],
    )

    trace["ipfs_docs_checked"] = [
        semester.get("cid")
        for semester in semesters
    ]

    # Newest → oldest
    semesters.sort(
        key=lambda item: item.get(
            "timestamp",
            0,
        ),
        reverse=True,
    )

    for semester in semesters:
        courses = (
            semester.get(
                "courses"
            )
            or []
        )

        for course in courses:
            code = (
                str(
                    course.get(
                        "code",
                        "",
                    )
                )
                .strip()
                .upper()
            )

            if code != cc:
                continue

            grade = course.get(
                "grade"
            )

            trace["matched_doc"] = (
                semester.get("cid")
            )

            trace["explanation"] = (
                f"Found {cc} with grade "
                f"{grade} in CID "
                f"{semester.get('cid')}"
            )

            return {
                "ok": True,
                "found": True,
                "grade": grade,
                "cid": semester.get(
                    "cid"
                ),
                "when": semester.get(
                    "timestamp"
                ),
                "trace": trace,
            }

    trace["explanation"] = (
        f"{cc} not found in any "
        "semester document."
    )

    return {
        "ok": True,
        "found": False,
        "trace": trace,
    }


# ===========================================================================
# Phase 3: Blockchain → IPFS document retrieval
# ===========================================================================


def resolve_document_from_blockchain(
    document_id: str,
    *,
    expected_student_id: Optional[str] = None,
    expected_document_type: Optional[str] = None,
    approved_only: bool = True,
) -> Dict[str, Any]:
    """
    Resolve the authoritative document version from blockchain.

    This is the Phase 3 bridge between repository metadata discovery
    and IPFS retrieval.

    IMPORTANT:
        The repository metadata CID is NOT used as the source of truth.

        The blockchain is queried using document_id and determines:
            - authoritative version
            - previous version
            - IPFS CID
            - content hash
            - approval status
            - approver
            - timestamp
    """

    normalized_document_id = (
        _canon_document_id(
            document_id
        )
    )

    trace: Dict[str, Any] = {
        "action": "resolve_document_from_blockchain",
        "document_id": normalized_document_id,
        "source": "blockchain",
        "approved_only": approved_only,
        "success": False,
        "explanation": "",
    }

    if not normalized_document_id:
        trace["explanation"] = (
            "Document ID is empty."
        )

        return {
            "ok": False,
            "reason": "invalid_document_id",
            "trace": trace,
        }

    try:
        resolved = (
            _bc.resolve_authoritative_document(
                normalized_document_id,
                expected_student_id=(
                    expected_student_id
                ),
                expected_document_type=(
                    expected_document_type
                ),
                approved_only=approved_only,
            )
        )

    except PermissionError as exc:
        trace["explanation"] = str(
            exc
        )

        return {
            "ok": False,
            "reason": "blockchain_identity_mismatch",
            "trace": trace,
        }

    except ValueError as exc:
        trace["explanation"] = str(
            exc
        )

        return {
            "ok": False,
            "reason": "blockchain_metadata_mismatch",
            "trace": trace,
        }

    except Exception as exc:
        trace["explanation"] = (
            "Failed to resolve document "
            f"from blockchain: {exc}"
        )

        return {
            "ok": False,
            "reason": "blockchain_error",
            "trace": trace,
        }

    if not resolved:
        trace["explanation"] = (
            "No authoritative document "
            "version was found on the blockchain."
        )

        return {
            "ok": False,
            "reason": "document_not_found",
            "trace": trace,
        }

    # The blockchain client may expose the authoritative version as either
    # ``resolved_version`` or the normalized document-version field ``version``.
    # Normalize it here so every Phase 3 layer receives the same schema.
    resolved_version = resolved.get("resolved_version")
    if resolved_version is None:
        resolved_version = resolved.get("version")

    # Keep the blockchain result authoritative and explicit. The version comes
    # from the blockchain client and is never inferred from repository metadata.
    normalized_metadata = dict(resolved)
    normalized_metadata["resolved_version"] = (
        int(resolved_version) if resolved_version is not None else None
    )

    trace.update(
        {
            "success": True,
            "resolved_version": normalized_metadata.get("resolved_version"),
            "ipfs_cid": normalized_metadata.get("ipfs_cid"),
            "content_hash": normalized_metadata.get("content_hash"),
            "approval_status": normalized_metadata.get("approval_status"),
        }
    )

    trace["explanation"] = (
        "Blockchain resolved the authoritative document version "
        f"{normalized_metadata.get('resolved_version')}."
    )

    return {
        "ok": True,
        "blockchain_metadata": normalized_metadata,
        "trace": trace,
    }


# ===========================================================================


def get_authorized_document(
    document_id: str,
    user: Optional[Dict[str, Any]],
    *,
    expected_student_id: Optional[str] = None,
    expected_document_type: Optional[str] = None,
    approved_only: bool = True,
    verify_integrity: bool = True,
) -> Dict[str, Any]:
    """
    Perform the complete Phase 3 protected document retrieval workflow.

    Security sequence:

        1. Validate document ID.
        2. Resolve document identity from blockchain.
        3. Determine the document's student.
        4. Perform RBAC before IPFS retrieval.
        5. Resolve authoritative approved version.
        6. Retrieve authoritative CID from IPFS.
        7. Verify content against blockchain content hash.
        8. Return authorized document.

    The critical security property is:

        Authorization happens BEFORE confidential IPFS retrieval.

    The blockchain is authoritative for:
        - document version
        - CID
        - content hash
        - approval status
        - provenance

    IPFS is responsible for:
        - encrypted document storage
        - encrypted document retrieval
    """

    normalized_document_id = (
        _canon_document_id(
            document_id
        )
    )

    trace: Dict[str, Any] = {
        "action": "get_authorized_document",
        "document_id": normalized_document_id,
        "rbac_allowed": False,
        "blockchain_resolved": False,
        "ipfs_retrieved": False,
        "integrity_verified": False,
        "authorized": False,
        "explanation": "",
    }

    # ----------------------------------------------------------------------
    # Step 1: Validate document ID
    # ----------------------------------------------------------------------

    if not normalized_document_id:
        trace["explanation"] = (
            "Document ID is empty."
        )

        return {
            "ok": False,
            "reason": "invalid_document_id",
            "trace": trace,
        }

    # ----------------------------------------------------------------------
    # Step 2: Resolve blockchain identity BEFORE IPFS retrieval
    #
    # This lets us determine which student owns the document.
    # No confidential IPFS content is accessed yet.
    # ----------------------------------------------------------------------

    try:
        document_details = (
            _bc.get_document_details(
                normalized_document_id
            )
        )

    except Exception as exc:
        trace["explanation"] = (
            "Failed to retrieve blockchain "
            f"document details: {exc}"
        )

        return {
            "ok": False,
            "reason": "blockchain_error",
            "trace": trace,
        }

    if not document_details:
        trace["explanation"] = (
            "Document does not exist on blockchain."
        )

        return {
            "ok": False,
            "reason": "document_not_found",
            "trace": trace,
        }

    blockchain_student_id = _canon_sid(
        document_details.get(
            "student_id",
            "",
        )
    )

    blockchain_document_type = str(
        document_details.get(
            "document_type",
            "",
        )
        or ""
    ).strip()

    # ----------------------------------------------------------------------
    # Step 3: Check consistency with the metadata-RAG context
    # ----------------------------------------------------------------------

    if expected_student_id:
        expected_sid = _canon_sid(
            expected_student_id
        )

        if (
            blockchain_student_id
            and blockchain_student_id
            != expected_sid
        ):
            trace["explanation"] = (
                "The blockchain document belongs "
                "to a different student than the "
                "metadata result."
            )

            return {
                "ok": False,
                "reason": "student_mismatch",
                "trace": trace,
            }

    # ----------------------------------------------------------------------
    # Step 4: Authorization gate
    #
    # IMPORTANT:
    # Nothing from IPFS has been retrieved yet.
    # ----------------------------------------------------------------------

    authorization_student_id = (
        blockchain_student_id
        or _canon_sid(
            expected_student_id
            or ""
        )
    )

    if not authorization_student_id:
        trace["explanation"] = (
            "Unable to determine the student "
            "associated with the document."
        )

        return {
            "ok": False,
            "reason": "student_identity_unavailable",
            "trace": trace,
        }

    try:
        allowed = _is_allowed_offchain(
            user,
            authorization_student_id,
        )

    except Exception as exc:
        trace["explanation"] = (
            "RBAC check failed: "
            f"{exc}"
        )

        return {
            "ok": False,
            "reason": "authorization_error",
            "trace": trace,
        }

    if not allowed:
        trace["explanation"] = (
            "Access denied by RBAC before "
            "confidential IPFS retrieval."
        )

        return {
            "ok": False,
            "reason": "forbidden",
            "trace": trace,
        }

    trace["rbac_allowed"] = True

    # ----------------------------------------------------------------------
    # Step 5: Resolve authoritative version
    # ----------------------------------------------------------------------

    blockchain_response = (
        resolve_document_from_blockchain(
            normalized_document_id,
            expected_student_id=(
                authorization_student_id
            ),
            expected_document_type=(
                expected_document_type
                or blockchain_document_type
                or None
            ),
            approved_only=approved_only,
        )
    )

    if not blockchain_response.get(
        "ok"
    ):
        trace["explanation"] = (
            "Blockchain authoritative "
            "resolution failed."
        )

        trace["blockchain_trace"] = (
            blockchain_response.get(
                "trace",
                {},
            )
        )

        return {
            "ok": False,
            "reason": blockchain_response.get(
                "reason",
                "blockchain_resolution_failed",
            ),
            "trace": trace,
        }

    blockchain_metadata = (
        blockchain_response.get(
            "blockchain_metadata",
            {},
        )
    )

    trace["blockchain_resolved"] = True

    # ----------------------------------------------------------------------
    # Step 6: Validate authoritative CID
    # ----------------------------------------------------------------------

    authoritative_cid = str(
        blockchain_metadata.get(
            "ipfs_cid",
            "",
        )
        or ""
    ).strip()

    expected_content_hash = str(
        blockchain_metadata.get(
            "content_hash",
            "",
        )
        or ""
    ).strip()

    if not authoritative_cid:
        trace["explanation"] = (
            "Blockchain document version does "
            "not contain an IPFS CID."
        )

        return {
            "ok": False,
            "reason": "missing_ipfs_cid",
            "blockchain_metadata": (
                blockchain_metadata
            ),
            "trace": trace,
        }

    # ----------------------------------------------------------------------
    # Step 7: Retrieve authoritative CID from IPFS
    #
    # Authorization has already succeeded.
    # ----------------------------------------------------------------------

    try:
        # IPFS returns the application-level encrypted envelope unchanged.
        # The blockchain content hash is a hash of the plaintext, therefore
        # verification must happen after ACL decryption.
        ipfs_response = (
            _ipfs.retrieve_resolved_document(
                authoritative_cid,
                expected_content_hash=None,
                verify_integrity=False,
            )
        )

        retrieved_document = ipfs_response.get("document")

        if (
            isinstance(retrieved_document, dict)
            and retrieved_document.get("enc") == "fernet-v1"
        ):
            # RBAC has already succeeded above. The ACL layer now unwraps the
            # DEK for an authorized principal and decrypts the document.
            decrypted_response = acl.decrypt_and_verify_for_user(
                user,
                authorization_student_id,
                authoritative_cid,
                retrieved_document,
                expected_content_hash=(
                    expected_content_hash
                    if verify_integrity
                    and expected_content_hash
                    else None
                ),
            )

            ipfs_response["document"] = decrypted_response.get(
                "document"
            )
            ipfs_response["content_hash"] = decrypted_response.get(
                "content_hash"
            )
            ipfs_response["integrity_verified"] = bool(
                decrypted_response.get(
                    "integrity_verified",
                    False,
                )
            )
            ipfs_response["application_encrypted"] = True
            ipfs_response["integrity_pending"] = False

        elif isinstance(retrieved_document, dict):
            # Plaintext payloads can be hashed directly.
            actual_hash = acl.calculate_content_hash(
                retrieved_document
            )
            ipfs_response["content_hash"] = actual_hash

            if verify_integrity and expected_content_hash:
                expected = str(
                    expected_content_hash
                ).strip().lower()

                if expected.startswith("0x"):
                    expected = expected[2:]

                if actual_hash.lower() != expected:
                    raise ValueError(
                        "Document integrity verification failed: "
                        f"expected {expected}, "
                        f"calculated {actual_hash}."
                    )

                ipfs_response["integrity_verified"] = True
            else:
                ipfs_response["integrity_verified"] = False

    except PermissionError as exc:
        trace["explanation"] = (
            "Document decryption is not permitted: "
            f"{exc}"
        )

        return {
            "ok": False,
            "reason": "acl_forbidden",
            "blockchain_metadata": (
                blockchain_metadata
            ),
            "trace": trace,
        }

    except ValueError as exc:
        trace["explanation"] = (
            "IPFS retrieval or integrity "
            f"verification failed: {exc}"
        )

        return {
            "ok": False,
            "reason": "integrity_verification_failed",
            "blockchain_metadata": (
                blockchain_metadata
            ),
            "trace": trace,
        }

    except Exception as exc:
        trace["explanation"] = (
            "Failed to retrieve document "
            f"from IPFS: {exc}"
        )

        return {
            "ok": False,
            "reason": "ipfs_error",
            "blockchain_metadata": (
                blockchain_metadata
            ),
            "trace": trace,
        }

    trace["ipfs_retrieved"] = True

    trace["integrity_verified"] = bool(
        ipfs_response.get(
            "integrity_verified",
            False,
        )
    )

    # ----------------------------------------------------------------------
    # Step 8: Mark authorization successful
    # ----------------------------------------------------------------------

    trace["authorized"] = True

    if verify_integrity and expected_content_hash:
        integrity_message = (
            " Integrity verified against "
            "the blockchain content hash."
            if trace["integrity_verified"]
            else " Integrity verification was not confirmed."
        )
    else:
        integrity_message = (
            " Blockchain content hash was not available "
            "for verification."
        )

    trace["explanation"] = (
        "Document access authorized. "
        f"Retrieved immutable version "
        f"{blockchain_metadata.get('resolved_version')} "
        f"from blockchain-resolved CID "
        f"{authoritative_cid}."
        f"{integrity_message}"
    )

    return {
        "ok": True,
        "authorized": True,
        "document": ipfs_response.get(
            "document"
        ),
        "document_id": normalized_document_id,
        "student_id": authorization_student_id,
        "document_type": (
            blockchain_metadata.get(
                "document_type"
            )
            or blockchain_document_type
        ),
        "version": blockchain_metadata.get(
            "resolved_version"
        ),
        "previous_version": blockchain_metadata.get(
            "previous_version"
        ),
        "ipfs_cid": authoritative_cid,
        "content_hash": expected_content_hash,
        "approval_status": blockchain_metadata.get(
            "approval_status"
        ),
        "approved_by": blockchain_metadata.get(
            "approved_by"
        ),
        "timestamp": blockchain_metadata.get(
            "timestamp"
        ),
        "integrity_verified": trace[
            "integrity_verified"
        ],
        "blockchain_metadata": blockchain_metadata,
        "trace": trace,
    }