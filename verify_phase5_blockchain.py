#!/usr/bin/env python3
"""
Read-only verification of the Phase 5 dataset against the deployed blockchain.

Run from the repository root:
    python verify_phase5_blockchain.py

Optional:
    python verify_phase5_blockchain.py --dataset phase5_dataset --report phase5_dataset/blockchain_verification_report.json

This script only calls read methods on UniversityBlockchainClient:
    document_exists()
    get_document_details()
    get_document_history()

It does not upload to IPFS or submit blockchain transactions.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow execution from the repository root or by absolute script path.
ROOT = Path(__file__).resolve().parent
PYTHON_BACKEND = ROOT / "python_backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PYTHON_BACKEND) not in sys.path:
    sys.path.insert(0, str(PYTHON_BACKEND))

try:
    from python_backend.blockchain_client import UniversityBlockchainClient
except ImportError:
    try:
        from blockchain_client import UniversityBlockchainClient  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Could not import UniversityBlockchainClient. Run this script "
            "from your project root with the project's virtual environment active."
        ) from exc


LOG = logging.getLogger("phase5_blockchain_verifier")


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"Required dataset file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def records_from_json(data: Any, file_label: str) -> List[Dict[str, Any]]:
    """Accept either a top-level list or a dict containing a records list."""
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = data.get("documents", data.get("records"))
        if not isinstance(records, list):
            raise ValueError(
                f"{file_label}: expected a list or a dict with a 'documents' "
                "or 'records' list."
            )
    else:
        raise ValueError(f"{file_label}: unsupported JSON top-level type.")

    invalid = [i for i, item in enumerate(records, start=1) if not isinstance(item, dict)]
    if invalid:
        raise ValueError(
            f"{file_label}: non-object records at positions {invalid[:10]}."
        )
    return records


def first_value(record: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def norm_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def norm_status(value: Any) -> str:
    return norm_text(value).upper().replace(" ", "_")


def normalize_version(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize common names used by the client and Solidity contract."""
    return {
        "version": as_int(first_value(record, "version", "version_number"), 0) or 0,
        "previous_version": as_int(
            first_value(record, "previous_version", "previousVersion"), 0
        ) or 0,
        "ipfs_cid": norm_text(
            first_value(record, "ipfs_cid", "ipfsCID", "cid")
        ),
        "content_hash": norm_text(
            first_value(record, "content_hash", "contentHash")
        ).lower(),
        "approval_status": norm_status(
            first_value(record, "approval_status", "approvalStatus")
        ),
        "approved_by": norm_text(first_value(record, "approved_by", "approvedBy")),
    }


