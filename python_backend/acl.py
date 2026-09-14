"""
Application-level Access Control and Envelope Encryption.

Responsibilities:
    - Derive deterministic key-encryption keys (KEKs) for principals.
    - Encrypt document plaintext using a random data-encryption key (DEK).
    - Wrap the DEK for authorized principals.
    - Store wrapped keys in SQLite.
    - Decrypt an encrypted document for an authorized principal.
    - Calculate canonical document hashes for blockchain integrity.

Security architecture:

    Plaintext document
            |
            v
       Random DEK
            |
            v
       Fernet encrypt
            |
            v
      Ciphertext payload
            |
            +---------------------> IPFS
            |
            v
       DEK wrapped separately
       for each authorized principal
            |
            v
       SQLite ACL database

The blockchain stores the content hash of the plaintext document.

IMPORTANT:
    This module does not perform LangGraph routing.

    This module does not decide repository access.

    RBAC authorization should happen before confidential IPFS retrieval.

    The ACL determines whether a user has a wrapped DEK for the
    requested document after the document has been resolved.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple, Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# ===========================================================================
# Storage
# ===========================================================================

DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "acl.sqlite",
)


def _init_db() -> None:
    """
    Initialize the local ACL database.
    """

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS doc_wrapped_keys (
                cid TEXT NOT NULL,
                principal TEXT NOT NULL,
                wrapped_key BLOB NOT NULL,
                PRIMARY KEY (cid, principal)
            )
            """
        )


_init_db()


# ===========================================================================
# Master secret
# ===========================================================================

# The master secret should preferably be supplied through the environment.

MASTER_FILE = os.path.join(
    os.path.dirname(__file__),
    ".acl_master_secret",
)


def _load_master_secret() -> bytes:
    """
    Load the ACL master secret.

    Priority:
        1. ACL_MASTER_SECRET environment variable
        2. Existing local .acl_master_secret file
        3. Generate a new 32-byte secret

    IMPORTANT:
        .acl_master_secret must never be committed to source control.
    """

    environment_secret = os.getenv(
        "ACL_MASTER_SECRET"
    )

    if environment_secret:
        # --------------------------------------------------------------
        # Try URL-safe Base64
        # --------------------------------------------------------------

        try:
            decoded = base64.urlsafe_b64decode(
                environment_secret
            )

            if decoded:
                return decoded

        except Exception:
            pass

        # --------------------------------------------------------------
        # Try hexadecimal
        # --------------------------------------------------------------

        try:
            return bytes.fromhex(
                environment_secret
            )

        except Exception:
            pass

        # --------------------------------------------------------------
        # Fall back to UTF-8 bytes
        # --------------------------------------------------------------

        return environment_secret.encode(
            "utf-8"
        )

    # ------------------------------------------------------------------
    # Existing local secret
    # ------------------------------------------------------------------

    if os.path.exists(MASTER_FILE):
        with open(
            MASTER_FILE,
            "rb",
        ) as secret_file:
            secret = secret_file.read().strip()

        if secret:
            return secret

    # ------------------------------------------------------------------
    # Generate a new secret
    # ------------------------------------------------------------------

    secret = os.urandom(32)

    with open(
        MASTER_FILE,
        "wb",
    ) as secret_file:
        secret_file.write(secret)

    return secret


_MASTER = _load_master_secret()


# ===========================================================================
# Key derivation
# ===========================================================================

def _derive_kek_for_principal(
    principal: str,
) -> bytes:
    """
    Derive a deterministic 32-byte key-encryption key (KEK)
    for a principal.

    Example principals:

        role:HOD
        role:DEAN
        role:AR
        student:S20841

    Derivation:

        HKDF(
            master,
            salt=principal,
            info=b"ACL-WRAP"
        )
    """

    normalized_principal = (
        str(principal or "")
        .strip()
    )

    if not normalized_principal:
        raise ValueError(
            "principal cannot be empty"
        )

    principal_bytes = (
        normalized_principal.encode(
            "utf-8"
        )
    )

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=principal_bytes,
        info=b"ACL-WRAP",
    )

    return hkdf.derive(
        _MASTER
    )


def _fernet_from_key(
    key32: bytes,
) -> Fernet:
    """
    Create a Fernet instance from a raw 32-byte key.
    """

    if not isinstance(
        key32,
        (bytes, bytearray),
    ):
        raise TypeError(
            "key32 must be bytes"
        )

    if len(key32) != 32:
        raise ValueError(
            "Fernet key material must be exactly 32 bytes"
        )

    return Fernet(
        base64.urlsafe_b64encode(
            bytes(key32)
        )
    )


