from python_backend.ai.agent import answer_question


if __name__ == "__main__":
    question = input("Ask AI agent a question: ")
    print("[AI answer]:", answer_question(question))
