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


def _llm_answer(prompt: str) -> Dict[str, Any]:
    if not _HAS_LLM:
        return {"answer": "I can’t answer that without the LLM installed."}
    model = os.getenv("OLLAMA_MODEL", "llama3")
    llm = OllamaLLM(model=model)
    answer = llm.invoke(prompt)
    return {
        "answer": answer,
        "trace": {
            "llm_model": model,
            "used_prompt": prompt,
            "note": "No structured data was retrieved for this answer.",
        },
    }


def answer_question(question: str, user_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    q = (question or "").strip()
    if not q:
        return {"answer": "Please provide a question."}

    sid = _extract_sid(q)
    course = _extract_course_code(q)

    # (A) Query for course grade
    if sid and course and ("grade" in q.lower() or "result" in q.lower()):
        resp = get_course_grade(sid, course, user_context)
        trace = resp.get("trace", {})
        if not resp.get("ok"):
            return {"answer": f"You’re not authorized to access off-chain records of {sid}.", "trace": trace}
        if not resp.get("found"):
            return {"answer": f"I couldn’t find {course} for {sid} in the available semester documents.", "trace": trace}
        grade = resp.get("grade")
        when = _format_ts(resp.get("when"))
        return {
            "answer": f"The grade of {course} for {sid} is {grade}." + (f" (as of {when})" if when else ""),
            "trace": trace,
        }

    # (B) Query for all grades
    if sid and _wants_all_grades(q):
        resp = get_offchain_semesters(sid, user_context)
        trace = resp.get("trace", {})
        if not resp.get("ok"):
            return {"answer": f"You’re not authorized to access off-chain records of {sid}.", "trace": trace}
        semesters = resp.get("semesters", [])
        if not semesters:
            return {"answer": f"No off-chain semester documents found for {sid}.", "trace": trace}
        latest = semesters[0]
        courses = latest.get("courses") or []
        if not courses:
            return {"answer": f"No course list found in the latest semester document for {sid}.", "trace": trace}
        lines = [f"Latest semester grades for {sid}:"]
        for c in courses:
            code = c.get("code", "")
            grade = c.get("grade", "")
            name = c.get("name", "")
            lines.append(f"- {code}: {grade} ({name})")
        return {
            "answer": "\n".join(lines),
            "trace": trace,
        }

    # (C) Query for transcript or semester list
    if sid and ("semester" in q.lower() or "transcript" in q.lower()):
        resp = get_offchain_semesters(sid, user_context)
        trace = resp.get("trace", {})
        if not resp.get("ok"):
            return {"answer": f"You’re not authorized to access off-chain records of {sid}.", "trace": trace}
        semesters = resp.get("semesters", [])
        if not semesters:
            return {"answer": f"No off-chain semester documents found for {sid}.", "trace": trace}
        lines = [f"Found {len(semesters)} semester document(s) for {sid}:"]
        for i, s in enumerate(semesters, start=1):
            lines.append(f"{i}. CID={s.get('cid')} time={_format_ts(s.get('timestamp'))} gpa={s.get('gpa')}")
        return {
            "answer": "\n".join(lines),
            "trace": trace,
        }

    # (D) Query for on-chain data
    if sid and any(k in q.lower() for k in ["year", "program", "on-chain", "onchain", "blockchain"]):
        data = get_onchain_student(sid)
        if not data:
            return {"answer": f"I couldn’t find on-chain data for {sid}."}
        return {
            "answer": (
                f"{sid}: {data.get('name')} — {data.get('program')} (year {data.get('year')}). "
                f"Active: {data.get('is_active')}. Latest CID: {data.get('documents_ipfs_hash')}"
            ),
            "trace": {
                "source": "blockchain",
                "student_id": sid,
                "contract_call": "get_onchain_student",
            },
        }

    # (E) Fallback
    return _llm_answer(q)
