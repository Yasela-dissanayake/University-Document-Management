#!/usr/bin/env python3
"""
Build a separate, read-only snapshot of document metadata that currently exists
on the configured UniversityRegistry blockchain.

Run from the repository root:
    python build_blockchain_snapshot.py

Optional:
    python build_blockchain_snapshot.py --output blockchain_snapshot_dataset
    python build_blockchain_snapshot.py --candidate-file path/to/extra_ids.txt

Safety:
- Uses only read methods from UniversityBlockchainClient.
- Does not create blockchain records or upload anything to IPFS.
- Candidate IDs from local files are used only as lookup hints; local values are
  never copied as authoritative blockchain metadata.
- This first version discovers candidate IDs from project files and existing
  dataset JSON. It cannot guarantee a complete inventory if document IDs are
  not enumerable from the contract or recoverable from these sources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

ROOT = Path(__file__).resolve().parent
PYTHON_BACKEND = ROOT / "python_backend"

for path in (ROOT, PYTHON_BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

try:
    from python_backend.blockchain_client import UniversityBlockchainClient
except ImportError:
    from blockchain_client import UniversityBlockchainClient  # type: ignore


LOG = logging.getLogger("blockchain_snapshot")

SKIP_DIRS = {
    ".git", ".venv", "venv", "university_venv", "node_modules",
    "__pycache__", ".next", "dist", "build", "artifacts", "cache",
}
TEXT_SUFFIXES = {".py", ".sol", ".json", ".txt", ".md", ".env.example"}

# Covers the document ID formats currently visible in this project, e.g.
# TX-STD001-001, REG-STD002-001, STD001-TRANSCRIPT.
DOCUMENT_ID_PATTERNS = (
    re.compile(r"\b(?:TX|REG|EX|SS)-STD\d{3,}-\d{3,}\b", re.IGNORECASE),
    re.compile(r"\bSTD\d{3,}-[A-Z][A-Z0-9_-]{2,}\b", re.IGNORECASE),
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(value: Any) -> str:
    return str(value if value is not None else "").strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def json_load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def records_from_json(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("documents", "records", "metadata_records"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def add_id(candidate_map: Dict[str, Set[str]], document_id: Any, source: str) -> None:
    value = norm(document_id)
    if value and len(value) <= 256 and "\n" not in value:
        candidate_map.setdefault(value, set()).add(source)


def discover_candidate_ids(
    scan_root: Path,
    extra_candidate_file: Optional[Path] = None,
) -> Dict[str, Set[str]]:
    """Collect candidate IDs from local files; none are trusted as chain facts."""
    candidates: Dict[str, Set[str]] = {}

    # Existing dataset files are hints only, not the output metadata source.
    for relative in (
        Path("phase5_dataset/documents.json"),
        Path("phase5_dataset/metadata_records.json"),
    ):
        path = scan_root / relative
        if not path.is_file():
            continue
        try:
            for record in records_from_json(json_load(path)):
                add_id(
                    candidates,
                    record.get("document_id", record.get("documentId")),
                    f"local_candidate:{relative.as_posix()}",
                )
        except Exception as exc:
            LOG.warning("Could not read candidate file %s: %s", path, exc)

    # Scan project source/config/data files for known document-ID patterns.
    # We only retain strings matching ID formats; file contents are not copied.
    for path in scan_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            relative_parts = path.relative_to(scan_root).parts
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        # Avoid scanning large or generated files.
        try:
            if path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = path.relative_to(scan_root).as_posix()
        for pattern in DOCUMENT_ID_PATTERNS:
            for match in pattern.findall(text):
                add_id(candidates, match, f"project_scan:{relative}")

    if extra_candidate_file:
        if not extra_candidate_file.is_file():
            raise FileNotFoundError(f"Candidate file not found: {extra_candidate_file}")
        for line in extra_candidate_file.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                add_id(candidates, value, f"extra_candidate_file:{extra_candidate_file.name}")

    return candidates


def normalize_version(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the output returned by UniversityBlockchainClient."""
    return {
        "version": as_int(data.get("version")),
        "previous_version": as_int(data.get("previous_version", data.get("previousVersion"))),
        "ipfs_cid": norm(data.get("ipfs_cid", data.get("ipfsCID", data.get("cid")))),
        "content_hash": norm(data.get("content_hash", data.get("contentHash"))).lower(),
        "approval_status": norm(
            data.get("approval_status", data.get("approvalStatus"))
        ).upper(),
        "approved_by": norm(data.get("approved_by", data.get("approvedBy"))),
        "timestamp": as_int(data.get("timestamp")),
    }


