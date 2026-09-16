from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    """Shared state for the Phase 3 + Phase 4 LangGraph workflow."""

    # User request / identity
    question: str
    user: Optional[Dict[str, Any]]

    # Query analysis / routing
    intent: str
    student_id: Optional[str]
    course_code: Optional[str]
    document_type: Optional[str]
    document_id: Optional[str]
    document_retrieval: bool
    metadata_only: bool
    repository: Optional[str]
    repositories: List[str]
    requires_confidential_access: bool

    # Authorization
    authorization: Dict[str, Any]
    authorized: bool

    # Metadata-only repository RAG
    metadata_results: List[Dict[str, Any]]
    metadata_search: Dict[str, Any]

    # Authoritative blockchain resolution
    blockchain_metadata: Dict[str, Any]
    document_version: Optional[int]
    version: Optional[int]
    previous_version: Optional[int]
    resolved_cid: Optional[str]
    ipfs_cid: Optional[str]
    resolved_content_hash: Optional[str]
    content_hash: Optional[str]
    approval_status: Optional[str]
    approved_by: Optional[str]
    blockchain_timestamp: Optional[int]

    # Confidential document / IPFS / decryption / integrity
    document: Optional[Dict[str, Any]]
    ipfs_retrieval: Dict[str, Any]
    decryption: Dict[str, Any]
    integrity_verified: bool

    # Phase 4 LLM Q&A
    llm_answer: Optional[str]
    llm_invoked: bool
    llm_model: Optional[str]
    llm_context_authorized: bool
    llm_error: Optional[str]

    # Response / diagnostics
    answer: str
    error: Optional[str]
    trace: Dict[str, Any]
