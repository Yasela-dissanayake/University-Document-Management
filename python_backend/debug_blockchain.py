from blockchain_client import UniversityBlockchainClient
import traceback

def comprehensive_debug():
    try:
        client = UniversityBlockchainClient()
        student_id = "S20841"
        
        print("=== BLOCKCHAIN CONNECTION TEST ===")
        print(f"✅ Connected to: {client.network_url}")
        print(f"📜 Contract Address: {client.contract_address}")
        print(f"👤 Account Address: {client.account.address}")
        print(f"💰 Account Balance: {client.w3.eth.get_balance(client.account.address) / 10**18:.4f} ETH")
        
        print(f"\n=== STUDENT RECORD VERIFICATION ===")
        
        # Test 1: Check if student exists using the contract method
        try:
            exists = client.contract.functions.studentRecordExists(student_id).call()
            print(f"🔍 Student {student_id} exists: {exists}")
        except Exception as e:
            print(f"❌ Error checking if student exists: {e}")
        
        # Test 2: Get total student count
        try:
            count = client.contract.functions.getStudentCount().call()
            print(f"📊 Total students in contract: {count}")
        except Exception as e:
            print(f"❌ Error getting student count: {e}")
        
        # Test 3: Get all student IDs
        try:
            all_ids = client.contract.functions.getAllStudentIds().call()
            print(f"📋 All student IDs: {all_ids}")
        except Exception as e:
            print(f"❌ Error getting all student IDs: {e}")
        
        # Test 4: Try to get student details
        try:
            details = client.get_student_details(student_id)
            if details:
                print(f"📝 Student details found:")
                for key, value in details.items():
                    print(f"   {key}: {value}")
            else:
                print(f"❌ No student details found for {student_id}")
        except Exception as e:
            print(f"❌ Error getting student details: {e}")
            print(f"   Exception type: {type(e).__name__}")
            print(f"   Exception details: {str(e)}")
        
        # Test 5: Try to get semester hashes with more detailed error handling
        print(f"\n=== SEMESTER HASHES TEST ===")
        try:
            # Direct contract call with error handling
            result = client.contract.functions.getStudentSemesterHashes(student_id).call()
            print(f"✅ Direct contract call successful: {result}")
        except Exception as e:
            print(f"❌ Direct contract call failed: {e}")
            print(f"   Exception type: {type(e).__name__}")
            
            # Try to decode the error
            if hasattr(e, 'args') and len(e.args) > 0:
                if isinstance(e.args[0], tuple) and len(e.args[0]) > 1:
                    error_data = e.args[0][1]
                    print(f"   Error data: {error_data}")
                    
                    # Try to decode revert reason if it's a string
                    if isinstance(error_data, str) and error_data.startswith('0x'):
                        try:
                            # Remove 0x and decode hex
                            hex_data = error_data[2:]
                            if len(hex_data) > 8:  # Skip function selector
                                decoded = bytes.fromhex(hex_data[8:])
                                # Try to decode as string
                                try:
                                    reason = decoded.decode('utf-8').strip('\x00')
                                    print(f"   Decoded revert reason: '{reason}'")
                                except:
                                    print(f"   Raw decoded bytes: {decoded}")
                        except Exception as decode_error:
                            print(f"   Could not decode error data: {decode_error}")
        
        # Test 6: Check recent transactions for this student
        print(f"\n=== RECENT ACTIVITY CHECK ===")
        try:
            # Get recent blocks to see if transactions went through
            latest_block = client.w3.eth.get_block('latest')
            print(f"📦 Latest block: {latest_block['number']}")
            print(f"⏰ Latest block timestamp: {latest_block['timestamp']}")
            
            # Check if we can find any transactions from our account
            recent_txs = []
            for i in range(5):  # Check last 5 blocks
                block_num = latest_block['number'] - i
                block = client.w3.eth.get_block(block_num, full_transactions=True)
                for tx in block['transactions']:
                    if tx['from'].lower() == client.account.address.lower():
                        recent_txs.append({
                            'hash': tx['hash'].hex(),
                            'to': tx['to'],
                            'block': block_num
                        })
            
            print(f"🔄 Recent transactions from your account: {len(recent_txs)}")
            for tx in recent_txs[-3:]:  # Show last 3
                print(f"   {tx['hash'][:16]}... -> {tx['to']} (block {tx['block']})")
                
        except Exception as e:
            print(f"❌ Error checking recent activity: {e}")
            
    except Exception as e:
        print(f"❌ Critical error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    comprehensive_debug()