"""
AI Agent Metrics Testing Script
--------------------------------
Tests both academic and workflow queries against defined metrics.
Generates detailed performance reports.
"""

import json
import time
import logging
from typing import Dict, Any, List
from datetime import datetime
from pathlib import Path

# Import your agent
from python_backend.ai.agent import answer_question

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects and analyzes metrics from query results"""
    
    def __init__(self):
        self.results = {
            "academic": [],
            "workflow": []
        }
        self.start_time = datetime.now()
    
    def test_query(self, query: str, category: str, user_context: Dict) -> Dict[str, Any]:
        """Test a single query and collect metrics"""
        start = time.time()
        
        try:
            result = answer_question(query, user_context)
            duration = time.time() - start
            
            metrics = {
                "query": query,
                "user": user_context.get("username"),
                "role": user_context.get("role"),
                "duration_ms": round(duration * 1000, 2),
                "success": bool(result.get("answer")),
                "has_trace": "trace" in result,
                "answer": result.get("answer", ""),
                "trace": result.get("trace", {}),
                "error": None
            }
            
            # Category-specific metrics
            if category == "academic":
                metrics.update(self._analyze_academic_query(result, query))
            else:
                metrics.update(self._analyze_workflow_query(result, query))
            
            return metrics
            
        except Exception as e:
            logger.error(f"Query failed: {query} - {e}")
            return {
                "query": query,
                "user": user_context.get("username"),
                "role": user_context.get("role"),
                "duration_ms": round((time.time() - start) * 1000, 2),
                "success": False,
                "has_trace": False,
                "answer": None,
                "trace": {},
                "error": str(e)
            }
    
    def _analyze_academic_query(self, result: Dict, query: str) -> Dict:
        """Analyze academic query specific metrics"""
        trace = result.get("trace", {})
        answer = result.get("answer", "").lower()
        
        return {
            "blockchain_retrieval": trace.get("method") in ["blockchain_tools", "tool"],
            "ipfs_retrieval": "ipfs" in str(trace).lower() or "cid" in str(trace).lower(),
            "acl_enforced": "role" in trace or "accessible_docs" in trace,
            "document_filtering": "accessible_docs" in trace or "filtered_count" in trace,
            "reasoning_included": bool(trace),
            "student_id_detected": "STD001" in query.upper(),
            "grade_found": any(grade in answer for grade in ["a+", "a-", "a", "b+", "b", "c", "d", "f"]),
            "numerical_data": any(char.isdigit() for char in answer)
        }
    
    def _analyze_workflow_query(self, result: Dict, query: str) -> Dict:
        """Analyze workflow query specific metrics"""
        trace = result.get("trace", {})
        answer = result.get("answer", "").lower()
        
        return {
            "status_retrieved": any(status in answer for status in ["pending", "approved", "rejected"]),
            "statistics_generated": any(word in answer for word in ["total", "count", "average", "percentage"]),
            "bottleneck_identified": "bottleneck" in query.lower() and ("hod" in answer or "dean" in answer or "stage" in answer),
            "approver_identified": any(role in answer for role in ["hod", "dean", "ar", "dvc"]),
            "time_mentioned": any(word in answer for word in ["days", "hours", "time", "duration"]),
            "letter_id_detected": any(f"ltr{i:05d}" in query.lower() for i in range(1, 10)),
            "natural_language": len(answer.split()) > 10  # Response is in sentences, not just data
        }
    
    def add_result(self, category: str, metrics: Dict):
        """Add test result to collection"""
        self.results[category].append(metrics)
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive metrics report"""
        report = {
            "test_summary": {
                "total_tests": len(self.results["academic"]) + len(self.results["workflow"]),
                "academic_tests": len(self.results["academic"]),
                "workflow_tests": len(self.results["workflow"]),
                "duration": str(datetime.now() - self.start_time),
                "timestamp": datetime.now().isoformat()
            },
            "academic_metrics": self._calculate_academic_metrics(),
            "workflow_metrics": self._calculate_workflow_metrics(),
            "performance_metrics": self._calculate_performance_metrics()
        }
        
        return report
    
    def _calculate_academic_metrics(self) -> Dict:
        """Calculate academic query metrics"""
        results = self.results["academic"]
        if not results:
            return {}
        
        total = len(results)
        
        return {
            "total_queries": total,
            "successful_queries": sum(1 for r in results if r["success"]),
            "success_rate": round(sum(1 for r in results if r["success"]) / total * 100, 2),
            "ai_query_accuracy": round(sum(1 for r in results if r.get("grade_found") or r.get("numerical_data")) / total * 100, 2),
            "blockchain_retrieval_success": round(sum(1 for r in results if r.get("blockchain_retrieval")) / total * 100, 2),
            "ipfs_retrieval_success": round(sum(1 for r in results if r.get("ipfs_retrieval")) / total * 100, 2),
            "acl_enforcement": round(sum(1 for r in results if r.get("acl_enforced")) / total * 100, 2),
            "document_filtering_accuracy": round(sum(1 for r in results if r.get("document_filtering")) / total * 100, 2),
            "reasoning_trace_included": round(sum(1 for r in results if r.get("reasoning_included")) / total * 100, 2),
            "avg_response_time_ms": round(sum(r["duration_ms"] for r in results) / total, 2),
            "errors": sum(1 for r in results if r.get("error"))
        }
    
    def _calculate_workflow_metrics(self) -> Dict:
        """Calculate workflow query metrics"""
        results = self.results["workflow"]
        if not results:
            return {}
        
        total = len(results)
        
        return {
            "total_queries": total,
            "successful_queries": sum(1 for r in results if r["success"]),
            "success_rate": round(sum(1 for r in results if r["success"]) / total * 100, 2),
            "status_query_accuracy": round(sum(1 for r in results if r.get("status_retrieved")) / total * 100, 2),
            "pending_detection": round(sum(1 for r in results if r.get("status_retrieved") and "pending" in r.get("answer", "").lower()) / total * 100, 2),
            "statistics_generation_accuracy": round(sum(1 for r in results if r.get("statistics_generated")) / total * 100, 2),
            "bottleneck_identification": round(sum(1 for r in results if r.get("bottleneck_identified")) / max(sum(1 for r in results if "bottleneck" in r["query"].lower()), 1) * 100, 2),
            "natural_language_understanding": round(sum(1 for r in results if r.get("natural_language")) / total * 100, 2),
            "avg_response_time_ms": round(sum(r["duration_ms"] for r in results) / total, 2),
            "errors": sum(1 for r in results if r.get("error"))
        }
    
    def _calculate_performance_metrics(self) -> Dict:
        """Calculate overall performance metrics"""
        all_results = self.results["academic"] + self.results["workflow"]
        
        if not all_results:
            return {}
        
        durations = [r["duration_ms"] for r in all_results]
        
        return {
            "total_queries_executed": len(all_results),
            "overall_success_rate": round(sum(1 for r in all_results if r["success"]) / len(all_results) * 100, 2),
            "avg_response_time_ms": round(sum(durations) / len(durations), 2),
            "min_response_time_ms": min(durations),
            "max_response_time_ms": max(durations),
            "traces_included": round(sum(1 for r in all_results if r["has_trace"]) / len(all_results) * 100, 2),
            "total_errors": sum(1 for r in all_results if r.get("error"))
        }


