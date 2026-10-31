#!/usr/bin/env python3
"""Read-only discovery of real UniversityRegistry records on Tenderly.

Run from repository root:
    python python_backend/evaluation/discover_live_records.py

Optional:
    python python_backend/evaluation/discover_live_records.py \
      --from-block 0 --chunk-size 1000 \
      --document-id TX-STD001-001 \
      --output python_backend/evaluation/live_deployment_report.json

No transactions are submitted. No document is decrypted or printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    import requests
except ImportError:
    requests = None

from web3 import HTTPProvider, Web3


ROOT = Path(__file__).resolve().parents[2]

ABI = [
    {"type": "function", "name": "studentCount", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "studentIds", "stateMutability": "view",
     "inputs": [{"name": "", "type": "uint256"}],
     "outputs": [{"name": "", "type": "string"}]},
    {"type": "function", "name": "documentExists", "stateMutability": "view",
     "inputs": [{"name": "_documentId", "type": "string"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "name": "getDocumentDetails", "stateMutability": "view",
     "inputs": [{"name": "_documentId", "type": "string"}],
     "outputs": [
         {"name": "documentId", "type": "string"},
         {"name": "studentId", "type": "string"},
         {"name": "documentType", "type": "string"},
         {"name": "latestVersion", "type": "uint256"},
         {"name": "exists", "type": "bool"}]},
    {"type": "function", "name": "getDocumentVersion", "stateMutability": "view",
     "inputs": [
         {"name": "_documentId", "type": "string"},
         {"name": "_version", "type": "uint256"}],
     "outputs": [
         {"name": "version", "type": "uint256"},
         {"name": "previousVersion", "type": "uint256"},
         {"name": "ipfsCID", "type": "string"},
         {"name": "contentHash", "type": "bytes32"},
         {"name": "approvalStatus", "type": "string"},
         {"name": "approvedBy", "type": "string"},
         {"name": "timestamp", "type": "uint256"}]},
    {"type": "function", "name": "createDocumentVersion",
     "stateMutability": "nonpayable",
     "inputs": [
         {"name": "_documentId", "type": "string"},
         {"name": "_studentId", "type": "string"},
         {"name": "_documentType", "type": "string"},
         {"name": "_ipfsCID", "type": "string"},
         {"name": "_contentHash", "type": "bytes32"},
         {"name": "_approvalStatus", "type": "string"},
         {"name": "_approvedBy", "type": "string"}],
     "outputs": [{"name": "newVersion", "type": "uint256"}]},
    {"type": "event", "name": "DocumentVersionCreated", "anonymous": False,
     "inputs": [
         {"indexed": True, "name": "documentId", "type": "string"},
         {"indexed": True, "name": "studentId", "type": "string"},
         {"indexed": False, "name": "version", "type": "uint256"},
         {"indexed": False, "name": "previousVersion", "type": "uint256"},
         {"indexed": False, "name": "ipfsCID", "type": "string"},
         {"indexed": False, "name": "contentHash", "type": "bytes32"},
         {"indexed": False, "name": "approvalStatus", "type": "string"},
         {"indexed": False, "name": "approvedBy", "type": "string"},
         {"indexed": False, "name": "timestamp", "type": "uint256"}]},
]


def load_environment() -> None:
    if load_dotenv is None:
        raise RuntimeError("Install python-dotenv: pip install python-dotenv")
    for env_path in (ROOT / ".env", ROOT / "python_backend" / ".env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)


def normalize_version(raw: Any) -> Dict[str, Any]:
    values = list(raw)
    content_hash = values[3]
    return {
        "version": int(values[0]),
        "previous_version": int(values[1]),
        "ipfs_cid": str(values[2]),
        "content_hash": "0x" + bytes(content_hash).hex(),
        "approval_status": str(values[4]),
        "approved_by": str(values[5]),
        "timestamp": int(values[6]),
    }


def check_ipfs(api_url: str, cid: str, timeout: float) -> Dict[str, Any]:
    if not cid:
        return {"checked": False, "available": False, "reason": "empty_cid"}
    if requests is None:
        return {"checked": False, "available": None,
                "reason": "requests package is not installed"}
    try:
        response = requests.post(
            api_url.rstrip("/") + "/api/v0/cat",
            params={"arg": cid},
            timeout=timeout,
            stream=True,
        )
        status = response.status_code
        sample = next(response.iter_content(chunk_size=512), b"") if status == 200 else b""
        response.close()
        return {
            "checked": True,
            "available": status == 200 and bool(sample),
            "http_status": status,
            "note": "Only a small byte sample was read; no decryption was performed.",
        }
    except Exception as exc:
        return {"checked": True, "available": False,
                "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only discovery of records in the configured UniversityRegistry."
    )
    parser.add_argument("--from-block", type=int,
                        default=int(os.getenv("DEPLOYMENT_BLOCK", "0") or 0))
    parser.add_argument("--to-block", type=int, default=None)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--document-id", action="append", default=[])
    parser.add_argument("--skip-ipfs-check", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "evaluation" / "live_deployment_report.json")
    args = parser.parse_args()

    if args.from_block < 0 or args.chunk_size < 1:
        parser.error("--from-block must be non-negative and --chunk-size positive")

    load_environment()
    rpc = os.getenv("WEB3_PROVIDER", "").strip()
    address_value = os.getenv("CONTRACT_ADDRESS", "").strip()
    ipfs_url = os.getenv("IPFS_API_URL", "http://127.0.0.1:5001").strip()
    if not rpc or not address_value:
        raise RuntimeError("WEB3_PROVIDER and CONTRACT_ADDRESS must be configured in .env")

    w3 = Web3(HTTPProvider(rpc, request_kwargs={"timeout": args.timeout}))
    if not w3.is_connected():
        raise RuntimeError("Tenderly RPC connection failed")
    address = Web3.to_checksum_address(address_value)
    contract = w3.eth.contract(address=address, abi=ABI)
    latest_block = int(w3.eth.block_number)
    end_block = latest_block if args.to_block is None else min(args.to_block, latest_block)

    report: Dict[str, Any] = {
        "network": {
            "chain_id": int(w3.eth.chain_id),
            "contract_address": address,
            "latest_block": latest_block,
            "scan_from_block": args.from_block,
            "scan_to_block": end_block,
        },
        "students": [],
        "documents": [],
        "warnings": [],
        "generated_at_unix": int(time.time()),
    }

    print("LIVE TENDERLY DEPLOYMENT DISCOVERY — READ ONLY")
    print(f"Chain ID: {w3.eth.chain_id}")
    print(f"Contract: {address}")
    print(f"Latest block: {latest_block}")
    print("No private key or transaction signing is used.\n")

    # Enumerate actual student registration IDs.
    try:
        count = int(contract.functions.studentCount().call())
        print(f"Registered students: {count}")
        for i in range(count):
            sid = str(contract.functions.studentIds(i).call()).strip()
            report["students"].append({"student_id": sid})
            print(f"  {sid}")
    except Exception as exc:
        report["warnings"].append(f"Student enumeration failed: {exc}")

    # Recover document IDs from the transaction calldata for each event.
    document_ids: Set[str] = {
        str(item).strip() for item in args.document_id if str(item).strip()
    }
    event_topic = Web3.keccak(
        text="DocumentVersionCreated(string,string,uint256,uint256,string,bytes32,string,string,uint256)"
    ).hex()
    if not event_topic.startswith("0x"):
        event_topic = "0x" + event_topic

    event_count = 0
    start = args.from_block
    print("\nScanning DocumentVersionCreated events...")
    while start <= end_block:
        stop = min(start + args.chunk_size - 1, end_block)
        try:
            logs = w3.eth.get_logs({
                "fromBlock": start,
                "toBlock": stop,
                "address": address,
                "topics": [event_topic],
            })
        except Exception as exc:
            report["warnings"].append(
                f"Log scan failed at blocks {start}-{stop}: {type(exc).__name__}: {exc}"
            )
            print(f"WARNING: event scan failed for {start}-{stop}: {exc}")
            print("Try a smaller --chunk-size or set --from-block to the deployment block.")
            break

        event_count += len(logs)
        for log in logs:
            try:
                tx = w3.eth.get_transaction(log["transactionHash"])
                function, decoded = contract.decode_function_input(tx["input"])
                if function.fn_name == "createDocumentVersion":
                    doc_id = str(decoded.get("_documentId", "")).strip()
                    if doc_id:
                        document_ids.add(doc_id)
            except Exception as exc:
                report["warnings"].append(
                    f"Could not decode transaction {log['transactionHash'].hex()}: {exc}"
                )
        print(f"  {start:,}-{stop:,}: {len(logs)} event(s), "
              f"{len(document_ids)} document ID(s) known")
        start = stop + 1

    report["document_version_event_count"] = event_count
    print(f"\nInspecting {len(document_ids)} document ID(s)...")

    for doc_id in sorted(document_ids):
        try:
            if not contract.functions.documentExists(doc_id).call():
                report["warnings"].append(f"Document does not exist: {doc_id}")
                continue
            raw_details = contract.functions.getDocumentDetails(doc_id).call()
            details = {
                "document_id": str(raw_details[0]),
                "student_id": str(raw_details[1]),
                "document_type": str(raw_details[2]),
                "latest_version": int(raw_details[3]),
                "exists": bool(raw_details[4]),
            }
            versions = []
            for number in range(1, details["latest_version"] + 1):
                version = normalize_version(
                    contract.functions.getDocumentVersion(doc_id, number).call()
                )
                if not args.skip_ipfs_check:
                    version["ipfs_check"] = check_ipfs(
                        ipfs_url, version["ipfs_cid"], args.timeout
                    )
                versions.append(version)
                ipfs_state = version.get("ipfs_check", {}).get("available", "not checked")
                print(f"  {doc_id} v{number}: {version['approval_status']} | "
                      f"CID {version['ipfs_cid']} | IPFS {ipfs_state}")

            approved = [
                v for v in versions
                if v["approval_status"].strip().upper() == "APPROVED"
            ]
            report["documents"].append({
                **details,
                "versions": versions,
                "latest_approved_version": (
                    max(approved, key=lambda item: item["version"]) if approved else None
                ),
            })
        except Exception as exc:
            report["warnings"].append(
                f"Could not inspect document {doc_id}: {type(exc).__name__}: {exc}"
            )

    all_versions = [
        version for document in report["documents"]
        for version in document["versions"]
    ]
    report["summary"] = {
        "registered_student_count": len(report["students"]),
        "discovered_document_count": len(report["documents"]),
        "document_version_count": len(all_versions),
        "approved_version_count": sum(
            v["approval_status"].strip().upper() == "APPROVED" for v in all_versions
        ),
        "ipfs_reachable_version_count": sum(
            v.get("ipfs_check", {}).get("available") is True for v in all_versions
        ),
        "warning_count": len(report["warnings"]),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nSUMMARY")
    print(json.dumps(report["summary"], indent=2))
    print(f"\nReport saved to: {args.output}")
    print("Read-only completed: no transactions, uploads, or decryption performed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"DISCOVERY FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
