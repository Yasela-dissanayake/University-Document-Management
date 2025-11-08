# python_backend/ai/tools.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Load .env early (safe no-op if package missing)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
except Exception:
    pass

from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend.ipfs_client import UniversityIPFSClient
from python_backend import rbac, acl

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
    sid = _canon_sid(student_id)
    try:
        cids = _bc.get_all_semester_hashes(sid) or []
        return [str(c) for c in cids if c]
    except Exception:
        return []


def _fetch_and_decrypt(cid: str, user: Optional[Dict[str, Any]], student_id: str) -> Optional[Dict[str, Any]]:
    try:
        payload = _ipfs.retrieve_academic_document(cid)
        if isinstance(payload, dict) and payload.get("enc") == "fernet-v1":
            return acl.decrypt_for_user(user, _canon_sid(student_id), cid, payload)
        return payload if isinstance(payload, dict) else {"raw": payload}
    except PermissionError:
        return None
    except Exception:
        return None


def get_offchain_semesters(student_id: str, user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Protected read with trace.
    Returns {ok, semesters?, trace?}
    """
    sid = _canon_sid(student_id)
    trace = {
        "student_id": sid,
        "action": "get_offchain_semesters",
        "rbac_allowed": False,
        "ipfs_docs_checked": [],
        "explanation": "",
    }

    if not _is_allowed_offchain(user, sid):
        trace["explanation"] = "Access denied due to RBAC."
        return {"ok": False, "reason": "forbidden", "trace": trace}

    trace["rbac_allowed"] = True
    cids = list_semester_cids(sid)
    trace["ipfs_docs_checked"] = cids
    out: List[Dict[str, Any]] = []

    for cid in cids:
        doc = _fetch_and_decrypt(cid, user, sid)
        if not isinstance(doc, dict):
            continue
        item = {"cid": cid}
        for k in ("timestamp", "document_type", "gpa", "courses", "student_id"):
            if k in doc:
                item[k] = doc[k]
        out.append(item)

    trace["explanation"] = f"Fetched and decrypted {len(out)} semester record(s)."
    return {"ok": True, "semesters": out, "trace": trace}


def get_course_grade(student_id: str, course_code: str, user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Returns {ok, reason?} or {ok: True, found: bool, grade?, trace?}
    """
    sid = _canon_sid(student_id)
    cc = str(course_code or "").strip().upper()
    semesters_resp = get_offchain_semesters(sid, user)
    trace = {
        "student_id": sid,
        "course_code": cc,
        "rbac_allowed": semesters_resp.get("ok", False),
        "ipfs_docs_checked": [],
        "matched_doc": None,
        "explanation": "",
    }

    if not semesters_resp.get("ok"):
        trace["explanation"] = "Access denied or off-chain semesters not available."
        return {"ok": False, "reason": "forbidden", "trace": trace}

    semesters: List[Dict[str, Any]] = semesters_resp.get("semesters", [])
    trace["ipfs_docs_checked"] = [s.get("cid") for s in semesters]

    # Sort newest → oldest
    semesters.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    for sem in semesters:
        courses = sem.get("courses") or []
        for c in courses:
            code = str(c.get("code", "")).strip().upper()
            if code == cc:
                grade = c.get("grade")
                trace["matched_doc"] = sem.get("cid")
                trace["explanation"] = f"Found {cc} with grade {grade} in CID {sem.get('cid')}"
                return {
                    "ok": True,
                    "found": True,
                    "grade": grade,
                    "cid": sem.get("cid"),
                    "when": sem.get("timestamp"),
                    "trace": trace,
                }

    trace["explanation"] = f"{cc} not found in any semester document."
    return {"ok": True, "found": False, "trace": trace}