# ===========================================================================
# Principal helpers
# ===========================================================================

DEFAULT_ROLE_PRINCIPALS = {
    "role:ADMIN",
    "role:HOD",
    "role:DEAN",
    "role:AR",
    "role:DVC",
}


def default_principals(
    student_id: str,
) -> List[str]:
    """
    Return the default principals for a document.

    Includes:
        - default administrative roles
        - the specific student principal
    """

    sid = (
        str(student_id or "")
        .strip()
        .upper()
    )

    principals = list(
        DEFAULT_ROLE_PRINCIPALS
    )

    if sid:
        principals.append(
            f"student:{sid}"
        )

    return sorted(
        set(principals)
    )


# ===========================================================================
# Content hashing
# ===========================================================================

def _canonical_bytes(
    doc: Dict[str, Any],
) -> bytes:
    """
    Serialize a document deterministically.

    This representation MUST remain consistent with the content hash
    stored on the blockchain.
    """

    if not isinstance(
        doc,
        dict,
    ):
        raise TypeError(
            "doc must be a dictionary"
        )

    return json.dumps(
        doc,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8"
    )


def _sha256_hex(
    value: bytes,
) -> str:
    """
    Return a lowercase SHA-256 hexadecimal digest.
    """

    return hashlib.sha256(
        value
    ).hexdigest()


def calculate_content_hash(
    doc: Dict[str, Any],
) -> str:
    """
    Calculate the canonical plaintext content hash.

    This is the hash that should be recorded in the blockchain
    DocumentVersion.contentHash field.
    """

    return _sha256_hex(
        _canonical_bytes(doc)
    )


# ===========================================================================
# Encryption and storage
# ===========================================================================

def encrypt_and_store(
    doc: Dict[str, Any],
    principals: Iterable[str],
    ipfs_client,
) -> Tuple[str, str]:
    """
    Encrypt a document and store it on IPFS.

    Process:

        1. Canonicalize plaintext.
        2. Calculate plaintext SHA-256 content hash.
        3. Generate a random DEK.
        4. Encrypt plaintext with DEK.
        5. Store encrypted payload on IPFS.
        6. Derive a KEK for every authorized principal.
        7. Wrap the DEK for each principal.
        8. Store wrapped DEKs in SQLite.

    Returns:

        (
            cid,
            content_hash
        )

    The content_hash represents the plaintext document, not the
    encrypted IPFS payload.
    """

    if not isinstance(
        doc,
        dict,
    ):
        raise TypeError(
            "doc must be a dictionary"
        )

    normalized_principals = [
        str(principal).strip()
        for principal in principals
        if principal
        and str(principal).strip()
    ]

    # Remove duplicates while preserving deterministic behavior.
    normalized_principals = sorted(
        set(normalized_principals)
    )

    if not normalized_principals:
        raise ValueError(
            "encrypt_and_store: principals list is empty"
        )

    # ------------------------------------------------------------------
    # 1. Canonical plaintext
    # ------------------------------------------------------------------

    plaintext = _canonical_bytes(
        doc
    )

    # ------------------------------------------------------------------
    # 2. Plaintext content hash
    #
    # This value is suitable for the blockchain contentHash.
    # ------------------------------------------------------------------

    content_hash = _sha256_hex(
        plaintext
    )

    # ------------------------------------------------------------------
    # 3. Generate random document DEK
    # ------------------------------------------------------------------

    data_key = os.urandom(
        32
    )

    f_plain = _fernet_from_key(
        data_key
    )

    # ------------------------------------------------------------------
    # 4. Encrypt plaintext
    # ------------------------------------------------------------------

    ciphertext = f_plain.encrypt(
        plaintext
    )

    ciphertext_b64 = (
        base64.urlsafe_b64encode(
            ciphertext
        ).decode("ascii")
    )

    # ------------------------------------------------------------------
    # 5. Build encrypted IPFS payload
    #
    # No document plaintext is placed in this payload.
    # ------------------------------------------------------------------

    key_id = _sha256_hex(
        data_key
    )[:16]

    payload = {
        "enc": "fernet-v1",
        "alg": "Fernet",
        "kid": f"dk:{key_id}",
        "ciphertext": ciphertext_b64,
    }

    # ------------------------------------------------------------------
    # 6. Store encrypted payload on IPFS
    # ------------------------------------------------------------------

    result = ipfs_client.store_academic_document(
        payload
    )

    if isinstance(
        result,
        dict,
    ):
        cid = (
            result.get("ipfs_hash")
            or result.get("cid")
            or result.get("Hash")
            or result.get("path")
            or ""
        )

    elif isinstance(
        result,
        (list, tuple),
    ) and len(result) >= 1:
        cid = str(
            result[0]
        )

    else:
        cid = str(
            result
        )

    cid = str(
        cid or ""
    ).strip()

    if not cid:
        raise RuntimeError(
            "IPFS store returned no CID"
        )

    # ------------------------------------------------------------------
    # 7. Wrap DEK for every authorized principal
    # ------------------------------------------------------------------

    with sqlite3.connect(
        DB_PATH
    ) as connection:

        for principal in normalized_principals:
            kek = (
                _derive_kek_for_principal(
                    principal
                )
            )

            f_wrap = _fernet_from_key(
                kek
            )

            wrapped_key = f_wrap.encrypt(
                data_key
            )

            connection.execute(
                """
                INSERT OR REPLACE INTO doc_wrapped_keys
                    (cid, principal, wrapped_key)
                VALUES
                    (?, ?, ?)
                """,
                (
                    cid,
                    principal,
                    wrapped_key,
                ),
            )

    return (
        cid,
        content_hash,
    )


