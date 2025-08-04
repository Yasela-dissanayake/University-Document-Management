from langgraph.graph import StateGraph, START, END
from python_backend.ai.tools import get_onchain_student, get_offchain_semester
from langchain_ollama import OllamaLLM as Ollama
from typing import TypedDict

# Define agent state structure
class State(TypedDict):
    question: str
    onchain: dict
    offchain: dict
    answer: str

# Initialize your local Llama (Ollama) LLM
llm = Ollama(model="llama3")

def generate_answer(state: State):

    onchain_data = state.get("onchain") or {}
    offchain_data = state.get("offchain") or {}
    context = {**onchain_data, **offchain_data}

    # Aggregate all context and pass to LLM
    # context = {**state.get("onchain", {}), **state.get("offchain", {})}
    prompt = f"Q: {state['question']}\nContext: {context}\nA:"
    answer = llm.invoke(prompt)
    return {"answer": answer}

# Build the graph with explicit start and connections
graph = StateGraph(State)

# Add nodes
graph.add_node("get_onchain", get_onchain_student)
graph.add_node("get_offchain", get_offchain_semester)
graph.add_node("generate_answer", generate_answer)

# Add edges to define the flow
graph.add_edge(START, "get_onchain")  # Start with getting onchain data
graph.add_edge("get_onchain", "get_offchain")  # Then get offchain data
graph.add_edge("get_offchain", "generate_answer")  # Then generate answer
graph.add_edge("generate_answer", END)  # End after generating answer

# Compile the graph
compiled_graph = graph.compile()

def answer_question(question: str):
    # Entry point for external API/showcase; runs the graph synchronously
    state = {"question": question}
    result = compiled_graph.invoke(state)
    return result["answer"]