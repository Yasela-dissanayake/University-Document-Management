# python_backend/ai/agent.py
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

# Optional LLM (Ollama) for general questions
try:
    from langchain_ollama import OllamaLLM  # type: ignore
    _HAS_LLM = True
except Exception:
    _HAS_LLM = False

from python_backend.ai.tools import (
    get_onchain_student,
    get_offchain_semesters,
    get_course_grade,
)


_STUDENT_RE = re.compile(r"\bS\d{5}\b", re.IGNORECASE)
_COURSE_RE = re.compile(r"\b([A-Za-z]{2,}\d{3,})\b")


def _extract_sid(text: str) -> Optional[str]:
    m = _STUDENT_RE.search(text or "")
    return m.group(0).upper() if m else None


def _extract_course_code(text: str) -> Optional[str]:
    # pick the first plausible token that looks like a course code
    m = _COURSE_RE.search(text or "")
    return m.group(1).upper() if m else None


def _wants_all_grades(text: str) -> bool:
    t = (text or "").lower()
    return "all the grades" in t or ("all" in t and "grades" in t)


def _format_ts(ts: Optional[int]) -> str:
    if not ts:
        return ""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)


def _llm_answer(prompt: str) -> str:
    if not _HAS_LLM:
        return "I can’t answer that without the LLM installed."
    model = os.getenv("OLLAMA_MODEL", "llama3")
    llm = OllamaLLM(model=model)
    return llm.invoke(prompt)


def answer_question(question: str, user_context: Optional[Dict[str, Any]] = None) -> Any:
    """
    Main entrypoint used by Flask. Returns a plain string answer (or a small dict).
    Enforces RBAC/ACL for off-chain fetches INSIDE the tools.
    """
    q = (question or "").strip()
    if not q:
        return "Please provide a question."

    # Simple routing for common tasks:
    sid = _extract_sid(q)
    course = _extract_course_code(q)

    # (A) "grade of <course> of <student>"
    if sid and course and ("grade" in q.lower() or "result" in q.lower()):
        resp = get_course_grade(sid, course, user_context)
        if not resp.get("ok"):
            return f"You’re not authorized to access off-chain records of {sid}."
        if not resp.get("found"):
            return f"I couldn’t find {course} for {sid} in the available semester documents."
        grade = resp.get("grade")
        when = _format_ts(resp.get("when"))
        return f"The grade of {course} for {sid} is {grade}." + (f" (as of {when})" if when else "")

    # (B) "show all grades of <student>"
    if sid and _wants_all_grades(q):
        resp = get_offchain_semesters(sid, user_context)
        if not resp.get("ok"):
            return f"You’re not authorized to access off-chain records of {sid}."
        semesters = resp.get("semesters", [])
        if not semesters:
            return f"No off-chain semester documents found for {sid}."
        # Prefer latest semester only unless explicitly asked for all semesters
        latest = semesters[0]
        courses = latest.get("courses") or []
        if not courses:
            return f"No course list found in the latest semester document for {sid}."
        lines = [f"Latest semester grades for {sid}:"]
        for c in courses:
            code = c.get("code", "")
            grade = c.get("grade", "")
            name = c.get("name", "")
            lines.append(f"- {code}: {grade} ({name})")
        return "\n".join(lines)

    # (C) If the user asked for “semesters” or “transcript” for a student → list available semesters
    if sid and ("semester" in q.lower() or "transcript" in q.lower()):
        resp = get_offchain_semesters(sid, user_context)
        if not resp.get("ok"):
            return f"You’re not authorized to access off-chain records of {sid}."
        semesters = resp.get("semesters", [])
        if not semesters:
            return f"No off-chain semester documents found for {sid}."
        lines = [f"Found {len(semesters)} semester document(s) for {sid}:"]
        for i, s in enumerate(semesters, start=1):
            lines.append(f"{i}. CID={s.get('cid')} time={_format_ts(s.get('timestamp'))} gpa={s.get('gpa')}")
        return "\n".join(lines)

    # (D) If there’s a student id but request sounds on-chain-ish → return on-chain info
    if sid and any(k in q.lower() for k in ["year", "program", "on-chain", "onchain", "blockchain"]):
        data = get_onchain_student(sid)
        if not data:
            return f"I couldn’t find on-chain data for {sid}."
        return (
            f"{sid}: {data.get('name')} — {data.get('program')} (year {data.get('year')}). "
            f"Active: {data.get('is_active')}. Latest CID: {data.get('documents_ipfs_hash')}"
        )

    # (E) Fallback to LLM for anything else
    return _llm_answer(q)
