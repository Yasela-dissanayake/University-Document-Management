"""
Letter Workflow Monitoring RAG Agent
python_backend/ai/letter_workflow_rag.py

Provides natural language querying for letter workflow monitoring and analysis.
READ-ONLY: Does not modify any data, only queries and analyzes.
"""

import os
import json
import sqlite3
import time
from typing import Dict, Any, Optional, List

from langchain_ollama import OllamaLLM









"""
Updated Letter Workflow RAG with Access Control
------------------------------------------------
Add these helper functions and update the query handlers
"""

# Add these at the top of letter_workflow_rag.py after imports

# Role hierarchy for access control
ROLE_LEVELS = {
    "HOD": 1,
    "DEAN": 2,
    "AR": 3,
    "DVC": 4,
    "ADMIN": 999  # Admin can see everything
}


def _can_user_access_letter(user_role: str, letter_creator_role: str) -> bool:
    """
    Check if user can access a letter based on role hierarchy.
    Users can only access letters created at their level or below.
    
    Args:
        user_role: Role of the user making the query
        letter_creator_role: Role of the user who created the letter
    
    Returns:
        True if access allowed, False otherwise
    
    Examples:
        - HOD accessing HOD's letter: ✅ Allowed
        - HOD accessing DEAN's letter: ❌ Denied (higher level)
        - DEAN accessing HOD's letter: ✅ Allowed (lower level)
        - ADMIN accessing any letter: ✅ Allowed (admin override)
    """
    user_level = ROLE_LEVELS.get(user_role, 0)
    creator_level = ROLE_LEVELS.get(letter_creator_role, 999)
    
    # Admin can access everything
    if user_role == "ADMIN":
        return True
    
    # User's level must be >= creator's level
    return user_level >= creator_level


def _filter_accessible_letters(letters: list, user_role: str) -> list:
    """
    Filter a list of letters to only those the user can access.
    
    Args:
        letters: List of letter dictionaries
        user_role: Role of the user
    
    Returns:
        Filtered list of accessible letters
    """
    if user_role == "ADMIN":
        return letters
    
    return [
        letter for letter in letters
        if _can_user_access_letter(user_role, letter.get('creator_role', ''))
    ]


# ============================================
# UPDATE THESE METHODS IN LetterWorkflowRAG CLASS
# ============================================




# Database path
LETTER_DB = os.path.join(os.path.dirname(__file__), "..", "letter_workflows.sqlite")


