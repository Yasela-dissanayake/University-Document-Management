#!/usr/bin/env python3
"""
Safely reset an existing user's password in the project's auth.sqlite database.

- Uses the same Werkzeug hashing functions as rbac.py.
- Creates a timestamped SQLite backup before changing anything.
- Updates only the password_hash for the exact username supplied.
- Never prints or stores the plaintext password in this script.
"""

from __future__ import annotations

import getpass
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


DB_PATH = Path(__file__).resolve().parent / "auth.sqlite"


def create_backup(db_path: Path) -> Path:
    """Create a consistent SQLite backup using SQLite's backup API."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_backup_{timestamp}{db_path.suffix}")

    source = sqlite3.connect(str(db_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    return backup_path


def main() -> int:
    if not DB_PATH.is_file():
        print(f"ERROR: Authentication database not found: {DB_PATH}")
        return 1

    username = input("Username to reset [ar_user]: ").strip() or "ar_user"
    if not username:
        print("ERROR: Username cannot be empty.")
        return 1

    password = getpass.getpass("Enter the new password: ")
    if len(password) < 8:
        print("ERROR: Use a password with at least 8 characters.")
        return 1

    confirmation = getpass.getpass("Confirm the new password: ")
    if password != confirmation:
        print("ERROR: Passwords do not match. No changes were made.")
        return 1

    # Check the target account before making a backup or changing the database.
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            required = {"username", "password_hash"}
            if not required.issubset(columns):
                print(
                    "ERROR: The users table does not contain the expected columns "
                    f"{sorted(required)}. Found: {sorted(columns)}"
                )
                return 1

            account = conn.execute(
                "SELECT username FROM users WHERE username = ?",
                (username,),
            ).fetchone()

        if account is None:
            print(f"ERROR: User '{username}' was not found. No changes were made.")
            return 1

        print(f"\nAccount found: {account[0]}")
        approval = input("Create a backup and reset this account's password? [y/N]: ")
        if approval.strip().lower() != "y":
            print("Cancelled. No changes were made.")
            return 0

        backup_path = create_backup(DB_PATH)
        new_hash = generate_password_hash(password)

        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (new_hash, username),
            )

            if cursor.rowcount != 1:
                conn.rollback()
                print(
                    "ERROR: Expected to update exactly one account, but updated "
                    f"{cursor.rowcount}. The change was rolled back."
                )
                return 1

            # Read back the stored hash and verify it with Werkzeug before commit.
            stored = conn.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()

            if not stored or not check_password_hash(stored[0], password):
                conn.rollback()
                print("ERROR: Password verification failed. The change was rolled back.")
                return 1

            conn.commit()

        print("\nPassword reset successfully.")
        print(f"Username: {username}")
        print(f"Database backup: {backup_path}")
        print("The new password was not displayed or saved by this script.")
        print("You can now use this username and your new password in the evaluation script.")
        return 0

    except sqlite3.Error as exc:
        print(f"Database error: {exc}")
        print("Check the database before retrying.")
        return 1
    finally:
        # Reduce the lifetime of the plaintext password in this script.
        password = ""
        confirmation = ""


if __name__ == "__main__":
    raise SystemExit(main())
