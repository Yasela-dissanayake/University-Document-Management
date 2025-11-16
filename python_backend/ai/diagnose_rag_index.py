"""
Diagnostic script to check what's actually in the RAG index
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings

CHROMA_DIR = os.path.join(os.path.dirname(__file__), "rag_store")

def diagnose_index():
    print("=" * 80)
    print("🔍 RAG INDEX DIAGNOSTIC")
    print("=" * 80)
    
    if not os.path.exists(CHROMA_DIR):
        print("\n❌ No RAG index found!")
        print(f"   Expected location: {CHROMA_DIR}")
        print("\n💡 Run this to build index:")
        print("   python -m python_backend.ai.rag_admin_utils rebuild")
        return
    
    print(f"\n✅ Index found at: {CHROMA_DIR}")
    
    try:
        embeddings = OllamaEmbeddings(model=os.getenv("OLLAMA_MODEL", "llama3"))
        vectorstore = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=embeddings
        )
        
        collection = vectorstore._collection
        total_count = collection.count()
        
        print(f"📊 Total chunks in index: {total_count}")
        
        if total_count == 0:
            print("\n⚠️  Index is empty! No documents indexed.")
            print("\n💡 Rebuild with:")
            print("   python -m python_backend.ai.rag_admin_utils rebuild")
            return
        
        # Get all documents
        print(f"\n📄 Fetching all documents...")
        results = collection.get(
            limit=total_count,
            include=['metadatas', 'documents']
        )
        
        if not results or 'metadatas' not in results:
            print("⚠️  No metadata found in index")
            return
        
        # Analyze metadata
        print(f"\n" + "=" * 80)
        print("📋 METADATA ANALYSIS")
        print("=" * 80)
        
        student_docs = {}
        for i, meta in enumerate(results['metadatas']):
            student_id = meta.get('student_id', 'UNKNOWN')
            if student_id not in student_docs:
                student_docs[student_id] = []
            student_docs[student_id].append({
                'chunk_id': i,
                'ipfs_hash': meta.get('ipfs_hash', 'N/A'),
                'document_type': meta.get('document_type', 'N/A'),
                'preview': results['documents'][i][:100] if i < len(results['documents']) else ''
            })
        
        print(f"\n👥 Students in index: {len(student_docs)}")
        
        for student_id, docs in student_docs.items():
            print(f"\n{'─' * 80}")
            print(f"📌 Student ID: {student_id}")
            print(f"   Chunks: {len(docs)}")
            
            # Group by document
            ipfs_hashes = set(doc['ipfs_hash'] for doc in docs)
            print(f"   Unique documents: {len(ipfs_hashes)}")
            
            # Show sample
            if docs:
                print(f"\n   Sample chunk preview:")
                print(f"   {docs[0]['preview'][:150]}...")
        
        # Test a specific query
        print(f"\n" + "=" * 80)
        print("🧪 TEST QUERY")
        print("=" * 80)
        
        test_query = "What is the GPA of S20841?"
        print(f"\nQuery: '{test_query}'")
        
        retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
        retrieved_docs = retriever.get_relevant_documents(test_query)
        
        print(f"\n📊 Retrieved {len(retrieved_docs)} documents")
        
        for i, doc in enumerate(retrieved_docs, 1):
            print(f"\n   Document {i}:")
            print(f"   Student ID: {doc.metadata.get('student_id', 'MISSING')}")
            print(f"   IPFS Hash: {doc.metadata.get('ipfs_hash', 'N/A')[:20]}...")
            print(f"   Type: {doc.metadata.get('document_type', 'N/A')}")
            print(f"   Content preview: {doc.page_content[:150]}...")
        
        # Test access control
        print(f"\n" + "=" * 80)
        print("🔐 ACCESS CONTROL TEST")
        print("=" * 80)
        
        test_users = [
            {"id": 1, "username": "asini", "role": "STUDENT", "student_id": "S20841"},
            {"id": 2, "username": "john", "role": "STUDENT", "student_id": "S19357"},
            {"id": 3, "username": "admin", "role": "ADMIN", "student_id": None},
        ]
        
        for user in test_users:
            print(f"\n👤 User: {user['username']} ({user['role']})")
            
            # Simulate the filtering logic
            accessible = []
            for doc in retrieved_docs:
                doc_student_id = doc.metadata.get('student_id', '')
                
                if user['role'] in ['ADMIN', 'HOD', 'DEAN', 'AR', 'DVC']:
                    accessible.append(doc)
                elif user['role'] == 'STUDENT':
                    user_sid = (user.get('student_id') or '').strip().upper()
                    if user_sid == doc_student_id:
                        accessible.append(doc)
            
            print(f"   Accessible docs: {len(accessible)}/{len(retrieved_docs)}")
            if accessible:
                print(f"   ✅ Would see data from: {accessible[0].metadata.get('student_id')}")
            else:
                print(f"   ❌ No accessible documents")
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    diagnose_index()