def expected_versions(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    plans = document.get("version_plan", [])
    if not isinstance(plans, list):
        return []
    result: List[Dict[str, Any]] = []
    for plan in plans:
        if isinstance(plan, dict):
            result.append(normalize_version(plan))
    return sorted(result, key=lambda item: item["version"])


def find_latest_approved(history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    approved = [
        item for item in history
        if item["approval_status"] in {"APPROVED", "ACCEPTED"}
    ]
    return max(approved, key=lambda item: item["version"]) if approved else None


def compare_document(
    blockchain: Any,
    expected_doc: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    document_id = norm_text(
        first_value(expected_doc, "document_id", "documentId")
    )
    result: Dict[str, Any] = {
        "document_id": document_id,
        "student_id_expected": norm_text(expected_doc.get("student_id")),
        "document_type_expected": norm_text(expected_doc.get("document_type")),
        "repository_expected": norm_text(expected_doc.get("repository")),
        "exists_on_chain": False,
        "status": "ERROR",
        "errors": [],
        "warnings": [],
        "expected_version_count": len(expected_versions(expected_doc)),
        "actual_version_count": None,
        "expected_latest_version": as_int(metadata.get("latest_version")),
        "actual_latest_version": None,
        "expected_latest_approved_version": as_int(metadata.get("latest_approved_version")),
        "actual_latest_approved_version": None,
        "expected_latest_approved_cid": norm_text(metadata.get("latest_approved_cid")),
        "actual_latest_approved_cid": None,
        "version_checks": [],
    }

    try:
        # Use the explicit existence probe first. Details may revert for missing IDs.
        exists = bool(blockchain.document_exists(document_id))
        result["exists_on_chain"] = exists
        if not exists:
            result["status"] = "MISSING_ON_CHAIN"
            result["errors"].append("Document ID does not exist on the deployed contract.")
            return result

        details = blockchain.get_document_details(document_id)
        if not isinstance(details, dict) or not details:
            result["errors"].append(
                "document_exists() returned True but document details were empty."
            )
            result["status"] = "ERROR"
            return result

        actual_student = norm_text(first_value(details, "student_id", "studentId"))
        actual_type = norm_text(first_value(details, "document_type", "documentType"))
        actual_latest = as_int(first_value(details, "latest_version", "latestVersion"), 0) or 0
        result["actual_latest_version"] = actual_latest

        if actual_student and result["student_id_expected"] and actual_student != result["student_id_expected"]:
            result["errors"].append(
                f"Student ID mismatch: chain={actual_student}, dataset={result['student_id_expected']}."
            )
        if actual_type and result["document_type_expected"] and actual_type != result["document_type_expected"]:
            result["errors"].append(
                f"Document type mismatch: chain={actual_type}, dataset={result['document_type_expected']}."
            )

        history_raw = blockchain.get_document_history(document_id)
        if history_raw is None:
            history_raw = []
        if not isinstance(history_raw, list):
            history_raw = list(history_raw)
        history = [
            normalize_version(item)
            for item in history_raw
            if isinstance(item, dict)
        ]
        history.sort(key=lambda item: item["version"])
        result["actual_version_count"] = len(history)

        if len(history_raw) != len(history):
            result["warnings"].append(
                "Some history entries were not dictionaries and could not be normalized."
            )

        actual_versions = {item["version"]: item for item in history}
        planned = expected_versions(expected_doc)

        for expected in planned:
            version_no = expected["version"]
            actual = actual_versions.get(version_no)
            check: Dict[str, Any] = {
                "version": version_no,
                "found_on_chain": actual is not None,
                "expected_approval_status": expected["approval_status"],
                "actual_approval_status": actual["approval_status"] if actual else None,
                "expected_cid": expected["ipfs_cid"] or None,
                "actual_cid": actual["ipfs_cid"] if actual else None,
                "expected_content_hash": expected["content_hash"] or None,
                "actual_content_hash": actual["content_hash"] if actual else None,
                "mismatches": [],
            }
            if actual is None:
                check["mismatches"].append("Version is missing from on-chain history.")
            else:
                if expected["approval_status"] and actual["approval_status"] != expected["approval_status"]:
                    check["mismatches"].append(
                        f"Approval status differs (chain={actual['approval_status'] or 'EMPTY'})."
                    )
                if expected["ipfs_cid"] and actual["ipfs_cid"] != expected["ipfs_cid"]:
                    check["mismatches"].append("IPFS CID differs from version plan.")
                if expected["content_hash"] and actual["content_hash"] != expected["content_hash"]:
                    check["mismatches"].append("Content hash differs from version plan.")
            result["version_checks"].append(check)

        if actual_latest != (max(actual_versions.keys()) if actual_versions else 0):
            result["warnings"].append(
                "Document latest_version does not equal the highest version in returned history."
            )

        latest_approved = find_latest_approved(history)
        if latest_approved:
            result["actual_latest_approved_version"] = latest_approved["version"]
            result["actual_latest_approved_cid"] = latest_approved["ipfs_cid"]
        else:
            result["warnings"].append("No APPROVED version was found in on-chain history.")

        expected_latest = result["expected_latest_version"]
        if expected_latest is not None and expected_latest != actual_latest:
            result["errors"].append(
                f"Latest version mismatch: chain={actual_latest}, dataset={expected_latest}."
            )

        expected_approved_version = result["expected_latest_approved_version"]
        actual_approved_version = result["actual_latest_approved_version"]
        if expected_approved_version is not None and expected_approved_version != actual_approved_version:
            result["errors"].append(
                "Latest approved version mismatch: "
                f"chain={actual_approved_version}, dataset={expected_approved_version}."
            )

        expected_approved_cid = result["expected_latest_approved_cid"]
        actual_approved_cid = result["actual_latest_approved_cid"] or ""
        if expected_approved_cid and expected_approved_cid != actual_approved_cid:
            result["errors"].append("Latest approved CID differs from metadata_records.json.")

        for check in result["version_checks"]:
            if check["mismatches"]:
                result["errors"].append(
                    f"V{check['version']}: " + "; ".join(check["mismatches"])
                )

        if result["errors"]:
            result["status"] = "MISMATCH"
        elif result["warnings"]:
            result["status"] = "VERIFIED_WITH_WARNINGS"
        else:
            result["status"] = "VERIFIED"

        return result

    except Exception as exc:
        result["status"] = "ERROR"
        result["errors"].append(
            f"Blockchain read failed; this is not evidence that the document is missing: "
            f"{type(exc).__name__}: {exc}"
        )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only verification of Phase 5 dataset records against the blockchain."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "phase5_dataset",
        help="Dataset directory (default: ./phase5_dataset).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional JSON report path. Defaults to <dataset>/blockchain_verification_report.json.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    dataset_dir = args.dataset.resolve()
    documents_path = dataset_dir / "documents.json"
    metadata_path = dataset_dir / "metadata_records.json"
    report_path = (
        args.report.resolve()
        if args.report
        else dataset_dir / "blockchain_verification_report.json"
    )

    try:
        documents = records_from_json(load_json(documents_path), str(documents_path))
        metadata_records = records_from_json(load_json(metadata_path), str(metadata_path))
    except Exception as exc:
        LOG.error("Could not load dataset: %s", exc)
        return 2

    documents_by_id: Dict[str, Dict[str, Any]] = {}
    for record in documents:
        document_id = norm_text(first_value(record, "document_id", "documentId"))
        if not document_id:
            LOG.warning("Skipping document record without document_id.")
            continue
        if document_id in documents_by_id:
            LOG.error("Duplicate document ID in documents.json: %s", document_id)
            return 2
        documents_by_id[document_id] = record

    metadata_by_id: Dict[str, Dict[str, Any]] = {}
    for record in metadata_records:
        document_id = norm_text(first_value(record, "document_id", "documentId"))
        if not document_id:
            LOG.warning("Skipping metadata record without document_id.")
            continue
        if document_id in metadata_by_id:
            LOG.error("Duplicate document ID in metadata_records.json: %s", document_id)
            return 2
        metadata_by_id[document_id] = record

    document_ids = sorted(set(documents_by_id) | set(metadata_by_id))
    missing_metadata = sorted(set(documents_by_id) - set(metadata_by_id))
    metadata_without_document = sorted(set(metadata_by_id) - set(documents_by_id))

    print("Connecting to blockchain through UniversityBlockchainClient...")
    try:
        blockchain = UniversityBlockchainClient()
    except Exception as exc:
        print(
            f"ERROR: Could not initialize blockchain client: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(
            "No records were classified as missing; no blockchain verification was performed.",
            file=sys.stderr,
        )
        return 2

    results: List[Dict[str, Any]] = []
    total = len(document_ids)
    for index, document_id in enumerate(document_ids, start=1):
        print(f"[{index:>3}/{total}] Checking {document_id} ...", flush=True)
        document = documents_by_id.get(document_id)
        metadata = metadata_by_id.get(document_id, {})
        if document is None:
            results.append({
                "document_id": document_id,
                "status": "METADATA_WITHOUT_DOCUMENT",
                "errors": ["Metadata record has no matching documents.json record."],
                "warnings": [],
            })
            continue
        if document_id not in metadata_by_id:
            # Still check the chain, but flag the local dataset inconsistency.
            result = compare_document(blockchain, document, {})
            result["errors"].append("No matching metadata_records.json record.")
            result["status"] = "MISMATCH" if result["status"] != "ERROR" else "ERROR"
            results.append(result)
            continue
        results.append(compare_document(blockchain, document, metadata))

    counts = Counter(item.get("status", "UNKNOWN") for item in results)
    report = {
        "report_type": "phase5_read_only_blockchain_verification",
        "dataset_directory": str(dataset_dir),
        "documents_json_count": len(documents),
        "metadata_records_count": len(metadata_records),
        "unique_document_ids_checked": len(document_ids),
        "dataset_ids_missing_metadata": missing_metadata,
        "metadata_ids_without_document": metadata_without_document,
        "status_counts": dict(sorted(counts.items())),
        "all_verified": len(results) > 0 and all(
            item.get("status") == "VERIFIED" for item in results
        ),
        "read_only": True,
        "results": results,
    }

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except Exception as exc:
        LOG.error("Could not write report to %s: %s", report_path, exc)
        return 2

    print("\n" + "=" * 72)
    print("PHASE 5 BLOCKCHAIN VERIFICATION SUMMARY")
    print("=" * 72)
    print(f"Dataset documents:           {len(documents)}")
    print(f"Dataset metadata records:    {len(metadata_records)}")
    print(f"Unique IDs checked:          {len(document_ids)}")
    print(f"Dataset documents missing metadata: {len(missing_metadata)}")
    print(f"Metadata without document:   {len(metadata_without_document)}")
    for status in (
        "VERIFIED",
        "VERIFIED_WITH_WARNINGS",
        "MISMATCH",
        "MISSING_ON_CHAIN",
        "ERROR",
        "METADATA_WITHOUT_DOCUMENT",
    ):
        print(f"{status:28} {counts.get(status, 0)}")
    print(f"\nJSON report: {report_path}")
    print("No IPFS uploads or blockchain transactions were submitted.")

    if counts.get("ERROR", 0):
        return 2
    if counts.get("MISMATCH", 0) or counts.get("MISSING_ON_CHAIN", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
