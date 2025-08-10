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
    student_id = "S19357"
    name = "Yasela Dissanayake"
    program = "Computer Science"
    year = 1
    first_semester = {
        "student_id": student_id,
        "document_type": "academic_transcript",
        "timestamp": "2025-08-01T12:00:00Z",
        "courses": [
             {"code": "BIO1002", "name": "Basic Life Sciences (Foundation Course)", "grade": "A-"},
  {"code": "CSC1002", "name": "Computer Applications", "grade": "A"},
  {"code": "CSC1013", "name": "Introduction to Computer Science and Programming", "grade": "A"},
  {"code": "CSC1041", "name": "Programming Laboratory I", "grade": "A"},
  {"code": "ENG1002", "name": "English for Academic Purposes", "grade": "A"},
  {"code": "MAT1013", "name": "Abstract Algebra I", "grade": "A+"},
  {"code": "MAT1032", "name": "Differential Equations", "grade": "A"},
  {"code": "MAT1042", "name": "Vector Methods", "grade": "B+"},
  {"code": "STA1013", "name": "Introduction to Statistics", "grade": "A"},
  {"code": "STA1031", "name": "Statistical Applications I", "grade": "A"},
  {"code": "CSC1023", "name": "Object-oriented Programming", "grade": "C+"},
  {"code": "CSC1051", "name": "Programming Laboratory II", "grade": "A"},
        ],
        "gpa": 3.8,
        "remarks": "Excellent first semester."
    }
    register_student(student_id, name, program, year, first_semester)
