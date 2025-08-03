from blockchain_client import UniversityBlockchainClient
from ipfs_client import UniversityIPFSClient

def add_semester(student_id, document):
    ipfs_client = UniversityIPFSClient()
    blockchain_client = UniversityBlockchainClient()
    ipfs_result = ipfs_client.store_academic_document(document)
    tx_hash = blockchain_client.add_semester_record(student_id, ipfs_result['ipfs_hash'], ipfs_result['content_hash'])
    print(f"✅ Semester updated for {student_id}: {tx_hash[:16]}...")
    print(f"IPFS hash: {ipfs_result['ipfs_hash']}")

if __name__ == "__main__":
    # EXAMPLE: Second semester
    document = {
        "student_id": "S20841",
        "document_type": "academic_transcript",
        "timestamp": "2026-02-01T12:00:00Z",
        "courses": [
            {"code": "CSC1023", "name": "Object-oriented Programming", "grade": "A"},
  {"code": "CSC1051", "name": "Programming Laboratory II", "grade": "A"},
  {"code": "MAT1023", "name": "Real Analysis I", "grade": "A"},
  {"code": "MAT1083", "name": "Mathematical Programming", "grade": "A-"},
  {"code": "STA1023", "name": "Introduction to Probability Theory", "grade": "A"},
  {"code": "STA1041", "name": "Statistics Applications II", "grade": "A"}
        ],
        "gpa": 4.0
    }
    add_semester("S20841", document)