class LetterWorkflowRAG:
    """
    RAG agent for letter workflow monitoring.
    Handles natural language queries about letter status, approvals, and analytics.
    """
    
    def __init__(self):
        self.llm = OllamaLLM(model=os.getenv("OLLAMA_MODEL", "llama3"))
    
    def _classify_query(self, question: str) -> str:
        """
        Classify the type of letter workflow query.
        
        Returns:
            - 'status': Asking about specific letter status
            - 'pending': Asking about pending approvals
            - 'statistics': Asking for metrics/analytics
            - 'bottleneck': Asking about delays/stuck letters
            - 'search': Searching for letters by criteria
            - 'history': Asking about approval history
        """
        question_lower = question.lower()
        
        # Status queries
        if any(kw in question_lower for kw in ['where is', 'status of', 'what happened to', 'track']):
            return 'status'
        
        # Pending queries
        if any(kw in question_lower for kw in ['pending', 'waiting', 'needs approval', 'my queue']):
            return 'pending'
        
        # Statistics queries
        if any(kw in question_lower for kw in ['how many', 'total', 'count', 'average', 'statistics', 'metrics']):
            return 'statistics'
        
        # Bottleneck queries
        if any(kw in question_lower for kw in ['stuck', 'delayed', 'slow', 'bottleneck', 'old', 'long']):
            return 'bottleneck'
        
        # History queries
        if any(kw in question_lower for kw in ['history', 'who approved', 'timeline', 'when']):
            return 'history'
        
        # Search queries
        if any(kw in question_lower for kw in ['find', 'search', 'show me', 'list']):
            return 'search'
        
        return 'general'
    
    def answer_letter_query(self, question: str, user: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Main entry point for letter workflow queries.
        
        Args:
            question: Natural language query
            user: Current user context (for access control)
        
        Returns:
            Dict with answer and trace information
        """
        
        if not user:
            return {
                "answer": "Authentication required to access letter workflow information.",
                "trace": {"error": "not_authenticated"}
            }
        
        # Check database exists
        if not os.path.exists(LETTER_DB):
            return {
                "answer": "Letter workflow system not initialized. No letters have been created yet.",
                "trace": {"error": "database_not_found"}
            }
        
        query_type = self._classify_query(question)
        
        try:
            if query_type == 'status':
                return self._handle_status_query(question, user)
            elif query_type == 'pending':
                return self._handle_pending_query(question, user)
            elif query_type == 'statistics':
                return self._handle_statistics_query(question, user)
            elif query_type == 'bottleneck':
                return self._handle_bottleneck_query(question, user)
            elif query_type == 'history':
                return self._handle_history_query(question, user)
            elif query_type == 'search':
                return self._handle_search_query(question, user)
            else:
                return self._handle_general_query(question, user)
        
        except Exception as e:
            return {
                "answer": f"Error processing letter workflow query: {e}",
                "trace": {"error": str(e), "query_type": query_type}
            }
    
#     def _handle_status_query(self, question: str, user: Dict) -> Dict[str, Any]:
#         """Handle queries about specific letter status"""
        
#         # Extract letter ID if present
#         import re
#         letter_id_match = re.search(r'LTR\d+', question, re.IGNORECASE)
        
#         if not letter_id_match:
#             return {
#                 "answer": "Please specify a letter ID (e.g., LTR00001) to check its status.",
#                 "trace": {"query_type": "status", "error": "no_letter_id"}
#             }
        
#         letter_id = letter_id_match.group(0).upper()
        
#         # Get letter details
#         with sqlite3.connect(LETTER_DB) as conn:
#             conn.row_factory = sqlite3.Row
#             row = conn.execute("""
#                 SELECT * FROM letters WHERE doc_id = ?
#             """, (letter_id,)).fetchone()
            
#             if not row:
#                 return {
#                     "answer": f"Letter {letter_id} not found in the system.",
#                     "trace": {"query_type": "status", "letter_id": letter_id, "found": False}
#                 }
            
#             letter = dict(row)
#             letter['workflow_path'] = json.loads(letter['workflow_path'])
#             letter['approval_history'] = json.loads(letter['approval_history'])
        
#         # Build context for LLM
#         context = {
#             "letter_id": letter['doc_id'],
#             "title": letter['title'],
#             "current_state": letter['current_state'],
#             "created_by": letter['created_by'],
#             "creator_role": letter['creator_role'],
#             "workflow_path": letter['workflow_path'],
#             "approval_history": letter['approval_history'],
#             "created_at": letter['created_at'],
#             "urgency": letter['urgency'],
#             "waiting_days": (int(time.time()) - letter['created_at']) // 86400
#         }
        
#         prompt = f"""You are a workflow monitoring assistant. Based on this letter information, answer the user's question.

# Letter Details:
# {json.dumps(context, indent=2)}

# User Question: {question}

# Provide a clear, concise answer about the letter's status. Include:
# - Current state
# - Who needs to approve next (if pending)
# - How long it's been in current state
# - Any relevant history

# Answer:"""
        
#         answer = self.llm.invoke(prompt)
        
#         return {
#             "answer": answer,
#             "trace": {
#                 "query_type": "status",
#                 "letter_id": letter_id,
#                 "current_state": letter['current_state'],
#                 "data": context
#             }
#         }
    
    def _handle_pending_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about pending approvals"""
        
        role = user.get("role", "").upper()
        
        # Get pending letters for user's role
        state = f"PENDING_{role}"
        
        with sqlite3.connect(LETTER_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT doc_id, title, letter_type, urgency, created_by, 
                       creator_role, current_state, created_at
                FROM letters 
                WHERE current_state = ?
                ORDER BY urgency DESC, created_at ASC
            """, (state,)).fetchall()
            
            pending = [{
                "doc_id": row["doc_id"],
                "title": row["title"],
                "letter_type": row["letter_type"],
                "urgency": row["urgency"],
                "created_by": row["created_by"],
                "waiting_days": (int(time.time()) - row["created_at"]) // 86400
            } for row in rows]
        
        if not pending:
            return {
                "answer": f"No letters are currently pending approval from {role}. You're all caught up! ✅",
                "trace": {
                    "query_type": "pending",
                    "role": role,
                    "count": 0
                }
            }
        
        context = {
            "role": role,
            "pending_count": len(pending),
            "pending_letters": pending[:10],  # Top 10
            "urgent_count": len([l for l in pending if l["urgency"] == "URGENT"]),
            "high_count": len([l for l in pending if l["urgency"] == "HIGH"])
        }
        
        prompt = f"""You are a workflow assistant. Based on this pending approval data, answer the user's question.

Pending Approvals for {role}:
{json.dumps(context, indent=2)}

User Question: {question}

Provide a clear summary of pending letters. Include counts, urgency levels, and any letters that have been waiting long.

Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "pending",
                "role": role,
                "pending_count": len(pending),
                "data": context
            }
        }
    
#     def _handle_statistics_query(self, question: str, user: Dict) -> Dict[str, Any]:
#         """Handle queries about system statistics"""
        
#         role = user.get("role", "").upper()
        
#         # Only admins and high-level roles can see system-wide stats
#         if role not in ["ADMIN", "DVC", "DEAN"]:
#             return {
#                 "answer": "You don't have permission to view system-wide statistics. Only ADMIN, DVC, and DEAN roles can access this information.",
#                 "trace": {"query_type": "statistics", "access_denied": True}
#             }
        
#         with sqlite3.connect(LETTER_DB) as conn:
#             # Total letters
#             total = conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
            
#             # By state
#             by_state = {}
#             for row in conn.execute("""
#                 SELECT current_state, COUNT(*) as count 
#                 FROM letters 
#                 GROUP BY current_state
#             """):
#                 by_state[row[0]] = row[1]
            
