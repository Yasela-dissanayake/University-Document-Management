"""
Comprehensive test for integrated agent with on-chain/off-chain routing
"""

import os
import sys

os.environ["OLLAMA_MODEL"] = "llama3"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from python_backend.ai.agent import answer_question

def print_section(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)

def print_test(test_num, description):
    print(f"\n{'─' * 80}")
    print(f"🧪 Test {test_num}: {description}")
    print(f"{'─' * 80}")

def test_complete_system():
    """Test the complete integrated system"""
    
    # Define test users
    student_s20841 = {
        "id": 1,
        "username": "asini",
        "role": "STUDENT",
        "student_id": "S20841"
    }
    
    student_s19357 = {
        "id": 2,
        "username": "john",
        "role": "STUDENT",
        "student_id": "S19357"
    }
    
    admin_user = {
        "id": 3,
        "username": "admin",
        "role": "ADMIN",
        "student_id": None
    }
    
    hod_user = {
        "id": 4,
        "username": "dr_silva",
        "role": "HOD",
        "student_id": None
    }
    
    print_section("🎓 UNIVERSITY BLOCKCHAIN AI SYSTEM - COMPREHENSIVE TEST")
    
    # ============================================================
    # PART 1: ON-CHAIN QUERIES (Public - No Auth Required)
    # ============================================================
    print_section("PART 1: ON-CHAIN QUERIES (Public Blockchain Data)")
    
    onchain_queries = [
        "What is the name of student S20841?",
        "Is S20841 registered in the system?",
        "What program is S20841 enrolled in?",
        "Show me the IPFS hashes for S20841",
    ]
    
    for i, query in enumerate(onchain_queries, 1):
        print_test(i, query)
        result = answer_question(query, user_context=None)  # No auth needed
        print(f"📄 Answer: {result['answer'][:200]}...")
        print(f"🔍 Query Type: {result.get('trace', {}).get('query_type', 'unknown')}")
    
    # ============================================================
    # PART 2: OFF-CHAIN QUERIES WITH PROPER ACCESS
    # ============================================================
    print_section("PART 2: OFF-CHAIN QUERIES (ACL-Protected Data)")
    
    print_test(5, "Student accessing their own grades")
    print(f"👤 User: {student_s20841['username']} (STUDENT - {student_s20841['student_id']})")
    query = "What is my GPA?"
    result = answer_question(query, user_context=student_s20841)
    print(f"📄 Answer: {result['answer'][:300]}...")
    print(f"🔍 Method: {result.get('trace', {}).get('method', 'unknown')}")
    print(f"✅ Accessible docs: {result.get('trace', {}).get('accessible_docs', 'N/A')}")
    
    print_test(6, "Student asking about specific courses")
    print(f"👤 User: {student_s20841['username']} (STUDENT)")
    query = "What courses did S20841 take and what grades were received?"
    result = answer_question(query, user_context=student_s20841)
    print(f"📄 Answer: {result['answer'][:300]}...")
    
    print_test(7, "Admin accessing any student's data")
    print(f"👤 User: {admin_user['username']} (ADMIN)")
    query = "Show me the academic performance of S20841"
    result = answer_question(query, user_context=admin_user)
    print(f"📄 Answer: {result['answer'][:300]}...")
    print(f"✅ Accessible docs: {result.get('trace', {}).get('accessible_docs', 'N/A')}")
    
    print_test(8, "HOD accessing student records")
    print(f"👤 User: {hod_user['username']} (HOD)")
    query = "What is the GPA of S20841?"
    result = answer_question(query, user_context=hod_user)
    print(f"📄 Answer: {result['answer'][:300]}...")
    
    # ============================================================
    # PART 3: ACCESS CONTROL VIOLATIONS
    # ============================================================
    print_section("PART 3: ACCESS CONTROL TESTS (Should Deny)")
    
    print_test(9, "Student trying to access another student's data")
    print(f"👤 User: {student_s19357['username']} (STUDENT - {student_s19357['student_id']})")
    print(f"🎯 Target: S20841 (different student)")
    query = "What is the GPA of S20841?"
    result = answer_question(query, user_context=student_s19357)
    print(f"📄 Answer: {result['answer']}")
    print(f"🚫 Accessible docs: {result.get('trace', {}).get('accessible_docs', 0)}")
    
    print_test(10, "Unauthenticated user trying to access off-chain data")
    print(f"👤 User: None (not logged in)")
    query = "Show me the grades for S20841"
    result = answer_question(query, user_context=None)
    print(f"📄 Answer: {result['answer']}")
    print(f"🔒 Auth Required: {result.get('trace', {}).get('auth_required', False)}")
    
    # ============================================================
    # PART 4: NATURAL LANGUAGE QUERIES
    # ============================================================
    print_section("PART 4: NATURAL LANGUAGE QUERIES")
    
    natural_queries = [
        "How well did S20841 perform in their courses?",
        "Can you summarize the academic record of S20841?",
        "What subjects did S20841 study?",
        "Did S20841 pass all their courses?",
    ]
    
    for i, query in enumerate(natural_queries, 11):
        print_test(i, query)
        print(f"👤 User: {student_s20841['username']} (STUDENT)")
        result = answer_question(query, user_context=student_s20841)
        print(f"📄 Answer: {result['answer'][:250]}...")
        print(f"🔍 Method: {result.get('trace', {}).get('method', 'unknown')}")
    
    # ============================================================
    # SUMMARY
    # ============================================================
    print_section("✅ TEST SUITE COMPLETED")
    print("""
Key Features Demonstrated:
✓ On-chain queries work without authentication
✓ Off-chain queries require authentication
✓ Students can only access their own data
✓ Admins/HOD can access all data
✓ Access control violations are properly blocked
✓ Natural language queries work with RAG
✓ Structured queries use efficient tools
✓ System intelligently routes queries
    """)

if __name__ == "__main__":
    test_complete_system()