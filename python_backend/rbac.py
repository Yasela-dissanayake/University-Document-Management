# python_backend/rbac.py
from __future__ import annotations

import os
import sqlite3
from typing import Optional, Dict

from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "auth.sqlite")

ALLOWED_OFFCHAIN_ROLES = {"ADMIN", "HOD", "DEAN", "AR", "DVC"}

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

def can_view_offchain(user: Optional[Dict], target_student_id: str) -> bool:
    """
    Off-chain access allowed if:
      - user exists AND
      - user.role in ALLOWED_OFFCHAIN_ROLES, OR
      - user.role == STUDENT and user.student_id == target_student_id (case-insensitive)
    """
    if not user:
        return False
    role = (user.get("role") or "").upper()
    if role in ALLOWED_OFFCHAIN_ROLES:
        return True
    if role == "STUDENT":
        sid_user = (user.get("student_id") or "").strip().upper()
        sid_target = (target_student_id or "").strip().upper()
        return sid_user and (sid_user == sid_target)
    return False