#             # By urgency
#             by_urgency = {}
#             for row in conn.execute("""
#                 SELECT urgency, COUNT(*) as count 
#                 FROM letters 
#                 GROUP BY urgency
#             """):
#                 by_urgency[row[0]] = row[1]
            
#             # By creator role
#             by_creator = {}
#             for row in conn.execute("""
#                 SELECT creator_role, COUNT(*) as count 
#                 FROM letters 
#                 GROUP BY creator_role
#             """):
#                 by_creator[row[0]] = row[1]
            
#             # Approved letters
#             approved = by_state.get("APPROVED", 0)
            
#             # Average approval time
#             avg_time = conn.execute("""
#                 SELECT AVG(updated_at - created_at) 
#                 FROM letters 
#                 WHERE current_state = 'APPROVED'
#             """).fetchone()[0]
            
#             # Oldest pending
#             oldest = conn.execute("""
#                 SELECT doc_id, title, current_state, created_at
#                 FROM letters 
#                 WHERE current_state != 'APPROVED'
#                 ORDER BY created_at ASC
#                 LIMIT 3
#             """).fetchall()
        
#         stats = {
#             "total_letters": total,
#             "by_state": by_state,
#             "by_urgency": by_urgency,
#             "by_creator": by_creator,
#             "approved_count": approved,
#             "pending_count": total - approved,
#             "avg_approval_time_days": (avg_time / 86400) if avg_time else 0,
#             "oldest_pending": [{
#                 "doc_id": row[0],
#                 "title": row[1],
#                 "state": row[2],
#                 "waiting_days": (int(time.time()) - row[3]) // 86400
#             } for row in oldest]
#         }
        
#         prompt = f"""You are a system analytics assistant. Based on these letter workflow statistics, answer the user's question.

# System Statistics:
# {json.dumps(stats, indent=2)}

# User Question: {question}

# Provide a clear analysis with relevant metrics. Include insights about efficiency, trends, or areas needing attention.

# Answer:"""
        
#         answer = self.llm.invoke(prompt)
        
#         return {
#             "answer": answer,
#             "trace": {
#                 "query_type": "statistics",
#                 "role": role,
#                 "stats": stats
#             }
#         }
    
    def _handle_bottleneck_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about bottlenecks and delays"""
        
        role = user.get("role", "").upper()
        
        if role not in ["ADMIN", "DVC", "DEAN"]:
            return {
                "answer": "You don't have permission to view bottleneck analysis.",
                "trace": {"access_denied": True}
            }
        
        with sqlite3.connect(LETTER_DB) as conn:
            # Letters pending more than 7 days
            delayed = conn.execute("""
                SELECT doc_id, title, current_state, created_by, created_at, urgency
                FROM letters 
                WHERE current_state != 'APPROVED'
                  AND (? - created_at) > (7 * 86400)
                ORDER BY created_at ASC
            """, (int(time.time()),)).fetchall()
            
            # Count by pending state
            state_counts = {}
            for row in conn.execute("""
                SELECT current_state, COUNT(*) as count
                FROM letters
                WHERE current_state != 'APPROVED'
                GROUP BY current_state
            """):
                state_counts[row[0]] = row[1]
        
        bottlenecks = [{
            "doc_id": row[0],
            "title": row[1],
            "state": row[2],
            "created_by": row[3],
            "waiting_days": (int(time.time()) - row[4]) // 86400,
            "urgency": row[5]
        } for row in delayed]
        
        context = {
            "delayed_letters": bottlenecks,
            "state_counts": state_counts,
            "total_delayed": len(bottlenecks)
        }
        
        prompt = f"""You are a workflow optimization analyst. Based on this bottleneck data, answer the user's question.

