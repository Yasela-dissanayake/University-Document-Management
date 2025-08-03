from blockchain_client import UniversityBlockchainClient
from web3.exceptions import ContractLogicError

def debug_semester_arrays():
    client = UniversityBlockchainClient()
    student_id = "S20841"
    
    print("=== DETAILED SEMESTER ARRAY DEBUGGING ===")
    
    # Test 1: Check if the contract has the function we expect
    try:
        print("📋 Available contract functions:")
        for func_name in client.contract.all_functions():
            if 'getStudent' in func_name.function_identifier or 'Semester' in func_name.function_identifier:
                print(f"   - {func_name.function_identifier}")
    except Exception as e:
        print(f"❌ Error listing functions: {e}")
    
    # Test 2: Try to access the student record directly
    try:
        print(f"\n🔍 Attempting to access student record directly...")
        # This should work if the mapping is public
        raw_student = client.contract.functions.students(student_id).call()
        print(f"✅ Raw student data: {raw_student}")
    except Exception as e:
        print(f"❌ Direct student access failed: {e}")
    
    # Test 3: Try to call the function with different approaches
    try:
        print(f"\n🔧 Testing different call methods...")
        
        # Method 1: Standard call
        try:
            result1 = client.contract.functions.getStudentSemesterHashes(student_id).call()
            print(f"✅ Method 1 (standard): {result1}")
        except ContractLogicError as e:
            print(f"❌ Method 1 failed: {e}")
            print(f"   Error details: {e.args}")
        
        # Method 2: Call with explicit block
        try:
            result2 = client.contract.functions.getStudentSemesterHashes(student_id).call(block_identifier='latest')
            print(f"✅ Method 2 (explicit block): {result2}")
        except ContractLogicError as e:
            print(f"❌ Method 2 failed: {e}")
            
        # Method 3: Try to get array length first (if there's such a function)
        try:
            # This won't work unless you have a specific function, but let's try
            length = client.contract.functions.getStudentSemesterCount(student_id).call()
            print(f"📊 Semester array length: {length}")
        except Exception as e:
            print(f"ℹ️ No semester count function available: {e}")
            
    except Exception as e:
        print(f"❌ All methods failed: {e}")
    
    # Test 4: Check contract bytecode to see if it matches
    try:
        print(f"\n🔍 Contract verification...")
        code = client.w3.eth.get_code(client.contract.address)
        print(f"📝 Contract has code: {len(code) > 0}")
        print(f"📝 Code length: {len(code)} bytes")
        
        # Try to estimate gas for the call
        try:
            gas_estimate = client.contract.functions.getStudentSemesterHashes(student_id).estimate_gas()
            print(f"⛽ Gas estimate: {gas_estimate}")
        except Exception as e:
            print(f"❌ Gas estimation failed: {e}")
            
    except Exception as e:
        print(f"❌ Contract verification failed: {e}")
    
    # Test 5: Create a simple test contract call
    print(f"\n🧪 Testing simpler contract functions...")
    try:
        count = client.contract.functions.getStudentCount().call()
        print(f"✅ getStudentCount works: {count}")
        
        exists = client.contract.functions.studentRecordExists(student_id).call()
        print(f"✅ studentRecordExists works: {exists}")
        
        details = client.contract.functions.getStudentDetails(student_id).call()
        print(f"✅ getStudentDetails works: {len(details)} fields")
        
    except Exception as e:
        print(f"❌ Simple functions also failing: {e}")

def test_manual_array_access():
    """Try to manually recreate what the function should do"""
    client = UniversityBlockchainClient()
    student_id = "S20841"
    
    print(f"\n=== MANUAL ARRAY RECONSTRUCTION ===")
    
    try:
        # Get the student details first
        details = client.get_student_details(student_id)
        if details:
            print(f"📝 Student found with latest hash: {details['documents_ipfs_hash']}")
            
            # Since we know the student exists and has at least one document,
            # the semester array should have at least one entry
            print(f"🔍 This suggests the semester arrays should contain at least 1 entry")
            print(f"💡 The issue might be in the array access logic in the contract")
        
        # Try to understand the contract structure better
        print(f"\n🏗️ Contract Analysis:")
        print(f"   - Student exists: ✅")
        print(f"   - Student details accessible: ✅")
        print(f"   - Semester arrays accessible: ❌")
        print(f"   - This suggests a problem with array initialization or access")
        
    except Exception as e:
        print(f"❌ Manual analysis failed: {e}")

if __name__ == "__main__":
    debug_semester_arrays()
    test_manual_array_access()