#!/usr/bin/env python3
"""
Live blockchain -> IPFS -> ACL retrieval evaluation.

This evaluator uses:
  * the document IDs in a live_deployment_report.json inventory;
  * the currently configured UniversityBlockchainClient;
  * the existing get_authorized_document() security workflow;
  * real users from python_backend/rbac.py's SQLite user database.

It does NOT:
  * create or modify blockchain records;
  * upload or modify IPFS objects;
  * bypass RBAC or ACL;
  * send document contents to an LLM;
  * write decrypted document contents or encryption keys to the report.

Run from the repository root:

    python python_backend/evaluation/evaluate_live_document_retrieval.py

Optional:

    python python_backend/evaluation/evaluate_live_document_retrieval.py \
      --inventory python_backend/evaluation/live_deployment_report.json \
      --output python_backend/evaluation/live_retrieval_report.json

    python python_backend/evaluation/evaluate_live_document_retrieval.py \
      --document-id TX-STD001-001

    python python_backend/evaluation/evaluate_live_document_retrieval.py \
      --user-id 3

The script evaluates every selected real local user against every selected
document. A denial is reported as a security outcome, not automatically as a
test failure: whether a particular user should have access depends on the
configured RBAC and ACL policies.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Paths and environment
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_project_environment() -> None:
    """Load project environment files without printing secret values."""
    try:
        from dotenv import load_dotenv

        candidates = [
            PROJECT_ROOT / ".env",
            PROJECT_ROOT / "python_backend" / ".env",
        ]

        for env_path in candidates:
            if env_path.exists():
                load_dotenv(env_path, override=False)
    except Exception:
        # The project may already have environment variables exported.
        pass


load_project_environment()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger("live_document_retrieval_evaluation")


# ---------------------------------------------------------------------------
# Safe helpers
# ---------------------------------------------------------------------------

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text[2:] if text.startswith("0x") else text


def classify_result(result: Dict[str, Any]) -> str:
    """
    Classify a retrieval without assuming that every user should be allowed.

    Successful retrieval requires both:
      - get_authorized_document() returned ok=True;
      - integrity_verified=True.
    """
    if result.get("ok") is True:
        if result.get("integrity_verified") is True:
            return "retrieved_and_integrity_verified"
        return "retrieved_but_integrity_not_verified"

    reason = str(result.get("reason") or "unknown_error")

    if reason == "forbidden":
        return "denied_by_rbac"

    if reason == "acl_forbidden":
        return "denied_by_acl"

    if reason in {
        "document_not_found",
        "invalid_document_id",
        "student_identity_unavailable",
        "student_mismatch",
        "blockchain_identity_mismatch",
        "blockchain_metadata_mismatch",
    }:
        return reason

    if reason in {
        "ipfs_error",
        "missing_ipfs_cid",
        "integrity_verification_failed",
        "blockchain_error",
        "authorization_error",
    }:
        return reason

    return "retrieval_failed"


def safe_trace_summary(trace: Any) -> Dict[str, Any]:
    """
    Retain only safe diagnostic flags.

    Never serialize document plaintext, ciphertext, keys, or raw payloads.
    """
    if not isinstance(trace, dict):
        return {}

    allowed_fields = (
        "rbac_allowed",
        "blockchain_resolved",
        "ipfs_retrieved",
        "integrity_verified",
        "authorized",
        "explanation",
    )

    summary: Dict[str, Any] = {}
    for field in allowed_fields:
        if field in trace:
            value = trace[field]
            if isinstance(value, (bool, int, float, str)) or value is None:
                summary[field] = value

    # Do not include nested blockchain metadata or raw trace objects.
    return summary


# ---------------------------------------------------------------------------
# Inventory and local user loading
# ---------------------------------------------------------------------------

def load_inventory(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Inventory file not found: {path}\n"
            "Run discover_live_records.py first."
        )

    with path.open("r", encoding="utf-8") as file:
        inventory = json.load(file)

    if not isinstance(inventory, dict):
        raise ValueError("Inventory JSON must contain an object.")

    network = inventory.get("network") or {}
    documents = inventory.get("documents") or []

    if not isinstance(documents, list):
        raise ValueError("Inventory 'documents' field must be a list.")

    if not documents:
        raise ValueError(
            "The inventory contains no document IDs. "
            "Check the discovery report before running retrieval evaluation."
        )

    return inventory


def load_real_users(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Load actual registered application users.

    rbac.get_all_users() deliberately returns no password hashes.
    """
    from python_backend import rbac

    users = rbac.get_all_users()

    if not isinstance(users, list):
        raise RuntimeError("RBAC get_all_users() did not return a list.")

    normalized: List[Dict[str, Any]] = []

    for user in users:
        if not isinstance(user, dict):
            continue

        normalized_user = {
            "id": user.get("id"),
            "username": str(user.get("username") or ""),
            "role": str(user.get("role") or "").strip().upper(),
            "student_id": str(user.get("student_id") or "").strip().upper(),
            "wallet_address": user.get("wallet_address"),
        }

        if user_id is not None and safe_int(normalized_user["id"], -1) != user_id:
            continue

        if not normalized_user["role"]:
            continue

        normalized.append(normalized_user)

    if user_id is not None and not normalized:
        raise ValueError(
            f"No registered application user with id={user_id} was found."
        )

    return normalized