Bottleneck Analysis:
{json.dumps(context, indent=2)}

User Question: {question}

Identify bottlenecks, suggest improvements, and provide actionable insights about delays.

Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "bottleneck",
                "bottlenecks_found": len(bottlenecks),
                "data": context
            }
        }
    
#     def _handle_history_query(self, question: str, user: Dict) -> Dict[str, Any]:
#         """Handle queries about approval history"""
        
#         # Extract letter ID
#         import re
#         letter_id_match = re.search(r'LTR\d+', question, re.IGNORECASE)
        
#         if not letter_id_match:
#             return {
#                 "answer": "Please specify a letter ID to view its approval history.",
#                 "trace": {"query_type": "history", "error": "no_letter_id"}
#             }
        
#         letter_id = letter_id_match.group(0).upper()
        
#         with sqlite3.connect(LETTER_DB) as conn:
#             conn.row_factory = sqlite3.Row
#             row = conn.execute("""
#                 SELECT doc_id, title, approval_history, workflow_path, current_state
#                 FROM letters 
#                 WHERE doc_id = ?
#             """, (letter_id,)).fetchone()
            
#             if not row:
#                 return {
#                     "answer": f"Letter {letter_id} not found.",
#                     "trace": {"query_type": "history", "letter_id": letter_id, "found": False}
#                 }
            
#             history = json.loads(row["approval_history"])
#             workflow = json.loads(row["workflow_path"])
        
#         context = {
#             "letter_id": letter_id,
#             "title": row["title"],
#             "current_state": row["current_state"],
#             "workflow_path": workflow,
#             "approval_history": history
#         }
        
#         prompt = f"""You are a workflow tracking assistant. Based on this approval history, answer the user's question.

# Approval History:
# {json.dumps(context, indent=2)}

# User Question: {question}

# Provide a clear timeline of approvals with who approved when.

# Answer:"""
        
#         answer = self.llm.invoke(prompt)
        
#         return {
#             "answer": answer,
#             "trace": {
#                 "query_type": "history",
#                 "letter_id": letter_id,
#                 "data": context
#             }
#         }
    
#     def _handle_search_query(self, question: str, user: Dict) -> Dict[str, Any]:
#         """Handle search queries for letters"""
        
#         # Simple search implementation
#         # You can enhance this with more sophisticated filtering
        
#         with sqlite3.connect(LETTER_DB) as conn:
#             conn.row_factory = sqlite3.Row
            
#             # Get all accessible letters (basic version)
#             rows = conn.execute("""
#                 SELECT doc_id, title, letter_type, urgency, current_state, 
#                        created_by, creator_role, created_at
#                 FROM letters 
#                 ORDER BY created_at DESC
#                 LIMIT 20
#             """).fetchall()
        
#         letters = [{
#             "doc_id": row["doc_id"],
#             "title": row["title"],
#             "letter_type": row["letter_type"],
#             "urgency": row["urgency"],
#             "current_state": row["current_state"],
#             "created_by": row["created_by"]
#         } for row in rows]
        
#         prompt = f"""Based on these letters, answer the user's search query.

# Available Letters (most recent 20):
# {json.dumps(letters, indent=2)}

# User Question: {question}

# Filter and present relevant letters based on the query.

# Answer:"""
        
#         answer = self.llm.invoke(prompt)
        
#         return {
#             "answer": answer,
#             "trace": {
#                 "query_type": "search",
#                 "letters_searched": len(letters)
#             }
#         }
    
    def _handle_general_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle general letter workflow queries"""
        
        # Get basic system info
        with sqlite3.connect(LETTER_DB) as conn:
            total = conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
            
            by_state = {}
            for row in conn.execute("""
                SELECT current_state, COUNT(*) as count 
                FROM letters 
                GROUP BY current_state
            """):
                by_state[row[0]] = row[1]
        
        context = {
            "total_letters": total,
            "by_state": by_state,
            "user_role": user.get("role")
        }
        
        prompt = f"""You are a letter workflow assistant. Based on this system context, answer the user's question.

System Context:
{json.dumps(context, indent=2)}

