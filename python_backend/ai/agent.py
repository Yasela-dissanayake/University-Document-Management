"""
Complete Integrated AI Agent
----------------------------
Routes queries to appropriate handlers:
1. On-chain queries → Blockchain tools
2. Off-chain academic queries → RAG with ACL
3. Letter workflow queries → Letter Workflow RAG (NEW)
4. Semester workflow queries → Semester Workflow RAG
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

# Import RAG systems
from python_backend.ai.rag_agent import rag_answer

# Import letter workflow RAG (NEW - safe, won't break anything)
try:
    from python_backend.ai.letter_workflow_rag import letter_workflow_query
    LETTER_RAG_AVAILABLE = True
except ImportError:
    LETTER_RAG_AVAILABLE = False
    logging.warning("Letter workflow RAG not available")


def _classify_query(question: str) -> str:
    """
    Classify query type to route appropriately:
    - 'letter_workflow': Letter status, approvals, analytics (NEW)
    - 'semester_workflow': Semester approval workflow
    - 'offchain': Document contents, grades, courses, GPA
    - 'onchain': Basic student info, registration, IPFS hashes
    """
    question_lower = question.lower()
    
    # Letter workflow keywords (NEW)
    letter_keywords = [
        'letter', 'ltr', 'approval', 'approve', 'pending letter',
        'letter status', 'letter workflow', 'letter statistics',
        'letter bottleneck', 'letter history', 'who approved letter',
        'create letter', 'letter dashboard'
    ]
    
    # Semester workflow keywords
    semester_workflow_keywords = [
        'semester workflow', 'semester status', 'semester approval',
        'pending semester', 'semester bottleneck', 'who approved semester'
    ]
    
    # Off-chain academic keywords
    offchain_keywords = [
        'grade', 'course', 'gpa', 'subject', 'mark', 'score',
        'performance', 'transcript', 'academic record',
        'what did', 'how did', 'course details', 'semester results',
        'passed', 'failed', 'credits', 'cgpa'
    ]
    
    # On-chain metadata keywords
    onchain_keywords = [
        'register', 'ipfs hash', 'cid', 'blockchain',
        'student id', 'name', 'program', 'year',
        'is registered', 'exists'
    ]
    
    # Check letter workflow first (highest priority for letter queries)
    letter_score = sum(1 for kw in letter_keywords if kw in question_lower)
    if letter_score > 0:
        return 'letter_workflow'
    
    # Check semester workflow
    semester_score = sum(1 for kw in semester_workflow_keywords if kw in question_lower)
    if semester_score > 0:
        return 'semester_workflow'
    
    # Check academic content
    offchain_score = sum(1 for kw in offchain_keywords if kw in question_lower)
    if offchain_score > 0:
        return 'offchain'
    
    # Default to on-chain
    onchain_score = sum(1 for kw in onchain_keywords if kw in question_lower)
    if onchain_score > 0:
        return 'onchain'
    
    # If unclear, default to general which will try to classify further
    return 'general'


def answer_question(question: str, user_context: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Main agent entry point - routes queries intelligently
    """
    
    query_type = _classify_query(question)
    logging.info(f"📊 Query classified as: {query_type}")
    
    # === LETTER WORKFLOW QUERIES (NEW) ===
    if query_type == 'letter_workflow':
        if not user_context:
            return {
                "answer": "🔒 Authentication required to access letter workflow information. Please log in.",
                "trace": {
                    "query_type": "letter_workflow",
                    "auth_required": True
                }
            }
        
        if not LETTER_RAG_AVAILABLE:
            return {
                "answer": "Letter workflow monitoring system is not available. Please ensure letter_workflow_rag.py is properly installed.",
                "trace": {
                    "query_type": "letter_workflow",
                    "error": "module_not_available"
                }
            }
        
        logging.info(f"📨 Letter workflow query from: {user_context.get('username')} ({user_context.get('role')})")
        result = letter_workflow_query(question, user=user_context)
        result["trace"]["query_type"] = "letter_workflow"
        return result
    
    # === SEMESTER WORKFLOW QUERIES ===
    elif query_type == 'semester_workflow':
        if not user_context:
            return {
                "answer": "🔒 Authentication required to access semester workflow information. Please log in.",
                "trace": {
                    "query_type": "semester_workflow",
                    "auth_required": True
                }
            }
        
        logging.info(f"📋 Semester workflow query from: {user_context.get('username')} ({user_context.get('role')})")
        
        # Placeholder - integrate semester workflow RAG later
        return {
            "answer": "Semester workflow monitoring is coming soon. Currently, you can view semester status in the Pending Approvals page.",
            "trace": {
                "query_type": "semester_workflow",
                "status": "coming_soon"
            }
        }
    
    # === OFF-CHAIN ACADEMIC QUERIES (ACL-Protected) ===
    elif query_type == 'offchain':
        if not user_context:
            return {
                "answer": "🔒 Authentication required to access academic records. Please log in to view detailed student information.",
                "trace": {
                    "query_type": "offchain",
                    "auth_required": True
                }
            }
        
        logging.info(f"🔐 Off-chain query from: {user_context.get('username')} ({user_context.get('role')})")
        
        # Try structured tools first
        tool_result = _try_tools_for_offchain(question, user_context)
        if tool_result:
            return tool_result
        
        # Fall back to RAG
        logging.info("📚 Using RAG for complex off-chain query")
        result = rag_answer(question, user=user_context)
        result["trace"]["query_type"] = "offchain"
        result["trace"]["method"] = "rag"
        return result
    
    # === ON-CHAIN QUERIES (Public) ===
    elif query_type == 'onchain':
        logging.info("⛓️ On-chain query - using public blockchain data")
        return _handle_onchain_query(question, user_context)
    
    # === GENERAL QUERIES ===
    else:
        # Try to intelligently route general queries
        return _handle_general_query(question, user_context)