def derive_approved_version(history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    approved = [
        version for version in history
        if version.get("approval_status") in {"APPROVED", "ACCEPTED"}
    ]
    if not approved:
        return None
    return max(approved, key=lambda item: item.get("version", 0))


def sha256_json(data: Any) -> str:
    encoded = json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def get_network_info(blockchain: Any) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "chain_id": None,
        "contract_address": None,
        "latest_block_number": None,
    }
    try:
        info["chain_id"] = int(blockchain.w3.eth.chain_id)
    except Exception as exc:
        info["chain_id_error"] = f"{type(exc).__name__}: {exc}"
    try:
        info["contract_address"] = norm(blockchain.contract.address)
    except Exception as exc:
        info["contract_address_error"] = f"{type(exc).__name__}: {exc}"
    try:
        info["latest_block_number"] = int(blockchain.w3.eth.block_number)
    except Exception as exc:
        info["latest_block_error"] = f"{type(exc).__name__}: {exc}"
    return info


def read_document(blockchain: Any, document_id: str, sources: Iterable[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "document_id": document_id,
        "candidate_sources": sorted(sources),
        "exists_on_chain": False,
        "read_status": "ERROR",
        "errors": [],
        "warnings": [],
    }

    try:
        exists = bool(blockchain.document_exists(document_id))
        result["exists_on_chain"] = exists
        if not exists:
            result["read_status"] = "NOT_FOUND_ON_CHAIN"
            return result

        details = blockchain.get_document_details(document_id)
        if not isinstance(details, dict) or not details:
            result["read_status"] = "ERROR"
            result["errors"].append(
                "Existence check succeeded but get_document_details returned no metadata."
            )
            return result

        history_raw = blockchain.get_document_history(document_id)
        if history_raw is None:
            history_raw = []
        if not isinstance(history_raw, list):
            history_raw = list(history_raw)

        history: List[Dict[str, Any]] = []
        for entry in history_raw:
            if not isinstance(entry, dict):
                result["warnings"].append(
                    f"Skipped unexpected history entry type: {type(entry).__name__}."
                )
                continue
            history.append(normalize_version(entry))
        history.sort(key=lambda item: item["version"])

        actual_latest = as_int(details.get("latest_version", details.get("latestVersion")))
        if history and actual_latest != max(item["version"] for item in history):
            result["warnings"].append(
                "Contract latest_version differs from the highest returned history version."
            )

        latest_approved = derive_approved_version(history)
        result.update({
            "student_id": norm(details.get("student_id", details.get("studentId"))),
            "document_type": norm(details.get("document_type", details.get("documentType"))),
            "latest_version": actual_latest,
            "version_count_returned": len(history),
            "latest_approved_version": (
                latest_approved["version"] if latest_approved else None
            ),
            "latest_approved_cid": (
                latest_approved["ipfs_cid"] if latest_approved else None
            ),
            "latest_approved_content_hash": (
                latest_approved["content_hash"] if latest_approved else None
            ),
            "versions": history,
            "read_status": "READ",
        })
        return result

    except Exception as exc:
        result["read_status"] = "ERROR"
        result["errors"].append(
            f"Blockchain read failed; document existence is unknown: "
            f"{type(exc).__name__}: {exc}"
        )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a new read-only snapshot from documents discoverable through "
            "local candidate IDs and the configured blockchain."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Project root (default: directory containing this script).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: <root>/blockchain_snapshot_dataset).",
    )
    parser.add_argument(
        "--candidate-file",
        type=Path,
        default=None,
        help="Optional text file with one additional document ID per line.",
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

    root = args.root.resolve()
    output = args.output.resolve() if args.output else root / "blockchain_snapshot_dataset"
    candidate_file = args.candidate_file.resolve() if args.candidate_file else None

    try:
        candidates = discover_candidate_ids(root, candidate_file)
    except Exception as exc:
        LOG.error("Candidate discovery failed: %s", exc)
        return 2

    if not candidates:
        LOG.error(
            "No candidate document IDs were found. Supply --candidate-file or check --root."
        )
        return 2

    print(f"Candidate IDs discovered locally: {len(candidates)}")
    print("Connecting to blockchain through UniversityBlockchainClient...")
    try:
        blockchain = UniversityBlockchainClient()
    except Exception as exc:
        LOG.error("Could not initialize blockchain client: %s: %s", type(exc).__name__, exc)
        return 2

    network = get_network_info(blockchain)
    print(
        "Connected. "
        f"chain_id={network.get('chain_id')}, "
        f"contract={network.get('contract_address')}, "
        f"latest_block={network.get('latest_block_number')}"
    )

    discovered: List[Dict[str, Any]] = []
    not_found: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    ordered_ids = sorted(candidates)

    for index, document_id in enumerate(ordered_ids, start=1):
        print(f"[{index:>3}/{len(ordered_ids)}] Reading {document_id} ...", flush=True)
        record = read_document(blockchain, document_id, candidates[document_id])
        if record["read_status"] == "READ":
            discovered.append(record)
        elif record["read_status"] == "NOT_FOUND_ON_CHAIN":
            not_found.append(record)
        else:
            errors.append(record)

    # documents.json and metadata_records.json contain only values read from the
    # blockchain. Candidate sources and diagnostic errors remain in the manifest.
    documents: List[Dict[str, Any]] = []
    metadata_records: List[Dict[str, Any]] = []
    version_history: List[Dict[str, Any]] = []
    for record in discovered:
        doc = {
            "document_id": record["document_id"],
            "student_id": record.get("student_id", ""),
            "document_type": record.get("document_type", ""),
            "latest_version": record.get("latest_version", 0),
            "latest_approved_version": record.get("latest_approved_version"),
            "latest_approved_cid": record.get("latest_approved_cid"),
            "latest_approved_content_hash": record.get("latest_approved_content_hash"),
            "metadata_origin": "live_blockchain_read",
        }
        documents.append(doc)
        metadata_records.append({
            "document_id": doc["document_id"],
            "student_id": doc["student_id"],
            "document_type": doc["document_type"],
            "latest_version": doc["latest_version"],
            "latest_approved_version": doc["latest_approved_version"],
            "latest_approved_cid": doc["latest_approved_cid"],
            "latest_approved_content_hash": doc["latest_approved_content_hash"],
            "metadata_origin": "live_blockchain_read",
        })
        version_history.append({
            "document_id": record["document_id"],
            "student_id": doc["student_id"],
            "versions": record.get("versions", []),
            "history_source": "UniversityBlockchainClient.get_document_history",
        })

    statuses = Counter(record["read_status"] for record in (
        discovered + not_found + errors
    ))
    created_at = now_utc()
    manifest = {
        "snapshot_type": "read_only_blockchain_metadata_snapshot",
        "created_at_utc": created_at,
        "network": network,
        "candidate_discovery": {
            "candidate_count": len(candidates),
            "candidate_ids_are_hints_only": True,
            "sources": sorted({
                source
                for source_set in candidates.values()
                for source in source_set
            }),
            "method": (
                "Scanned local dataset JSON and project text/source files for "
                "document ID patterns, plus optional candidate file."
            ),
        },
        "counts": {
            "candidate_ids": len(candidates),
            "documents_read_from_chain": len(discovered),
            "not_found_on_chain": len(not_found),
            "read_errors": len(errors),
            "version_entries": sum(len(x.get("versions", [])) for x in version_history),
        },
        "read_status_counts": dict(sorted(statuses.items())),
        "completeness": {
            "complete_chain_inventory": False,
            "reason": (
                "This first snapshot only checks candidate IDs discovered from local "
                "files. Completeness requires a contract-level document index or "
                "historical event/transaction discovery."
            ),
        },
        "safety": {
            "read_only": True,
            "blockchain_transactions_submitted": 0,
            "ipfs_uploads_performed": 0,
            "decrypted_document_content_saved": False,
        },
        "dataset_sha256": {
            "documents.json": sha256_json(documents),
            "metadata_records.json": sha256_json(metadata_records),
            "version_history.json": sha256_json(version_history),
        },
        "not_found_documents": [
            {
                "document_id": item["document_id"],
                "candidate_sources": item["candidate_sources"],
            }
            for item in not_found
        ],
        "read_errors": errors,
    }

    try:
        output.mkdir(parents=True, exist_ok=True)
        payloads = {
            "documents.json": documents,
            "metadata_records.json": metadata_records,
            "version_history.json": version_history,
            # CID availability is deliberately not claimed by this step.
            "ipfs_verification.json": {
                "status": "NOT_RUN",
                "note": "This snapshot step does not fetch or decrypt IPFS objects.",
                "records": [],
            },
            "snapshot_manifest.json": manifest,
        }
        for filename, payload in payloads.items():
            target = output / filename
            with target.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
    except Exception as exc:
        LOG.error("Failed writing snapshot to %s: %s", output, exc)
        return 2

    print("\n" + "=" * 72)
    print("BLOCKCHAIN SNAPSHOT SUMMARY")
    print("=" * 72)
    print(f"Candidate IDs:              {len(candidates)}")
    print(f"Read from blockchain:       {len(discovered)}")
    print(f"Not found on blockchain:    {len(not_found)}")
    print(f"Read errors:                {len(errors)}")
    print(f"Version entries captured:   {manifest['counts']['version_entries']}")
    print(f"Output directory:           {output}")
    print("Complete contract inventory: NOT YET ESTABLISHED")
    print("Blockchain writes:           0")
    print("IPFS uploads:                0")

    if errors:
        print("Some records had read errors; inspect snapshot_manifest.json.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