User Question: {question}

Provide a helpful answer about the letter workflow system.

Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "general",
                "context": context
            }
        }



    def _handle_status_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about specific letter status - WITH ACCESS CONTROL"""
        
        # Extract letter ID if present
        import re
        letter_id_match = re.search(r'LTR\d+', question, re.IGNORECASE)
        
        if not letter_id_match:
            return {
                "answer": "Please specify a letter ID (e.g., LTR00001) to check its status.",
                "trace": {"query_type": "status", "error": "no_letter_id"}
            }
        
        letter_id = letter_id_match.group(0).upper()
        user_role = user.get("role", "").upper()
        
        # Get letter details
        with sqlite3.connect(LETTER_DB) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT * FROM letters WHERE doc_id = ?
            """, (letter_id,)).fetchone()
            
            if not row:
                return {
                    "answer": f"Letter {letter_id} not found in the system.",
                    "trace": {"query_type": "status", "letter_id": letter_id, "found": False}
                }
            
            letter = dict(row)
            letter['workflow_path'] = json.loads(letter['workflow_path'])
            letter['approval_history'] = json.loads(letter['approval_history'])
        
        # ✅ ACCESS CONTROL CHECK
        if not _can_user_access_letter(user_role, letter['creator_role']):
            return {
                "answer": f"Access denied. Letter {letter_id} was created by {letter['creator_role']}, which is above your access level. You can only access letters created at your level or below.",
                "trace": {
                    "query_type": "status",
                    "letter_id": letter_id,
                    "access_denied": True,
                    "reason": f"User role {user_role} cannot access letters created by {letter['creator_role']}"
                }
            }
        
        # Build context for LLM (same as before)
        context = {
            "letter_id": letter['doc_id'],
            "title": letter['title'],
            "current_state": letter['current_state'],
            "created_by": letter['created_by'],
            "creator_role": letter['creator_role'],
            "workflow_path": letter['workflow_path'],
            "approval_history": letter['approval_history'],
            "created_at": letter['created_at'],
            "urgency": letter['urgency'],
            "waiting_days": (int(time.time()) - letter['created_at']) // 86400
        }
        
        prompt = f"""You are a workflow monitoring assistant. Based on this letter information, answer the user's question.

    Letter Details:
    {json.dumps(context, indent=2)}

    User Question: {question}

    Provide a clear, concise answer about the letter's status. Include:
    - Current state
    - Who needs to approve next (if pending)
    - How long it's been in current state
    - Any relevant history

    Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "status",
                "letter_id": letter_id,
                "current_state": letter['current_state'],
                "access_granted": True,
                "data": context
            }
        }


    def _handle_search_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle search queries for letters - WITH ACCESS CONTROL"""
        
        user_role = user.get("role", "").upper()
        
        with sqlite3.connect(LETTER_DB) as conn:
            conn.row_factory = sqlite3.Row
            
            # Get all letters
            rows = conn.execute("""
                SELECT doc_id, title, letter_type, urgency, current_state, 
                    created_by, creator_role, created_at
                FROM letters 
                ORDER BY created_at DESC
                LIMIT 50
            """).fetchall()
        
        all_letters = [{
            "doc_id": row["doc_id"],
            "title": row["title"],
            "letter_type": row["letter_type"],
            "urgency": row["urgency"],
            "current_state": row["current_state"],
            "created_by": row["created_by"],
            "creator_role": row["creator_role"]
        } for row in rows]
        
        # ✅ FILTER BY ACCESS CONTROL
        accessible_letters = _filter_accessible_letters(all_letters, user_role)
        
        if not accessible_letters:
            return {
                "answer": "No accessible letters found. You can only view letters created at your level or below in the hierarchy.",
                "trace": {
                    "query_type": "search",
                    "total_letters": len(all_letters),
                    "accessible_letters": 0,
                    "access_filtered": True
                }
            }
        
        prompt = f"""Based on these letters you have access to, answer the user's search query.

    Accessible Letters ({len(accessible_letters)} out of {len(all_letters)} total):
    {json.dumps(accessible_letters[:20], indent=2)}

    Note: User role is {user_role}. They can only see letters created at their level or below.

    User Question: {question}

    Filter and present relevant letters based on the query.

    Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "search",
                "total_letters": len(all_letters),
                "accessible_letters": len(accessible_letters),
                "filtered_count": len(all_letters) - len(accessible_letters)
            }
        }


    def _handle_history_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about approval history - WITH ACCESS CONTROL"""
        
        # Extract letter ID
        import re
        letter_id_match = re.search(r'LTR\d+', question, re.IGNORECASE)
        
        if not letter_id_match:
            return {
                "answer": "Please specify a letter ID to view its approval history.",
                "trace": {"query_type": "history", "error": "no_letter_id"}
            }
        
        letter_id = letter_id_match.group(0).upper()
        user_role = user.get("role", "").upper()
        
        with sqlite3.connect(LETTER_DB) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT doc_id, title, approval_history, workflow_path, 
                    current_state, creator_role
                FROM letters 
                WHERE doc_id = ?
            """, (letter_id,)).fetchone()
            
            if not row:
                return {
                    "answer": f"Letter {letter_id} not found.",
                    "trace": {"query_type": "history", "letter_id": letter_id, "found": False}
                }
            
            # ✅ ACCESS CONTROL CHECK
            if not _can_user_access_letter(user_role, row["creator_role"]):
                return {
                    "answer": f"Access denied. You cannot view the history of letter {letter_id} as it was created by {row['creator_role']}, which is above your access level.",
                    "trace": {
                        "query_type": "history",
                        "letter_id": letter_id,
                        "access_denied": True
                    }
                }
            
            history = json.loads(row["approval_history"])
            workflow = json.loads(row["workflow_path"])
        
        context = {
            "letter_id": letter_id,
            "title": row["title"],
            "current_state": row["current_state"],
            "workflow_path": workflow,
            "approval_history": history
        }
        
        prompt = f"""You are a workflow tracking assistant. Based on this approval history, answer the user's question.

    Approval History:
    {json.dumps(context, indent=2)}

    User Question: {question}

    Provide a clear timeline of approvals with who approved when.

    Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "history",
                "letter_id": letter_id,
                "access_granted": True,
                "data": context
            }
        }


    def _handle_statistics_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about system statistics - WITH FILTERED DATA"""
        
        role = user.get("role", "").upper()
        
        # Only admins and high-level roles can see system-wide stats
        if role not in ["ADMIN", "DVC", "DEAN"]:
            return {
                "answer": "You don't have permission to view system-wide statistics. Only ADMIN, DVC, and DEAN roles can access this information.",
                "trace": {"query_type": "statistics", "access_denied": True}
            }
        
        with sqlite3.connect(LETTER_DB) as conn:
            # Get all letters
            all_letters = conn.execute("""
                SELECT creator_role FROM letters
            """).fetchall()
            
            # ✅ FILTER BY ACCESS CONTROL
            accessible_count = sum(
                1 for letter in all_letters
                if _can_user_access_letter(role, letter[0])
            )
            
            # Get statistics only for accessible letters
            if role == "ADMIN":
                # Admin sees everything
                state_filter = ""
                params = []
            else:
                # Filter by accessible creator roles
                accessible_roles = [r for r, lvl in ROLE_LEVELS.items() if lvl <= ROLE_LEVELS.get(role, 0)]
                placeholders = ",".join("?" * len(accessible_roles))
                state_filter = f"WHERE creator_role IN ({placeholders})"
                params = accessible_roles
            
            # Total accessible letters
            total = conn.execute(f"SELECT COUNT(*) FROM letters {state_filter}", params).fetchone()[0]
            
            # By state (accessible only)
            by_state = {}
            for row in conn.execute(f"""
                SELECT current_state, COUNT(*) as count 
                FROM letters 
                {state_filter}
                GROUP BY current_state
            """, params):
                by_state[row[0]] = row[1]
            
            # Other statistics...
            # (Similar filtering applied to all queries)
        
        stats = {
            "accessible_letters": total,
            "total_system_letters": len(all_letters),
            "by_state": by_state,
            "user_role": role,
            "access_level_note": f"Showing statistics for letters accessible to {role} level"
        }
        
        prompt = f"""You are a system analytics assistant. Based on these statistics for letters you can access, answer the user's question.

    Statistics (filtered by access level):
    {json.dumps(stats, indent=2)}

    User Question: {question}

    Note: These statistics only include letters the user has permission to view.

    Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "statistics",
                "role": role,
                "accessible_letters": total,
                "stats": stats
            }
        }



# Singleton instance
_letter_rag_instance = None

def letter_workflow_query(question: str, user: Optional[Dict] = None) -> Dict[str, Any]:
    """Public entry point for letter workflow queries"""
    global _letter_rag_instance
    if _letter_rag_instance is None:
        _letter_rag_instance = LetterWorkflowRAG()
    return _letter_rag_instance.answer_letter_query(question, user)