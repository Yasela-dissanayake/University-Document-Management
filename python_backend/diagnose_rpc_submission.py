# diagnose_rpc_submission.py
"""
Diagnose eth_sendRawTransaction against the Tenderly Virtual Sepolia RPC.

This script:
1. Builds the same createDocumentVersion transaction.
2. Estimates gas.
3. Builds and signs the transaction.
4. Sends the raw transaction DIRECTLY using requests.
5. Prints the HTTP status and complete RPC response body.

It does NOT retry and does NOT submit the transaction more than once.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from web3 import Web3

from blockchain_client import UniversityBlockchainClient, _hex_to_bytes32


load_dotenv()


def print_section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    print_section("TENDERLY RAW TRANSACTION SUBMISSION DIAGNOSTIC")

    # ------------------------------------------------------------------
    # 1. Initialize existing blockchain client
    # ------------------------------------------------------------------

    client = UniversityBlockchainClient()

    rpc_url = os.getenv("WEB3_PROVIDER")
    contract_address = os.getenv("CONTRACT_ADDRESS")

    if not rpc_url:
        print("❌ WEB3_PROVIDER is not configured.")
        return 1

    if not contract_address:
        print("❌ CONTRACT_ADDRESS is not configured.")
        return 1

    print(f"RPC URL      : {rpc_url}")
    print(f"Contract     : {client.contract.address}")
    print(f"Account      : {client.account.address}")
    print(f"Chain ID     : {client.chain_id}")

    # ------------------------------------------------------------------
    # 2. Verify basic RPC connectivity
    # ------------------------------------------------------------------

    print_section("1. BASIC RPC CHECKS")

    try:
        block_number = client.w3.eth.block_number
        balance = client.w3.eth.get_balance(client.account.address)
        nonce_latest = client.w3.eth.get_transaction_count(
            client.account.address,
            "latest",
        )
        nonce_pending = client.w3.eth.get_transaction_count(
            client.account.address,
            "pending",
        )
        gas_price = client.w3.eth.gas_price

        print(f"Block number : {block_number}")
        print(f"Balance      : {balance} wei")
        print(f"Latest nonce : {nonce_latest}")
        print(f"Pending nonce: {nonce_pending}")
        print(f"Gas price    : {gas_price}")

    except Exception as exc:
        print("❌ Basic RPC check failed.")
        print(type(exc).__name__, exc)
        return 1

    # ------------------------------------------------------------------
    # 3. Build the exact document-version transaction
    # ------------------------------------------------------------------

    print_section("2. BUILD TRANSACTION")

    document_id = "STD001-TRANSCRIPT"
    student_id = "STD001"
    document_type = "Transcript"

    # Use a NEW CID if you want this to represent a new document version.
    # This diagnostic is intended to test RPC submission, so the values
    # should correspond to a valid createDocumentVersion call.
    ipfs_cid = "QmYcWPSfxKHycwg6fBcTMEftGkf2FAtFX4MFQRLjobk6aQ"

    content_hash = (
        "ec070641adb635fc119d620b6a80d3d36da47211e5011de63fbf38a551f39f61"
    )

    approval_status = "APPROVED"
    approved_by = "Registrar"

    print(f"Document ID  : {document_id}")
    print(f"Student ID   : {student_id}")
    print(f"Document type: {document_type}")
    print(f"IPFS CID     : {ipfs_cid}")
    print(f"Content hash : {content_hash}")
    print(f"Status       : {approval_status}")
    print(f"Approved by  : {approved_by}")

    try:
        fn = client.contract.functions.createDocumentVersion(
            document_id,
            student_id,
            document_type,
            ipfs_cid,
            _hex_to_bytes32(content_hash),
            approval_status,
            approved_by,
        )

        print("✅ Contract function constructed.")

    except Exception as exc:
        print("❌ Failed to construct contract function.")
        print(type(exc).__name__, exc)
        return 1

    # ------------------------------------------------------------------
    # 4. Estimate gas WITHOUT sending
    # ------------------------------------------------------------------

    print_section("3. GAS ESTIMATION")

    try:
        gas_estimate = fn.estimate_gas(
            {
                "from": client.account.address,
                "value": 0,
            }
        )

        print(f"Gas estimate: {gas_estimate}")

    except Exception as exc:
        print("❌ Gas estimation failed.")
        print(type(exc).__name__, exc)
        return 1

    # Give the transaction the same buffer used by the client.
    gas_limit = int(gas_estimate * 1.5)

    print(f"Gas limit   : {gas_limit}")

    # ------------------------------------------------------------------
    # 5. Build transaction
    # ------------------------------------------------------------------

    print_section("4. BUILD SIGNED TRANSACTION")

    try:
        nonce = client.w3.eth.get_transaction_count(
            client.account.address,
            "pending",
        )

        gas_price = client.w3.eth.gas_price

        tx: Dict[str, Any] = fn.build_transaction(
            {
                "from": client.account.address,
                "chainId": client.chain_id,
                "nonce": nonce,
                "gas": gas_limit,
                "gasPrice": gas_price,
                "value": 0,
            }
        )

        print(f"Nonce       : {tx['nonce']}")
        print(f"Gas         : {tx['gas']}")
        print(f"Gas price   : {tx['gasPrice']}")
        print(f"Chain ID    : {tx['chainId']}")
        print(f"To          : {tx['to']}")
        print(f"Value       : {tx['value']}")
        print(f"Data length : {len(tx['data'])}")

    except Exception as exc:
        print("❌ Transaction construction failed.")
        print(type(exc).__name__, exc)
        return 1

    # ------------------------------------------------------------------
    # 6. Sign transaction
    # ------------------------------------------------------------------

    print_section("5. SIGN TRANSACTION")

    try:
        signed = client.account.sign_transaction(tx)

        raw = getattr(signed, "raw_transaction", None)

        if raw is None:
            raw = getattr(signed, "rawTransaction", None)

        if raw is None:
            print("❌ Could not extract signed raw transaction.")
            return 1

        raw_hex = Web3.to_hex(raw)

        print("✅ Transaction signed.")
        print(f"Raw transaction bytes: {len(raw)}")
        print(f"Raw transaction length: {len(raw_hex)} hex characters")
        print(f"Raw transaction prefix: {raw_hex[:80]}...")

    except Exception as exc:
        print("❌ Transaction signing failed.")
        print(type(exc).__name__, exc)
        return 1

    # ------------------------------------------------------------------
    # 7. Calculate local transaction hash
    # ------------------------------------------------------------------

    print_section("6. LOCAL TRANSACTION HASH")

    try:
        local_tx_hash = Web3.keccak(raw)

        print(f"Local tx hash: {local_tx_hash.hex()}")

    except Exception as exc:
        print("❌ Could not calculate transaction hash.")
        print(type(exc).__name__, exc)
        return 1

    # ------------------------------------------------------------------
    # 8. DIRECT JSON-RPC eth_sendRawTransaction
    #
    # IMPORTANT:
    # We deliberately bypass Web3's send_raw_transaction() here.
    # This allows us to see Tenderly's actual HTTP response body.
    # ------------------------------------------------------------------

    print_section("7. DIRECT TENDERLY RPC SUBMISSION")

    payload = {
        "jsonrpc": "2.0",
        "method": "eth_sendRawTransaction",
        "params": [raw_hex],
        "id": 1,
    }

    print("Sending eth_sendRawTransaction directly...")
    print("This is the ONLY point where this diagnostic submits the transaction.")

    try:
        response = requests.post(
            rpc_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30,
        )

        print()
        print(f"HTTP status : {response.status_code}")
        print(f"HTTP reason : {response.reason}")

        print()
        print("Response headers:")
        for key, value in response.headers.items():
            print(f"  {key}: {value}")

        print()
        print("Response body:")
        print("-" * 70)

        try:
            response_json = response.json()

            print(
                json.dumps(
                    response_json,
                    indent=2,
                    ensure_ascii=False,
                )
            )

        except ValueError:
            print(response.text)

        print("-" * 70)

        # --------------------------------------------------------------
        # Interpret result
        # --------------------------------------------------------------

        if response.status_code == 200:
            try:
                response_json = response.json()
            except ValueError:
                response_json = {}

            if "result" in response_json:
                print()
                print("✅ TENDERLY ACCEPTED THE RAW TRANSACTION.")
                print(f"Transaction hash: {response_json['result']}")
                print()
                print(
                    "The previous Web3 error was therefore likely caused by "
                    "the way the request was being handled rather than by "
                    "the transaction itself."
                )
                return 0

            if "error" in response_json:
                print()
                print("❌ Tenderly returned a JSON-RPC error.")
                print()
                print("This is the important part to diagnose next.")
                return 2

        elif response.status_code == 403:
            print()
            print("❌ TENDERLY RETURNED HTTP 403 FOR eth_sendRawTransaction.")
            print()
            print(
                "This strongly indicates an RPC/provider-level rejection."
            )
            print(
                "The response body above should tell us why."
            )
            return 3

        else:
            print()
            print(
                f"❌ Tenderly returned unexpected HTTP status "
                f"{response.status_code}."
            )
            return 4

    except requests.exceptions.Timeout:
        print("❌ RPC request timed out.")
        return 5

    except requests.exceptions.ConnectionError as exc:
        print("❌ Could not connect to RPC endpoint.")
        print(type(exc).__name__, exc)
        return 6

    except requests.exceptions.RequestException as exc:
        print("❌ HTTP request failed.")
        print(type(exc).__name__, exc)
        return 7

    except Exception as exc:
        print("❌ Unexpected error during direct RPC submission.")
        print(type(exc).__name__, exc)
        return 8


if __name__ == "__main__":
    sys.exit(main())