def load_test_queries():
    """Load test queries from JSON file"""
    # For now, we'll use the embedded queries
    # In production, load from the JSON artifact created earlier
    
    with open("python_backend/test_queries.json", "r") as f:
        return json.load(f)


def run_comprehensive_test():
    """Run all test queries and generate report"""
    
    print("="*80)
    print("AI AGENT COMPREHENSIVE METRICS TEST")
    print("="*80)
    print()
    
    # Load queries
    try:
        queries_data = load_test_queries()
    except FileNotFoundError:
        print("❌ test_queries.json not found. Please create it first.")
        return
    
    academic_queries = queries_data["academic_queries"]
    workflow_queries = queries_data["workflow_queries"]
    test_users = queries_data["test_user_contexts"]
    
    collector = MetricsCollector()
    
    # Test Academic Queries
    print(f"📚 Testing {len(academic_queries)} academic queries...")
    print("-"*80)
    
    for i, query in enumerate(academic_queries, 1):
        print(f"  [{i}/{len(academic_queries)}] {query[:60]}...")
        
        # Test with different user roles
        user = test_users["admin"]  # Use admin for most tests
        
        # Test ACL with student user for specific queries
        if "STD002" in query or "confidential" in query.lower():
            user = test_users["student"]
        
        metrics = collector.test_query(query, "academic", user)
        collector.add_result("academic", metrics)
        
        if not metrics["success"]:
            print(f"    ⚠️  Failed: {metrics.get('error', 'Unknown error')}")
    
    print()
    print(f"📨 Testing {len(workflow_queries)} workflow queries...")
    print("-"*80)
    
    # Test Workflow Queries
    for i, query in enumerate(workflow_queries, 1):
        print(f"  [{i}/{len(workflow_queries)}] {query[:60]}...")
        
        # Vary user roles for workflow queries
        if "my approval" in query.lower() or "assigned to me" in query.lower():
            user = test_users["hod"]
        elif "dean" in query.lower():
            user = test_users["dean"]
        else:
            user = test_users["admin"]
        
        metrics = collector.test_query(query, "workflow", user)
        collector.add_result("workflow", metrics)
        
        if not metrics["success"]:
            print(f"    ⚠️  Failed: {metrics.get('error', 'Unknown error')}")
    
    # Generate Report
    print()
    print("="*80)
    print("GENERATING METRICS REPORT")
    print("="*80)
    
    report = collector.generate_report()
    
    # Save detailed results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    with open(f"test_results_{timestamp}.json", "w") as f:
        json.dump({
            "report": report,
            "detailed_results": collector.results
        }, f, indent=2)
    
    print(f"\n✅ Detailed results saved to: test_results_{timestamp}.json")
    
    # Print Summary
    print()
    print("="*80)
    print("METRICS SUMMARY")
    print("="*80)
    print()
    
    print("📚 ACADEMIC QUERIES METRICS:")
    print("-"*80)
    for key, value in report["academic_metrics"].items():
        print(f"  {key:.<50} {value}")
    
    print()
    print("📨 WORKFLOW QUERIES METRICS:")
    print("-"*80)
    for key, value in report["workflow_metrics"].items():
        print(f"  {key:.<50} {value}")
    
    print()
    print("⚡ PERFORMANCE METRICS:")
    print("-"*80)
    for key, value in report["performance_metrics"].items():
        print(f"  {key:.<50} {value}")
    
    print()
    print("="*80)


if __name__ == "__main__":
    run_comprehensive_test()