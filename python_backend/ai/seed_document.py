"""
Phase 3 document seeding and validation.

This script is intentionally idempotent for the current Phase 3 test document.

Current blockchain state:
    TX-98765 -> latest version V4

Because V4 has already been created successfully, running this script will NOT
create V5. It will validate the existing latest approved version and verify the
IPFS + ACL document.

If the document is still at V3, the script can create V4 using the deterministic
test document below.

Run from the project root:

    python python_backend/ai/seed_document.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Make project-root imports reliable when this file is executed directly.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend.ipfs_client import UniversityIPFSClient
from python_backend import acl


# ===========================================================================
# TEST CONFIGURATION
# ===========================================================================

DOCUMENT_ID = "TX-98765"
STUDENT_ID = "STD001"
DOCUMENT_TYPE = "Transcript"

APPROVAL_STATUS = "APPROVED"
APPROVED_BY = "Registrar"

# The Phase 3 test user has the role principal role:ADMIN.
TEST_USER = {
    "username": "phase3_test_admin",
    "role": "ADMIN",
    "student_id": STUDENT_ID,
}

# This is the test document used if V4 has not yet been created.
# Keep the structure deterministic so the plaintext hash is reproducible.
TEST_DOCUMENT: Dict[str, Any] = {
    "document_id": DOCUMENT_ID,
    "student_id": STUDENT_ID,
    "document_type": DOCUMENT_TYPE,
    "semester": "Semester 5",
    "program": "Computer Engineering",
    "courses": [
        {
            "code": "CO521",
            "name": "Advanced Computer Engineering",
            "grade": "A",
        },
        {
            "code": "CO523",
            "name": "Database Systems",
            "grade": "A-",
        },
        {
            "code": "CO525",
            "name": "Software Engineering",
            "grade": "B+",
        },
    ],
    "gpa": 3.67,
    "status": "Approved",
}


# ===========================================================================
# HELPERS
# ===========================================================================

def section(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def normalize_hash(value: Any) -> str:
    """
    Normalize a SHA-256/bytes32 hash for comparison.

    Ethereum/web3 commonly represents bytes32 values as:
        0x<64 hex characters>

    ACL/plaintext hashing uses:
        <64 hex characters>
    """
    if value is None:
        return ""

    text = str(value).strip().lower()

    if text.startswith("0x"):
        text = text[2:]

    return text


def canonical_document_bytes(document: Dict[str, Any]) -> bytes:
    """
    Match the canonical JSON representation used by the ACL layer.

    acl.py uses sorted keys and compact separators when calculating the
    plaintext hash.
    """
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def calculate_plaintext_hash(document: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_document_bytes(document)).hexdigest()


def print_json(data: Any) -> None:
    print(
        json.dumps(
            data,
            indent=2,
            sort_keys=False,
            default=str,
        )
    )


def extract_document_payload(
    ipfs_client: UniversityIPFSClient,
    cid: str,
) -> Dict[str, Any]:
    """
    Retrieve the encrypted ACL envelope from IPFS.

    The outer IPFS encryption is handled by UniversityIPFSClient.
    The returned object is then passed to acl.decrypt_for_user() for the
    document-level ACL decryption.
    """
    payload = ipfs_client.retrieve_academic_document(cid)

    if not isinstance(payload, dict):
        raise RuntimeError(
            "IPFS payload is not a JSON object."
        )

    return payload


def decrypt_acl_document(
    ipfs_client: UniversityIPFSClient,
    cid: str,
    user: Dict[str, Any],
    student_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Retrieve the encrypted payload from IPFS and decrypt it using the ACL.

    Returns:
        plaintext_document, encrypted_payload
    """
    encrypted_payload = extract_document_payload(ipfs_client, cid)

    if encrypted_payload.get("enc") != "fernet-v1":
        raise RuntimeError(
            "Expected an ACL-encrypted fernet-v1 payload, but received "
            f"{encrypted_payload.get('enc')!r}."
        )

    plaintext_document = acl.decrypt_for_user(
        user,
        student_id,
        cid,
        encrypted_payload,
    )

    if not isinstance(plaintext_document, dict):
        raise RuntimeError(
            "ACL decryption did not return a JSON object."
        )

    return plaintext_document, encrypted_payload


