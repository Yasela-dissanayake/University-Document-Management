# python_backend/rbac.py
from __future__ import annotations

import os
import re
import sqlite3
from typing import Optional, Dict

from werkzeug.security import generate_password_hash, check_password_hash



DB_PATH = os.path.join(os.path.dirname(__file__), "auth.sqlite")

ALLOWED_OFFCHAIN_ROLES = {"ADMIN", "HOD", "DEAN", "AR", "DVC"}

_STUDENT_ID_RE = re.compile(r"^S\d{5}$", re.IGNORECASE)

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            student_id TEXT,
            wallet_address TEXT
        )
        """)
_init_db()

def create_user(username: str, password: str, role: str,
                student_id: Optional[str] = None,
                wallet_address: Optional[str] = None) -> int:
    username = username.strip()
    role = role.strip().upper()
    ph = generate_password_hash(password)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("""
            INSERT INTO users (username, password_hash, role, student_id, wallet_address)
            VALUES (?, ?, ?, ?, ?)
        """, (username, ph, role, student_id, wallet_address))
        return cur.lastrowid

def get_user_by_username(username: str) -> Optional[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT id, username, password_hash, role, student_id, wallet_address FROM users WHERE username=?",
                           (username.strip(),)).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "username": row[1], "password_hash": row[2],
        "role": row[3], "student_id": row[4], "wallet_address": row[5]
    }

def get_user_by_id(uid: int) -> Optional[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT id, username, password_hash, role, student_id, wallet_address FROM users WHERE id=?",
                           (uid,)).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "username": row[1], "password_hash": row[2],
        "role": row[3], "student_id": row[4], "wallet_address": row[5]
    }

def authenticate(username: str, password: str) -> Optional[Dict]:
    user = get_user_by_username(username)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None

def _canon(s: str) -> str:
    return (s or "").strip().upper()

def can_view_offchain(user: Optional[Dict], target_student_id: str) -> bool:
    """
    Off-chain access allowed if:
      - user exists AND
      - user.role in ALLOWED_OFFCHAIN_ROLES, OR
      - user.role == STUDENT and (user.student_id == target_student_id) [case-insensitive]
        * Robustness: if user.student_id is empty, but username itself looks like S12345,
          we treat username as the student's id.
    """
    if not user:
        return False

    role = _canon(user.get("role") or "")
    if role in ALLOWED_OFFCHAIN_ROLES:
        return True

    if role == "STUDENT":
        sid_user = user.get("student_id") or ""
        sid_user = _canon(sid_user)
        if not sid_user:
            # derive from username if it *looks* like a student id
            uname = _canon(user.get("username") or "")
            if _STUDENT_ID_RE.match(uname):
                sid_user = uname
        sid_target = _canon(target_student_id)
        return bool(sid_user) and (sid_user == sid_target)

    return False



# ---------------------------------------------------------------------
# === Extended RBAC with Role Hierarchy & Workflow Permissions ===
# ---------------------------------------------------------------------

ROLE_HIERARCHY = {
    "ADMIN": ["DVC", "AR", "DEAN", "HOD", "STUDENT"],
    "DVC": ["AR", "DEAN", "HOD", "STUDENT"],
    "AR": ["DEAN", "HOD", "STUDENT"],
    "DEAN": ["HOD", "STUDENT"],
    "HOD": ["STUDENT"],
    "STUDENT": []
}

ROLE_PERMISSIONS = {
    "ADMIN": [
        "create_user", "approve_document", "reject_document",
        "view_onchain", "view_offchain", "assign_role"
    ],
    "DVC": ["approve_document", "reject_document", "view_onchain", "view_offchain"],
    "AR": ["approve_document", "reject_document", "view_onchain", "view_offchain"],
    "DEAN": ["approve_document", "reject_document", "view_onchain", "view_offchain"],
    "HOD": ["create_document", "approve_document", "view_onchain", "view_offchain"],
    "STUDENT": ["create_document", "view_onchain", "view_offchain_own"]
}

# --- Hierarchy & permission utilities ---

def can_act_on(target_role: str, actor_role: str) -> bool:
    """Check if actor_role can perform actions on target_role (hierarchy)."""
    return target_role in ROLE_HIERARCHY.get(actor_role, [])


def has_permission_for_action(username: str, action: str) -> bool:
    """Check if a user (by username) has a given permission action."""
    user = get_user_by_username(username)
    if not user:
        return False
    role = user.get("role", "")
    allowed = ROLE_PERMISSIONS.get(role, [])
    return action in allowed


# --- Workflow state machine for order enforcement ---
WORKFLOW_ORDER = ["DRAFT", "HOD_APPROVED", "DEAN_APPROVED", "AR_APPROVED", "DVC_APPROVED", "FINAL"]

def next_state(current_state: str, actor_role: str) -> str:
    """
    Determine the next valid state based on the actor's role.
    Ensures order enforcement (HOD → DEAN → AR → DVC).
    """
    transitions = {
        "HOD": "HOD_APPROVED",
        "DEAN": "DEAN_APPROVED",
        "AR": "AR_APPROVED",
        "DVC": "DVC_APPROVED"
    }
    next_expected = transitions.get(actor_role)
    if not next_expected:
        raise ValueError(f"Role {actor_role} cannot approve documents.")

    # verify sequence order
    try:
        idx = WORKFLOW_ORDER.index(current_state)
    except ValueError:
        raise ValueError(f"Unknown current state: {current_state}")

    if idx + 1 < len(WORKFLOW_ORDER) and WORKFLOW_ORDER[idx + 1] == next_expected:
        return next_expected
    else:
        raise ValueError(f"Invalid order: {actor_role} cannot move from {current_state} to {next_expected}")


def is_final_state(state: str) -> bool:
    return state == "FINAL"

def is_validator_role(role: Optional[str]) -> bool:
    """
    Returns True if the role is allowed to act as a document validator.
    fix: list previously contained lowercase 'admin' mixed with uppercase roles;
         now all entries are uppercase for clarity.
    """
    if not role:
        return False
    validator_roles = {"HOD", "DEAN", "AR", "DVC", "ADMIN"}
    return role.strip().upper() in validator_roles

# ==========================
# Admin role management area
# ==========================

def get_all_users():
    """
    Fetch all registered users from the SQLite database.
    Returns a list of dictionaries like:
    [{"id": 1, "username": "admin", "role": "ADMIN", "student_id": "S12345", "wallet_address": "0x..."}]
    """
    users = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, username, role, student_id, wallet_address FROM users")
            rows = cur.fetchall()

        for r in rows:
            users.append({
                "id": r[0],
                "username": r[1],
                "role": r[2],
                "student_id": r[3],
                "wallet_address": r[4]
            })
    except Exception as e:
        print("❌ Error fetching users:", e)
    return users



def set_user_role(user_id: int, new_role: str) -> dict:
    """
    Update a user's role in the database.
    Returns a success or error dictionary for JSON response.
    """
    try:
        new_role = new_role.strip().upper()
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
            conn.commit()

            if cur.rowcount == 0:
                return {"error": f"User with id {user_id} not found."}

        print(f"✅ Role updated: User ID {user_id} → {new_role}")
        return {"message": f"Role updated to {new_role} for user ID {user_id}."}

    except Exception as e:
        print(f"❌ Error updating user role: {e}")
        return {"error": str(e)}

def can_update_semester(user: Optional[Dict], student_id: str) -> bool:
    """
    Check if user can update semester records.
    Only ADMIN and EXAM_DIVISION can update semester records.
    Students CANNOT update their own records.
    """
    if not user:
        return False
    
    role = (user.get("role") or "").strip().upper()
    
    # Only these roles can update semester records
    allowed_roles = ["ADMIN", "EXAM_DIVISION", "REGISTRAR"]
    
    return role in allowed_roles


def can_approve_semester(user: Optional[Dict], current_state: str) -> bool:
    """
    Check if user can approve semester record at current workflow state.
    
    Workflow: DRAFT → HOD → DEAN → APPROVED
    """
    if not user:
        return False
    
    role = (user.get("role") or "").strip().upper()
    
    # Define who can approve at each state
    approval_permissions = {
        "DRAFT": [],  # No approval needed at draft
        "PENDING_HOD": ["HOD"],
        "PENDING_DEAN": ["DEAN"],
        "PENDING_AR": ["AR"],  # Optional: if you want AR review
    }
    
    return role in approval_permissions.get(current_state, [])


def next_semester_state(current_state: str, approver_role: str) -> str:
    """
    Determine next state in semester approval workflow.
    
    Workflow states:
    - DRAFT: Initial submission by EXAM_DIVISION
    - PENDING_HOD: Waiting for HOD approval
    - PENDING_DEAN: Waiting for DEAN approval
    - APPROVED: Final approved state
    """
    
    workflow_transitions = {
        "DRAFT": {
            "EXAM_DIVISION": "PENDING_HOD",  # Submit for approval
            "ADMIN": "PENDING_HOD"
        },
        "PENDING_HOD": {
            "HOD": "PENDING_DEAN"
        },
        "PENDING_DEAN": {
            "DEAN": "APPROVED"
        }
    }
    
    transitions = workflow_transitions.get(current_state, {})
    next_state = transitions.get(approver_role)
    
    if not next_state:
        raise ValueError(
            f"Role '{approver_role}' cannot approve at state '{current_state}'"
        )
    
    return next_state

