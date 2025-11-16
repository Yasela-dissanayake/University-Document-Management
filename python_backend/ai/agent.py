"""
Integrated AI Agent for University Blockchain System
---------------------------------------------------
Handles both on-chain (public) and off-chain (ACL-protected) queries
Uses existing tools + RAG for comprehensive query answering
"""

import json
import logging
import os
from typing import Dict, Any, Optional

from langchain_ollama import OllamaLLM

# Import existing tools
from python_backend.ai.tools import (
    get_onchain_student,
    list_semester_cids,
    get_offchain_semesters,
    get_course_grade
)

# Import user-aware RAG
from python_backend.ai.rag_agent import rag_answer


# Load system prompt
PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompts", "base_prompt.txt")
if os.path.exists(PROMPT_FILE):
    with open(PROMPT_FILE, "r") as f:
        BASE_SYSTEM_PROMPT = f.read()
else:
    BASE_SYSTEM_PROMPT = """You are an AI assistant for a university blockchain system.
You help users query student records and academic documents."""


def _classify_query(question: str) -> str:
    """
    Classify query type to route appropriately:
    - 'onchain': Basic student info, registration, IPFS hashes
    - 'offchain': Document contents, grades, courses, GPA, academic performance
    """
    question_lower = question.lower()
    
    # Off-chain indicators (detailed academic content)
    offchain_keywords = [
        'grade', 'course', 'gpa', 'subject', 'mark', 'score',
        'performance', 'transcript', 'academic record',
        'what did', 'how did', 'course details', 'semester results',
        'passed', 'failed', 'credits', 'cgpa'
    ]
    
    # On-chain indicators (metadata only)
    onchain_keywords = [
        'register', 'ipfs hash', 'cid', 'blockchain',
        'student id', 'name', 'program', 'year',
        'is registered', 'exists', 'status'
    ]
    
    # Count keyword matches
    offchain_score = sum(1 for kw in offchain_keywords if kw in question_lower)
    onchain_score = sum(1 for kw in onchain_keywords if kw in question_lower)
    
    # If explicitly asking about document content, it's off-chain
    if offchain_score > 0:
        return 'offchain'
    
    # Otherwise assume on-chain
    return 'onchain'


