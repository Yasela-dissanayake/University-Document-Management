#!/usr/bin/env python3
"""
Discover document IDs from UniversityRegistry historical events and transaction
inputs, then build a second read-only snapshot from the live contract.

Run from the repository root:
    python discover_blockchain_documents.py

Optional:
    python discover_blockchain_documents.py --output blockchain_snapshot_dataset
    python discover_blockchain_documents.py --from-block 0 --chunk-size 1000

Safety:
- This script performs read-only RPC calls only.
- It does not submit transactions or upload to IPFS.
- It supplements, rather than overwrites, build_blockchain_snapshot.py outputs.
- It writes separate history-discovery files into the output directory.

Important:
- Indexed Solidity `string` event parameters are represented by hashes in event
  topics, not their original string values. This script therefore follows each
  matching event's transaction hash and attempts to decode the transaction input
  using the deployed contract ABI.
- If the provider prunes historical data or the event is unavailable in the ABI,
  the script reports the limitation rather than treating it as proof that no
  documents exist.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent
PYTHON_BACKEND = ROOT / "python_backend"
for item in (ROOT, PYTHON_BACKEND):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

try:
    from python_backend.blockchain_client import UniversityBlockchainClient
except ImportError:
    from blockchain_client import UniversityBlockchainClient  # type: ignore

LOG = logging.getLogger("blockchain_history_discovery")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(value: Any) -> str:
    return str(value if value is not None else "").strip()


def json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_existing_candidate_ids(output_dir: Path) -> Set[str]:
    ids: Set[str] = set()
    for filename in ("documents.json", "metadata_records.json"):
        path = output_dir / filename
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOG.warning("Could not read %s: %s", path, exc)
            continue
        records = data if isinstance(data, list) else []
        for item in records:
            if isinstance(item, dict):
                value = norm(item.get("document_id", item.get("documentId")))
                if value:
                    ids.add(value)
    return ids


def get_event_abi(contract: Any, event_name: str) -> Optional[Any]:
    try:
        return getattr(contract.events, event_name)
    except Exception:
        return None


def event_topic_hex(event_class: Any) -> Optional[str]:
    try:
        return norm(event_class().topic).lower()
    except Exception:
        try:
            return norm(event_class()._get_event_abi().get("signature")).lower()
        except Exception:
            return None


def decode_transaction_input(contract: Any, tx: Any) -> Dict[str, Any]:
    """Return decoded function name and arguments, if ABI decoding succeeds."""
    try:
        input_data = tx.get("input", tx.get("data", "0x"))
        function, args = contract.decode_function_input(input_data)
        return {
            "function_name": getattr(function, "fn_name", None)
            or getattr(function, "function_identifier", None)
            or str(function),
            "arguments": dict(args),
            "decode_status": "DECODED",
        }
    except Exception as exc:
        return {
            "function_name": None,
            "arguments": {},
            "decode_status": "FAILED",
            "decode_error": f"{type(exc).__name__}: {exc}",
        }


def choose_arg(args: Dict[str, Any], *keys: str) -> Optional[str]:
    normalized = {str(key).replace("_", "").lower(): value for key, value in args.items()}
    for key in keys:
        value = normalized.get(key.replace("_", "").lower())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def discover_events(
    blockchain: Any,
    from_block: int,
    to_block: int,
    chunk_size: int,
    event_name: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Query matching events in bounded ranges. Returns (event records, errors,
    event metadata). Uses contract.events event ABI if present.
    """
    w3 = blockchain.w3
    contract = blockchain.contract
    event_class = get_event_abi(contract, event_name)
    if event_class is None:
        raise RuntimeError(
            f"Event '{event_name}' is not available in the loaded contract ABI. "
            "Check the artifact ABI and the event name in UniversityRegistry.sol."
        )

    topic0 = event_topic_hex(event_class)
    if not topic0 or not topic0.startswith("0x"):
        raise RuntimeError(
            f"Could not determine event topic for '{event_name}' from the loaded ABI."
        )

    event_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    metadata = [{
        "event_name": event_name,
        "topic0": topic0,
        "from_block": from_block,
        "to_block": to_block,
        "chunk_size": chunk_size,
    }]

    start = from_block
    while start <= to_block:
        end = min(start + chunk_size - 1, to_block)
        try:
            logs = w3.eth.get_logs({
                "fromBlock": start,
                "toBlock": end,
                "address": contract.address,
                "topics": [topic0],
            })
            for log in logs:
                event_records.append({
                    "block_number": int(log["blockNumber"]),
                    "transaction_hash": log["transactionHash"].hex(),
                    "transaction_index": int(log.get("transactionIndex", 0)),
                    "log_index": int(log.get("logIndex", 0)),
                    "topics": [topic.hex() for topic in log.get("topics", [])],
                    "data": log.get("data", "0x").hex()
                    if hasattr(log.get("data", "0x"), "hex")
                    else str(log.get("data", "0x")),
                })
            print(
                f"Scanned blocks {start:,}-{end:,}: {len(logs)} matching events",
                flush=True,
            )
        except Exception as exc:
            errors.append({
                "from_block": start,
                "to_block": end,
                "error": f"{type(exc).__name__}: {exc}",
            })
            LOG.warning(
                "Event scan failed for blocks %s-%s: %s",
                start, end, exc,
            )
            # Do not silently skip a failed range. Continue so other ranges
            # can still be inspected, but record that discovery is incomplete.
        start = end + 1

    return event_records, errors, metadata


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover UniversityRegistry document IDs from historical events."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "blockchain_snapshot_dataset",
        help="Snapshot output directory (default: ./blockchain_snapshot_dataset).",
    )
    parser.add_argument(
        "--from-block",
        type=int,
        default=0,
        help="First block to scan (default: 0).",
    )
    parser.add_argument(
        "--to-block",
        type=int,
        default=None,
        help="Last block to scan (default: latest block).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Block range size per RPC query (default: 1000).",
    )
    parser.add_argument(
        "--event-name",
        default="DocumentVersionCreated",
        help="Contract event name (default: DocumentVersionCreated).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Optional pause in seconds between RPC chunks.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, "INFO"),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    if args.from_block < 0:
        parser.error("--from-block must be >= 0")
    if args.chunk_size < 1:
        parser.error("--chunk-size must be >= 1")
    if args.sleep < 0:
        parser.error("--sleep must be >= 0")

    output = args.output.resolve()
    print("Connecting to blockchain through UniversityBlockchainClient...")
    try:
        blockchain = UniversityBlockchainClient()
        latest_block = int(blockchain.w3.eth.block_number)
        chain_id = int(blockchain.w3.eth.chain_id)
        contract_address = norm(blockchain.contract.address)
    except Exception as exc:
        LOG.error("Could not initialize/read blockchain client: %s: %s", type(exc).__name__, exc)
        return 2

    end_block = latest_block if args.to_block is None else args.to_block
    if end_block > latest_block:
        parser.error(
            f"--to-block {end_block} is above provider latest block {latest_block}"
        )
    if args.from_block > end_block:
        parser.error("--from-block must be <= --to-block/latest block")

    print(
        f"Connected: chain_id={chain_id}, contract={contract_address}, "
        f"block_range={args.from_block:,}-{end_block:,}"
    )
    try:
        event_records, scan_errors, event_metadata = discover_events(
            blockchain=blockchain,
            from_block=args.from_block,
            to_block=end_block,
            chunk_size=args.chunk_size,
            event_name=args.event_name,
        )
    except Exception as exc:
        LOG.error("Could not start event discovery: %s", exc)
        return 2

    # De-duplicate transactions because a transaction can emit more than one
    # version event.
    tx_hashes = sorted({item["transaction_hash"] for item in event_records})
    tx_details: Dict[str, Dict[str, Any]] = {}
    document_evidence: Dict[str, Dict[str, Any]] = {}
    decode_failures: List[Dict[str, Any]] = []

    for index, tx_hash in enumerate(tx_hashes, start=1):
        print(f"[{index:>4}/{len(tx_hashes)}] Decoding transaction {tx_hash}", flush=True)
        try:
            tx = blockchain.w3.eth.get_transaction(tx_hash)
            decoded = decode_transaction_input(blockchain.contract, tx)
            tx_details[tx_hash] = {
                "transaction_hash": tx_hash,
                "block_number": int(tx.get("blockNumber") or 0),
                **decoded,
            }
            args_dict = decoded.get("arguments", {})
            document_id = choose_arg(
                args_dict, "documentId", "_documentId", "docId", "_docId"
            )
            student_id = choose_arg(args_dict, "studentId", "_studentId")
            document_type = choose_arg(
                args_dict, "documentType", "_documentType", "docType", "_docType"
            )

            if document_id:
                entry = document_evidence.setdefault(document_id, {
                    "document_id": document_id,
                    "transaction_hashes": [],
                    "transaction_count": 0,
                    "student_ids_seen": [],
                    "document_types_seen": [],
                    "functions_seen": [],
                    "first_seen_block": None,
                    "last_seen_block": None,
                })
                entry["transaction_hashes"].append(tx_hash)
                entry["transaction_count"] += 1
                if student_id and student_id not in entry["student_ids_seen"]:
                    entry["student_ids_seen"].append(student_id)
                if document_type and document_type not in entry["document_types_seen"]:
                    entry["document_types_seen"].append(document_type)
                function_name = norm(decoded.get("function_name"))
                if function_name and function_name not in entry["functions_seen"]:
                    entry["functions_seen"].append(function_name)
                block_no = int(tx.get("blockNumber") or 0)
                if entry["first_seen_block"] is None or block_no < entry["first_seen_block"]:
                    entry["first_seen_block"] = block_no
                if entry["last_seen_block"] is None or block_no > entry["last_seen_block"]:
                    entry["last_seen_block"] = block_no
            else:
                decode_failures.append({
                    "transaction_hash": tx_hash,
                    "block_number": int(tx.get("blockNumber") or 0),
                    "function_name": decoded.get("function_name"),
                    "decode_status": decoded.get("decode_status"),
                    "reason": (
                        "Decoded transaction did not expose a recognized document ID argument."
                        if decoded.get("decode_status") == "DECODED"
                        else decoded.get("decode_error", "Transaction input decode failed.")
                    ),
                })
        except Exception as exc:
            decode_failures.append({
                "transaction_hash": tx_hash,
                "reason": f"Transaction fetch failed: {type(exc).__name__}: {exc}",
            })

    # Verify each historically recovered ID against the contract's current state.
    verified_current: List[Dict[str, Any]] = []
    missing_current: List[Dict[str, Any]] = []
    current_read_errors: List[Dict[str, Any]] = []
    for index, document_id in enumerate(sorted(document_evidence), start=1):
        print(
            f"[{index:>4}/{len(document_evidence)}] Verifying current state for {document_id}",
            flush=True,
        )
        try:
            exists = bool(blockchain.document_exists(document_id))
            if not exists:
                missing_current.append({
                    **document_evidence[document_id],
                    "current_state": "NOT_FOUND_ON_CHAIN",
                    "note": "Historical transaction input mentioned this ID, but current contract existence check returned false.",
                })
                continue

            details = blockchain.get_document_details(document_id) or {}
            history_raw = blockchain.get_document_history(document_id) or []
            history = []
            for item in history_raw:
                if not isinstance(item, dict):
                    continue
                history.append({
                    "version": int(item.get("version", 0) or 0),
                    "previous_version": int(item.get("previous_version", 0) or 0),
                    "ipfs_cid": norm(item.get("ipfs_cid", item.get("ipfsCID"))),
                    "content_hash": norm(item.get("content_hash", item.get("contentHash"))).lower(),
                    "approval_status": norm(item.get("approval_status", item.get("approvalStatus"))).upper(),
                    "approved_by": norm(item.get("approved_by", item.get("approvedBy"))),
                    "timestamp": int(item.get("timestamp", 0) or 0),
                })
            history.sort(key=lambda x: x["version"])
            verified_current.append({
                **document_evidence[document_id],
                "current_state": "READ",
                "student_id": norm(details.get("student_id", details.get("studentId"))),
                "document_type": norm(details.get("document_type", details.get("documentType"))),
                "latest_version": int(details.get("latest_version", details.get("latestVersion", 0)) or 0),
                "version_count": len(history),
                "versions": history,
            })
        except Exception as exc:
            current_read_errors.append({
                "document_id": document_id,
                "error": f"{type(exc).__name__}: {exc}",
                **document_evidence[document_id],
            })

    output.mkdir(parents=True, exist_ok=True)
    json_write(output / "historical_event_logs.json", event_records)
    json_write(output / "historical_transaction_decoding.json", list(tx_details.values()))
    json_write(output / "historical_document_id_evidence.json", {
        "document_ids_recovered": len(document_evidence),
        "records": [document_evidence[key] for key in sorted(document_evidence)],
    })
    json_write(output / "historical_discovery_current_records.json", verified_current)

    # Combine historical IDs with IDs in the previous snapshot, keeping candidate
    # provenance distinct from historical evidence.
    prior_ids = load_existing_candidate_ids(output)
    history_ids = set(document_evidence)
    combined = sorted(prior_ids | history_ids)
    json_write(output / "historical_discovery_manifest.json", {
        "created_at_utc": utc_now(),
        "network": {
            "chain_id": chain_id,
            "contract_address": contract_address,
            "latest_block_number": latest_block,
        },
        "scan": {
            "event_name": args.event_name,
            "from_block": args.from_block,
            "to_block": end_block,
            "chunk_size": args.chunk_size,
            "event_log_count": len(event_records),
            "unique_transaction_count": len(tx_hashes),
            "transactions_decoded": len(tx_details),
            "document_ids_recovered_from_transaction_inputs": len(document_evidence),
            "ids_found_in_prior_snapshot_files": len(prior_ids),
            "combined_candidate_id_count": len(combined),
            "current_records_read": len(verified_current),
            "historical_ids_not_found_in_current_contract": len(missing_current),
            "current_read_errors": len(current_read_errors),
            "event_scan_errors": scan_errors,
            "transaction_decode_failures": decode_failures,
            "current_read_errors_details": current_read_errors,
            "historical_ids_not_found_current_details": missing_current,
            "completeness_established": False,
            "completeness_note": (
                "This is an event-based discovery pass. It is not guaranteed to find "
                "documents created before the event was emitted, through another function, "
                "or outside the scanned block range. Review scan errors and decode failures."
            ),
        },
        "safety": {
            "read_only": True,
            "transactions_submitted": 0,
            "ipfs_uploads": 0,
        },
    })

    print("\n" + "=" * 72)
    print("HISTORICAL DOCUMENT DISCOVERY SUMMARY")
    print("=" * 72)
    print(f"Block range scanned:                  {args.from_block:,}-{end_block:,}")
    print(f"Event logs found:                     {len(event_records)}")
    print(f"Unique event transaction hashes:      {len(tx_hashes)}")
    print(f"Transactions fetched/decoded:         {len(tx_details)}")
    print(f"Document IDs recovered from inputs:   {len(document_evidence)}")
    print(f"Recovered IDs currently readable:     {len(verified_current)}")
    print(f"Historical IDs not currently found:   {len(missing_current)}")
    print(f"Event scan range errors:               {len(scan_errors)}")
    print(f"Transaction decode/fetch failures:     {len(decode_failures)}")
    print(f"Output directory:                     {output}")
    print("Blockchain writes:                     0")
    print("IPFS uploads:                          0")
    print("Complete inventory established:       NO")

    return 2 if scan_errors or current_read_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
