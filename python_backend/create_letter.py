# python_backend/create_letter.py
import time
from ipfs_client import UniversityIPFSClient
from blockchain_client import UniversityBlockchainClient

def create_letter(student_id: str, title: str, body: str, issuer_role: str):
    ipfs_client = UniversityIPFSClient()
    blockchain_client = UniversityBlockchainClient()

    # Document JSON metadata
    document = {
        "student_id": student_id,
        "document_type": "letter",
        "title": title,
        "body": body,
        "issuer_role": issuer_role,
        "timestamp": int(time.time()),
        "status": "PENDING_APPROVAL"
    }

    # Upload to IPFS
    ipfs_result = ipfs_client.store_academic_document(document)
    tx_hash = blockchain_client.add_letter_record(
        student_id=student_id,
        documents_ipfs_hash=ipfs_result["ipfs_hash"],
        content_hash=ipfs_result["content_hash"],
        timestamp=document["timestamp"],
    )

    print(f"✅ Letter created and stored for {student_id}")
    print(f"IPFS CID: {ipfs_result['ipfs_hash']}")
    print(f"Blockchain Tx: {tx_hash[:16]}...")

if __name__ == "__main__":
    create_letter(
        student_id="S19357",
        title="Request for Exam Approval",
        body="This is to request approval for semester-end examination submission.",
        issuer_role="HOD",
    )
