# python_backend/wallet_manager.py
from __future__ import annotations
import os, sqlite3, base64
from typing import Optional, Tuple
from eth_account import Account
from cryptography.fernet import Fernet

DB_PATH = os.path.join(os.path.dirname(__file__), "wallets.sqlite")
# 32-byte urlsafe base64 key; generate once:  >>> from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())
WALLET_ENC_KEY = os.getenv("WALLET_ENCRYPTION_KEY")
if not WALLET_ENC_KEY:
    raise RuntimeError("Set WALLET_ENCRYPTION_KEY in your .env (Fernet key)")

fernet = Fernet(WALLET_ENC_KEY.encode())

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            student_id TEXT PRIMARY KEY,
            address    TEXT NOT NULL,
            enc_priv   BLOB NOT NULL
        )""")
_init_db()

def get_or_create_wallet(student_id: str) -> Tuple[str, bool]:
    """Return (address, created_new). Creates & stores if not present."""
    student_id = student_id.strip()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT address FROM wallets WHERE student_id=?", (student_id,)).fetchone()
        if row:
            return row[0], False

        acct = Account.create()  # random high-entropy key
        enc = fernet.encrypt(acct.key)
        conn.execute("INSERT INTO wallets (student_id, address, enc_priv) VALUES (?,?,?)",
                     (student_id, acct.address, enc))
        return acct.address, True

def get_private_key(student_id: str) -> Optional[bytes]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT enc_priv FROM wallets WHERE student_id=?", (student_id,)).fetchone()
        if not row:
            return None
        return fernet.decrypt(row[0])

def get_address(student_id: str) -> Optional[str]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT address FROM wallets WHERE student_id=?", (student_id,)).fetchone()
        return row[0] if row else None
