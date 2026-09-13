"""
End-to-end Hybrid Blockchain + IPFS document test.

Flow:
    Plain document
        -> ACL/envelope encryption
        -> encrypted IPFS storage
        -> real CID + plaintext SHA-256
        -> blockchain immutable document version
        -> blockchain CID/hash retrieval
        -> IPFS retrieval
        -> authorized decryption
        -> plaintext hash verification

This test uses the same ACL/IPFS storage path used by the application.
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict


# Allow imports when this file is executed from python_backend/
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from blockchain_client import UniversityBlockchainClient
from ipfs_client import UniversityIPFSClient
import acl


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

STUDENT_ID = "STD001"
DOCUMENT_ID = "STD001-TRANSCRIPT"
DOCUMENT_TYPE = "Transcript"

# This must correspond to a principal/user already supported by your
# existing ACL implementation.
TEST_PRINCIPAL = "STD001"

APPROVAL_STATUS = "APPROVED"
APPROVED_BY = "Registrar"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def canonical_json_bytes(document: Dict[str, Any]) -> bytes:
    """
    Produce the canonical plaintext representation used for hashing.

    This matches the canonicalization used by acl.py:
        json.dumps(
            doc,
            sort_keys=True,
            separators=(",", ":")
        )
    """
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def calculate_content_hash(document: Dict[str, Any]) -> str:
    """Calculate SHA-256 hash of the canonical plaintext document."""
    return hashlib.sha256(
        canonical_json_bytes(document)
    ).hexdigest()


def normalize_content_hash(content_hash: Any) -> str:
    """
    Normalize a content hash for reliable comparison.

    Blockchain bytes32 values are commonly represented as:
        0x<64 hex characters>

    The local SHA-256 calculation and ACL layer may return:
        <64 hex characters>

    Both representations refer to the same 32-byte digest.

    The normalized representation returned by this function:
        - is a string
        - is lowercase
        - does not contain the optional 0x prefix
    """
    if content_hash is None:
        raise ValueError("Content hash cannot be None.")

    if isinstance(content_hash, bytes):
        normalized = content_hash.hex()
    else:
        normalized = str(content_hash).strip().lower()

        if normalized.startswith("0x"):
            normalized = normalized[2:]

    if len(normalized) != 64:
        raise ValueError(
            "Invalid content hash length. Expected 64 hexadecimal "
            f"characters, got {len(normalized)}."
        )

    try:
        int(normalized, 16)
    except ValueError as exc:
        raise ValueError(
            "Invalid content hash. Expected a hexadecimal SHA-256 digest."
        ) from exc

    return normalized


def hashes_match(hash_a: Any, hash_b: Any) -> bool:
    """Compare two content hashes after normalization."""
    return normalize_content_hash(hash_a) == normalize_content_hash(hash_b)


def print_separator() -> None:
    print("=" * 72)


# ---------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------

def main() -> None:
    print_separator()
    print("HYBRID BLOCKCHAIN + IPFS DOCUMENT FLOW TEST")
    print_separator()

    print(f"Student ID : {STUDENT_ID}")
    print(f"Document ID: {DOCUMENT_ID}")
    print(f"Document   : {DOCUMENT_TYPE}")

    # ---------------------------------------------------------------
    # 1. Connect to blockchain
    # ---------------------------------------------------------------

    print("\n[1] Connecting to blockchain...")

    blockchain = UniversityBlockchainClient()

    print("    Blockchain connection: OK")

    # ---------------------------------------------------------------
    # 2. Verify student exists
    # ---------------------------------------------------------------

    print("\n[2] Checking student...")

    student = blockchain.get_student_details(STUDENT_ID)

    if not student.get("student_id"):
        raise RuntimeError(
            f"Student '{STUDENT_ID}' does not exist on the deployed "
            "UniversityRegistry contract."
        )

    print(f"    Student: {student['student_id']}")
    print(f"    Name   : {student['name']}")
    print(f"    Program: {student['program']}")
    print(f"    Active : {student['is_active']}")

    # ---------------------------------------------------------------
    # 3. Prepare test document
    # ---------------------------------------------------------------

    print("\n[3] Preparing plaintext document...")

    document = {
        "document_id": DOCUMENT_ID,
        "student_id": STUDENT_ID,
        "document_type": DOCUMENT_TYPE,
        "semester": "Semester 1",
        "academic_year": "2026",
        "student_name": student["name"],
        "program": student["program"],
        "courses": [
            {
                "course_code": "CS401",
                "course_name": "Advanced Algorithms",
                "grade": "A",
            },
            {
                "course_code": "CS402",
                "course_name": "Distributed Systems",
                "grade": "A-",
            },
            {
                "course_code": "CS403",
                "course_name": "Artificial Intelligence",
                "grade": "A",
            },
        ],
        "gpa": 3.75,
    }

    plaintext_hash = calculate_content_hash(document)

    print("    Plaintext document prepared")
    print(f"    SHA-256: {plaintext_hash}")

    # ---------------------------------------------------------------
    # 4. Initialize IPFS
    # ---------------------------------------------------------------

    print("\n[4] Connecting to IPFS...")

    ipfs = UniversityIPFSClient()

    print("    IPFS connection: OK")

    # ---------------------------------------------------------------
    # 5. Encrypt + store through existing ACL mechanism
    # ---------------------------------------------------------------

    print("\n[5] Encrypting document and storing on IPFS...")

    principals = [TEST_PRINCIPAL]

    cid, returned_content_hash = acl.encrypt_and_store(
        document,
        principals,
        ipfs,
    )

    print("    Encryption: OK")
    print(f"    IPFS CID: {cid}")
    print(f"    Returned content hash: {returned_content_hash}")

    if not hashes_match(returned_content_hash, plaintext_hash):
        raise RuntimeError(
            "Content hash returned by acl.encrypt_and_store() does not "
            "match the independently calculated plaintext hash."
        )

    print("    Plaintext hash consistency: PASS")

    # ---------------------------------------------------------------
    # 6. Verify raw IPFS object is not plaintext
    # ---------------------------------------------------------------

    print("\n[6] Checking encrypted IPFS storage...")

    raw_ipfs_content = ipfs.client.cat(cid)

    if not raw_ipfs_content:
        raise RuntimeError("IPFS returned an empty object.")

    plaintext_bytes = canonical_json_bytes(document)

    if plaintext_bytes in raw_ipfs_content:
        raise RuntimeError(
            "SECURITY CHECK FAILED: plaintext document content appears "
            "directly inside the IPFS object."
        )

    print(f"    Retrieved IPFS bytes: {len(raw_ipfs_content)}")
    print("    Plaintext exposure check: PASS")

    # ---------------------------------------------------------------
    # 7. Create blockchain document version
    # ---------------------------------------------------------------

    print("\n[7] Creating immutable blockchain document version...")

    tx_hash = blockchain.create_document_version(
        document_id=DOCUMENT_ID,
        student_id=STUDENT_ID,
        document_type=DOCUMENT_TYPE,
        ipfs_cid=cid,
        content_hash=returned_content_hash,
        approval_status=APPROVAL_STATUS,
        approved_by=APPROVED_BY,
    )

    print("    Blockchain transaction:", tx_hash)

    # ---------------------------------------------------------------
    # 8. Read latest blockchain version
    # ---------------------------------------------------------------

    print("\n[8] Reading latest blockchain version...")

    latest = blockchain.get_latest_document_version(
        DOCUMENT_ID
    )

    print(f"    Version          : {latest['version']}")
    print(f"    Previous version : {latest['previous_version']}")
    print(f"    IPFS CID         : {latest['ipfs_cid']}")
    print(f"    Content hash     : {latest['content_hash']}")
    print(f"    Approval status  : {latest['approval_status']}")
    print(f"    Approved by      : {latest['approved_by']}")

    # ---------------------------------------------------------------
    # 9. Verify blockchain ↔ IPFS metadata
    # ---------------------------------------------------------------

    print("\n[9] Verifying blockchain ↔ IPFS consistency...")

    if latest["ipfs_cid"] != cid:
        raise RuntimeError(
            "Blockchain CID does not match the CID returned by IPFS."
        )

    blockchain_hash_normalized = normalize_content_hash(
        latest["content_hash"]
    )

    returned_hash_normalized = normalize_content_hash(
        returned_content_hash
    )

    plaintext_hash_normalized = normalize_content_hash(
        plaintext_hash
    )

    print(f"    Blockchain hash : {blockchain_hash_normalized}")
    print(f"    Plaintext hash  : {plaintext_hash_normalized}")

    if blockchain_hash_normalized != returned_hash_normalized:
        raise RuntimeError(
            "Blockchain content hash does not match the IPFS/plaintext "
            "hash."
        )

    print("    CID consistency: PASS")
    print("    Content hash consistency: PASS")

    # ---------------------------------------------------------------
    # 10. Retrieve encrypted object from IPFS
    # ---------------------------------------------------------------

    print("\n[10] Retrieving encrypted document from IPFS...")

    retrieved_payload = ipfs.retrieve_academic_document(
        latest["ipfs_cid"]
    )

    if not isinstance(retrieved_payload, dict):
        raise RuntimeError(
            "Expected encrypted IPFS payload to be a JSON object."
        )

    print("    Encrypted payload retrieved: OK")

    # ---------------------------------------------------------------
    # 11. Authorized decryption
    # ---------------------------------------------------------------

    print("\n[11] Performing authorized decryption...")

    # We intentionally use the existing ACL mechanism rather than
    # directly decrypting with the storage Fernet key.
    #
    # The exact user object expected by acl.decrypt_for_user() depends
    # on your existing ACL/RBAC implementation. Therefore, first try
    # to construct the minimum compatible user representation.

    user = {
        "username": TEST_PRINCIPAL,
        "user_id": TEST_PRINCIPAL,
        "student_id": STUDENT_ID,
    }

    try:
        decrypted_document = acl.decrypt_for_user(
            user,
            STUDENT_ID,
            latest["ipfs_cid"],
            retrieved_payload,
        )
    except Exception as exc:
        raise RuntimeError(
            "Authorized ACL decryption failed. "
            "This is an integration issue between the test user "
            "representation and the existing ACL implementation.\n"
            f"Original error: {exc}"
        ) from exc

    if isinstance(decrypted_document, str):
        try:
            decrypted_document = json.loads(decrypted_document)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Authorized decryption succeeded, but the resulting "
                "content is not valid JSON."
            ) from exc

    if not isinstance(decrypted_document, dict):
        raise RuntimeError(
            "Authorized decryption did not return a document object."
        )

    print("    Authorized decryption: OK")

    # ---------------------------------------------------------------
    # 12. Verify recovered plaintext
    # ---------------------------------------------------------------

    print("\n[12] Verifying recovered plaintext...")

    recovered_hash = calculate_content_hash(
        decrypted_document
    )

    print(f"    Original hash   : {plaintext_hash}")
    print(f"    Recovered hash  : {recovered_hash}")
    print(f"    Blockchain hash : {latest['content_hash']}")

    if not hashes_match(recovered_hash, plaintext_hash):
        raise RuntimeError(
            "Recovered document hash does not match the original "
            "plaintext hash."
        )

    if not hashes_match(recovered_hash, latest["content_hash"]):
        raise RuntimeError(
            "Recovered document hash does not match the blockchain hash."
        )

    print("    Plaintext integrity: PASS")

    # ---------------------------------------------------------------
    # Final result
    # ---------------------------------------------------------------

    print()
    print_separator()
    print("END-TO-END RESULT: PASSED")
    print_separator()

    print(
        "\nVerified pipeline:"
        "\n  Plaintext document"
        "\n       ↓"
        "\n  ACL/envelope encryption"
        "\n       ↓"
        "\n  Encrypted IPFS object"
        "\n       ↓"
        "\n  Real CID + plaintext SHA-256"
        "\n       ↓"
        "\n  Blockchain immutable version"
        "\n       ↓"
        "\n  Blockchain CID/hash resolution"
        "\n       ↓"
        "\n  IPFS retrieval"
        "\n       ↓"
        "\n  Authorized decryption"
        "\n       ↓"
        "\n  Plaintext integrity verification"
    )


if __name__ == "__main__":
    main()