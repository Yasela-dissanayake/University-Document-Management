#!/usr/bin/env python3
"""Inspect one decrypted university document through the existing secured path.

This utility intentionally does NOT invoke LangGraph's Phase 4 document_qa
node or Ollama. It calls the existing Phase 3 tools.get_authorized_document()
function, which resolves the document from the blockchain, checks document-
level RBAC, retrieves the authoritative IPFS object, decrypts it through ACL,
and verifies plaintext integrity against the blockchain content hash.

Plaintext is displayed only in this terminal session. It is never written to a
file by this script. Be aware that terminal scrollback may retain the output.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Any, Dict

from python_backend import rbac
from python_backend.ai import tools


def authenticate(username: str) -> Dict[str, Any]:
    """Authenticate with the project's existing SQLite RBAC database."""
    password = getpass.getpass(f"Password for {username}: ")
    try:
        user = rbac.authenticate(username, password)
    finally:
        # Drop our local reference as soon as authentication has completed.
        password = ""

    if not user:
        raise PermissionError("Authentication failed.")

    # The retrieval path needs identity/role fields, not the password hash.
    safe_user = dict(user)
    safe_user.pop("password_hash", None)
    return safe_user


def preliminary_graph_style_check(
    user: Dict[str, Any], student_id: str
) -> None:
    """Apply the same initial role gate used by agent.authorize_query().

    tools.get_authorized_document() performs a second, document-specific check
    using the document type obtained from the blockchain. That second check is
    the decisive check, particularly for EXAM_DIVISION.
    """
    role = str(user.get("role", "")).strip().upper()
    allowed = rbac.can_view_offchain(user, student_id)
    provisional_exam_lookup = (
        role == "EXAM_DIVISION"
        and rbac.can_attempt_document_lookup(user, "document_retrieval")
    )

    if not (allowed or provisional_exam_lookup):
        raise PermissionError(
            "The existing LangGraph authorization policy would deny this request."
        )


def inspect_document(
    document_id: str,
    expected_student_id: str,
    user: Dict[str, Any],
) -> Dict[str, Any]:
    """Retrieve the authorized, decrypted, integrity-verified document."""
    preliminary_graph_style_check(user, expected_student_id)

    result = tools.get_authorized_document(
        document_id=document_id,
        user=user,
        expected_student_id=expected_student_id,
        expected_document_type=None,
        approved_only=True,
        verify_integrity=True,
    )

    if not isinstance(result, dict) or result.get("ok") is not True:
        reason = result.get("reason", "unknown_error") if isinstance(result, dict) else "invalid_result"
        explanation = ""
        if isinstance(result, dict):
            explanation = str((result.get("trace") or {}).get("explanation", ""))
        raise RuntimeError(
            f"Document retrieval did not succeed (reason={reason}). "
            f"{explanation}".strip()
        )

    if result.get("authorized") is not True:
        raise PermissionError("Document-level authorization was not confirmed.")

    if result.get("integrity_verified") is not True:
        raise RuntimeError(
            "The document was not confirmed against the blockchain content hash; "
            "plaintext will not be displayed."
        )

    document = result.get("document")
    if not isinstance(document, (dict, list)) or not document:
        raise RuntimeError(
            "The retrieval returned no usable decrypted JSON document."
        )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Display a decrypted document after the project's RBAC, blockchain, "
            "ACL decryption, and integrity checks. No LLM is invoked."
        )
    )
    parser.add_argument("--username", help="Existing RBAC username (e.g. ar_user).")
    parser.add_argument("--document-id", required=True, help="Document ID, e.g. EX-STD001-001.")
    parser.add_argument("--student-id", required=True, help="Expected document owner, e.g. STD001.")
    args = parser.parse_args()

    username = (args.username or input("RBAC username: ")).strip()
    document_id = args.document_id.strip()
    student_id = args.student_id.strip().upper()

    if not username or not document_id or not student_id:
        parser.error("Username, document ID, and student ID are required.")

    print("\nAuthenticating against the project's RBAC database...")
    try:
        user = authenticate(username)
        print(f"Authenticated: {username} (role={user.get('role', 'UNKNOWN')})")
        print(f"Requesting: {document_id} for {student_id}")
        print("Using blockchain-authoritative CID and approved version.")
        print("Running document-level RBAC, ACL decryption, and integrity verification...")

        result = inspect_document(document_id, student_id, user)

        print("\n" + "=" * 72)
        print("DECRYPTED DOCUMENT — AUTHORIZED AND INTEGRITY VERIFIED")
        print("=" * 72)
        print(f"Document ID : {result.get('document_id')}")
        print(f"Student ID  : {result.get('student_id')}")
        print(f"Type        : {result.get('document_type')}")
        print(f"Version     : {result.get('version')}")
        print(f"Approval    : {result.get('approval_status')}")
        print(f"Integrity   : {result.get('integrity_verified')}")
        print("\nPlaintext JSON follows (shown only in this terminal):\n")
        print(json.dumps(result["document"], indent=2, ensure_ascii=False, default=str))
        print("\n" + "=" * 72)
        print("No document plaintext was written to a file. No LLM was invoked.")
        print("Remember that your terminal may retain scrollback containing this data.")
        return 0

    except (PermissionError, RuntimeError, ValueError) as exc:
        print(f"\nINSPECTION STOPPED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Do not dump result objects or plaintext in an error path.
        print(
            f"\nINSPECTION FAILED ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