def get_latest_blockchain_details(
    blockchain_client: UniversityBlockchainClient,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve logical document metadata from the blockchain.
    """
    try:
        return blockchain_client.get_document_details(DOCUMENT_ID)
    except AttributeError:
        # Compatibility fallback for clients that expose document_exists and
        # other version functions but not get_document_details.
        exists = blockchain_client.document_exists(DOCUMENT_ID)
        if not exists:
            return None
        raise RuntimeError(
            "The current blockchain_client does not expose "
            "get_document_details()."
        )


def resolve_authoritative_version(
    blockchain_client: UniversityBlockchainClient,
) -> Dict[str, Any]:
    """
    Resolve the authoritative approved version.

    The blockchain remains authoritative for:
        - version
        - previous_version
        - CID
        - content_hash
        - approval status
        - approver
        - timestamp
    """
    if not hasattr(blockchain_client, "resolve_authoritative_document"):
        raise RuntimeError(
            "resolve_authoritative_document() is not available in the "
            "current blockchain_client.py. Use the Phase 3 blockchain "
            "client version before running this script."
        )

    resolved = blockchain_client.resolve_authoritative_document(
        DOCUMENT_ID,
        expected_student_id=STUDENT_ID,
        expected_document_type=DOCUMENT_TYPE,
        approved_only=True,
    )

    if not resolved:
        raise RuntimeError(
            f"Could not resolve an approved blockchain version for "
            f"{DOCUMENT_ID}."
        )

    return resolved


def verify_blockchain_metadata(
    blockchain_client: UniversityBlockchainClient,
    resolved: Dict[str, Any],
    expected_hash: Optional[str] = None,
) -> None:
    """
    Validate the authoritative blockchain record.
    """
    if resolved.get("document_id") != DOCUMENT_ID:
        raise RuntimeError(
            "Blockchain document ID mismatch."
        )

    if resolved.get("student_id") != STUDENT_ID:
        raise RuntimeError(
            "Blockchain student ID mismatch."
        )

    if resolved.get("document_type") != DOCUMENT_TYPE:
        raise RuntimeError(
            "Blockchain document type mismatch."
        )

    version = int(resolved.get("resolved_version", 0))
    latest_version = int(resolved.get("latest_version", 0))
    previous_version = int(resolved.get("previous_version", 0))

    if version <= 0:
        raise RuntimeError(
            f"Invalid blockchain version: V{version}"
        )

    if latest_version != version:
        raise RuntimeError(
            f"Latest version mismatch. "
            f"Expected resolved V{version}, latest is V{latest_version}."
        )

    if version > 1 and previous_version != version - 1:
        raise RuntimeError(
            f"Invalid version chain. V{version} should point to "
            f"V{version - 1}, but points to V{previous_version}."
        )

    if version == 1 and previous_version != 0:
        raise RuntimeError(
            "V1 must have previous_version = 0."
        )

    if str(resolved.get("approval_status", "")).upper() != APPROVAL_STATUS:
        raise RuntimeError(
            "Resolved version is not APPROVED."
        )

    if not resolved.get("ipfs_cid"):
        raise RuntimeError(
            "Blockchain resolved an empty IPFS CID."
        )

    blockchain_hash = normalize_hash(
        resolved.get("content_hash")
    )

    if expected_hash is not None:
        expected = normalize_hash(expected_hash)

        if blockchain_hash != expected:
            raise RuntimeError(
                "Blockchain content hash does not match the plaintext hash.\n"
                f"Expected: {expected}\n"
                f"Found:    {blockchain_hash}"
            )

    print("✅ Blockchain metadata validation passed")
    print(f"   Document       : {DOCUMENT_ID}")
    print(f"   Student        : {STUDENT_ID}")
    print(f"   Latest version : V{latest_version}")
    print(f"   Resolved       : V{version}")
    print(f"   Previous       : V{previous_version}")
    print(f"   CID            : {resolved.get('ipfs_cid')}")
    print(f"   Approval       : {resolved.get('approval_status')}")
    print(f"   Approved by    : {resolved.get('approved_by')}")
    print(f"   Content hash   : {blockchain_hash}")


def verify_history(
    blockchain_client: UniversityBlockchainClient,
    expected_latest_version: int,
) -> None:
    """
    Verify the complete immutable version chain.
    """
    if not hasattr(blockchain_client, "get_document_history"):
        print(
            "⚠️ get_document_history() is not available; "
            "skipping full history validation."
        )
        return

    history = blockchain_client.get_document_history(DOCUMENT_ID)

    if history is None:
        raise RuntimeError(
            "Blockchain returned no document history."
        )

    if not isinstance(history, list):
        history = list(history)

    if len(history) != expected_latest_version:
        raise RuntimeError(
            f"History length mismatch. Expected {expected_latest_version}, "
            f"found {len(history)}."
        )

    if not history:
        raise RuntimeError(
            "Document history is empty."
        )

    normalized_history = []

    for item in history:
        if isinstance(item, dict):
            normalized_history.append(item)
        else:
            # The Phase 3 blockchain client normally returns dictionaries.
            # Keep a useful error instead of silently guessing tuple indexes.
            raise RuntimeError(
                "Unexpected document history entry format: "
                f"{type(item).__name__}"
            )

    normalized_history.sort(
        key=lambda item: int(item.get("version", 0))
    )

    for index, item in enumerate(normalized_history, start=1):
        version = int(item.get("version", 0))
        previous = int(item.get("previous_version", 0))

        if version != index:
            raise RuntimeError(
                f"History contains unexpected version V{version}; "
                f"expected V{index}."
            )

        expected_previous = 0 if version == 1 else version - 1

        if previous != expected_previous:
            raise RuntimeError(
                f"V{version} has previous_version={previous}; "
                f"expected {expected_previous}."
            )

    print("✅ Immutable version-chain validation passed")
    print(
        "   Chain: "
        + " → ".join(
            f"V{item['version']}"
            for item in normalized_history
        )
    )


# ===========================================================================
# CREATE V4 ONLY WHEN NECESSARY
# ===========================================================================

def create_new_version_if_needed(
    blockchain_client: UniversityBlockchainClient,
    ipfs_client: UniversityIPFSClient,
    existing_details: Dict[str, Any],
) -> Tuple[str, str, int]:
    """
    Create the next version only when the blockchain is currently at V3.

    If V4 already exists, this function does nothing and returns the existing
    authoritative CID/hash by resolving the blockchain record.
    """
    current_version = int(
        existing_details.get("latest_version", 0)
    )

    print(f"Existing latest version: V{current_version}")

    if current_version >= 4:
        print(
            "\nℹ️ V4 already exists. "
            "No new blockchain version will be created."
        )

        resolved = resolve_authoritative_version(
            blockchain_client
        )

        return (
            str(resolved["ipfs_cid"]),
            normalize_hash(resolved["content_hash"]),
            int(resolved["resolved_version"]),
        )

    if current_version != 3:
        raise RuntimeError(
            f"Expected the current test document to be at V3 or later, "
            f"but found V{current_version}."
        )

    section("2. CALCULATE PLAINTEXT CONTENT HASH")

    plaintext_hash = calculate_plaintext_hash(TEST_DOCUMENT)

    print("Plaintext content hash:")
    print(plaintext_hash)

    section("3. ENCRYPT AND STORE DOCUMENT IN IPFS")

    principals = [
        "role:ADMIN",
        f"student:{STUDENT_ID}",
    ]

    result = acl.encrypt_and_store(
        TEST_DOCUMENT,
        principals,
        ipfs_client,
    )

    if not isinstance(result, tuple) or len(result) != 2:
        raise RuntimeError(
            "acl.encrypt_and_store() must return "
            "(cid, content_hash)."
        )

    cid, acl_content_hash = result

    cid = str(cid)
    acl_content_hash = normalize_hash(acl_content_hash)

    print("REAL IPFS CID:")
    print(cid)

    print("\nHash returned by ACL:")
    print(acl_content_hash)

    print("\nHash calculated before encryption:")
    print(plaintext_hash)

    if acl_content_hash != plaintext_hash:
        raise RuntimeError(
            "ACL content hash does not match the plaintext hash.\n"
            f"ACL:       {acl_content_hash}\n"
            f"Plaintext: {plaintext_hash}"
        )

    section("4. CREATE NEW IMMUTABLE BLOCKCHAIN VERSION")

    expected_new_version = current_version + 1

    print(f"Current blockchain version: V{current_version}")
    print(f"Expected new version: V{expected_new_version}")
    print(f"IPFS CID: {cid}")
    print(f"Content hash: {plaintext_hash}")
    print(f"Approval status: {APPROVAL_STATUS}")

    tx_hash = blockchain_client.create_document_version(
        document_id=DOCUMENT_ID,
        student_id=STUDENT_ID,
        document_type=DOCUMENT_TYPE,
        ipfs_cid=cid,
        content_hash=plaintext_hash,
        approval_status=APPROVAL_STATUS,
        approved_by=APPROVED_BY,
    )

    print("\nBlockchain transaction hash:")
    print(tx_hash)

    resolved = resolve_authoritative_version(
        blockchain_client
    )

    actual_version = int(
        resolved.get("resolved_version", 0)
    )

    if actual_version != expected_new_version:
        raise RuntimeError(
            f"Unexpected blockchain version. "
            f"Expected V{expected_new_version}, "
            f"found V{actual_version}."
        )

    return cid, plaintext_hash, actual_version


# ===========================================================================
# FINAL END-TO-END VALIDATION
# ===========================================================================

def validate_existing_or_new_version(
    blockchain_client: UniversityBlockchainClient,
    ipfs_client: UniversityIPFSClient,
    expected_cid: str,
    expected_hash: str,
    expected_version: int,
) -> None:
    section("5. VERIFY BLOCKCHAIN VERSION")

    resolved = resolve_authoritative_version(
        blockchain_client
    )

    print_json(resolved)

    resolved_cid = str(
        resolved.get("ipfs_cid", "")
    )

    resolved_hash = normalize_hash(
        resolved.get("content_hash")
    )

    if resolved_cid != expected_cid:
        raise RuntimeError(
            "Blockchain CID does not match the expected CID.\n"
            f"Expected: {expected_cid}\n"
            f"Found:    {resolved_cid}"
        )

    if resolved_hash != normalize_hash(expected_hash):
        raise RuntimeError(
            "Blockchain content hash does not match the plaintext hash.\n"
            f"Expected: {normalize_hash(expected_hash)}\n"
            f"Found:    {resolved_hash}"
        )

    verify_blockchain_metadata(
        blockchain_client,
        resolved,
        expected_hash=expected_hash,
    )

    actual_version = int(
        resolved.get("resolved_version", 0)
    )

    if actual_version != expected_version:
        raise RuntimeError(
            f"Unexpected blockchain version. "
            f"Expected V{expected_version}, "
            f"found V{actual_version}."
        )

    section("6. RETRIEVE ENCRYPTED DOCUMENT FROM IPFS")

    encrypted_payload = extract_document_payload(
        ipfs_client,
        resolved_cid,
    )

    print("IPFS payload retrieved successfully.")
    print(f"Encryption format: {encrypted_payload.get('enc')}")
    print(f"Encryption algorithm: {encrypted_payload.get('alg')}")
    print(f"Key identifier: {encrypted_payload.get('kid')}")

    if encrypted_payload.get("enc") != "fernet-v1":
        raise RuntimeError(
            "Unexpected IPFS encryption format."
        )

    if "ciphertext" not in encrypted_payload:
        raise RuntimeError(
            "Encrypted IPFS payload does not contain ciphertext."
        )

    print("✅ Encrypted document is present on IPFS")

    section("7. ACL DECRYPTION")

    plaintext_document, _ = decrypt_acl_document(
        ipfs_client,
        resolved_cid,
        TEST_USER,
        STUDENT_ID,
    )

    print("Decrypted document:")
    print_json(plaintext_document)

    if plaintext_document.get("document_id") != DOCUMENT_ID:
        raise RuntimeError(
            "Decrypted document ID does not match."
        )

    if plaintext_document.get("student_id") != STUDENT_ID:
        raise RuntimeError(
            "Decrypted document student ID does not match."
        )

    if plaintext_document.get("document_type") != DOCUMENT_TYPE:
        raise RuntimeError(
            "Decrypted document type does not match."
        )

    print("✅ ACL authorization and decryption succeeded")

    section("8. FINAL PLAINTEXT INTEGRITY CHECK")

    decrypted_hash = calculate_plaintext_hash(
        plaintext_document
    )

    blockchain_hash = normalize_hash(
        resolved.get("content_hash")
    )

    print("Hash calculated from decrypted plaintext:")
    print(decrypted_hash)

    print("\nHash stored on blockchain:")
    print(blockchain_hash)

    print("\nExpected plaintext hash:")
    print(normalize_hash(expected_hash))

    if decrypted_hash != blockchain_hash:
        raise RuntimeError(
            "FINAL INTEGRITY FAILURE.\n"
            "The decrypted plaintext does not match the blockchain hash."
        )

    if decrypted_hash != normalize_hash(expected_hash):
        raise RuntimeError(
            "FINAL INTEGRITY FAILURE.\n"
            "The decrypted plaintext does not match the expected hash."
        )

    print("\n✅ Final plaintext integrity verification passed")

    section("9. IMMUTABLE VERSION HISTORY")

    verify_history(
        blockchain_client,
        expected_latest_version=expected_version,
    )

    section("PHASE 3 DOCUMENT SEEDING / VALIDATION COMPLETE")

    print("✅ Blockchain authoritative resolution : PASS")
    print("✅ Approved version                    : PASS")
    print("✅ IPFS encrypted storage              : PASS")
    print("✅ ACL authorization                    : PASS")
    print("✅ ACL decryption                      : PASS")
    print("✅ Plaintext hash verification         : PASS")
    print("✅ Blockchain integrity verification   : PASS")
    print("✅ Immutable version chain              : PASS")

    print("\nFinal authoritative document:")
    print(f"  Document ID : {DOCUMENT_ID}")
    print(f"  Student ID  : {STUDENT_ID}")
    print(f"  Version     : V{expected_version}")
    print(f"  IPFS CID    : {resolved_cid}")
    print(f"  Hash        : {blockchain_hash}")
    print(f"  Status      : {resolved.get('approval_status')}")
    print(f"  Approved by : {resolved.get('approved_by')}")


# ===========================================================================
# MAIN
# ===========================================================================

def main() -> None:
    section("PHASE 3 DOCUMENT SEEDING")

    print(f"Document ID : {DOCUMENT_ID}")
    print(f"Student ID  : {STUDENT_ID}")
    print(f"Document type: {DOCUMENT_TYPE}")
    print(f"Approval status: {APPROVAL_STATUS}")

    section("1. CHECK EXISTING BLOCKCHAIN DOCUMENT")

    blockchain_client = UniversityBlockchainClient()
    ipfs_client = UniversityIPFSClient()

    existing_details = get_latest_blockchain_details(
        blockchain_client
    )

    if existing_details is None:
        raise RuntimeError(
            f"Blockchain document {DOCUMENT_ID} does not exist."
        )

    print_json(existing_details)

    # If V4 is already present, do not create another version.
    # This is the state reached by the successful transaction in the previous
    # run. The script simply resolves and validates V4.
    current_version = int(
        existing_details.get("latest_version", 0)
    )

    if current_version >= 4:
        section("2. EXISTING V4 DETECTED — VALIDATION ONLY")

        resolved = resolve_authoritative_version(
            blockchain_client
        )

        expected_cid = str(
            resolved.get("ipfs_cid", "")
        )

        expected_hash = normalize_hash(
            resolved.get("content_hash")
        )

        expected_version = int(
            resolved.get("resolved_version", 0)
        )

        if expected_version < 4:
            raise RuntimeError(
                f"Blockchain latest version is V{current_version}, "
                f"but approved resolver returned V{expected_version}."
            )

        validate_existing_or_new_version(
            blockchain_client,
            ipfs_client,
            expected_cid,
            expected_hash,
            expected_version,
        )

        return

    # V3 is the expected state before creating the corrected V4.
    if current_version != 3:
        raise RuntimeError(
            f"Unexpected current version V{current_version}. "
            "This test script expects V3 before creating V4."
        )

    expected_cid, expected_hash, expected_version = (
        create_new_version_if_needed(
            blockchain_client,
            ipfs_client,
            existing_details,
        )
    )

    validate_existing_or_new_version(
        blockchain_client,
        ipfs_client,
        expected_cid,
        expected_hash,
        expected_version,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user.")
        raise SystemExit(130)
    except Exception as exc:
        print("\n" + "=" * 80)
        print("❌ PHASE 3 VALIDATION FAILED")
        print("=" * 80)
        print(f"{type(exc).__name__}: {exc}")
        raise
