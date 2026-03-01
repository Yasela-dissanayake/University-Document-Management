import ipfshttpclient
import json
import hashlib
import logging
import os
from cryptography.fernet import Fernet
from typing import Dict, Any

import base64
from cryptography.fernet import InvalidToken
import pathlib

class UniversityIPFSClient:
    def __init__(self, ipfs_api_url: str = '/ip4/127.0.0.1/tcp/5001'):
        """Initialize IPFS client for university document storage"""
        try:
            # self.client = ipfshttpclient.connect(ipfs_api_url)
            self.client = ipfshttpclient.Client(addr=ipfs_api_url, base="api/v0")


            # ---- Persistent Fernet Key Storage ----
            # key_path = "fernet.key"
            # Always use the project root's key file (same as Flask)
            root_dir = pathlib.Path(__file__).resolve().parent.parent  # => python_backend/
            key_path = os.path.join(root_dir, "fernet.key")

            if os.path.exists(key_path):
                with open(key_path, "rb") as key_file:
                    self.encryption_key = key_file.read()
            else:
                self.encryption_key = Fernet.generate_key()
                with open(key_path, "wb") as key_file:
                    key_file.write(self.encryption_key)
            self.cipher_suite = Fernet(self.encryption_key)
            # ----------------------------------------

            logging.info("IPFS client initialized successfully")
        except Exception as e:
            logging.error(f"Failed to connect to IPFS: {e}")
            raise

    def encrypt_document(self, content: str) -> bytes:
        """Encrypt document content before storing on IPFS"""
        return self.cipher_suite.encrypt(content.encode('utf-8'))

    # def decrypt_document(self, encrypted_content: bytes) -> str:
    #     """
    #     Decrypt document content retrieved from IPFS.

    #     Handles both:
    #     - Direct Fernet ciphertext bytes.
    #     - JSON envelope objects with 'ciphertext' field (like your current data).
    #     """
    #     # Case 1: direct Fernet bytes (legacy)
    #     try:
    #         return self.cipher_suite.decrypt(encrypted_content).decode("utf-8")
    #     except (InvalidToken, Exception):
    #         pass

    #     # Case 2: JSON envelope
    #     try:
    #         # Parse JSON wrapper
    #         wrapper = json.loads(encrypted_content.decode("utf-8"))
    #         if isinstance(wrapper, dict) and "ciphertext" in wrapper:
    #             cipher_b64 = wrapper["ciphertext"]
    #             # Decode base64 -> bytes
    #             cipher_bytes = base64.b64decode(cipher_b64)
    #             # Decrypt inner ciphertext
    #             decrypted_inner = self.cipher_suite.decrypt(cipher_bytes)
    #             # The decrypted inner content is a JSON string
    #             return decrypted_inner.decode("utf-8")
    #         raise ValueError("No ciphertext field in IPFS data.")
    #     except (json.JSONDecodeError, InvalidToken, Exception) as e:
    #         raise ValueError(f"Failed to decrypt IPFS JSON wrapper: {e}")
        

    def store_academic_document(self, document_data: Dict[str, Any]) -> Dict[str, str]:
        """Store academic document on IPFS and return metadata"""
        try:
            # Prepare document for storage
            document_json = json.dumps(document_data, indent=2)
            encrypted_content = self.encrypt_document(document_json)
            
            # Upload to IPFS
            ipfs_hash = self.client.add_bytes(encrypted_content)
            
            # Generate document metadata
            content_hash = hashlib.sha256(document_json.encode()).hexdigest()
            metadata = {
                'ipfs_hash': ipfs_hash,
                'content_hash': content_hash,
                'document_type': document_data.get('document_type', 'unknown'),
                'student_id': document_data.get('student_id', ''),
                'timestamp': document_data.get('timestamp', ''),
                'size_bytes': len(encrypted_content)
            }
            logging.info(f"Document stored on IPFS: {ipfs_hash}")
            return metadata

        except Exception as e:
            logging.error(f"Failed to store document on IPFS: {e}")
            raise

    # def retrieve_academic_document(self, ipfs_hash: str) -> Dict[str, Any]:
    #     """Retrieve and decrypt academic document from IPFS"""
    #     try:
    #         # Step 1: Fetch raw encrypted data
    #         encrypted_content = self.client.cat(ipfs_hash)

    #         # Step 2: Attempt decryption
    #         decrypted_content = self.decrypt_document(encrypted_content)

    #         # Step 3: If decrypt_document already returns dict, just return it
    #         if isinstance(decrypted_content, dict):
    #             return decrypted_content

    #         # Step 4: Otherwise, if it's a string, try to parse JSON
    #         try:
    #             document_data = json.loads(decrypted_content)
    #         except Exception:
    #             # Not valid JSON, return raw text
    #             logging.warning("Decrypted content is not valid JSON, returning raw text.")
    #             document_data = {"raw_text": decrypted_content}

    #         logging.info(f"✅ Document successfully decrypted and loaded from IPFS: {ipfs_hash}")
    #         return document_data

    #     except Exception as e:
    #         logging.error(f"❌ Failed to retrieve document from IPFS: {e}")
    #         raise

    def decrypt_document(self, encrypted_content: bytes) -> str:
        """
        Decrypt document content retrieved from IPFS.
        
        This only decrypts the OUTER layer (IPFS storage encryption).
        If the result contains a nested 'ciphertext' field with 'kid' (key ID),
        that's application-level encryption and should be handled by the frontend.
        """
        from cryptography.fernet import InvalidToken
        
        try:
            # Decrypt the outer Fernet layer (IPFS storage encryption)
            decrypted = self.cipher_suite.decrypt(encrypted_content)
            decrypted_str = decrypted.decode("utf-8")
            
            # Return the decrypted content as-is
            # If it contains nested encryption, that's intentional
            return decrypted_str
                
        except InvalidToken as e:
            raise ValueError(f"Failed to decrypt IPFS document - invalid encryption key: {e}")
        except Exception as e:
            raise ValueError(f"Failed to decrypt IPFS document: {e}")


    def retrieve_academic_document(self, ipfs_hash: str) -> Dict[str, Any]:
        """
        Retrieve and decrypt academic document from IPFS.
        
        Note: This only decrypts the IPFS storage layer. Documents may contain
        additional application-level encryption that should be handled by the client.
        """
        try:
            # Step 1: Fetch raw encrypted data from IPFS
            encrypted_content = self.client.cat(ipfs_hash)

            # Step 2: Decrypt the IPFS storage layer
            decrypted_content = self.decrypt_document(encrypted_content)

            # Step 3: Parse as JSON
            try:
                document_data = json.loads(decrypted_content)
                
                # Check if this is still encrypted at application level
                if isinstance(document_data, dict) and "ciphertext" in document_data and "kid" in document_data:
                    logging.warning(f"⚠️  Document {ipfs_hash} contains nested encryption with key ID: {document_data.get('kid')}")
                    logging.warning("    This document needs application-level decryption (likely frontend-only)")
                
                logging.info(f"✅ Document successfully retrieved from IPFS: {ipfs_hash}")
                return document_data
                
            except json.JSONDecodeError as e:
                # Not valid JSON, return as raw text wrapped in dict
                logging.warning(f"Decrypted content is not valid JSON, returning raw text: {e}")
                return {"raw_text": decrypted_content}

        except Exception as e:
            logging.error(f"❌ Failed to retrieve document from IPFS: {ipfs_hash} - {e}")
            raise

    def verify_document_integrity(self, ipfs_hash: str, expected_content_hash: str) -> bool:
        """
        Verify document integrity by comparing content hashes.

        IMPORTANT FIX: The content_hash stored on-chain is computed in acl.py as:
            sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')))  (compact)
        This method must use the same serialization to avoid always-False comparisons.
        """
        try:
            document_data = self.retrieve_academic_document(ipfs_hash)
            # Use compact JSON (no indent) to match acl.py's _canonical_bytes()
            document_json = json.dumps(document_data, sort_keys=True, separators=(',', ':'))
            actual_hash = hashlib.sha256(document_json.encode('utf-8')).hexdigest()
            return actual_hash == expected_content_hash
        except Exception as e:
            logging.error(f"Failed to verify document integrity: {e}")
            return False
