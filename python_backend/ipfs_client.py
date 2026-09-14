"""
University IPFS client.

Responsibilities:
    - Store encrypted university documents on IPFS.
    - Retrieve encrypted documents from IPFS.
    - Decrypt the IPFS storage-encryption layer.
    - Parse retrieved JSON documents.
    - Verify document integrity against the blockchain content hash.

Phase 3 architecture:

    Repository Metadata RAG
            |
            v
       document_id
            |
            v
       Blockchain
            |
            +--> authoritative version
            +--> authoritative CID
            +--> content hash
            |
            v
           IPFS
            |
            v
       encrypted document
            |
            v
     authorized decryption

IMPORTANT:
    This class does NOT perform RBAC/ACL authorization.

Authorization must happen before this client is called by the
LangGraph workflow.

The CID passed to the Phase 3 retrieval methods should normally be
the CID resolved from the blockchain, not the CID stored in the
repository metadata registry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import pathlib
from typing import Any, Dict, Optional

import ipfshttpclient
from cryptography.fernet import Fernet, InvalidToken


logger = logging.getLogger(__name__)


class UniversityIPFSClient:
    """
    IPFS client for university document storage.

    The IPFS layer stores encrypted document content.

    Environment-independent defaults:
        IPFS API:
            /ip4/127.0.0.1/tcp/5001

        Fernet key:
            <project-root>/fernet.key
    """

    def __init__(
        self,
        ipfs_api_url: str = "/ip4/127.0.0.1/tcp/5001",
    ) -> None:
        """
        Initialize the IPFS client.

        The existing persistent Fernet key is reused so that documents
        previously stored with this backend can still be decrypted.
        """

        try:
            self.client = ipfshttpclient.Client(
                addr=ipfs_api_url,
                base="api/v0",
            )

            # ----------------------------------------------------------
            # Persistent Fernet key storage
            #
            # Keep using the existing project-root key so that this
            # change does not invalidate previously stored documents.
            # ----------------------------------------------------------

            root_dir = (
                pathlib.Path(__file__)
                .resolve()
                .parent
                .parent
            )

            key_path = os.path.join(
                root_dir,
                "fernet.key",
            )

            self.key_path = key_path

            if os.path.exists(key_path):
                with open(
                    key_path,
                    "rb",
                ) as key_file:
                    self.encryption_key = (
                        key_file.read()
                    )
            else:
                self.encryption_key = (
                    Fernet.generate_key()
                )

                with open(
                    key_path,
                    "wb",
                ) as key_file:
                    key_file.write(
                        self.encryption_key
                    )

            self.cipher_suite = Fernet(
                self.encryption_key
            )

            logger.info(
                "IPFS client initialized successfully"
            )

        except Exception as exc:
            logger.error(
                "Failed to initialize IPFS client: %s",
                exc,
            )
            raise

    # ==================================================================
    # Encryption
    # ==================================================================

    def encrypt_document(
        self,
        content: str,
    ) -> bytes:
        """
        Encrypt document content before storing it on IPFS.

        This is the outer IPFS storage-encryption layer.
        """

        if not isinstance(content, str):
            raise TypeError(
                "content must be a string"
            )

        return self.cipher_suite.encrypt(
            content.encode("utf-8")
        )

    # ==================================================================
    # Decryption
    # ==================================================================

    def decrypt_document(
        self,
        encrypted_content: bytes,
    ) -> str:
        """
        Decrypt the outer IPFS storage-encryption layer.

        The decrypted value is returned as-is.

        If the resulting JSON contains an application-level ciphertext
        and key identifier, that additional encryption is intentionally
        NOT decrypted here. It must be handled by the appropriate
        application-level authorization/decryption mechanism.
        """

        if not isinstance(
            encrypted_content,
            (bytes, bytearray),
        ):
            raise TypeError(
                "encrypted_content must be bytes"
            )

        try:
            decrypted = (
                self.cipher_suite.decrypt(
                    bytes(encrypted_content)
                )
            )

            return decrypted.decode(
                "utf-8"
            )

        except InvalidToken as exc:
            raise ValueError(
                "Failed to decrypt IPFS document: "
                "invalid encryption key or corrupted ciphertext."
            ) from exc

        except UnicodeDecodeError as exc:
            raise ValueError(
                "IPFS document decrypted successfully "
                "but does not contain valid UTF-8 text."
            ) from exc

        except Exception as exc:
            raise ValueError(
                f"Failed to decrypt IPFS document: {exc}"
            ) from exc

    # ==================================================================
    # Store document
    # ==================================================================

    def store_academic_document(
        self,
        document_data: Dict[str, Any],
    ) -> Dict[str, str]:
        """
        Encrypt and store an academic document on IPFS.

        Returns metadata containing:
            ipfs_hash
            content_hash
            document_type
            student_id
            timestamp
            size_bytes

        IMPORTANT:
            The returned content_hash is calculated from the exact
            indented JSON representation currently used by the
            existing storage implementation.

        For Phase 3 blockchain version records, the caller should use
        the canonical content-hashing mechanism expected by the
        blockchain/integrity workflow.
        """

        if not isinstance(
            document_data,
            dict,
        ):
            raise TypeError(
                "document_data must be a dictionary"
            )

        try:
            # ----------------------------------------------------------
            # Serialize document
            # ----------------------------------------------------------

            document_json = json.dumps(
                document_data,
                indent=2,
            )

            # ----------------------------------------------------------
            # Encrypt outer IPFS storage layer
            # ----------------------------------------------------------

            encrypted_content = (
                self.encrypt_document(
                    document_json
                )
            )

            # ----------------------------------------------------------
            # Upload encrypted content to IPFS
            # ----------------------------------------------------------

            ipfs_hash = self.client.add_bytes(
                encrypted_content
            )

            # ----------------------------------------------------------
            # Existing content hash behavior
            # ----------------------------------------------------------

            content_hash = hashlib.sha256(
                document_json.encode("utf-8")
            ).hexdigest()

            metadata = {
                "ipfs_hash": ipfs_hash,
                "content_hash": content_hash,
                "document_type": document_data.get(
                    "document_type",
                    "unknown",
                ),
                "student_id": document_data.get(
                    "student_id",
                    "",
                ),
                "timestamp": document_data.get(
                    "timestamp",
                    "",
                ),
                "size_bytes": str(
                    len(encrypted_content)
                ),
            }

            logger.info(
                "Document stored on IPFS: %s",
                ipfs_hash,
            )

            return metadata

        except Exception as exc:
            logger.error(
                "Failed to store document on IPFS: %s",
                exc,
            )
            raise

    # ==================================================================
    # Raw IPFS retrieval
    # ==================================================================

    def fetch_encrypted_document(
        self,
        ipfs_hash: str,
    ) -> bytes:
        """
        Fetch the raw encrypted bytes from IPFS.

        No decryption is performed here.
        """

        cid = str(
            ipfs_hash or ""
        ).strip()

        if not cid:
            raise ValueError(
                "ipfs_hash cannot be empty"
            )

        try:
            return self.client.cat(cid)

        except Exception as exc:
            logger.error(
                "Failed to fetch IPFS object %s: %s",
                cid,
                exc,
            )
            raise

    # ==================================================================
    # Retrieve and decrypt document
    # ==================================================================

    def retrieve_academic_document(
        self,
        ipfs_hash: str,
    ) -> Dict[str, Any]:
        """
        Retrieve and decrypt an academic document from IPFS.

        This method decrypts only the outer IPFS storage layer.

        It does NOT:
            - perform RBAC;
            - perform ACL checks;
            - resolve blockchain versions;
            - determine the authoritative CID;
            - decrypt application-level user-specific encryption.
        """

        cid = str(
            ipfs_hash or ""
        ).strip()

        if not cid:
            raise ValueError(
                "ipfs_hash cannot be empty"
            )

        try:
            # ----------------------------------------------------------
            # Step 1: Fetch encrypted bytes
            # ----------------------------------------------------------

            encrypted_content = (
                self.fetch_encrypted_document(
                    cid
                )
            )

            # ----------------------------------------------------------
            # Step 2: Decrypt outer IPFS layer
            # ----------------------------------------------------------

            decrypted_content = (
                self.decrypt_document(
                    encrypted_content
                )
            )

            # ----------------------------------------------------------
            # Step 3: Parse JSON
            # ----------------------------------------------------------

            try:
                document_data = json.loads(
                    decrypted_content
                )

            except json.JSONDecodeError as exc:
                logger.warning(
                    "Decrypted IPFS content is not valid JSON "
                    "for CID %s: %s",
                    cid,
                    exc,
                )

                return {
                    "raw_text": decrypted_content
                }

            # ----------------------------------------------------------
            # Step 4: Detect nested application-level encryption
            # ----------------------------------------------------------

            if (
                isinstance(
                    document_data,
                    dict,
                )
                and "ciphertext" in document_data
                and "kid" in document_data
            ):
                logger.warning(
                    "Document %s contains nested application-level "
                    "encryption with key ID: %s",
                    cid,
                    document_data.get("kid"),
                )

                logger.warning(
                    "Nested application-level encryption must be "
                    "handled by the authorized application layer."
                )

            logger.info(
                "Document successfully retrieved from IPFS: %s",
                cid,
            )

            return document_data

        except Exception as exc:
            logger.error(
                "Failed to retrieve document from IPFS %s: %s",
                cid,
                exc,
            )
            raise

    # ==================================================================
    # Phase 3: Retrieve blockchain-resolved document
    # ==================================================================

    def retrieve_resolved_document(
        self,
        resolved_cid: str,
        *,
        expected_content_hash: Optional[str] = None,
        verify_integrity: bool = True,
    ) -> Dict[str, Any]:
        """
        Retrieve a document using a CID resolved from the blockchain.

        This method is intended for Phase 3.

        The LangGraph workflow should first:
            1. authorize the user;
            2. identify the document through repository metadata;
            3. resolve the authoritative version/CID from blockchain;
            4. call this method using that blockchain CID.

        IMPORTANT PHASE 3 INTEGRITY RULE:
            If the IPFS object contains application-level encryption
            (for example, an ACL envelope with ``enc``, ``kid`` and
            ``ciphertext``), this method MUST NOT hash that encrypted
            envelope against the blockchain content_hash.

            The blockchain content_hash represents the canonical plaintext
            document. Therefore integrity verification for an encrypted
            application envelope is deferred until the authorized ACL layer
            decrypts the document.

        For legacy/direct plaintext documents, this method can still verify
        integrity immediately when ``verify_integrity=True`` and an expected
        blockchain hash is supplied.

        Returns:
            {
                "document": {...},
                "ipfs_cid": "...",
                "integrity_verified": True/False,
                "integrity_pending": True/False,
                "application_encrypted": True/False
            }
        """

        cid = str(
            resolved_cid or ""
        ).strip()

        if not cid:
            raise ValueError(
                "resolved_cid cannot be empty"
            )

        # Retrieve and decrypt ONLY the outer IPFS storage layer.
        document = (
            self.retrieve_academic_document(
                cid
            )
        )

        # --------------------------------------------------------------
        # Phase 3: application-level encrypted envelope
        # --------------------------------------------------------------
        #
        # The object returned by IPFS can itself be encrypted by the ACL
        # layer. Its ciphertext/envelope representation is NOT the
        # blockchain document content. Hashing it here would produce a
        # different value from the blockchain plaintext content_hash.
        #
        # The current Phase 3 tools.py intentionally calls this method with
        # expected_content_hash=None and verify_integrity=False, then passes
        # the returned envelope to acl.decrypt_and_verify_for_user().
        # --------------------------------------------------------------
        is_application_encrypted = (
            isinstance(document, dict)
            and (
                document.get("enc") == "fernet-v1"
                or (
                    "ciphertext" in document
                    and "kid" in document
                )
            )
        )

        if is_application_encrypted:
            if verify_integrity and expected_content_hash:
                logger.info(
                    "Skipping IPFS-layer integrity comparison for "
                    "application-encrypted CID %s. The blockchain hash "
                    "will be verified after authorized ACL decryption "
                    "against the plaintext document.",
                    cid,
                )

            return {
                "document": document,
                "ipfs_cid": cid,
                "integrity_verified": False,
                "integrity_pending": True,
                "application_encrypted": True,
            }

        # --------------------------------------------------------------
        # Legacy/direct plaintext document
        # --------------------------------------------------------------
        integrity_verified = False

        if (
            verify_integrity
            and expected_content_hash
        ):
            integrity_verified = (
                self.verify_document_integrity(
                    cid,
                    expected_content_hash,
                    document_data=document,
                )
            )

            if not integrity_verified:
                raise ValueError(
                    "Document integrity verification failed "
                    f"for blockchain-resolved CID '{cid}'."
                )

        return {
            "document": document,
            "ipfs_cid": cid,
            "integrity_verified": integrity_verified,
            "integrity_pending": False,
            "application_encrypted": False,
        }

    # ==================================================================
    # Content hash calculation
    # ==================================================================

    @staticmethod
    def calculate_content_hash(
        document_data: Dict[str, Any],
    ) -> str:
        """
        Calculate the canonical SHA-256 content hash used for
        Phase 3 integrity verification.

        Canonical representation:

            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
            )

        This must be the same representation used when the
        corresponding blockchain contentHash is created.
        """

        if not isinstance(
            document_data,
            dict,
        ):
            raise TypeError(
                "document_data must be a dictionary"
            )

        canonical_json = json.dumps(
            document_data,
            sort_keys=True,
            separators=(",", ":"),
        )

        return hashlib.sha256(
            canonical_json.encode("utf-8")
        ).hexdigest()

    # ==================================================================
    # Integrity verification
    # ==================================================================

    def verify_document_integrity(
        self,
        ipfs_hash: str,
        expected_content_hash: str,
        *,
        document_data: Optional[
            Dict[str, Any]
        ] = None,
    ) -> bool:
        """
        Verify a PLAINTEXT document against its expected content hash.

        IMPORTANT:
            ``expected_content_hash`` normally comes from the blockchain.

            If ``document_data`` is an application-level encrypted envelope,
            this method returns False instead of hashing the envelope. The
            encrypted envelope is not the document content represented by
            the blockchain hash.

            For Phase 3 ACL-managed documents, the correct sequence is:

                IPFS envelope
                    -> ACL authorization
                    -> application decryption
                    -> canonical plaintext
                    -> calculate_content_hash()
                    -> compare with blockchain hash

        If document_data is already available, it is reused to avoid
        retrieving the same IPFS object twice.

        The canonical representation is:

            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
            )

        The comparison accepts both:
            - 64-character SHA-256 hexadecimal values
            - 0x-prefixed hexadecimal values
        """

        try:
            expected = str(
                expected_content_hash or ""
            ).strip().lower()

            if expected.startswith("0x"):
                expected = expected[2:]

            if not expected:
                logger.warning(
                    "Expected content hash is empty."
                )
                return False

            if document_data is None:
                document_data = (
                    self.retrieve_academic_document(
                        ipfs_hash
                    )
                )

            # ----------------------------------------------------------
            # CRITICAL PHASE 3 FIX:
            # Never compare an encrypted application-level envelope
            # against the blockchain plaintext content_hash.
            # ----------------------------------------------------------
            if (
                isinstance(document_data, dict)
                and (
                    document_data.get("enc") == "fernet-v1"
                    or (
                        "ciphertext" in document_data
                        and "kid" in document_data
                    )
                )
            ):
                logger.info(
                    "Skipping integrity verification for application-level "
                    "encrypted envelope at CID %s. Plaintext integrity must "
                    "be verified after authorized ACL decryption.",
                    ipfs_hash,
                )
                return False

            actual_hash = (
                self.calculate_content_hash(
                    document_data
                )
            )

            actual = actual_hash.lower()

            verified = (
                actual == expected
            )

            if not verified:
                logger.warning(
                    "Document integrity mismatch for CID %s: "
                    "expected=%s actual=%s",
                    ipfs_hash,
                    expected,
                    actual,
                )
            else:
                logger.info(
                    "Document integrity verified for CID %s.",
                    ipfs_hash,
                )

            return verified

        except Exception as exc:
            logger.error(
                "Failed to verify document integrity: %s",
                exc,
            )
            return False

    # ==================================================================
    # Compatibility helper for legacy JSON envelopes
    # ==================================================================

    def decrypt_json_envelope(
        self,
        encrypted_content: bytes,
    ) -> str:
        """
        Compatibility helper for older JSON-envelope data.

        Supported format:

            {
                "ciphertext": "<base64 ciphertext>"
            }

        The ciphertext is decrypted using the same persistent Fernet
        key used by the existing IPFS storage layer.

        This method is retained for compatibility and is not part of
        the normal Phase 3 retrieval path.
        """

        # --------------------------------------------------------------
        # First try the normal direct Fernet format.
        # --------------------------------------------------------------

        try:
            return self.decrypt_document(
                encrypted_content
            )

        except ValueError:
            pass

        # --------------------------------------------------------------
        # Try the legacy JSON envelope.
        # --------------------------------------------------------------

        try:
            wrapper = json.loads(
                bytes(
                    encrypted_content
                ).decode("utf-8")
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "IPFS data is neither valid Fernet ciphertext "
                f"nor a valid JSON envelope: {exc}"
            ) from exc

        if not isinstance(
            wrapper,
            dict,
        ):
            raise ValueError(
                "IPFS JSON envelope must be an object."
            )

        ciphertext_b64 = wrapper.get(
            "ciphertext"
        )

        if not ciphertext_b64:
            raise ValueError(
                "No ciphertext field found in IPFS JSON envelope."
            )

        try:
            cipher_bytes = (
                base64.b64decode(
                    ciphertext_b64
                )
            )

            decrypted_inner = (
                self.cipher_suite.decrypt(
                    cipher_bytes
                )
            )

            return decrypted_inner.decode(
                "utf-8"
            )

        except (
            ValueError,
            InvalidToken,
            UnicodeDecodeError,
        ) as exc:
            raise ValueError(
                f"Failed to decrypt IPFS JSON envelope: {exc}"
            ) from exc