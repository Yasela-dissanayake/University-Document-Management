"""
RAG (Retrieval-Augmented Generation) module for Hybrid Blockchain–AI System
---------------------------------------------------------------------------
Builds and uses a persistent vector store (Chroma) from decrypted IPFS academic
documents, enabling context-aware Q&A through Ollama LLM.

Author: Yasela (University of Peradeniya)
"""

import os
import json
import logging
from typing import Dict, Any, List

from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain_ollama import OllamaLLM
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains.retrieval_qa.base import RetrievalQA

from python_backend.ipfs_client import UniversityIPFSClient
from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend import acl  # <-- Import ACL system

# ---------- Configuration ----------
CHROMA_DIR = os.path.join(os.path.dirname(__file__), "rag_store")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

# System user for RAG with ADMIN privileges to decrypt all documents
RAG_SYSTEM_USER = {
    "id": -1,
    "username": "rag_system",
    "role": "ADMIN",  # ADMIN role can decrypt all documents
    "student_id": None
}


# ---------- Core RAG Class ----------
class UniversityRAG:
    def __init__(self):
        self.embeddings = OllamaEmbeddings(model=OLLAMA_MODEL)
        self.vectorstore = None
        self.qa_chain = None
        self.ipfs_client = UniversityIPFSClient()
        self.bc_client = UniversityBlockchainClient()

        if not os.path.exists(CHROMA_DIR):
            os.makedirs(CHROMA_DIR)

        self._ensure_index_built()

    # ---------------------------------
    # Auto index builder
    # ---------------------------------
    def _ensure_index_built(self):
        """Automatically build or load vector index"""
        try:
            if os.path.exists(os.path.join(CHROMA_DIR, "chroma.sqlite3")):
                logging.info("✅ Loading existing Chroma vectorstore...")
                self.vectorstore = Chroma(
                    persist_directory=CHROMA_DIR,
                    embedding_function=self.embeddings
                )
            else:
                logging.info("⚙️ No existing index found – building new RAG index...")
                
                # Load documents with detailed error logging
                try:
                    documents = self._load_documents_from_ipfs()
                except Exception as e:
                    logging.error(f"❌ Failed to load documents from IPFS: {e}")
                    import traceback
                    traceback.print_exc()
                    self.vectorstore = None
                    return
                
                if not documents:
                    logging.warning("⚠️ No IPFS documents found to build RAG index.")
                    self.vectorstore = None
                    return
                
                # Build vectorstore with detailed error logging
                try:
                    self.vectorstore = self._build_vectorstore(documents)
                    logging.info(f"✅ Built vectorstore with {len(documents)} documents.")
                except Exception as e:
                    logging.error(f"❌ Failed to build vectorstore: {e}")
                    import traceback
                    traceback.print_exc()
                    self.vectorstore = None
                    return
                    
        except Exception as e:
            logging.error(f"❌ Failed to build or load vectorstore: {e}")
            import traceback
            traceback.print_exc()
            self.vectorstore = None
            return

        # Initialize QA chain
        if self.vectorstore:
            try:
                retriever = self.vectorstore.as_retriever(search_kwargs={"k": 3})
                llm = OllamaLLM(model=OLLAMA_MODEL)
                self.qa_chain = RetrievalQA.from_chain_type(
                    llm=llm, retriever=retriever, chain_type="stuff"
                )
                logging.info("✅ RAG QA chain initialized successfully.")
            except Exception as e:
                logging.error(f"❌ Failed to initialize QA chain: {e}")
                import traceback
                traceback.print_exc()
                self.qa_chain = None
        else:
            logging.warning("⚠️ Vectorstore is None, cannot initialize QA chain")

    # ---------------------------------
    # Document loader (Blockchain + IPFS + ACL)
    # ---------------------------------
    def _load_documents_from_ipfs(self) -> List[Dict[str, Any]]:
        """Fetch all student documents from IPFS via blockchain metadata and decrypt using ACL"""
        docs = []
        test_student_id = "S20841"

        try:
            # --- Step 1: Get student details from blockchain ---
            student = self.bc_client.get_student_details(test_student_id)
            if not student:
                logging.warning(f"No on-chain data found for {test_student_id}.")
                return []

            # --- Step 2: Collect IPFS hashes ---
            semester_hashes = self.bc_client.get_all_semester_hashes(test_student_id) or []
            if student.get("documents_ipfs_hash"):
                semester_hashes.append(student["documents_ipfs_hash"])

            # Remove blanks and duplicates
            semester_hashes = list({h.strip() for h in semester_hashes if h and h.strip()})
            if not semester_hashes:
                logging.warning(f"No valid IPFS hashes found for {test_student_id}.")
                return []

            logging.info(f"📦 Found {len(semester_hashes)} valid IPFS hashes for {test_student_id}")

            # --- Step 3: Retrieve and decrypt each document using ACL ---
            for ipfs_hash in semester_hashes:
                try:
                    # Retrieve the encrypted payload from IPFS
                    payload = self.ipfs_client.retrieve_academic_document(ipfs_hash)

                    # Check if this is ACL-encrypted
                    if isinstance(payload, dict) and payload.get("enc") == "fernet-v1":
                        logging.info(f"🔐 Document {ipfs_hash[:10]}... is ACL-encrypted, decrypting...")
                        try:
                            # Decrypt using ACL with system admin privileges
                            document = acl.decrypt_for_user(
                                user=RAG_SYSTEM_USER,
                                student_id=test_student_id,
                                cid=ipfs_hash,
                                enc_payload=payload
                            )
                            logging.info(f"✅ Successfully decrypted ACL document: {ipfs_hash[:10]}...")
                        except PermissionError as e:
                            logging.error(f"❌ ACL permission denied for {ipfs_hash}: {e}")
                            continue
                        except Exception as e:
                            logging.error(f"❌ ACL decryption failed for {ipfs_hash}: {e}")
                            continue
                    else:
                        # Not ACL-encrypted, use as-is
                        document = payload
                        logging.info(f"📄 Document {ipfs_hash[:10]}... is not ACL-encrypted")

                    # Convert to text for RAG
                    doc_text = self._json_to_text(document)

                    # Debug preview
                    logging.info(f"📘 Loaded IPFS doc {ipfs_hash[:10]} preview:\n{doc_text[:300]}")

                    docs.append({"text": doc_text, "metadata": {"ipfs_hash": ipfs_hash}})
                    logging.info(f"✅ Retrieved and parsed IPFS document: {ipfs_hash}")

                except Exception as e:
                    logging.error(f"❌ Failed to load IPFS doc {ipfs_hash}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

            if not docs:
                logging.warning(f"⚠️ No documents successfully retrieved for {test_student_id}.")
            else:
                logging.info(f"📚 Successfully loaded {len(docs)} IPFS documents for RAG.")
            return docs

        except Exception as e:
            logging.error(f"Error retrieving documents for RAG: {e}")
            import traceback
            traceback.print_exc()
            return []

    # ---------------------------------
    # Text conversion helper
    # ---------------------------------
    def _json_to_text(self, data: Any) -> str:
        """Convert decrypted academic JSON into readable text for embeddings."""
        try:
            # If it's a string, attempt to parse
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    # Not JSON – return raw text
                    return f"Raw text document:\n{data}\n\n<RAW_JSON_END>"

            # If not dict, stringify
            if not isinstance(data, dict):
                text_body = f"Unrecognized format: {str(data)}"
                text_body += "\n\nFULL_JSON:\n" + json.dumps(data, indent=2)
                return text_body

            lines = []
            
            # Extract student info
            sid = data.get("student_id") or data.get("studentID") or data.get("id") or "Unknown"
            dtype = data.get("document_type") or data.get("type") or "academic_record"
            gpa = data.get("gpa") or data.get("GPA")

            lines.append(f"Student ID: {sid}")
            lines.append(f"Document Type: {dtype}")
            if gpa:
                lines.append(f"GPA: {gpa}")

            # Courses
            courses = data.get("courses") or data.get("Subjects") or []
            if isinstance(courses, list) and courses:
                lines.append("\nCourses and Grades:")
                for c in courses:
                    code = c.get("code") or c.get("course_code") or "N/A"
                    name = c.get("name") or c.get("course_name") or "Unknown Course"
                    grade = c.get("grade") or c.get("Grade") or "-"
                    lines.append(f" - {code} ({name}): {grade}")
            else:
                lines.append("\nNo courses listed.")

            if "timestamp" in data:
                lines.append(f"\nTimestamp: {data['timestamp']}")

            # ALWAYS append full JSON to ensure RAG has complete data
            lines.append("\n\n--- FULL JSON DATA ---")
            lines.append(json.dumps(data, indent=2))

            return "\n".join(lines)

        except Exception as e:
            logging.error(f"Failed to convert JSON to text for embedding: {e}")
            return json.dumps(data, indent=2) if isinstance(data, dict) else str(data)

    # ---------------------------------
    # Vectorstore builder
    # ---------------------------------
    def _build_vectorstore(self, documents: List[Dict[str, Any]]):
        """Split text, embed, and persist in Chroma"""
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        all_texts, all_metas = [], []

        # Log one sample for verification
        logging.info("📄 Sample document text for embedding preview:")
        if documents:
            logging.info("\n" + documents[0]["text"][:400])
        else:
            logging.warning("No documents provided to build vectorstore!")

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

    # ---------------------------------
    # Main RAG answer method
    # ---------------------------------
    def answer(self, question: str) -> Dict[str, Any]:
        """Retrieve-then-generate answer based on stored IPFS documents"""
        if not self.qa_chain:
            return {"answer": "RAG system not initialized or no documents indexed."}
        try:
            result = self.qa_chain.run(question)
            return {
                "answer": result,
                "trace": {"method": "RAG", "model": OLLAMA_MODEL}
            }
        except Exception as e:
            logging.error(f"RAG answer generation failed: {e}")
            return {"answer": f"Error: {e}"}


# ---------- Singleton Instance ----------
_rag_instance = None

def rag_answer(question: str) -> Dict[str, Any]:
    """Public entry point used by agent.py"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = UniversityRAG()
    return _rag_instance.answer(question)