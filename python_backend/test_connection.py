from blockchain_client import UniversityBlockchainClient
from ipfs_client import UniversityIPFSClient

def test_blockchain_ipfs_integration():
    print("🧪 Testing Blockchain + IPFS Integration...")
    
    try:
        # Test blockchain connection
        blockchain_client = UniversityBlockchainClient()
        print("✅ Blockchain client connected")
        
        # Test IPFS connection  
        ipfs_client = UniversityIPFSClient()
        print("✅ IPFS client connected")
        
        # Test document storage workflow
        test_document = {
            "document_type": "academic_transcript",
            "student_id": "S19357",
            "timestamp": "2025-08-02T12:00:00Z",
            "courses": [
                {"code": "BIO1002", "name": "Basic Life Sciences (Foundation Course)", "grade": "A-"},
                {"code": "CSC1002", "name": "Computer Applications", "grade": "A"},
                {"code": "CSC1013", "name": "Introduction to Computer Science and Programming", "grade": "A"},
                {"code": "CSC1041", "name": "Programming Laboratory I", "grade": "A"},
                {"code": "ENG1002", "name": "English for Academic Purposes", "grade": "A"},
                {"code": "MAT1013", "name": "Abstract Algebra I", "grade": "A+"},
                {"code": "MAT1032", "name": "Differential Equations", "grade": "A"},
                {"code": "MAT1042", "name": "Vector Methods", "grade": "B+"},
                {"code": "STA1013", "name": "Introduction to Statistics", "grade": "B+"},
                {"code": "STA1031", "name": "Statistical Applications I", "grade": "A"},
                {"code": "CSC1023", "name": "Object-oriented Programming", "grade": "C+"},
                {"code": "CSC1051", "name": "Programming Laboratory II", "grade": "A"},
                {"code": "MAT1023", "name": "Real Analysis I", "grade": "A"},
                {"code": "MAT1053", "name": "Classical Mechanics I", "grade": "A-"},
                {"code": "STA1023", "name": "Introduction to Probability Theory", "grade": "A"},
                {"code": "STA1041", "name": "Statistics Applications II", "grade": "A"},
                {"code": "CSC2012", "name": "Data Structures", "grade": "A"},
                {"code": "CSC2021", "name": "Programming Using Data Structures", "grade": "A"},
                {"code": "CSC2052", "name": "Computer Architecture", "grade": "A"},
                {"code": "CSC2102", "name": "Web Programming I", "grade": "A"},
                {"code": "ENG2002", "name": "English for Professional Purposes", "grade": "P"},
                {"code": "MAT2023", "name": "Real Analysis II", "grade": "B"},
                {"code": "MAT2092", "name": "Graph Theory", "grade": "A"},
                {"code": "STA2013", "name": "Probability Theory", "grade": "A"},
                {"code": "STA2042", "name": "Sampling Techniques", "grade": "B+"},
                {"code": "CSC2032", "name": "Database Management Systems", "grade": "C"},
                {"code": "CSC2041", "name": "Programming using Database Management Systems", "grade": "A"},
                {"code": "CSC2112", "name": "Introduction to Computer Networks", "grade": "B"},
                {"code": "MAT2072", "name": "Numerical Analysis I", "grade": "A"},
                {"code": "STA2033", "name": "Theory of Statistics", "grade": "B+"},
                {"code": "STA2102", "name": "Statistical Quality Control", "grade": "B+"},
                {"code": "CSC3093", "name": "Object Oriented Analysis and Design", "grade": "A-"},
                {"code": "CSC3103", "name": "Server Side Web Programming", "grade": "A"},
                {"code": "CSC3112", "name": "Software Engineering", "grade": "A"},
                {"code": "CSC3132", "name": "Digital Image Processing", "grade": "A-"},
                {"code": "CSC3141", "name": "Image Processing Laboratory", "grade": "A"},
                {"code": "CSC3152", "name": "Design and Analysis of Algorithms", "grade": "B+"},
                {"code": "CSC3213", "name": "Project in Computer Science I", "grade": "A"},
                {"code": "CSC3033", "name": "Operating Systems Concepts", "grade": "B+"},
                {"code": "CSC3073", "name": "Computer Graphics", "grade": "A"},
                {"code": "CSC3081", "name": "Computer Graphics Programming", "grade": "A-"},
                {"code": "CSC3182", "name": "Advanced Computer Networks", "grade": "A-"},
                {"code": "CSC3252", "name": "Scientific Writing and Presentation", "grade": "A-"},
                {"code": "MAT3092", "name": "Combinatorics", "grade": "A-"},
                {"code": "CSC4063", "name": "Distributed Computing", "grade": "B+"}
    ],

            "gpa": 3.69,
            "remarks": "Excellent performance."
        }
        
        # Store on IPFS
        ipfs_result = ipfs_client.store_academic_document(test_document)
        print(f"✅ Test document stored on IPFS: {ipfs_result['ipfs_hash'][:16]}...")
        
        # Register on blockchain
        student_data = {
            "student_id": "S19357",
            "name": "Yasela Dissanayake", 
            "program": "Computer Science",
            "year": 4
        }
        
        tx_hash = blockchain_client.register_student_record(
            student_data,
            ipfs_result['ipfs_hash'],
            ipfs_result['content_hash']
        )
        print(f"✅ Student registered on blockchain: {tx_hash[:16]}...")
        
        # Verify retrieval
        student_details = blockchain_client.get_student_details("S19357")
        if student_details:
            print(f"✅ Student details retrieved: {student_details['name']}")
        
        print("\n🎉 All tests passed! Blockchain + IPFS integration working!")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == "__main__":
    test_blockchain_ipfs_integration()
