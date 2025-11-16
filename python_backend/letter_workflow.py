"""
Letter Workflow Management System
python_backend/letter_workflow.py

Features:
- Dynamic workflow based on creator role
- Robust letter creation with multiple fields
- Database persistence
- Order enforcement
"""

import sqlite3
import json
import time
from typing import Dict, List, Optional

# Database path
LETTER_DB = "python_backend/letter_workflows.sqlite"

# Role hierarchy for letters
ROLE_LEVELS = {
    "HOD": 1,
    "DEAN": 2,
    "AR": 3,
    "DVC": 4
}

def init_letter_db():
    """Initialize letter workflow database"""
    with sqlite3.connect(LETTER_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                letter_type TEXT,
                student_id TEXT,
                urgency TEXT DEFAULT 'NORMAL',
                created_by TEXT NOT NULL,
                creator_role TEXT NOT NULL,
                current_state TEXT NOT NULL,
                workflow_path TEXT NOT NULL,
                approval_history TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_letter_state 
            ON letters(current_state)
        """)

# Initialize on import
init_letter_db()


def get_workflow_path(creator_role: str) -> List[str]:
    """
    Get the approval workflow path based on creator's role.
    Lower roles cannot access letters created by higher roles.
    
    Examples:
    - HOD creates: HOD → DEAN → AR → DVC
    - DEAN creates: DEAN → AR → DVC (HOD cannot access)
    - AR creates: AR → DVC (HOD, DEAN cannot access)
    - DVC creates: DVC (already final, no approvals needed)
    """
    creator_level = ROLE_LEVELS.get(creator_role, 0)
    
    if creator_level == 0:
        raise ValueError(f"Invalid creator role: {creator_role}")
    
    # Build path from creator's level onwards
    path = []
    for role, level in sorted(ROLE_LEVELS.items(), key=lambda x: x[1]):
        if level >= creator_level:
            path.append(role)
    
    return path


def get_initial_state(creator_role: str) -> str:
    """Get initial state based on creator role"""
    path = get_workflow_path(creator_role)
    
    if len(path) == 1:
        # Creator is DVC, already final
        return "APPROVED"
    else:
        # Waiting for next role in path
        return f"PENDING_{path[1]}"


def create_letter(
    title: str,
    content: str,
    letter_type: str,
    student_id: Optional[str],
    urgency: str,
    created_by: str,
    creator_role: str
) -> Dict:
    """
    Create a new letter with workflow.
    
    Args:
        title: Letter title
        content: Letter body/content
        letter_type: Type (e.g., 'RECOMMENDATION', 'TRANSCRIPT_REQUEST', 'CLEARANCE')
        student_id: Related student ID (optional)
        urgency: 'LOW', 'NORMAL', 'HIGH', 'URGENT'
        created_by: Username of creator
        creator_role: Role of creator (HOD, DEAN, AR, DVC)
    """
    
    # Validate creator role
    if creator_role not in ROLE_LEVELS:
        raise ValueError(f"Invalid creator role: {creator_role}")
    
    # Generate document ID
    timestamp = int(time.time())
    
    with sqlite3.connect(LETTER_DB) as conn:
        # Get next letter number
        count = conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
        doc_id = f"LTR{count + 1:05d}"
        
        # Get workflow path and initial state
        workflow_path = get_workflow_path(creator_role)
        initial_state = get_initial_state(creator_role)
        
        # Create approval history
        history = [{
            "action": "CREATED",
            "role": creator_role,
            "user": created_by,
            "timestamp": timestamp,
            "state": initial_state
        }]
        
        # Insert letter
        conn.execute("""
            INSERT INTO letters 
            (doc_id, title, content, letter_type, student_id, urgency,
             created_by, creator_role, current_state, workflow_path,
             approval_history, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            doc_id, title, content, letter_type, student_id, urgency,
            created_by, creator_role, initial_state, json.dumps(workflow_path),
            json.dumps(history), timestamp, timestamp
        ))
    
    return {
        "doc_id": doc_id,
        "title": title,
        "current_state": initial_state,
        "workflow_path": workflow_path,
        "next_approver": workflow_path[1] if len(workflow_path) > 1 else None
    }