# ===========================================================================
# Wrapped-key lookup
# ===========================================================================

def _find_wrapped_for(
    cid: str,
    principals: Iterable[str],
) -> Optional[
    Tuple[str, bytes]
]:
    """
    Find the first wrapped DEK available for one of the supplied
    principals.
    """

    normalized_cid = str(
        cid or ""
    ).strip()

    if not normalized_cid:
        return None

    normalized_principals = [
        str(principal).strip()
        for principal in principals
        if principal
        and str(principal).strip()
    ]

    normalized_principals = sorted(
        set(normalized_principals)
    )

    if not normalized_principals:
        return None

    placeholders = ",".join(
        "?"
        for _ in normalized_principals
    )

    query = f"""
        SELECT principal, wrapped_key
        FROM doc_wrapped_keys
        WHERE cid = ?
          AND principal IN ({placeholders})
        ORDER BY principal
        LIMIT 1
    """

    with sqlite3.connect(
        DB_PATH
    ) as connection:

        row = connection.execute(
            query,
            (
                normalized_cid,
                *normalized_principals,
            ),
        ).fetchone()

    if not row:
        return None

    return (
        str(row[0]),
        bytes(row[1]),
    )


# ===========================================================================
# Principal resolution for a user
# ===========================================================================

def _user_principals(
    user: Optional[Dict[str, Any]],
    student_id: str,
) -> List[str]:
    """
    Determine candidate ACL principals for a user.

    Current supported principals:

        role:<ROLE>
        student:<STUDENT_ID>

    A student's own student principal is included when the supplied
    target student ID is available.
    """

    principals: List[str] = []

    if user:
        role = (
            str(
                user.get(
                    "role",
                    "",
                )
                or ""
            )
            .strip()
            .upper()
        )

        if role:
            principals.append(
                f"role:{role}"
            )

        if role == "STUDENT":
            user_student_id = (
                str(
                    user.get(
                        "student_id",
                        "",
                    )
                    or ""
                )
                .strip()
                .upper()
            )

            if user_student_id:
                principals.append(
                    f"student:{user_student_id}"
                )

    normalized_student_id = (
        str(student_id or "")
        .strip()
        .upper()
    )

    if normalized_student_id:
        principals.append(
            f"student:{normalized_student_id}"
        )

    return sorted(
        set(principals)
    )


# ===========================================================================
# Decryption
# ===========================================================================