def select_documents(
    inventory: Dict[str, Any],
    document_ids: Optional[List[str]],
) -> List[Dict[str, Any]]:
    all_documents = inventory.get("documents") or []

    by_id: Dict[str, Dict[str, Any]] = {}
    for item in all_documents:
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("document_id") or "").strip()
        if doc_id:
            by_id[doc_id] = item

    if document_ids:
        selected: List[Dict[str, Any]] = []
        missing: List[str] = []

        for doc_id in document_ids:
            if doc_id in by_id:
                selected.append(by_id[doc_id])
            else:
                missing.append(doc_id)

        if missing:
            raise ValueError(
                "These document IDs were not found in the inventory: "
                + ", ".join(missing)
            )
        return selected

    return list(by_id.values())


# ---------------------------------------------------------------------------
# Deployment consistency
# ---------------------------------------------------------------------------

def verify_configured_deployment(
    inventory: Dict[str, Any],
    blockchain_client: Any,
) -> Dict[str, Any]:
    """
    Ensure the inventory belongs to the currently configured chain/contract.

    A newer latest block is acceptable; a different chain or contract is not.
    """
    inventory_network = inventory.get("network") or {}

    inventory_chain_id = safe_int(inventory_network.get("chain_id"), -1)
    inventory_address = str(
        inventory_network.get("contract_address") or ""
    ).strip().lower()

    live_chain_id = safe_int(getattr(blockchain_client, "chain_id", -1), -1)
    live_address = str(
        getattr(blockchain_client, "contract_address", "") or ""
    ).strip().lower()

    if inventory_chain_id != live_chain_id:
        raise RuntimeError(
            "Inventory chain ID does not match the configured blockchain client. "
            f"Inventory={inventory_chain_id}, configured={live_chain_id}."
        )

    if inventory_address and live_address and inventory_address != live_address:
        raise RuntimeError(
            "Inventory contract address does not match the configured contract. "
            f"Inventory={inventory_address}, configured={live_address}."
        )

    try:
        current_block = int(blockchain_client.w3.eth.block_number)
    except Exception:
        current_block = None

    return {
        "chain_id": live_chain_id,
        "contract_address": live_address or inventory_address,
        "inventory_latest_block": safe_int(
            inventory_network.get("latest_block"), -1
        ),
        "current_latest_block": current_block,
        "inventory_scan_from_block": safe_int(
            inventory_network.get("scan_from_block"), -1
        ),
        "inventory_scan_to_block": safe_int(
            inventory_network.get("scan_to_block"), -1
        ),
    }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_one(
    document: Dict[str, Any],
    user: Dict[str, Any],
    retrieval_function: Any,
) -> Dict[str, Any]:
    document_id = str(document.get("document_id") or "").strip()
    expected_student_id = str(document.get("student_id") or "").strip().upper()
    expected_document_type = str(document.get("document_type") or "").strip()

    started = time.perf_counter()

    try:
        # This is the production retrieval workflow. It performs blockchain
        # resolution, RBAC, approved-version selection, IPFS retrieval,
        # ACL decryption and content-hash verification.
        result = retrieval_function(
            document_id,
            user,
            expected_student_id=expected_student_id or None,
            expected_document_type=expected_document_type or None,
            approved_only=True,
            verify_integrity=True,
        )

        if not isinstance(result, dict):
            result = {
                "ok": False,
                "reason": "invalid_retrieval_response",
                "trace": {},
            }

    except Exception as exc:
        logger.exception(
            "Unexpected retrieval exception for document %s and user id %s",
            document_id,
            user.get("id"),
        )
        result = {
            "ok": False,
            "reason": "unexpected_exception",
            "exception_type": type(exc).__name__,
            "trace": {},
        }

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)

    metadata = result.get("blockchain_metadata") or {}
    trace = result.get("trace") or {}

    # The decrypted document is intentionally not copied into the report.
    document_content = result.get("document")
    content_present = isinstance(document_content, dict) and bool(document_content)

    outcome = classify_result(result)

    record: Dict[str, Any] = {
        "document_id": document_id,
        "document_owner_student_id": expected_student_id,
        "document_type": expected_document_type,
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "role": user.get("role"),
            "student_id": user.get("student_id"),
        },
        "outcome": outcome,
        "ok": bool(result.get("ok", False)),
        "authorized": bool(result.get("authorized", False)),
        "integrity_verified": bool(result.get("integrity_verified", False)),
        "document_content_returned_in_memory": content_present,
        "document_content_written_to_report": False,
        "reason": result.get("reason"),
        "exception_type": result.get("exception_type"),
        "resolved_version": result.get("version")
            or metadata.get("resolved_version")
            or metadata.get("version"),
        "approval_status": result.get("approval_status")
            or metadata.get("approval_status"),
        "ipfs_cid": result.get("ipfs_cid")
            or metadata.get("ipfs_cid"),
        "expected_content_hash": normalize_hash(
            result.get("content_hash") or metadata.get("content_hash")
        ),
        "actual_content_hash": normalize_hash(
            result.get("content_hash")
            if result.get("integrity_verified") is True
            else ""
        ),
        "elapsed_ms": elapsed_ms,
        "trace": safe_trace_summary(trace),
    }

    # Avoid leaking raw error messages: they can contain provider URLs,
    # payload data, or implementation details. Preserve only the reason and
    # exception class in the machine-readable report.
    return record


