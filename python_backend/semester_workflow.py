"""
Semester Workflow Management
python_backend/semester_workflow.py

Manages the off-chain approval workflow for semester records before they are
anchored on-chain. Workflow: DRAFT → PENDING_HOD → PENDING_DEAN → APPROVED
"""

import sqlite3
import json
import time  # fix: was missing (caused NameError in get_pending_approvals)
import os
from typing import Dict, List, Optional

# Import RBAC for state-transition logic.
# Fix: gracefully handle both execution contexts:
#   1. Launched from repo root: `python_backend.rbac`
#   2. Launched from inside python_backend/: plain `rbac`
try:
    from python_backend import rbac  # type: ignore
except ImportError:
    import rbac  # type: ignore


# Database for semester workflows
WORKFLOW_DB = os.path.join(os.path.dirname(__file__), "semester_workflows.sqlite")


def init_workflow_db():
    """Initialize semester workflow database"""
    with sqlite3.connect(WORKFLOW_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS semester_workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                ipfs_hash TEXT NOT NULL UNIQUE,
                content_hash TEXT NOT NULL,
                workflow_state TEXT NOT NULL,
                submitted_by TEXT,
                submitted_at INTEGER,
                current_version INTEGER DEFAULT 1,
                is_final_approved BOOLEAN DEFAULT 0,
                approval_history TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_student_workflows
            ON semester_workflows(student_id, workflow_state)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_approved
            ON semester_workflows(student_id, is_final_approved)
        """)


# Initialize on module load
init_workflow_db()


# ─── Workflow state helpers ───────────────────────────────────────────────────

# Ordered next-approver roles after PENDING_x
_NEXT_APPROVER: Dict[str, Optional[str]] = {
    "PENDING_HOD":  "HOD",
    "PENDING_DEAN": "DEAN",
    "PENDING_AR":   "AR",
    "APPROVED":     None,
}


def _next_approver_label(state: str) -> Optional[str]:
    """Return the human-readable next approver for a given state, or None."""
    return _NEXT_APPROVER.get(state)


# ─── CRUD ────────────────────────────────────────────────────────────────────

def create_semester_workflow(
    student_id: str,
    ipfs_hash: str,
    content_hash: str,
    submitted_by: str,
    timestamp: int
) -> int:
    """Create a new semester workflow record in state DRAFT."""

    history = [{
        "action": "CREATED",
        "user": submitted_by,
        "timestamp": timestamp,
        "state": "DRAFT"
    }]

    with sqlite3.connect(WORKFLOW_DB) as conn:
        cursor = conn.execute("""
            INSERT INTO semester_workflows
            (student_id, ipfs_hash, content_hash, workflow_state,
             submitted_by, submitted_at, approval_history,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            student_id, ipfs_hash, content_hash, "DRAFT",
            submitted_by, timestamp, json.dumps(history),
            timestamp, timestamp
        ))
        return cursor.lastrowid


def get_semester_workflow(ipfs_hash: str) -> Optional[Dict]:
    """Get workflow status for a semester record by IPFS hash."""
    with sqlite3.connect(WORKFLOW_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT * FROM semester_workflows WHERE ipfs_hash = ?
        """, (ipfs_hash,)).fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "student_id": row["student_id"],
            "ipfs_hash": row["ipfs_hash"],
            "content_hash": row["content_hash"],
            "workflow_state": row["workflow_state"],
            "submitted_by": row["submitted_by"],
            "submitted_at": row["submitted_at"],
            "current_version": row["current_version"],
            "is_final_approved": bool(row["is_final_approved"]),
            "approval_history": json.loads(row["approval_history"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }


def get_student_workflows(student_id: str, only_approved: bool = False) -> List[Dict]:
    """Get all workflow records for a student."""
    query = """
        SELECT * FROM semester_workflows
        WHERE student_id = ?
    """
    params: list = [student_id]

    if only_approved:
        query += " AND is_final_approved = 1"

    query += " ORDER BY submitted_at DESC"

    with sqlite3.connect(WORKFLOW_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

        return [{
            "id": row["id"],
            "student_id": row["student_id"],
            "ipfs_hash": row["ipfs_hash"],
            "workflow_state": row["workflow_state"],
            "submitted_by": row["submitted_by"],
            "submitted_at": row["submitted_at"],
            "is_final_approved": bool(row["is_final_approved"]),
            "approval_history": json.loads(row["approval_history"])
        } for row in rows]


def approve_semester_workflow(
    ipfs_hash: str,
    approver_role: str,
    approver_user: str,
    timestamp: int
) -> Dict:
    """
    Approve a semester workflow, transitioning it to the next state.

    Returns a dict with: success, previous_state, new_state,
                         is_final_approved, next_approver.
    """
    workflow = get_semester_workflow(ipfs_hash)
    if not workflow:
        raise ValueError(f"Workflow not found for IPFS hash: {ipfs_hash}")

    current_state = workflow["workflow_state"]

    # Use rbac to determine next state (fix: was NameError before)
    next_state = rbac.next_semester_state(current_state, approver_role)

    # Update approval history
    history = workflow["approval_history"]
    history.append({
        "action": "APPROVED",
        "role": approver_role,
        "user": approver_user,
        "timestamp": timestamp,
        "state": next_state,
        "previous_state": current_state
    })

    # Check if this is the final approval
    is_final = (next_state == "APPROVED")

    # Update database
    with sqlite3.connect(WORKFLOW_DB) as conn:
        conn.execute("""
            UPDATE semester_workflows
            SET workflow_state = ?,
                is_final_approved = ?,
                approval_history = ?,
                current_version = current_version + 1,
                updated_at = ?
            WHERE ipfs_hash = ?
        """, (next_state, is_final, json.dumps(history), timestamp, ipfs_hash))

    # fix: next_approver was previously hardcoded to only handle PENDING_DEAN
    next_approver = _next_approver_label(next_state)

    return {
        "success": True,
        "previous_state": current_state,
        "new_state": next_state,
        "is_final_approved": is_final,
        "next_approver": next_approver,
    }


def get_pending_approvals(role: str) -> List[Dict]:
    """Get all semester records pending approval by a specific role."""

    # Map role to workflow state
    role_states = {
        "HOD":  "PENDING_HOD",
        "DEAN": "PENDING_DEAN",
        "AR":   "PENDING_AR",
    }

    state = role_states.get(role)
    if not state:
        return []

    with sqlite3.connect(WORKFLOW_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM semester_workflows
            WHERE workflow_state = ?
            ORDER BY submitted_at ASC
        """, (state,)).fetchall()

        now = int(time.time())  # fix: time was imported but missing from original
        return [{
            "id": row["id"],
            "student_id": row["student_id"],
            "ipfs_hash": row["ipfs_hash"],
            "workflow_state": row["workflow_state"],
            "submitted_by": row["submitted_by"],
            "submitted_at": row["submitted_at"],
            "waiting_days": (now - row["submitted_at"]) // 86400
        } for row in rows]
