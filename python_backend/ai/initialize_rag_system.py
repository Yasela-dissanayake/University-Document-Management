#!/usr/bin/env python3
"""
Initialize RAG System for Academic Documents
--------------------------------------------
This script will:
1. Scan the blockchain for all students
2. Fetch their academic documents from IPFS
3. Build the RAG vector store for AI queries

Run this ONCE after setting up your system.
"""

import os
import sys
import logging
from pathlib import Path

# Setup path - go up to project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def main():
    print("=" * 70)
    print("🚀 RAG System Initialization")
    print("=" * 70)
    print()
    
    # Import here to ensure proper path
    from python_backend.ai.rag_agent import rebuild_index
    
    print("📋 This will:")
    print("  1. Scan blockchain for all students")
    print("  2. Fetch their academic documents from IPFS")
    print("  3. Build the vector database for AI queries")
    print()
    
    response = input("Continue? [y/N]: ")
    if response.lower() != 'y':
        print("❌ Cancelled")
        return
    
    print()
    print("🔄 Starting initialization...")
    print("-" * 70)
    
    try:
        success = rebuild_index()
        
        print()
        print("=" * 70)
        if success:
            print("✅ RAG System initialized successfully!")
            print()
            print("Next steps:")
            print("  1. Restart your Flask application")
            print("  2. Try queries like: 'What is the grade of CSC3103 of STD001?'")
        else:
            print("⚠️ RAG System initialized but no documents were found")
            print()
            print("Possible reasons:")
            print("  1. No students registered in the blockchain")
            print("  2. No semester records uploaded to IPFS")
            print("  3. Connection issues with blockchain/IPFS")
            print()
            print("To add test data, run:")
            print("  python python_backend/register_student.py")
            print("  python python_backend/update_semester.py")
        print("=" * 70)
        
    except Exception as e:
        print()
        print("=" * 70)
        print(f"❌ Error during initialization: {e}")
        print()
        import traceback
        traceback.print_exc()
        print("=" * 70)


if __name__ == "__main__":
    main()