def build_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    outcomes = Counter(record.get("outcome", "unknown") for record in records)

    verified = outcomes.get("retrieved_and_integrity_verified", 0)
    retrieved_unverified = outcomes.get("retrieved_but_integrity_not_verified", 0)
    rbac_denied = outcomes.get("denied_by_rbac", 0)
    acl_denied = outcomes.get("denied_by_acl", 0)

    latencies = [
        float(record["elapsed_ms"])
        for record in records
        if isinstance(record.get("elapsed_ms"), (int, float))
    ]

    return {
        "total_attempts": len(records),
        "retrieved_and_integrity_verified": verified,
        "retrieved_but_integrity_not_verified": retrieved_unverified,
        "denied_by_rbac": rbac_denied,
        "denied_by_acl": acl_denied,
        "other_failures": len(records) - verified - retrieved_unverified - rbac_denied - acl_denied,
        "outcomes": dict(sorted(outcomes.items())),
        "successful_integrity_verification_rate": (
            round(verified / len(records), 4) if records else None
        ),
        "mean_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "max_latency_ms": round(max(latencies), 3) if latencies else None,
        "note": (
            "Access denials are reported separately and are not automatically "
            "classified as defects. Expected access must be assessed against "
            "the configured RBAC and per-document ACL policy."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate real blockchain/IPFS/ACL document retrieval using "
            "registered application users."
        )
    )

    default_inventory = (
        PROJECT_ROOT
        / "python_backend"
        / "evaluation"
        / "live_deployment_report.json"
    )
    default_output = (
        PROJECT_ROOT
        / "python_backend"
        / "evaluation"
        / "live_retrieval_report.json"
    )

    parser.add_argument(
        "--inventory",
        type=Path,
        default=default_inventory,
        help=f"Live discovery JSON (default: {default_inventory})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"Output JSON report (default: {default_output})",
    )
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="Evaluate a specific document ID. Can be repeated.",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="Evaluate using one actual registered application user ID.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Optional delay in seconds between retrieval attempts.",
    )
    parser.add_argument(
        "--limit-documents",
        type=int,
        default=None,
        help="Optional maximum number of documents to evaluate.",
    )
    parser.add_argument(
        "--limit-users",
        type=int,
        default=None,
        help="Optional maximum number of registered users to evaluate.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.delay < 0:
        raise ValueError("--delay cannot be negative.")

    print("=" * 72)
    print("LIVE DOCUMENT RETRIEVAL EVALUATION")
    print("=" * 72)
    print("Loading inventory and existing application components...")

    inventory = load_inventory(args.inventory)
    documents = select_documents(inventory, args.document_ids)

    if args.limit_documents is not None:
        if args.limit_documents <= 0:
            raise ValueError("--limit-documents must be greater than zero.")
        documents = documents[:args.limit_documents]

    users = load_real_users(args.user_id)

    if args.limit_users is not None:
        if args.limit_users <= 0:
            raise ValueError("--limit-users must be greater than zero.")
        users = users[:args.limit_users]

    if not users:
        raise RuntimeError(
            "No registered application users were found in the RBAC database. "
            "This evaluator does not invent user accounts or silently bypass "
            "the application's authentication context."
        )

    # Import only after .env is loaded. The existing module initializes the
    # project's configured blockchain and IPFS clients and exposes the actual
    # production retrieval function.
    from python_backend.ai import tools as retrieval_tools
    from python_backend import rbac

    deployment = verify_configured_deployment(
        inventory,
        retrieval_tools._bc,
    )

    print(f"Chain ID: {deployment['chain_id']}")
    print(f"Contract: {deployment['contract_address']}")
    print(f"Current latest block: {deployment['current_latest_block']}")
    print(f"Documents selected: {len(documents)}")
    print(f"Registered users selected: {len(users)}")
    print(f"Planned retrieval attempts: {len(documents) * len(users)}")
    print()
    print(
        "NOTE: This performs real retrievals for each selected user/document "
        "combination. It does not modify blockchain or IPFS data."
    )
    print()

    records: List[Dict[str, Any]] = []
    total = len(documents) * len(users)
    attempt = 0

    for document in documents:
        for user in users:
            attempt += 1
            doc_id = str(document.get("document_id") or "")

            print(
                f"[{attempt}/{total}] "
                f"{doc_id} | "
                f"user_id={user.get('id')} "
                f"role={user.get('role')} "
                f"student_id={user.get('student_id') or '-'}",
                flush=True,
            )

            record = evaluate_one(
                document,
                user,
                retrieval_tools.get_authorized_document,
            )
            records.append(record)

            print(
                f"    -> {record['outcome']} "
                f"({record['elapsed_ms']} ms)"
            )

            if args.delay:
                time.sleep(args.delay)

    summary = build_summary(records)

    report = {
        "evaluation": {
            "name": "live_blockchain_ipfs_acl_document_retrieval",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(PROJECT_ROOT),
            "inventory_file": str(args.inventory),
            "output_file": str(args.output),
            "read_only": True,
            "uses_real_registered_users": True,
            "uses_existing_authorized_retrieval_workflow": True,
            "llm_invoked": False,
            "document_plaintext_in_report": False,
        },
        "deployment": deployment,
        "selection": {
            "document_count": len(documents),
            "user_count": len(users),
            "document_ids": [
                str(item.get("document_id") or "")
                for item in documents
            ],
            "users": [
                {
                    "id": user.get("id"),
                    "username": user.get("username"),
                    "role": user.get("role"),
                    "student_id": user.get("student_id"),
                }
                for user in users
            ],
        },
        "summary": summary,
        "results": records,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")

    with temporary_output.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    temporary_output.replace(args.output)

    print()
    print("=" * 72)
    print("EVALUATION SUMMARY")
    print("=" * 72)
    print(f"Total attempts: {summary['total_attempts']}")
    print(
        "Retrieved + integrity verified: "
        f"{summary['retrieved_and_integrity_verified']}"
    )
    print(f"RBAC denials: {summary['denied_by_rbac']}")
    print(f"ACL denials: {summary['denied_by_acl']}")
    print(
        "Retrieved but integrity not verified: "
        f"{summary['retrieved_but_integrity_not_verified']}"
    )
    print(f"Other failures: {summary['other_failures']}")
    print(f"Mean latency: {summary['mean_latency_ms']} ms")
    print(f"Report saved to: {args.output}")
    print()
    print(
        "Reminder: review access denials against the actual RBAC and ACL "
        "policy before treating them as failures."
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nEvaluation interrupted. No blockchain/IPFS data was modified.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
