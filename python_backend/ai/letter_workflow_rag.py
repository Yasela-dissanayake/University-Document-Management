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
    
    def _handle_status_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about specific letter status"""
        
        # Extract letter ID if present
        import re
        letter_id_match = re.search(r'LTR\d+', question, re.IGNORECASE)
        
        if not letter_id_match:
            return {
                "answer": "Please specify a letter ID (e.g., LTR00001) to check its status.",
                "trace": {"query_type": "status", "error": "no_letter_id"}
            }
        
        letter_id = letter_id_match.group(0).upper()
        
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
        
        # Build context for LLM
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
                "data": context
            }
        }
    
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
    
    def _handle_statistics_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about system statistics"""
        
        role = user.get("role", "").upper()
        
        # Only admins and high-level roles can see system-wide stats
        if role not in ["ADMIN", "DVC", "DEAN"]:
            return {
                "answer": "You don't have permission to view system-wide statistics. Only ADMIN, DVC, and DEAN roles can access this information.",
                "trace": {"query_type": "statistics", "access_denied": True}
            }
        
        with sqlite3.connect(LETTER_DB) as conn:
            # Total letters
            total = conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
            
            # By state
            by_state = {}
            for row in conn.execute("""
                SELECT current_state, COUNT(*) as count 
                FROM letters 
                GROUP BY current_state
            """):
                by_state[row[0]] = row[1]
            
            # By urgency
            by_urgency = {}
            for row in conn.execute("""
                SELECT urgency, COUNT(*) as count 
                FROM letters 
                GROUP BY urgency
            """):
                by_urgency[row[0]] = row[1]
            
            # By creator role
            by_creator = {}
            for row in conn.execute("""
                SELECT creator_role, COUNT(*) as count 
                FROM letters 
                GROUP BY creator_role
            """):
                by_creator[row[0]] = row[1]
            
            # Approved letters
            approved = by_state.get("APPROVED", 0)
            
            # Average approval time
            avg_time = conn.execute("""
                SELECT AVG(updated_at - created_at) 
                FROM letters 
                WHERE current_state = 'APPROVED'
            """).fetchone()[0]
            
            # Oldest pending
            oldest = conn.execute("""
                SELECT doc_id, title, current_state, created_at
                FROM letters 
                WHERE current_state != 'APPROVED'
                ORDER BY created_at ASC
                LIMIT 3
            """).fetchall()
        
        stats = {
            "total_letters": total,
            "by_state": by_state,
            "by_urgency": by_urgency,
            "by_creator": by_creator,
            "approved_count": approved,
            "pending_count": total - approved,
            "avg_approval_time_days": (avg_time / 86400) if avg_time else 0,
            "oldest_pending": [{
                "doc_id": row[0],
                "title": row[1],
                "state": row[2],
                "waiting_days": (int(time.time()) - row[3]) // 86400
            } for row in oldest]
        }
        
        prompt = f"""You are a system analytics assistant. Based on these letter workflow statistics, answer the user's question.

System Statistics:
{json.dumps(stats, indent=2)}

User Question: {question}

Provide a clear analysis with relevant metrics. Include insights about efficiency, trends, or areas needing attention.

Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "statistics",
                "role": role,
                "stats": stats
            }
        }
    
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
    
    def _handle_history_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle queries about approval history"""
        
        # Extract letter ID
        import re
        letter_id_match = re.search(r'LTR\d+', question, re.IGNORECASE)
        
        if not letter_id_match:
            return {
                "answer": "Please specify a letter ID to view its approval history.",
                "trace": {"query_type": "history", "error": "no_letter_id"}
            }
        
        letter_id = letter_id_match.group(0).upper()
        
        with sqlite3.connect(LETTER_DB) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT doc_id, title, approval_history, workflow_path, current_state
                FROM letters 
                WHERE doc_id = ?
            """, (letter_id,)).fetchone()
            
            if not row:
                return {
                    "answer": f"Letter {letter_id} not found.",
                    "trace": {"query_type": "history", "letter_id": letter_id, "found": False}
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
                "data": context
            }
        }
    
    def _handle_search_query(self, question: str, user: Dict) -> Dict[str, Any]:
        """Handle search queries for letters"""
        
        # Simple search implementation
        # You can enhance this with more sophisticated filtering
        
        with sqlite3.connect(LETTER_DB) as conn:
            conn.row_factory = sqlite3.Row
            
            # Get all accessible letters (basic version)
            rows = conn.execute("""
                SELECT doc_id, title, letter_type, urgency, current_state, 
                       created_by, creator_role, created_at
                FROM letters 
                ORDER BY created_at DESC
                LIMIT 20
            """).fetchall()
        
        letters = [{
            "doc_id": row["doc_id"],
            "title": row["title"],
            "letter_type": row["letter_type"],
            "urgency": row["urgency"],
            "current_state": row["current_state"],
            "created_by": row["created_by"]
        } for row in rows]
        
        prompt = f"""Based on these letters, answer the user's search query.

Available Letters (most recent 20):
{json.dumps(letters, indent=2)}

User Question: {question}

Filter and present relevant letters based on the query.

Answer:"""
        
        answer = self.llm.invoke(prompt)
        
        return {
            "answer": answer,
            "trace": {
                "query_type": "search",
                "letters_searched": len(letters)
            }
        }
    
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


# Singleton instance
_letter_rag_instance = None

def letter_workflow_query(question: str, user: Optional[Dict] = None) -> Dict[str, Any]:
    """Public entry point for letter workflow queries"""
    global _letter_rag_instance
    if _letter_rag_instance is None:
        _letter_rag_instance = LetterWorkflowRAG()
    return _letter_rag_instance.answer_letter_query(question, user)