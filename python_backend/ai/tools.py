from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend.ipfs_client import UniversityIPFSClient

# Instantiate clients at module-level, or inject via constructor for testability
blockchain_client = UniversityBlockchainClient()
ipfs_client = UniversityIPFSClient()

def get_onchain_student(state):
    """Get student details from blockchain"""
    question = state['question']
    
    # Improved student ID extraction with multiple patterns
    import re
    
    # Try multiple patterns to extract student ID
    patterns = [
        r'(?:student|id|ID|Student)\s*([A-Z]\d{5})',  # "student S12345"
        r'\b([A-Z]\d{5})\b',  # Just "S12345" as a word
        r'([A-Z]\d{4,6})',    # More flexible digit count
    ]
    
    student_id = None
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            student_id = match.group(1)
            break
    
    # Fallback: try the last word if it looks like a student ID
    if not student_id:
        words = question.strip().split()
        for word in reversed(words):  # Check from end
            if re.match(r'^[A-Z]\d{4,6}$', word):
                student_id = word
                break
    
    if not student_id:
        return {"onchain": {"error": "No student_id detected."}}
    
    print(f"🔍 Looking for student: {student_id}")
    
    try:
        student = blockchain_client.get_student_details(student_id)
        if student is None:
            return {"onchain": {"error": f"Student {student_id} not found."}}
        
        print(f"✅ Found student data: {student}")
        return {"onchain": student, "student_id": student_id}
        
    except Exception as e:
        print(f"❌ Error getting student {student_id}: {e}")
        return {"onchain": {"error": f"Error retrieving student {student_id}: {str(e)}"}}

def get_offchain_semester(state):
    """Get semester records from IPFS via blockchain hashes"""
    # Get student_id from previous step
    student_id = state.get("student_id")
    if not student_id:
        return {"offchain": {"error": "No student_id available from previous step."}}
    
    question = state["question"]
    
    # Parse semester number if given, else default to last (-1 index)
    import re
    match = re.search(r"semester\s*(\d+)", question, re.IGNORECASE)
    semester_idx = int(match.group(1)) - 1 if match else -1
    
    print(f"🔍 Getting semester records for student: {student_id}")
    
    try:
        all_hashes = blockchain_client.get_all_semester_hashes(student_id)
        if not all_hashes:
            return {"offchain": {"error": "No semester records found."}}
        
        print(f"📋 Found {len(all_hashes)} semester records")
        
        # Handle negative indexing and bounds checking
        if semester_idx < 0:
            semester_idx = len(all_hashes) + semester_idx
        
        if semester_idx < 0 or semester_idx >= len(all_hashes):
            return {"offchain": {"error": f"Semester index {semester_idx + 1} out of range. Available: 1-{len(all_hashes)}"}}
        
        ipfs_hash = all_hashes[semester_idx]
        print(f"📄 Retrieving document from IPFS: {ipfs_hash}")
        
        try:
            doc = ipfs_client.retrieve_academic_document(ipfs_hash)
            return {"offchain": doc}
        except Exception as e:
            print(f"❌ IPFS retrieval error: {e}")
            return {"offchain": {"error": f"Could not retrieve semester document: {str(e)}"}}
            
    except Exception as e:
        print(f"❌ Error getting semester hashes: {e}")
        return {"offchain": {"error": f"Error retrieving semester records: {str(e)}"}}