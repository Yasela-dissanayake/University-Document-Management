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
        "student_id": "S19357",
        "document_type": "academic_transcript",
        "timestamp": "2026-02-01T12:00:00Z",
        "courses": [
  {"code": "CSC2012", "name": "Data Structures", "grade": "A"},
  {"code": "CSC2021", "name": "Programming Using Data Structures", "grade": "A"},
  {"code": "CSC2052", "name": "Computer Architecture", "grade": "A"},
  {"code": "CSC2102", "name": "Web Programming I", "grade": "A"},
  {"code": "ENG2002", "name": "English for Professional Purposes", "grade": "P"},
  {"code": "MAT2023", "name": "Real Analysis II", "grade": "B"},
  {"code": "MAT2092", "name": "Graph Theory", "grade": "A"},
        ],
        "gpa": 3.6
    }
    add_semester("S19357", document)
