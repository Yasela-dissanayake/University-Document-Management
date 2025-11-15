"""
Debug script to test double-decryption step-by-step
"""
import sys
import os
import json
import base64
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from python_backend.ipfs_client import UniversityIPFSClient

def test_double_decryption():
    """Test double-layer decryption"""
    print("=" * 70)
    print("🔐 DEBUGGING DOUBLE-ENCRYPTION IPFS DECRYPTION")
    print("=" * 70)
    
    # Test with first hash
    test_hash = "QmdB88ui2P3yckT63KR1Ko3bGR729xRhqUwiPpVoLoaYYw"
    
    # Initialize IPFS client
    print("\n📌 Step 1: Initializing IPFS client...")
    ipfs_client = UniversityIPFSClient()
    print("✅ IPFS client initialized")
    
    # Fetch raw data
    print(f"\n📌 Step 2: Fetching raw data from IPFS: {test_hash[:20]}...")
    encrypted_content = ipfs_client.client.cat(test_hash)
    print(f"✅ Fetched {len(encrypted_content)} bytes")
    print(f"Raw bytes preview: {encrypted_content[:100]}")
    
    # First decryption
    print("\n📌 Step 3: First Fernet decryption (outer layer)...")
    try:
        first_decrypt = ipfs_client.cipher_suite.decrypt(encrypted_content)
        first_str = first_decrypt.decode("utf-8")
        print(f"✅ First decryption successful! Got {len(first_str)} chars")
        print(f"First 300 chars:")
        print("-" * 70)
        print(first_str[:300])
        print("-" * 70)
        
        # Parse as JSON
        print("\n📌 Step 4: Parsing first decryption as JSON...")
        try:
            wrapper = json.loads(first_str)
            print("✅ Successfully parsed as JSON!")
            print(f"Keys: {list(wrapper.keys())}")
            
            if "ciphertext" in wrapper:
                print(f"✅ Found nested 'ciphertext' field (double encryption detected!)")
                cipher_b64 = wrapper["ciphertext"]
                print(f"Inner ciphertext length: {len(cipher_b64)} chars (base64)")
                print(f"Inner ciphertext preview: {cipher_b64[:100]}...")
                
                # Decode base64
                print("\n📌 Step 5: Decoding inner ciphertext from base64...")
                cipher_bytes = base64.b64decode(cipher_b64)
                print(f"✅ Decoded to {len(cipher_bytes)} bytes")
                
                # Second decryption
                print("\n📌 Step 6: Second Fernet decryption (inner layer)...")
                try:
                    second_decrypt = ipfs_client.cipher_suite.decrypt(cipher_bytes)
                    final_content = second_decrypt.decode("utf-8")
                    print(f"✅ Second decryption successful! Got {len(final_content)} chars")
                    print(f"Final decrypted content (first 500 chars):")
                    print("-" * 70)
                    print(final_content[:500])
                    print("-" * 70)
                    
                    # Parse final content
                    print("\n📌 Step 7: Parsing final content as JSON...")
                    try:
                        final_data = json.loads(final_content)
                        print("✅ Successfully parsed final JSON!")
                        print(f"Keys: {list(final_data.keys())}")
                        print(f"\nFull document:")
                        print("=" * 70)
                        print(json.dumps(final_data, indent=2))
                        print("=" * 70)
                    except json.JSONDecodeError as e:
                        print(f"⚠️ Final content is not JSON: {e}")
                        
                except Exception as e:
                    print(f"❌ Second decryption failed: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print("ℹ️ No nested encryption, single-layer only")
                print(f"Content: {wrapper}")
                
        except json.JSONDecodeError as e:
            print(f"⚠️ First decryption is not JSON: {e}")
            print(f"Content: {first_str[:500]}")
            
    except Exception as e:
        print(f"❌ First decryption failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Test with updated method
    print("\n" + "=" * 70)
    print("📌 Testing updated retrieve_academic_document method...")
    print("=" * 70)
    try:
        doc = ipfs_client.retrieve_academic_document(test_hash)
        print("✅ Method succeeded!")
        print(f"Type: {type(doc)}")
        if isinstance(doc, dict):
            print(f"Keys: {list(doc.keys())}")
            print(f"\nDocument content:")
            print(json.dumps(doc, indent=2))
        else:
            print(f"Content: {doc}")
    except Exception as e:
        print(f"❌ Method failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Test all hashes
    print("\n" + "=" * 70)
    print("📌 Testing all 4 IPFS hashes...")
    print("=" * 70)
    
    all_hashes = [
        "QmdB88ui2P3yckT63KR1Ko3bGR729xRhqUwiPpVoLoaYYw",
        "QmZQ1JmVTmjsDFqKNr1CQdr3fjaZkkQeMP6SSjySxHzgCc",
        "QmXnPeKXGgEZMJ8ucJjVft9DSebAXQLD2wsfgSnwRa1oYk",
        "QmbCrNU8HEZvw6KWpZHKtJriLLbZnZt1N2TwxUTcQ5qXrd"
    ]
    
    for i, hash_val in enumerate(all_hashes, 1):
        print(f"\n📄 Document {i}/4: {hash_val[:20]}...")
        try:
            doc = ipfs_client.retrieve_academic_document(hash_val)
            if isinstance(doc, dict):
                print(f"   ✅ Keys: {list(doc.keys())[:5]}...")
                if 'student_id' in doc:
                    print(f"   Student ID: {doc['student_id']}")
                if 'gpa' in doc or 'GPA' in doc:
                    print(f"   GPA: {doc.get('gpa') or doc.get('GPA')}")
            else:
                print(f"   ⚠️ Not a dict: {type(doc)}")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

if __name__ == "__main__":
    test_double_decryption()