def get_letter(doc_id: str) -> Optional[Dict]:
    """Get letter details"""
    with sqlite3.connect(LETTER_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT * FROM letters WHERE doc_id = ?
        """, (doc_id,)).fetchone()
        
        if not row:
            return None
        
        return {
            "id": row["id"],
            "doc_id": row["doc_id"],
            "title": row["title"],
            "content": row["content"],
            "letter_type": row["letter_type"],
            "student_id": row["student_id"],
            "urgency": row["urgency"],
            "created_by": row["created_by"],
            "creator_role": row["creator_role"],
            "current_state": row["current_state"],
            "workflow_path": json.loads(row["workflow_path"]),
            "approval_history": json.loads(row["approval_history"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }


def can_user_access_letter(user_role: str, letter: Dict) -> bool:
    """
    Check if user can access this letter.
    Users can only access letters created at their level or below.
    """
    user_level = ROLE_LEVELS.get(user_role, 0)
    creator_level = ROLE_LEVELS.get(letter["creator_role"], 999)
    
    # ADMIN can see all
    if user_role == "ADMIN":
        return True
    
    # User's level must be >= creator's level
    return user_level >= creator_level


def can_user_approve_letter(user_role: str, letter: Dict) -> bool:
    """
    Check if user can approve this letter at current state.
    User must be the next approver in the workflow.
    """
    current_state = letter["current_state"]
    
    # Already approved
    if current_state == "APPROVED":
        return False
    
    # Extract expected role from state (e.g., "PENDING_DEAN" → "DEAN")
    if current_state.startswith("PENDING_"):
        expected_role = current_state.replace("PENDING_", "")
        return user_role == expected_role
    
    return False


def approve_letter(doc_id: str, approver_role: str, approver_user: str) -> Dict:
    """
    Approve a letter, moving it to next state in workflow.
    
    Returns:
        Dict with success status, new state, and next approver
    """
    letter = get_letter(doc_id)
    if not letter:
        raise ValueError(f"Letter {doc_id} not found")
    
    # Check if user can approve
    if not can_user_approve_letter(approver_role, letter):
        raise ValueError(f"{approver_role} cannot approve letter at state {letter['current_state']}")
    
    # Get workflow path and current position
    workflow_path = letter["workflow_path"]
    current_state = letter["current_state"]
    
    # Find current position in workflow
    current_role = current_state.replace("PENDING_", "")
    current_idx = workflow_path.index(current_role)
    
    # Determine next state
    if current_idx + 1 < len(workflow_path):
        # More approvals needed
        next_role = workflow_path[current_idx + 1]
        new_state = f"PENDING_{next_role}"
        next_approver = next_role
        is_final = False
    else:
        # This was the last approval
        new_state = "APPROVED"
        next_approver = None
        is_final = True
    
    # Update database
    timestamp = int(time.time())
    history = letter["approval_history"]
    history.append({
        "action": "APPROVED",
        "role": approver_role,
        "user": approver_user,
        "timestamp": timestamp,
        "state": new_state,
        "previous_state": current_state
    })
    
    with sqlite3.connect(LETTER_DB) as conn:
        conn.execute("""
            UPDATE letters 
            SET current_state = ?,
                approval_history = ?,
                updated_at = ?
            WHERE doc_id = ?
        """, (new_state, json.dumps(history), timestamp, doc_id))
    
    return {
        "success": True,
        "doc_id": doc_id,
        "previous_state": current_state,
        "new_state": new_state,
        "is_final_approved": is_final,
        "next_approver": next_approver
    }


def get_pending_letters_for_role(role: str) -> List[Dict]:
    """Get all letters pending approval by a specific role"""
    state = f"PENDING_{role}"
    
    with sqlite3.connect(LETTER_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM letters 
            WHERE current_state = ?
            ORDER BY created_at ASC
        """, (state,)).fetchall()
        
        letters = []
        for row in rows:
            letters.append({
                "doc_id": row["doc_id"],
                "title": row["title"],
                "letter_type": row["letter_type"],
                "student_id": row["student_id"],
                "urgency": row["urgency"],
                "created_by": row["created_by"],
                "creator_role": row["creator_role"],
                "current_state": row["current_state"],
                "created_at": row["created_at"],
                "waiting_days": (int(time.time()) - row["created_at"]) // 86400
            })
        
        return letters


def get_accessible_letters_for_role(role: str) -> List[Dict]:
    """
    Get all letters accessible to a role.
    Users can see letters created at their level or below.
    """
    if role == "ADMIN":
        # Admin sees all
        level_filter = ""
        params = []
    else:
        user_level = ROLE_LEVELS.get(role, 0)
        # Get roles at or below user's level
        accessible_roles = [r for r, l in ROLE_LEVELS.items() if l >= user_level]
        placeholders = ",".join("?" * len(accessible_roles))
        level_filter = f"WHERE creator_role IN ({placeholders})"
        params = accessible_roles
    
    with sqlite3.connect(LETTER_DB) as conn:
        conn.row_factory = sqlite3.Row
        query = f"""
            SELECT * FROM letters 
            {level_filter}
            ORDER BY created_at DESC
            LIMIT 50
        """
        rows = conn.execute(query, params).fetchall()
        
        return [{
            "doc_id": row["doc_id"],
            "title": row["title"],
            "letter_type": row["letter_type"],
            "urgency": row["urgency"],
            "created_by": row["created_by"],
            "creator_role": row["creator_role"],
            "current_state": row["current_state"],
            "created_at": row["created_at"]
        } for row in rows]


def get_letter_statistics() -> Dict:
    """Get system-wide letter statistics (for admins)"""
    with sqlite3.connect(LETTER_DB) as conn:
        # Total letters
        total = conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
        
        # By state
        by_state = {}
        for row in conn.execute("""
            SELECT current_state, COUNT(*) as count 
            FROM letters 
            GROUP BY current_state
        """):
            by_state[row[0]] = row[1]
        
        # By urgency
        by_urgency = {}
        for row in conn.execute("""
            SELECT urgency, COUNT(*) as count 
            FROM letters 
            GROUP BY urgency
        """):
            by_urgency[row[0]] = row[1]
        
        # Approved count
        approved = by_state.get("APPROVED", 0)
        
        # Average approval time
        avg_time = conn.execute("""
            SELECT AVG(updated_at - created_at) 
            FROM letters 
            WHERE current_state = 'APPROVED'
        """).fetchone()[0]
        
        return {
            "total_letters": total,
            "by_state": by_state,
            "by_urgency": by_urgency,
            "approved_count": approved,
            "pending_count": total - approved,
            "avg_approval_time_seconds": avg_time,
            "avg_approval_time_days": (avg_time / 86400) if avg_time else 0
        }