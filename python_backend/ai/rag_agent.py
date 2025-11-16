"""
User-Aware RAG Agent with ACL-based access control
-------------------------------------------------
Ensures students can only query their own off-chain documents,
while authorized roles can access documents based on ACL permissions.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional

from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain_ollama import OllamaLLM
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.retrieval_qa.base import RetrievalQA

from python_backend.ipfs_client import UniversityIPFSClient
from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend import acl

# ---------- Configuration ----------
CHROMA_DIR = os.path.join(os.path.dirname(__file__), "rag_store")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")


class UserAwareRAG:
    """
    RAG system that respects ACL permissions at query time.
    Documents are indexed globally, but retrieval is filtered by user permissions.
    """
    
    def __init__(self):
        self.embeddings = OllamaEmbeddings(model=OLLAMA_MODEL)
        self.vectorstore = None
        self.ipfs_client = UniversityIPFSClient()
        self.bc_client = UniversityBlockchainClient()

        if not os.path.exists(CHROMA_DIR):
            os.makedirs(CHROMA_DIR)

        self._ensure_index_built()

    def _ensure_index_built(self):
        """Build or load the global document index"""
        try:
            if os.path.exists(os.path.join(CHROMA_DIR, "chroma.sqlite3")):
                logging.info("✅ Loading existing Chroma vectorstore...")
                self.vectorstore = Chroma(
                    persist_directory=CHROMA_DIR,
                    embedding_function=self.embeddings
                )
                logging.info(f"✅ Loaded vectorstore with {self.vectorstore._collection.count()} chunks")
            else:
                logging.info("⚙️ Building new RAG index from all students...")
                documents = self._load_all_student_documents()
                
                if not documents:
                    logging.warning("⚠️ No documents found to build RAG index.")
                    self.vectorstore = None
                    return
                
                self.vectorstore = self._build_vectorstore(documents)
                logging.info(f"✅ Built vectorstore with {len(documents)} documents.")
                    
        except Exception as e:
            logging.error(f"❌ Failed to build or load vectorstore: {e}")
            import traceback
            traceback.print_exc()
            self.vectorstore = None

    def _load_all_student_documents(self) -> List[Dict[str, Any]]:
        """
        Load documents from all students for indexing.
        Metadata includes student_id so we can filter at query time.
        """
        docs = []
        
        # In production, you'd get all student IDs from blockchain
        # For now, we'll use a test set
        student_ids = ["S20841", "S19357", "S20123"]  # Add more as needed
        
        for student_id in student_ids:
            try:
                logging.info(f"📦 Loading documents for {student_id}...")
                student_docs = self._load_student_documents(student_id)
                docs.extend(student_docs)
                logging.info(f"✅ Loaded {len(student_docs)} docs for {student_id}")
            except Exception as e:
                logging.error(f"❌ Failed to load documents for {student_id}: {e}")
                continue
        
        return docs

    def _load_student_documents(self, student_id: str) -> List[Dict[str, Any]]:
        """Load all documents for a specific student"""
        docs = []
        
        try:
            student = self.bc_client.get_student_details(student_id)
            if not student:
                return []

            semester_hashes = self.bc_client.get_all_semester_hashes(student_id) or []
            if student.get("documents_ipfs_hash"):
                semester_hashes.append(student["documents_ipfs_hash"])

            semester_hashes = list({h.strip() for h in semester_hashes if h and h.strip()})
            
            # Use ADMIN role to decrypt all documents for indexing
            system_user = {"id": -1, "username": "rag_system", "role": "ADMIN"}

            for ipfs_hash in semester_hashes:
                try:
                    payload = self.ipfs_client.retrieve_academic_document(ipfs_hash)

                    if isinstance(payload, dict) and payload.get("enc") == "fernet-v1":
                        document = acl.decrypt_for_user(
                            user=system_user,
                            student_id=student_id,
                            cid=ipfs_hash,
                            enc_payload=payload
                        )
                    else:
                        document = payload

                    doc_text = self._json_to_text(document)
                    
                    # Store student_id in metadata for filtering
                    docs.append({
                        "text": doc_text,
                        "metadata": {
                            "ipfs_hash": ipfs_hash,
                            "student_id": student_id,  # KEY: for access control
                            "document_type": document.get("document_type", "academic_record")
                        }
                    })

                except Exception as e:
                    logging.error(f"❌ Failed to load {ipfs_hash} for {student_id}: {e}")
                    continue

            return docs

        except Exception as e:
            logging.error(f"Error loading documents for {student_id}: {e}")
            return []

    def _json_to_text(self, data: Any) -> str:
        """Convert JSON document to text for embeddings"""
        try:
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return f"Raw text: {data}"

            if not isinstance(data, dict):
                return str(data)

            lines = []
            sid = data.get("student_id", "Unknown")
            dtype = data.get("document_type", "academic_record")
            gpa = data.get("gpa") or data.get("GPA")

            lines.append(f"Student ID: {sid}")
            lines.append(f"Document Type: {dtype}")
            if gpa:
                lines.append(f"GPA: {gpa}")

            courses = data.get("courses") or data.get("Subjects") or []
            if isinstance(courses, list) and courses:
                lines.append("\nCourses and Grades:")
                for c in courses:
                    code = c.get("code") or c.get("course_code", "N/A")
                    name = c.get("name") or c.get("course_name", "Unknown")
                    grade = c.get("grade") or c.get("Grade", "-")
                    lines.append(f" - {code} ({name}): {grade}")

            if "timestamp" in data:
                lines.append(f"\nTimestamp: {data['timestamp']}")

            lines.append("\n--- FULL JSON ---")
            lines.append(json.dumps(data, indent=2))

            return "\n".join(lines)

        except Exception as e:
            logging.error(f"JSON to text conversion failed: {e}")
            return json.dumps(data, indent=2) if isinstance(data, dict) else str(data)

    def _build_vectorstore(self, documents: List[Dict[str, Any]]):
        """Build the vector store from documents"""
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        all_texts, all_metas = [], []

        for doc in documents:
            chunks = text_splitter.split_text(doc["text"])
            all_texts.extend(chunks)
            all_metas.extend([doc["metadata"]] * len(chunks))

        store = Chroma.from_texts(
            texts=all_texts,
            embedding=self.embeddings,
            metadatas=all_metas,
            persist_directory=CHROMA_DIR
        )
        store.persist()
        return store

    def _get_user_principals(self, user: Optional[Dict]) -> List[str]:
        """Get ACL principals for a user"""
        if not user:
            return []
        
        principals = []
        role = (user.get("role") or "").strip().upper()
        if role:
            principals.append(f"role:{role}")
        
        # Students can only access their own documents
        if role == "STUDENT":
            student_id = (user.get("student_id") or "").strip().upper()
            if student_id:
                principals.append(f"student:{student_id}")
        
        return principals

    def _can_access_document(self, user: Optional[Dict], doc_metadata: Dict) -> bool:
        """Check if user can access a document based on ACL rules"""
        if not user:
            return False
        
        role = (user.get("role") or "").strip().upper()
        doc_student_id = doc_metadata.get("student_id", "")
        
        # Admins and authorized roles can access all documents
        if role in ["ADMIN", "HOD", "DEAN", "AR", "DVC"]:
            return True
        
        # Students can only access their own documents
        if role == "STUDENT":
            user_student_id = (user.get("student_id") or "").strip().upper()
            return user_student_id == doc_student_id
        
        return False

    def answer(self, question: str, user: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Answer a question with user-aware access control.
        Only returns information from documents the user has permission to access.
        """
        if not self.vectorstore:
            return {"answer": "RAG system not initialized or no documents indexed."}
        
        if not user:
            return {
                "answer": "Authentication required to access off-chain documents. Please log in.",
                "trace": {"error": "No user context provided"}
            }

        try:
            # Get relevant documents
            retriever = self.vectorstore.as_retriever(search_kwargs={"k": 10})
            raw_docs = retriever.get_relevant_documents(question)
            
            # Filter documents based on user permissions
            accessible_docs = [
                doc for doc in raw_docs 
                if self._can_access_document(user, doc.metadata)
            ]
            
            if not accessible_docs:
                role = user.get("role", "USER")
                if role == "STUDENT":
                    return {
                        "answer": "No accessible documents found. You can only access your own academic records.",
                        "trace": {"filtered_count": len(raw_docs), "accessible_count": 0}
                    }
                else:
                    return {
                        "answer": "No relevant documents found for your query.",
                        "trace": {"filtered_count": len(raw_docs), "accessible_count": 0}
                    }
            
            # Build context from accessible documents
            context = "\n\n".join([doc.page_content for doc in accessible_docs[:3]])
            
            # Generate answer using LLM
            llm = OllamaLLM(model=OLLAMA_MODEL)
            prompt = f"""Based on the following academic records, answer this question: {question}

Academic Records:
{context}

Answer:"""
            
            answer = llm.invoke(prompt)
            
            return {
                "answer": answer,
                "trace": {
                    "method": "RAG",
                    "model": OLLAMA_MODEL,
                    "user": user.get("username"),
                    "role": user.get("role"),
                    "total_docs_found": len(raw_docs),
                    "accessible_docs": len(accessible_docs),
                    "documents_used": [doc.metadata for doc in accessible_docs[:3]]
                }
            }
            
        except Exception as e:
            logging.error(f"RAG answer generation failed: {e}")
            import traceback
            traceback.print_exc()
            return {"answer": f"Error: {e}"}


# ---------- Singleton Instance ----------
_rag_instance = None

def rag_answer(question: str, user: Optional[Dict] = None) -> Dict[str, Any]:
    """Public entry point with user context"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = UserAwareRAG()
    return _rag_instance.answer(question, user)