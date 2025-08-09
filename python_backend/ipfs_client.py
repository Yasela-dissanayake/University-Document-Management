import ipfshttpclient
import json
import hashlib
import logging
import os
from cryptography.fernet import Fernet
from typing import Dict, Any

class UniversityIPFSClient:
    def __init__(self, ipfs_api_url: str = '/ip4/127.0.0.1/tcp/5001'):
        """Initialize IPFS client for university document storage"""
        try:
            # self.client = ipfshttpclient.connect(ipfs_api_url)
            self.client = ipfshttpclient.Client(addr=ipfs_api_url, base="api/v0")


            # ---- Persistent Fernet Key Storage ----
            key_path = "fernet.key"
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

    def decrypt_document(self, encrypted_content: bytes) -> str:
        """Decrypt document content retrieved from IPFS"""
        return self.cipher_suite.decrypt(encrypted_content).decode('utf-8')

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

    def retrieve_academic_document(self, ipfs_hash: str) -> Dict[str, Any]:
        """Retrieve and decrypt academic document from IPFS"""
        try:
            encrypted_content = self.client.cat(ipfs_hash)
            decrypted_content = self.decrypt_document(encrypted_content)
            document_data = json.loads(decrypted_content)
            logging.info(f"Document retrieved from IPFS: {ipfs_hash}")
            return document_data
        except Exception as e:
            logging.error(f"Failed to retrieve document from IPFS: {e}")
            raise

    def verify_document_integrity(self, ipfs_hash: str, expected_content_hash: str) -> bool:
        """Verify document integrity using content hash"""
        try:
            document_data = self.retrieve_academic_document(ipfs_hash)
            document_json = json.dumps(document_data, indent=2, sort_keys=True)
            actual_hash = hashlib.sha256(document_json.encode()).hexdigest()
            return actual_hash == expected_content_hash
        except Exception as e:
            logging.error(f"Failed to verify document integrity: {e}")
            return False
