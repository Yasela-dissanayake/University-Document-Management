"""
RAG Administration Utilities
---------------------------
Helper script for managing the RAG system
"""

import os
import sys
import shutil
from typing import List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend.ipfs_client import UniversityIPFSClient
from python_backend import acl

CHROMA_DIR = os.path.join(os.path.dirname(__file__), "rag_store")


def rebuild_index(student_ids: List[str] = None):
    """
    Rebuild the RAG index.
    
    Args:
        student_ids: Optional list of student IDs to index. 
                    If None, will try to index all known students.
    """
    print("=" * 70)
    print("🔧 RAG INDEX REBUILD UTILITY")
    print("=" * 70)
    
    # Delete existing index
    if os.path.exists(CHROMA_DIR):
        print(f"\n🗑️  Deleting existing index at: {CHROMA_DIR}")
        shutil.rmtree(CHROMA_DIR)
        print("✅ Old index deleted")
    else:
        print("\nℹ️  No existing index found")
    
    # Reset singleton
    import python_backend.ai.rag_agent as rag_module
    rag_module._rag_instance = None
    print("✅ RAG instance reset")
    
    # Trigger rebuild by importing and initializing
    print("\n⚙️  Building new index...")
    from python_backend.ai.rag_agent import UserAwareRAG
    
    rag = UserAwareRAG()
    
    if rag.vectorstore:
        count = rag.vectorstore._collection.count()
        print(f"\n✅ Index rebuilt successfully!")
        print(f"📊 Total chunks indexed: {count}")
    else:
        print("\n⚠️  Index build failed or no documents found")
    
    print("\n" + "=" * 70)


def check_index_status():
    """Check the current status of the RAG index"""
    print("=" * 70)
    print("📊 RAG INDEX STATUS")
    print("=" * 70)
    
    if not os.path.exists(CHROMA_DIR):
        print("\n❌ No index found")
        print(f"   Expected location: {CHROMA_DIR}")
        return
    
    print(f"\n✅ Index exists at: {CHROMA_DIR}")
    
    try:
        from langchain_community.vectorstores import Chroma
        from langchain_community.embeddings import OllamaEmbeddings
        
        embeddings = OllamaEmbeddings(model=os.getenv("OLLAMA_MODEL", "llama3"))
        vectorstore = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=embeddings
        )
        
        count = vectorstore._collection.count()
        print(f"📊 Total chunks: {count}")
        
        # Try to get sample metadata
        if count > 0:
            results = vectorstore._collection.get(limit=5)
            if results and 'metadatas' in results:
                print(f"\n📄 Sample documents indexed:")
                seen_students = set()
                for meta in results['metadatas']:
                    sid = meta.get('student_id', 'Unknown')
                    if sid not in seen_students:
                        seen_students.add(sid)
                        print(f"   • {sid}: {meta.get('document_type', 'N/A')}")
                
                print(f"\n👥 Unique students in index: {len(seen_students)}")
    
    except Exception as e:
        print(f"\n⚠️  Error checking index: {e}")
    
    print("\n" + "=" * 70)


def test_student_access(student_id: str):
    """Test what documents are accessible for a specific student"""
    print("=" * 70)
    print(f"🔍 TESTING ACCESS FOR STUDENT: {student_id}")
    print("=" * 70)
    
    bc = UniversityBlockchainClient()
    ipfs = UniversityIPFSClient()
    
    try:
        # Get student details
        student = bc.get_student_details(student_id)
        if not student:
            print(f"\n❌ Student {student_id} not found in blockchain")
            return
        
        print(f"\n✅ Student found:")
        print(f"   Name: {student.get('name')}")
        print(f"   Program: {student.get('program')}")
        
        # Get semester hashes
        semester_hashes = bc.get_all_semester_hashes(student_id) or []
        if student.get("documents_ipfs_hash"):
            semester_hashes.append(student["documents_ipfs_hash"])
        
        semester_hashes = list({h.strip() for h in semester_hashes if h and h.strip()})
        
        print(f"\n📦 Found {len(semester_hashes)} IPFS documents")
        
        # Try to decrypt each with different user contexts
        system_user = {"id": -1, "username": "system", "role": "ADMIN"}
        student_user = {"id": 100, "username": "test_student", "role": "STUDENT", "student_id": student_id}
        
        print(f"\n🔐 Testing decryption:")
        for i, ipfs_hash in enumerate(semester_hashes, 1):
            print(f"\n   Document {i}: {ipfs_hash[:20]}...")
            
            try:
                payload = ipfs.retrieve_academic_document(ipfs_hash)
                
                if isinstance(payload, dict) and payload.get("enc") == "fernet-v1":
                    print(f"      Type: ACL-encrypted")
                    
                    # Try with student user
                    try:
                        doc = acl.decrypt_for_user(student_user, student_id, ipfs_hash, payload)
                        print(f"      ✅ Student can access")
                        print(f"      Preview: {str(doc)[:100]}...")
                    except PermissionError:
                        print(f"      ❌ Student CANNOT access")
                    except Exception as e:
                        print(f"      ⚠️  Decryption error: {e}")
                else:
                    print(f"      Type: Not encrypted")
                    print(f"      ✅ Publicly readable")
            
            except Exception as e:
                print(f"      ❌ Error: {e}")
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 70)


def list_all_students():
    """List all students that could be indexed"""
    print("=" * 70)
    print("👥 STUDENTS IN SYSTEM")
    print("=" * 70)
    
    # This is a simplified version - in production you'd query the blockchain
    # for all registered students
    known_students = ["S20841", "S19357", "S20123"]
    
    bc = UniversityBlockchainClient()
    
    print("\nKnown student IDs:")
    for sid in known_students:
        try:
            student = bc.get_student_details(sid)
            if student:
                print(f"\n✅ {sid}")
                print(f"   Name: {student.get('name', 'N/A')}")
                print(f"   Program: {student.get('program', 'N/A')}")
                
                semester_hashes = bc.get_all_semester_hashes(sid) or []
                if student.get("documents_ipfs_hash"):
                    semester_hashes.append(student["documents_ipfs_hash"])
                semester_hashes = list({h.strip() for h in semester_hashes if h and h.strip()})
                print(f"   Documents: {len(semester_hashes)}")
            else:
                print(f"\n❌ {sid} - Not found")
        except Exception as e:
            print(f"\n⚠️  {sid} - Error: {e}")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RAG Administration Utilities")
    parser.add_argument("command", choices=["rebuild", "status", "test", "list"],
                       help="Command to execute")
    parser.add_argument("--student-id", help="Student ID for test command")
    
    args = parser.parse_args()
    
    if args.command == "rebuild":
        rebuild_index()
    elif args.command == "status":
        check_index_status()
    elif args.command == "test":
        if not args.student_id:
            print("Error: --student-id required for test command")
            sys.exit(1)
        test_student_access(args.student_id)
    elif args.command == "list":
        list_all_students()