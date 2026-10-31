"""Grant EXAM_DIVISION access to approved Examination Result Sheet versions.

Run from repository root:
    python -m python_backend.evaluation.grant_exam_division_access --document-id EX-STD001-001
    python -m python_backend.evaluation.grant_exam_division_access --document-id EX-STD001-001 --apply

Dry-run is the default. --apply writes only the local ACL SQLite wrapped-key
record. It does not submit blockchain transactions or modify IPFS content.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    ROOT = Path(__file__).resolve().parents[2]
    load_dotenv(ROOT / ".env")
except Exception:
    ROOT = Path(__file__).resolve().parents[2]

from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend import acl

ALLOWED_TYPE = "EXAMINATION RESULT SHEET"
SOURCE_PRINCIPAL = "role:ADMIN"
TARGET_PRINCIPAL = "role:EXAM_DIVISION"


def grant_one(client: UniversityBlockchainClient, document_id: str, apply: bool) -> None:
    details = client.get_document_details(document_id)
    if not details or not details.get("exists"):
        raise RuntimeError(f"{document_id}: document does not exist on blockchain")

    actual_type = str(details.get("document_type") or "").strip().upper()
    if actual_type != ALLOWED_TYPE:
        raise PermissionError(
            f"{document_id}: refused; blockchain type is {actual_type!r}, "
            f"expected {ALLOWED_TYPE!r}"
        )

    resolved = client.resolve_authoritative_document(document_id, approved_only=True)
    if not resolved:
        raise RuntimeError(f"{document_id}: no approved blockchain version exists")
    if str(resolved.get("approval_status") or "").strip().upper() != "APPROVED":
        raise PermissionError(f"{document_id}: resolved version is not APPROVED")

    cid = str(resolved.get("ipfs_cid") or "").strip()
    if not cid:
        raise RuntimeError(f"{document_id}: approved version has no IPFS CID")

    print(
        f"{'APPLY' if apply else 'DRY RUN'}: {document_id} | "
        f"type={actual_type} | version={resolved.get('resolved_version', resolved.get('version'))} "
        f"| status={resolved.get('approval_status')} | CID={cid}"
    )
    if apply:
        acl.grant_principal_access(cid, SOURCE_PRINCIPAL, TARGET_PRINCIPAL)
        print(f"  Granted {TARGET_PRINCIPAL} using existing {SOURCE_PRINCIPAL} key.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", action="append", required=True,
                        help="Blockchain document ID; repeat for multiple documents")
    parser.add_argument("--apply", action="store_true",
                        help="Write local ACL wrapped-key grants (default is dry-run)")
    args = parser.parse_args()

    client = UniversityBlockchainClient()
    failures = 0
    for document_id in args.document_id:
        try:
            grant_one(client, document_id.strip(), args.apply)
        except Exception as exc:
            failures += 1
            print(f"ERROR {document_id}: {exc}")
    print(f"Completed: {len(args.document_id) - failures} successful, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
