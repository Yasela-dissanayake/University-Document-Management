from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend.ipfs_client import UniversityIPFSClient

# Instantiate clients at module-level, or inject via constructor for testability
blockchain_client = UniversityBlockchainClient()
ipfs_client = UniversityIPFSClient()

def get_onchain_student(state):
    # Naively extract student_id from question; improve parsing as needed!
    question = state['question']
    # For now, just look for a pattern; replace with real parse/NLP as you improve
    import re
    match = re.search(r'(?:student|id|ID|Student)[^a-zA-Z0-9]?([A-Z]\d{5})', question)
    student_id = (match.group(1) if match else None) or question.strip().split()[-1]
    if not student_id:
        return {"onchain": {"error": "No student_id detected."}}
    try:
        student = blockchain_client.get_student_details(student_id)
    except Exception:
        return {"onchain": {"error": f"Student {student_id} not found."}}
    return {"onchain": student, "student_id": student_id}

def get_offchain_semester(state):
    # Get student_id and (optionally) semester index from state or question
    student_id = state.get("student_id") or ""
    question = state["question"]
    # Parse semester number if given, else default to last
    import re
    match = re.search(r"semester\s*(\d+)", question, re.IGNORECASE)
    semester_idx = int(match.group(1)) - 1 if match else -1
    all_hashes = blockchain_client.get_all_semester_hashes(student_id)
    if not all_hashes:
        return {"offchain": {"error": "No semester records found."}}
    ipfs_hash = all_hashes[semester_idx]
    try:
        doc = ipfs_client.retrieve_academic_document(ipfs_hash)
    except Exception:
        doc = {"error": f"Could not decrypt semester at {ipfs_hash}"}
    return {"offchain": doc}
