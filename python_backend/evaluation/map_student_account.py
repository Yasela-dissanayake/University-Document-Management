"""Safely map an existing student login to its blockchain student ID.

Example dry-run:
    python -m python_backend.evaluation.map_student_account --username STD001 --student-id STD001

Apply the verified mapping:
    python -m python_backend.evaluation.map_student_account --username STD001 --student-id STD001 --apply

The script validates the blockchain student record before changing auth.sqlite.
It never changes passwords, roles, blockchain records, or IPFS documents.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

try:
    from dotenv import load_dotenv
    ROOT = Path(__file__).resolve().parents[2]
    load_dotenv(ROOT / ".env")
except Exception:
    ROOT = Path(__file__).resolve().parents[2]

from python_backend import rbac
from python_backend.blockchain_client import UniversityBlockchainClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="Persist the mapping (default is dry-run)")
    args = parser.parse_args()

    username = args.username.strip()
    student_id = args.student_id.strip().upper()
    user = rbac.get_user_by_username(username)
    if not user:
        raise SystemExit(f"User {username!r} was not found in auth.sqlite.")
    if str(user.get("role") or "").strip().upper() != "STUDENT":
        raise SystemExit("Refusing to map a non-STUDENT account.")

    client = UniversityBlockchainClient()
    student = client.get_student_details(student_id)
    if not student or str(student.get("student_id") or "").strip().upper() != student_id:
        raise SystemExit(
            f"Student ID {student_id!r} could not be verified on the configured blockchain."
        )

    old_id = str(user.get("student_id") or "").strip()
    print(f"User: {username} (id={user['id']}, role=STUDENT)")
    print(f"Current student_id: {old_id or '(empty)'}")
    print(f"Requested student_id: {student_id}")
    if old_id and old_id.upper() != student_id:
        raise SystemExit("Refusing to overwrite a different existing student_id.")

    if args.apply:
        with sqlite3.connect(rbac.DB_PATH) as connection:
            cursor = connection.execute(
                "UPDATE users SET student_id = ? WHERE id = ? AND role = 'STUDENT'",
                (student_id, user["id"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Account mapping update did not affect exactly one row.")
        print("Mapping saved successfully.")
    else:
        print("Dry run only. Re-run with --apply to save this mapping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())