def _try_tools_for_offchain(question: str, user: Dict) -> Optional[Dict[str, Any]]:
    """Try to answer off-chain query using structured tools"""
    question_lower = question.lower()
    
    import re
    student_id_match = re.search(r'S\d{5}', question, re.IGNORECASE)
    if not student_id_match:
        return None
    
    student_id = student_id_match.group(0).upper()
    
    # Specific course grade query
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
    
    # All semesters query
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
    
    return None


def _handle_onchain_query(question: str, user: Optional[Dict]) -> Dict[str, Any]:
    """Handle on-chain queries using blockchain tools"""
    
    import re
    student_id_match = re.search(r'S\d{5}', question, re.IGNORECASE)
    
    if not student_id_match:
        return {
            "answer": "Please specify a student ID (e.g., S20841) in your query.",
            "trace": {"query_type": "onchain", "error": "No student ID found"}
        }
    
    student_id = student_id_match.group(0).upper()
    
    student = get_onchain_student(student_id)
    
    if not student:
        return {
            "answer": f"Student {student_id} not found in the blockchain.",
            "trace": {"query_type": "onchain", "student_id": student_id, "found": False}
        }
    
    cids = list_semester_cids(student_id)
    
    llm = OllamaLLM(model=os.getenv("OLLAMA_MODEL", "llama3"))
    
    prompt = f"""Based on this blockchain data for student {student_id}, answer the user's question naturally.

Blockchain Data:
- Name: {student.get('name')}
- Program: {student.get('program')}
- Year: {student.get('year')}
- Status: {'Active' if student.get('is_active') else 'Inactive'}
- Registration Timestamp: {student.get('timestamp')}
- Semester Records: {len(cids)} documents

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


def _handle_general_query(question: str, user: Optional[Dict]) -> Dict[str, Any]:
    """Handle general queries that don't fit specific categories"""
    
    llm = OllamaLLM(model=os.getenv("OLLAMA_MODEL", "llama3"))
    
    system_info = """You are an AI assistant for a university blockchain document management system.

Available features:
- Student registration and academic records on blockchain
- Letter workflow system (HOD → DEAN → AR → DVC approval chain)
- Semester record management with approval workflows
- AI-powered query system for both on-chain and off-chain data

You can help with:
- Letter workflow status and monitoring
- Academic record queries (grades, courses, GPA)
- Student registration information
- System usage guidance
"""
    
    prompt = f"""{system_info}

User Question: {question}

Provide a helpful response. If the query is about specific data, ask for more details (like student ID or letter ID).

Answer:"""
    
    answer = llm.invoke(prompt)
    
    return {
        "answer": answer,
        "trace": {
            "query_type": "general",
            "method": "llm_direct"
        }
    }