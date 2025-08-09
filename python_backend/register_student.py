from blockchain_client import UniversityBlockchainClient
from ipfs_client import UniversityIPFSClient

def register_student(student_id, name, program, year, document):
    ipfs_client = UniversityIPFSClient()
    blockchain_client = UniversityBlockchainClient()
    
    # Store the first semester document on IPFS (encrypted)
    ipfs_result = ipfs_client.store_academic_document(document)
    student_data = {
        "student_id": student_id,
        "name": name,
        "program": program,
        "year": year
    }
    # Register the student and the first semester/grades on-chain
    tx_hash = blockchain_client.register_student_record(
        student_data,
        ipfs_result['ipfs_hash'],
        ipfs_result['content_hash']
    )
    print(f"✅ Registered student {student_id}")
    print(f"Tx hash: {tx_hash}")
    print(f"First semester IPFS hash: {ipfs_result['ipfs_hash']}")

if __name__ == "__main__":
    # Example student, you may edit these values as needed
    student_id = "S19819"
    name = "Sathija Bhanuka"
    program = "Computer Science Honours"
    year = 4
    first_semester = {
        "student_id": student_id,
        "document_type": "academic_transcript",
        "timestamp": "2025-08-01T12:00:00Z",
        "courses": [
             {"code": "BIO1002", "name": "Basic Life Sciences (Foundation Course)", "grade": "A"},
  {"code": "CSC1002", "name": "Computer Applications", "grade": "A"},
  {"code": "CSC1013", "name": "Introduction to Computer Science and Programming", "grade": "A"},
  {"code": "CSC1041", "name": "Programming Laboratory I", "grade": "A"},
  {"code": "ENG1002", "name": "English for Academic Purposes", "grade": "B+"},
  {"code": "MAT1073", "name": "Mathematics for Operations Research", "grade": "A"},
  {"code": "MAT1092", "name": "Introduction to Mathematical Computing", "grade": "A"},
  {"code": "SCI1041", "name": "Essential Skills for Career Development", "grade": "0"},
  {"code": "STA1013", "name": "Introduction to Statistics", "grade": "A+"},
  {"code": "STA1031", "name": "Statistical Applications I", "grade": "A+"},
        ],
        "gpa": 4.0,
        "remarks": "Excellent first semester."
    }
    register_student(student_id, name, program, year, first_semester)
