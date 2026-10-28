# Ollama LLM-as-a-Judge

## Files
- `llm_judge.py`: local Ollama judge; validates structured JSON scores.
- `evaluate_llm_judge.py`: invokes the current LangGraph `answer_question` entry point.
- `llm_judge_dataset.json`: synthetic, privacy-safe initial cases.
- `requirements-addition.txt`: dependency.

## Setup
1. Ensure Ollama is running and install the model:
   ```bash
   ollama pull llama3
   ollama list
   ```
2. In the project's active virtual environment:
   ```bash
   pip install langchain-ollama
   ```
3. Copy the three Python/JSON files into `python_backend/ai/`.
4. From the project root run:
   ```bash
   python -m python_backend.ai.evaluate_llm_judge
   ```
The report is written to `python_backend/ai/llm_judge_report.json`.

Optional environment variables:
```bash
OLLAMA_JUDGE_MODEL=llama3 OLLAMA_BASE_URL=http://localhost:11434 python -m python_backend.ai.evaluate_llm_judge
```

## Privacy and limitations
- Production `agent.py` is not modified.
- The complete LangGraph public entry point is called with `user_context=None`.
- Synthetic IDs are used. No agent trace, raw blockchain metadata, CID, hash, or retrieved document is sent to the judge or saved in the report.
- If the graph trace indicates the document-retrieval node ran, its answer is redacted and not judged.
- This first stage evaluates end-to-end routing/refusal and non-sensitive responses, not the quality of answers to authorized confidential document retrieval. Later, use an isolated test backend and synthetic documents for that evaluation.
- LLM-as-a-Judge is approximate; manually inspect a sample. Deterministic access-control and integrity tests remain mandatory.
