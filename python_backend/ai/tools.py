# python_backend/ai/tools.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# Load .env early (safe no-op if package missing)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
except Exception:
    pass

from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend.ipfs_client import UniversityIPFSClient
from python_backend import rbac, acl


# Singleton-ish clients for tools (separate from Flask app instances)
_bc = UniversityBlockchainClient()
_ipfs = UniversityIPFSClient()


def _canon_sid(s: str) -> str:
    return (s or "").strip().upper()


def _is_allowed_offchain(user: Optional[Dict[str, Any]], student_id: str) -> bool:
    return rbac.can_view_offchain(user, _canon_sid(student_id))


def get_onchain_student(student_id: str) -> Optional[Dict[str, Any]]:
    """Public read: on-chain student record."""
    sid = _canon_sid(student_id)
    try:
        return _bc.get_student_details(sid)
    except Exception:
        return None


def list_semester_cids(student_id: str) -> List[str]:
    """Public read: list CIDs for a student (on-chain)."""
    sid = _canon_sid(student_id)
    try:
        cids = _bc.get_all_semester_hashes(sid) or []
        return [str(c) for c in cids if c]
    except Exception:
        return []


def _fetch_and_decrypt(cid: str, user: Optional[Dict[str, Any]], student_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve JSON from IPFS and decrypt if it's an encrypted payload."""
    try:
        payload = _ipfs.retrieve_academic_document(cid)
        if isinstance(payload, dict) and payload.get("enc") == "fernet-v1":
            # Encrypted payload → require ACL unwrap
            return acl.decrypt_for_user(user, _canon_sid(student_id), cid, payload)
        # Plaintext (legacy) documents
        return payload if isinstance(payload, dict) else {"raw": payload}
    except PermissionError:
        # Permission handled by caller; return None to indicate forbidden
        return None
    except Exception:
        return None


def get_offchain_semesters(student_id: str, user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Protected read: returns {ok, reason?, semesters?}
    semesters: list of {cid, timestamp?, document_type?, gpa?, courses?: [...]}
    """
    sid = _canon_sid(student_id)
    if not _is_allowed_offchain(user, sid):
        return {"ok": False, "reason": "forbidden"}

    cids = list_semester_cids(sid)
    out: List[Dict[str, Any]] = []

    for cid in cids:
        doc = _fetch_and_decrypt(cid, user, sid)
        if not isinstance(doc, dict):
            continue
        item = {"cid": cid}
        # carry over common fields if present
        for k in ("timestamp", "document_type", "gpa", "courses", "student_id"):
            if k in doc:
                item[k] = doc[k]
        out.append(item)

    return {"ok": True, "semesters": out}


def get_course_grade(student_id: str, course_code: str, user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Protected helper to answer: grade of <course_code> of <student_id>.
    Returns {ok, reason?} or {ok: True, found: bool, grade?, cid?, when?}
    Scans newest→oldest by timestamp (if timestamps are present).
    """
    sid = _canon_sid(student_id)
    cc = str(course_code or "").strip().upper()
    resp = get_offchain_semesters(sid, user)
    if not resp.get("ok"):
        return {"ok": False, "reason": resp.get("reason", "forbidden")}

    semesters: List[Dict[str, Any]] = resp.get("semesters", [])

    # Sort newest→oldest if we have timestamps
    semesters.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    for sem in semesters:
        courses = sem.get("courses") or []
        for c in courses:
            code = str(c.get("code", "")).strip().upper()
            if code == cc:
                grade = c.get("grade")
                return {
                    "ok": True,
                    "found": True,
                    "grade": grade,
                    "cid": sem.get("cid"),
                    "when": sem.get("timestamp"),
                }

    return {"ok": True, "found": False}