def answer_question(question: str, user_context: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Main agent entry point that intelligently routes queries:
    
    1. Classify query type (on-chain vs off-chain)
    2. Check authentication for off-chain queries
    3. Try tool-based approach first (structured data)
    4. Fall back to RAG for complex/natural language queries
    """
    
    query_type = _classify_query(question)
    logging.info(f"📊 Query classified as: {query_type}")
    
    # === OFF-CHAIN QUERIES (ACL-Protected) ===
    if query_type == 'offchain':
        if not user_context:
            return {
                "answer": "🔒 Authentication required to access academic records. Please log in to view detailed student information.",
                "trace": {
                    "query_type": "offchain",
                    "auth_required": True,
                    "reason": "Off-chain data requires authentication"
                }
            }
        
        logging.info(f"🔐 Off-chain query from user: {user_context.get('username')} ({user_context.get('role')})")
        
        # Try structured tool approach first for specific queries
        tool_result = _try_tools_for_offchain(question, user_context)
        if tool_result:
            return tool_result
        
        # Fall back to RAG for complex/natural language queries
        logging.info("📚 Using RAG for complex off-chain query")
        result = rag_answer(question, user=user_context)
        result["trace"]["query_type"] = "offchain"
        result["trace"]["method"] = "rag"
        return result
    
    # === ON-CHAIN QUERIES (Public) ===
    logging.info("⛓️ On-chain query - using public blockchain data")
    return _handle_onchain_query(question, user_context)


def _try_tools_for_offchain(question: str, user: Dict) -> Optional[Dict[str, Any]]:
    """
    Try to answer off-chain query using structured tools.
    Returns result if successful, None if query is too complex for tools.
    """
    question_lower = question.lower()
    
    # Extract student ID from question
    import re
    student_id_match = re.search(r'S\d{5}', question, re.IGNORECASE)
    if not student_id_match:
        return None
    
    student_id = student_id_match.group(0).upper()
    
    # Try specific tool patterns
    
    # Pattern 1: Specific course grade query
    course_match = re.search(r'grade.*(for|in|of)\s+([A-Z]{2,4}\d{3,4})', question, re.IGNORECASE)
    if course_match:
        course_code = course_match.group(2).upper()
        logging.info(f"🎯 Specific course grade query: {student_id} - {course_code}")
        
        result = get_course_grade(student_id, course_code, user)
        
        if result.get("ok") and result.get("found"):
            answer = f"The grade for {course_code} for student {student_id} is: {result.get('grade')}"
            return {
                "answer": answer,
                "trace": {
                    "query_type": "offchain",
                    "method": "tool",
                    "tool": "get_course_grade",
                    "result": result
                }
            }
        elif result.get("ok") and not result.get("found"):
            return {
                "answer": f"No grade found for course {course_code} for student {student_id}.",
                "trace": result.get("trace")
            }
        else:
            return {
                "answer": "Access denied or error retrieving course grade.",
                "trace": result.get("trace")
            }
    
    # Pattern 2: All semesters/grades query
    if any(kw in question_lower for kw in ['all grades', 'all courses', 'semester', 'transcript']):
        logging.info(f"🎯 All semesters query: {student_id}")
        
        result = get_offchain_semesters(student_id, user)
        
        if result.get("ok"):
            semesters = result.get("semesters", [])
            if not semesters:
                return {
                    "answer": f"No semester records found for {student_id}.",
                    "trace": result.get("trace")
                }
            
            # Format the answer
            answer_lines = [f"Academic records for {student_id}:\n"]
            for i, sem in enumerate(semesters, 1):
                answer_lines.append(f"\nSemester {i}:")
                if sem.get("gpa"):
                    answer_lines.append(f"  GPA: {sem['gpa']}")
                courses = sem.get("courses", [])
                if courses:
                    answer_lines.append(f"  Courses:")
                    for course in courses:
                        code = course.get("code", "N/A")
                        name = course.get("name", "Unknown")
                        grade = course.get("grade", "-")
                        answer_lines.append(f"    • {code} ({name}): {grade}")
            
            return {
                "answer": "\n".join(answer_lines),
                "trace": {
                    "query_type": "offchain",
                    "method": "tool",
                    "tool": "get_offchain_semesters",
                    "semesters_found": len(semesters)
                }
            }
        else:
            return {
                "answer": "Access denied or error retrieving semester records.",
                "trace": result.get("trace")
            }
    
    # Query too complex for simple tools, return None to trigger RAG
    return None


def _handle_onchain_query(question: str, user: Optional[Dict]) -> Dict[str, Any]:
    """Handle on-chain queries using blockchain tools"""
    
    # Extract student ID
    import re
    student_id_match = re.search(r'S\d{5}', question, re.IGNORECASE)
    
    if not student_id_match:
        return {
            "answer": "Please specify a student ID (e.g., S20841) in your query.",
            "trace": {"query_type": "onchain", "error": "No student ID found"}
        }
    
    student_id = student_id_match.group(0).upper()
    
    # Get on-chain student data
    student = get_onchain_student(student_id)
    
    if not student:
        return {
            "answer": f"Student {student_id} not found in the blockchain.",
            "trace": {"query_type": "onchain", "student_id": student_id, "found": False}
        }
    
    # Get semester CIDs
    cids = list_semester_cids(student_id)
    
    # Use LLM to format the response naturally
    llm = OllamaLLM(model=os.getenv("OLLAMA_MODEL", "llama3"))
    
    prompt = f"""Based on this blockchain data for student {student_id}, answer the user's question naturally.

Blockchain Data:
- Name: {student.get('name')}
- Program: {student.get('program')}
- Year: {student.get('year')}
- Status: {'Active' if student.get('is_active') else 'Inactive'}
- Registration Timestamp: {student.get('timestamp')}
- Main IPFS Hash: {student.get('documents_ipfs_hash')}
- Semester Records (IPFS CIDs): {len(cids)} documents
  {chr(10).join(f'  - {cid}' for cid in cids[:5])}

User Question: {question}

Provide a clear, concise answer:"""
    
    answer = llm.invoke(prompt)
    
    return {
        "answer": answer,
        "trace": {
            "query_type": "onchain",
            "method": "blockchain_tools",
            "student_id": student_id,
            "data_retrieved": {
                "student_found": True,
                "semester_records": len(cids)
            }
        }
    }


def rebuild_rag_index():
    """
    Utility function to rebuild the RAG index.
    Call this after adding new documents to the system.
    """
    import shutil
    
    rag_store_path = os.path.join(os.path.dirname(__file__), "rag_store")
    
    if os.path.exists(rag_store_path):
        shutil.rmtree(rag_store_path)
        logging.info("🗑️ Deleted old RAG index")
    
    # Reset singleton
    import python_backend.ai.rag_agent as rag_module
    rag_module._rag_instance = None
    
    logging.info("✅ RAG index will be rebuilt on next query")
    return {"status": "success", "message": "RAG index scheduled for rebuild"}