def decrypt_for_user(
    user: Optional[Dict[str, Any]],
    student_id: str,
    cid: str,
    enc_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Decrypt an encrypted document for an authorized user.

    The payload must have:

        enc = "fernet-v1"

    The function:

        1. Determines the user's candidate principals.
        2. Looks for a wrapped DEK for those principals.
        3. Derives the corresponding KEK.
        4. Unwraps the DEK.
        5. Decrypts the document ciphertext.
        6. Parses and returns the plaintext JSON object.

    Raises:
        ValueError
            Invalid payload or malformed ciphertext.

        PermissionError
            No wrapped key is available for the user's principals.
    """

    if not isinstance(
        enc_payload,
        dict,
    ):
        raise ValueError(
            "decrypt_for_user: encrypted payload must be a dictionary"
        )

    if enc_payload.get(
        "enc"
    ) != "fernet-v1":
        raise ValueError(
            "decrypt_for_user: payload is not an encrypted "
            "fernet-v1 document"
        )

    normalized_cid = str(
        cid or ""
    ).strip()

    if not normalized_cid:
        raise ValueError(
            "decrypt_for_user: cid cannot be empty"
        )

    # ------------------------------------------------------------------
    # Candidate principals
    # ------------------------------------------------------------------

    principals = _user_principals(
        user,
        student_id,
    )

    if not principals:
        raise PermissionError(
            "No valid principals were available for this user."
        )

    # ------------------------------------------------------------------
    # Locate wrapped DEK
    # ------------------------------------------------------------------

    found = _find_wrapped_for(
        normalized_cid,
        principals,
    )

    if not found:
        raise PermissionError(
            "No wrapped key for your principals "
            "on this document."
        )

    principal_used, wrapped_key = found

    # ------------------------------------------------------------------
    # Derive KEK
    # ------------------------------------------------------------------

    kek = _derive_kek_for_principal(
        principal_used
    )

    f_wrap = _fernet_from_key(
        kek
    )

    # ------------------------------------------------------------------
    # Unwrap DEK
    # ------------------------------------------------------------------

    try:
        data_key = f_wrap.decrypt(
            wrapped_key
        )

    except Exception as exc:
        raise PermissionError(
            "Unable to unwrap the document key "
            f"for principal '{principal_used}'."
        ) from exc

    # ------------------------------------------------------------------
    # Validate ciphertext
    # ------------------------------------------------------------------

    ciphertext_b64 = str(
        enc_payload.get(
            "ciphertext",
            "",
        )
        or ""
    ).strip()

    if not ciphertext_b64:
        raise ValueError(
            "Encrypted payload missing ciphertext."
        )

    # ------------------------------------------------------------------
    # Base64 decode
    # ------------------------------------------------------------------

    try:
        ciphertext = (
            base64.urlsafe_b64decode(
                ciphertext_b64.encode(
                    "ascii"
                )
            )
        )

    except Exception as exc:
        raise ValueError(
            "Encrypted payload contains invalid "
            "base64 ciphertext."
        ) from exc

    # ------------------------------------------------------------------
    # Decrypt document
    # ------------------------------------------------------------------

    f_plain = _fernet_from_key(
        data_key
    )

    try:
        plaintext = f_plain.decrypt(
            ciphertext
        )

    except Exception as exc:
        raise ValueError(
            "Unable to decrypt document ciphertext."
        ) from exc

    # ------------------------------------------------------------------
    # Decode JSON
    # ------------------------------------------------------------------

    try:
        document = json.loads(
            plaintext.decode(
                "utf-8"
            )
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "Decrypted document is not valid JSON."
        ) from exc

    if not isinstance(
        document,
        dict,
    ):
        raise ValueError(
            "Decrypted document must be a JSON object."
        )

    return document


# ===========================================================================
# Phase 3: Decrypt and verify
# ===========================================================================

def decrypt_and_verify_for_user(
    user: Optional[Dict[str, Any]],
    student_id: str,
    cid: str,
    enc_payload: Dict[str, Any],
    expected_content_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Decrypt a document for an authorized user and optionally verify
    its canonical plaintext content hash.

    This is a convenience function for Phase 3.

    The expected hash should normally come from the blockchain.

    Returns:

        {
            "document": {...},
            "content_hash": "...",
            "integrity_verified": True/False
        }
    """

    document = decrypt_for_user(
        user,
        student_id,
        cid,
        enc_payload,
    )

    actual_hash = calculate_content_hash(
        document
    )

    integrity_verified = False

    if expected_content_hash:
        expected = str(
            expected_content_hash
        ).strip().lower()

        if expected.startswith(
            "0x"
        ):
            expected = expected[2:]

        integrity_verified = (
            actual_hash.lower()
            == expected
        )

        if not integrity_verified:
            raise ValueError(
                "Document integrity verification failed: "
                f"expected {expected}, "
                f"calculated {actual_hash}."
            )

    return {
        "document": document,
        "content_hash": actual_hash,
        "integrity_verified": integrity_verified,
    }