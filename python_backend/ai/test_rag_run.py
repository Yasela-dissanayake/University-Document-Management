# python_backend/ai/test_rag_run.py
import os
from pprint import pprint

# Optional: set defaults for Ollama model
os.environ["OLLAMA_MODEL"] = "llama3"

from python_backend.ai.rag_agent import rag_answer

def main():
    print("🚀 Testing RAG system with live Blockchain + IPFS + Ollama\n")

    queries = [
        "Summarize S20841’s academic performance.",
        "List all documents stored for S20841.",
        "What are the course grades of S20841?",
        "What is the GPA of S20841?",
    ]

    for q in queries:
        print("\n🧠 Query:", q)
        resp = rag_answer(q)
        print("Answer:", resp.get("answer", "No answer"))
        print("Trace:", resp.get("trace", {}))
        print("--------------------------------------------------------")

if __name__ == "__main__":
    main()
