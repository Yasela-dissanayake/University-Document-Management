"""
LangGraph orchestration for the University Hybrid Blockchain-AI agent.

Current flow:
    User Question
        -> Query Analyzer
        -> Authorization Gate
        -> Repository Orchestrator
        -> Repository Metadata RAG
        -> Blockchain Authoritative Resolution
        -> Authorized IPFS Retrieval / Decryption
        -> Phase 3 Response

For public on-chain questions:
    User Question
        -> Query Analyzer
        -> Authorization Gate
        -> Blockchain Read
        -> Phase 3 Response

Security rules:
    Authorization happens BEFORE confidential document retrieval.
    Repository RAG is metadata-only and is never treated as the
    authoritative source for document content, version, or CID.

Query-classification rule:
    Explicit blockchain wording is required before generic words such as
    "registered" or "registration" are interpreted as an on-chain query.
    This prevents document/Registrar queries from being misrouted.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from python_backend import rbac
from python_backend.ai.graph_state import AgentState
from python_backend.ai.repository_metadata import REPOSITORY_DISPLAY_NAMES
from python_backend.ai.repository_rag import RepositoryMetadataRAG
from python_backend.ai import tools

logger = logging.getLogger(__name__)


# Phase 2 metadata-only RAG service. It never retrieves IPFS content.
metadata_rag = RepositoryMetadataRAG()


REPOSITORY_KEYWORDS = {
    "academic": {
        "grade", "grades", "gpa", "cgpa", "course", "courses",
        "subject", "subjects", "mark", "marks", "score", "scores",
        "transcript", "academic", "semester", "semesters",
        "credits", "credit", "results", "result", "performance",
    },
    "registrar": {
        "registration", "registered", "enrollment", "enrolment",
        "certificate", "certification", "admission", "identity",
    },
    "examination": {
        "exam", "examination", "examinations", "exam result",
        "exam results", "mark sheet", "results", "result sheet",
    },
    "student_services": {
        "scholarship", "accommodation", "hostel", "student service",
        "student services", "request", "requests", "welfare", "leave",
    },
}

CONFIDENTIAL_KEYWORDS = {
    "grade", "grades", "gpa", "cgpa", "course", "courses",
    "subject", "subjects", "mark", "marks", "score", "scores",
    "transcript", "academic", "semester", "semesters", "credits",
    "results", "result", "performance", "certificate", "document",
    "documents", "record", "records", "history", "version", "versions",
    "ipfs", "cid",
}

PUBLIC_ONCHAIN_KEYWORDS = {
    # Generic public on-chain attributes. Terms such as "registered" and
    # "registration" are intentionally excluded here because they are also
    # valid Registrar/document vocabulary. They are treated as on-chain only
    # when accompanied by explicit blockchain/public-state wording below.
    "blockchain", "student id", "student identifier", "program",
    "year", "exists", "status",
}

WORKFLOW_KEYWORDS = {
    "letter_workflow": {
        "letter", "letter status", "letter workflow", "letter approval",
        "pending letter", "letter history", "who approved letter",
    },
    "semester_workflow": {
        "semester workflow", "semester status", "semester approval",
        "pending semester", "semester bottleneck", "who approved semester",
    },
}

REPOSITORY_DISPLAY_NAMES = {
    "academic": "Academic Repository",
    "registrar": "Registrar Repository",
    "examination": "Examination Repository",
    "student_services": "Student Services Repository",
}


def _normalize_question(question: str) -> str:
    return " ".join((question or "").strip().split())


def _extract_student_id(question: str) -> Optional[str]:
    match = re.search(r"\b(?:S\d{5}|STD\d{3})\b", question or "", re.IGNORECASE)
    return match.group(0).upper() if match else None


def _extract_course_code(
    question: str,
    student_id: Optional[str] = None,
) -> Optional[str]:
    """Extract a course code without mistaking a student ID for one.

    Student IDs such as STD001 also match the generic ``letters + digits``
    pattern used for course codes, so any detected student ID is explicitly
    excluded from the course-code candidates.
    """
    student_id_upper = (student_id or "").upper()

    for match in re.finditer(
        r"\b[A-Z]{2,4}\d{3,4}\b",
        question or "",
        re.IGNORECASE,
    ):
        candidate = match.group(0).upper()
        if candidate != student_id_upper:
            return candidate

    return None


def _contains_keyword(text: str, keyword: str) -> bool:
    text = text.lower()
    keyword = keyword.lower()
    if " " in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _keyword_score(text: str, keywords: set[str]) -> int:
    return sum(1 for keyword in keywords if _contains_keyword(text, keyword))


def _detect_workflow(text: str) -> Optional[str]:
    scores = {
        name: _keyword_score(text, keywords)
        for name, keywords in WORKFLOW_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _detect_repository(text: str) -> Optional[str]:
    scores = {
        repository: _keyword_score(text, keywords)
        for repository, keywords in REPOSITORY_KEYWORDS.items()
    }

    best_score = max(scores.values())
    if best_score == 0:
        return None

    tied = [r for r, score in scores.items() if score == best_score]
    if len(tied) == 1:
        return tied[0]

    # Resolve common ambiguous cases conservatively. Academic vocabulary such
    # as "transcript" must take precedence over generic registrar vocabulary
    # such as "registered" when both appear in the same request.
    academic_priority_terms = {
        "transcript", "gpa", "cgpa", "grade", "grades", "course", "courses",
        "semester", "semesters", "academic", "credits", "performance",
    }
    if any(_contains_keyword(text, term) for term in academic_priority_terms):
        return "academic"

    if "exam" in text or "examination" in text:
        return "examination"
    if (
        "registration" in text
        or "registered" in text
        or "enrollment" in text
        or "enrolment" in text
    ):
        return "registrar"

    return None


def _detect_repositories(text: str) -> list[str]:
    """Return all repositories with meaningful keyword matches."""
    scores = {
        repository: _keyword_score(text, keywords)
        for repository, keywords in REPOSITORY_KEYWORDS.items()
    }
    ranked = sorted(
        ((repository, score) for repository, score in scores.items() if score > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    return [repository for repository, _ in ranked]


def _extract_document_id(question: str) -> Optional[str]:
    """Extract an explicit university document identifier from a question."""
    match = re.search(r"\b(?:TX|REG|EX)-[A-Z0-9-]+\b", question or "", re.IGNORECASE)
    return match.group(0).upper() if match else None


def _is_document_retrieval_query(question: str) -> bool:
    """Return True when the user explicitly requests document content/retrieval."""
    lowered = (question or "").lower()
    retrieval_terms = {
        "retrieve document",
        "get document",
        "open document",
        "fetch document",
        "download document",
        "access document",
        "show document",
        "view document",
        "retrieve the document",
        "get the document",
        "open the document",
        "fetch the document",
        "show me the document",
        "show me the contents",
        "document content",
        "document contents",
    }
    if any(_contains_keyword(lowered, term) for term in retrieval_terms):
        return True

    # An explicit document ID combined with a retrieval verb is an
    # unambiguous confidential-document request.
    document_id = _extract_document_id(question)
    retrieval_verbs = {"retrieve", "fetch", "get", "open", "view", "download", "access", "read", "show"}
    if document_id and any(_contains_keyword(lowered, verb) for verb in retrieval_verbs):
        return True

    return False


def _is_metadata_only_query(question: str) -> bool:
    """Return True when the user asks for metadata/reference information only.

    Metadata-only requests may require authentication because repository metadata
    can itself be confidential, but they must not trigger IPFS retrieval or
    document decryption.

    In addition to explicit "metadata" wording, repository discovery requests
    such as "find registration and examination records" are treated as
    metadata-only. The presence of a discovery verb alone is not sufficient:
    requests such as "find the grades" or "show the marks" still require
    confidential document retrieval.
    """
    lowered = (question or "").lower()

    explicit_metadata_terms = {
        "metadata",
        "document metadata",
        "document details",
        "record details",
        "record metadata",
        "document reference",
        "document references",
        "document list",
        "list documents",
        "find documents",
        "which documents",
        "what documents",
        "document information",
        "record information",
    }

    if any(
        _contains_keyword(lowered, term)
        for term in explicit_metadata_terms
    ):
        return True

    discovery_verbs = {
        "find",
        "list",
        "identify",
        "search",
        "locate",
    }

    record_reference_terms = {
        "record",
        "records",
        "document",
        "documents",
        "reference",
        "references",
    }

    has_discovery_verb = any(
        _contains_keyword(lowered, verb)
        for verb in discovery_verbs
    )
    has_record_reference = any(
        _contains_keyword(lowered, term)
        for term in record_reference_terms
    )

    return has_discovery_verb and has_record_reference


def _requires_confidential_access(
    question: str,
    intent: str,
    student_id: Optional[str],
) -> bool:
    if intent in {"academic_document", "document_history", "workflow", "document_retrieval"}:
        return True

    if _keyword_score(question, CONFIDENTIAL_KEYWORDS) > 0:
        return True

    if student_id and _contains_keyword(question, "document"):
        return True

    return False


def _initial_trace(question: str) -> Dict[str, Any]:
    return {
        "phase": 3,
        "graph": "university_document_agent",
        "nodes": [],
        "question": question,
    }


def _add_trace_node(
    trace: Dict[str, Any],
    node: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    updated = dict(trace)
    nodes = list(updated.get("nodes", []))
    entry: Dict[str, Any] = {"node": node}
    if details:
        entry["details"] = details
    nodes.append(entry)
    updated["nodes"] = nodes
    return updated


def _metadata_document_id(results: list[Dict[str, Any]]) -> Optional[str]:
    """Extract a document ID from the best metadata-RAG result."""
    if not results:
        return None

    for result in results:
        if not isinstance(result, dict):
            continue
        for key in ("document_id", "documentId", "id"):
            value = result.get(key)
            if value:
                return str(value).strip() or None
    return None


def _document_type_from_result(results: list[Dict[str, Any]]) -> Optional[str]:
    """Extract a document type from metadata without treating it as authoritative."""
    if not results:
        return None

    for result in results:
        if not isinstance(result, dict):
            continue
        for key in ("document_type", "documentType", "type"):
            value = result.get(key)
            if value:
                return str(value).strip() or None
    return None


def _safe_trace_document(document: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return trace-safe document information without exposing document contents."""
    if not document:
        return {"document_present": False}

    return {
        "document_present": True,
        "document_keys": sorted(str(key) for key in document.keys()),
        "content_exposed_to_trace": False,
    }


def analyze_query(state: AgentState) -> Dict[str, Any]:
    """Node 1: classify the request and extract routing entities."""
    question = _normalize_question(state.get("question", ""))

    if not question:
        trace = _add_trace_node(
            _initial_trace(question),
            "analyze_query",
            {"status": "error", "reason": "empty_question"},
        )
        return {
            "question": question,
            "intent": "invalid",
            "authorization": {},
            "trace": trace,
            "error": "Please enter a question.",
        }

    lowered = question.lower()
    student_id = _extract_student_id(question)
    course_code = _extract_course_code(question, student_id)
    document_id = _extract_document_id(question)
    workflow = _detect_workflow(lowered)
    metadata_only = _is_metadata_only_query(lowered)
    document_retrieval = _is_document_retrieval_query(lowered)

    if workflow:
        intent = "workflow"
        repository = None
        repositories = []
    elif document_retrieval:
        intent = "document_retrieval"
        # An explicit document request may still benefit from repository
        # metadata discovery when the question identifies a repository/domain
        # (for example, "academic transcript").  The metadata remains
        # non-authoritative; blockchain resolution is still mandatory before
        # confidential retrieval.
        repositories = _detect_repositories(lowered)
        repository = _detect_repository(lowered)
    else:
        # Explicit blockchain wording takes precedence over repository
        # keywords. This prevents generic words such as "registered" or
        # "registration" from incorrectly routing document/Registrar queries
        # to blockchain. For example:
        #
        #   "Is student S19357 registered on the blockchain?"
        #       -> onchain_metadata
        #
        # while:
        #
        #   "What transcript metadata is registered for student S19357?"
        #       -> academic_document
        explicit_onchain_query = (
            _contains_keyword(lowered, "blockchain")
            and (
                _contains_keyword(lowered, "registered")
                or _contains_keyword(lowered, "registration")
                or _contains_keyword(lowered, "exists")
                or _contains_keyword(lowered, "status")
                or _contains_keyword(lowered, "student id")
                or _contains_keyword(lowered, "student identifier")
            )
        )

        repositories = _detect_repositories(lowered)
        repository = _detect_repository(lowered)

        if explicit_onchain_query:
            intent = "onchain_metadata"
            repository = None
            repositories = []
        elif repository:
            intent = (
                "academic_document"
                if repository == "academic"
                else "repository_document"
            )
        elif _keyword_score(lowered, PUBLIC_ONCHAIN_KEYWORDS) > 0:
            intent = "onchain_metadata"
            repository = None
            repositories = []
        else:
            intent = "general"
            repository = None
            repositories = []

    if (
        not document_retrieval
        and (_contains_keyword(lowered, "history") or _contains_keyword(lowered, "version"))
    ):
        intent = "document_history"

    requires_confidential = _requires_confidential_access(
        lowered, intent, student_id
    )

    trace = _add_trace_node(
        _initial_trace(question),
        "analyze_query",
        {
            "intent": intent,
            "student_id_detected": bool(student_id),
            "course_code_detected": bool(course_code),
            "document_id_detected": bool(document_id),
            "document_retrieval": document_retrieval,
            "repository": repository,
            "repositories": repositories,
            "requires_confidential_access": requires_confidential,
            "metadata_only": metadata_only,
            "classification_rule": (
                "explicit_blockchain_wording_required_for_onchain_routing"
                if intent == "onchain_metadata"
                else (
                    "metadata_discovery_request_detected"
                    if metadata_only
                    else "repository_keywords_precede_generic_public_keywords"
                )
            ),
        },
    )

    logger.info(
        "LangGraph analysis: intent=%s student_id=%s repository=%s protected=%s",
        intent, student_id, repository, requires_confidential,
    )

    return {
        "question": question,
        "intent": intent,
        "student_id": student_id,
        "course_code": course_code,
        "document_id": document_id,
        "repository": repository,
        "repositories": repositories,
        "requires_confidential_access": requires_confidential,
        "metadata_only": metadata_only,
        "metadata_results": [],
        "metadata_search": {},
        "authorization": {},
        "trace": trace,
        "error": None,
    }


def authorize_query(state: AgentState) -> Dict[str, Any]:
    """
    Node 2: authorization gate.

    No blockchain/IPFS confidential-document retrieval is performed before
    this node succeeds.
    """
    intent = state.get("intent", "general")
    student_id = state.get("student_id")
    user = state.get("user")
    required = bool(state.get("requires_confidential_access", False))

    authenticated = bool(user)
    allowed = True
    reason = "public_or_non_confidential_request"

    if required:
        if not authenticated:
            allowed = False
            reason = "authentication_required"
        elif not student_id:
            allowed = False
            reason = "student_id_required_for_protected_request"
        else:
            try:
                allowed = bool(rbac.can_view_offchain(user, student_id))
                reason = "rbac_allowed" if allowed else "rbac_denied"
            except Exception:
                logger.exception("RBAC check failed")
                allowed = False
                reason = "rbac_check_error"

    authorization = {
        "authenticated": authenticated,
        "allowed": allowed,
        "required": required,
        "reason": reason,
        "student_id": student_id,
        "intent": intent,
    }

    trace = _add_trace_node(
        state.get("trace", {}),
        "authorize_query",
        {
            "authenticated": authenticated,
            "required": required,
            "allowed": allowed,
            "reason": reason,
        },
    )

    return {"authorization": authorization, "trace": trace}


def route_repository(state: AgentState) -> Dict[str, Any]:
    """Node 3: Repository Orchestrator. Phase 2 selects one or more logical repositories."""
    repository = state.get("repository")
    repositories = list(state.get("repositories") or [])
    intent = state.get("intent", "general")

    if repository and repository not in repositories:
        repositories.insert(0, repository)

    if not repositories and intent == "onchain_metadata":
        route = "blockchain"
    elif not repositories and intent == "workflow":
        route = "workflow"
    elif not repositories:
        route = "general"
    else:
        route = repositories[0]

    trace = _add_trace_node(
        state.get("trace", {}),
        "route_repository",
        {
            "route": route,
            "repositories": repositories,
            "display_name": REPOSITORY_DISPLAY_NAMES.get(route, route.title()),
            "retrieval_started": False,
        },
    )

    return {"repository": repository, "repositories": repositories, "trace": trace}


def retrieve_metadata(state: AgentState) -> Dict[str, Any]:
    """Node 4: metadata-only repository RAG; no blockchain/IPFS content retrieval."""
    intent = state.get("intent", "general")
    repositories = list(state.get("repositories") or [])
    question = state.get("question", "")
    student_id = state.get("student_id")
    course_code = state.get("course_code")

    if intent not in {"academic_document", "repository_document", "document_history", "document_retrieval"} or not repositories:
        trace = _add_trace_node(state.get("trace", {}), "retrieve_metadata", {
            "search_started": False, "reason": "metadata_rag_not_required",
            "content_retrieved": False,
        })
        return {"metadata_results": [], "metadata_search": {"searched": False, "result_count": 0}, "trace": trace}

    try:
        results = metadata_rag.search(
            question, repositories, student_id=student_id, course_code=course_code,
            top_k_per_repository=3,
        )
        search_info = {
            "searched": True, "repositories": repositories,
            "result_count": len(results), "content_retrieved": False,
            "retriever": "repository_metadata_lexical_rag",
        }
        trace = _add_trace_node(state.get("trace", {}), "retrieve_metadata", search_info)
        return {"metadata_results": results, "metadata_search": search_info, "trace": trace}
    except Exception as exc:
        logger.exception("Repository metadata RAG failed")
        trace = _add_trace_node(state.get("trace", {}), "retrieve_metadata", {
            "search_started": True, "repositories": repositories,
            "result_count": 0, "content_retrieved": False,
            "error": type(exc).__name__,
        })
        return {"metadata_results": [], "metadata_search": {"searched": True, "error": str(exc)}, "trace": trace, "error": "metadata_rag_error"}


def phase2_metadata_response(state: AgentState) -> Dict[str, Any]:
    """Terminal Phase 2 response containing metadata references only."""
    intent = state.get("intent", "general")
    repositories = list(state.get("repositories") or [])
    results = list(state.get("metadata_results") or [])

    if intent in {"academic_document", "repository_document", "document_history"}:
        if results:
            names = [REPOSITORY_DISPLAY_NAMES.get(r, r.title()) for r in repositories]
            answer = (
                f"Phase 2 metadata retrieval complete. Found {len(results)} document reference(s) "
                f"across {', '.join(names)}. Only document metadata was retrieved; "
                "confidential document content was not accessed."
            )
        else:
            answer = (
                "Phase 2 metadata retrieval completed, but no matching document references "
                "were found. No confidential document content was accessed."
            )
    elif intent == "onchain_metadata":
        answer = "Phase 2 routing complete. This is an on-chain metadata query. Blockchain retrieval will be integrated in the next phase."
    elif intent == "workflow":
        answer = "Phase 2 routing complete. This is a workflow query. Workflow retrieval remains a separate integration."
    else:
        answer = "Phase 2 routing complete. The request did not require repository metadata retrieval."

    trace = _add_trace_node(state.get("trace", {}), "phase2_metadata_response", {
        "intent": intent, "repositories": repositories,
        "metadata_result_count": len(results),
        "confidential_content_retrieved": False,
        "blockchain_retrieval_started": False, "ipfs_retrieval_started": False,
    })
    return {"answer": answer, "error": None, "trace": trace}


def retrieve_onchain_metadata(state: AgentState) -> Dict[str, Any]:
    """Retrieve public student metadata directly from the blockchain."""
    student_id = state.get("student_id")

    if not student_id:
        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_onchain_metadata",
            {"retrieval_started": False, "reason": "student_id_required"},
        )
        return {
            "blockchain_metadata": {},
            "trace": trace,
            "error": "student_id_required",
        }

    try:
        metadata = tools.get_onchain_student(student_id)
        if not metadata:
            trace = _add_trace_node(
                state.get("trace", {}),
                "retrieve_onchain_metadata",
                {
                    "retrieval_started": True,
                    "found": False,
                    "student_id": student_id,
                    "source": "blockchain",
                },
            )
            return {
                "blockchain_metadata": {},
                "trace": trace,
                "error": None,
            }

        blockchain_metadata = dict(metadata)
        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_onchain_metadata",
            {
                "retrieval_started": True,
                "found": True,
                "student_id": student_id,
                "source": "blockchain",
                "content_retrieved": False,
            },
        )
        return {
            "blockchain_metadata": blockchain_metadata,
            "trace": trace,
            "error": None,
        }
    except Exception as exc:
        logger.exception("Blockchain metadata retrieval failed")
        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_onchain_metadata",
            {
                "retrieval_started": True,
                "found": False,
                "source": "blockchain",
                "error_type": type(exc).__name__,
            },
        )
        return {
            "blockchain_metadata": {},
            "trace": trace,
            "error": "blockchain_retrieval_error",
        }


def retrieve_authorized_document(state: AgentState) -> Dict[str, Any]:
    """
    Resolve and retrieve the authoritative confidential document.

    Ordering is deliberate:
        1. Phase 2 metadata RAG identifies a document reference.
        2. RBAC has already succeeded in authorize_query.
        3. tools.get_authorized_document performs the authoritative
           blockchain resolution and authorized IPFS/decryption path.

    The LangGraph node itself never reads IPFS before authorization.
    """
    student_id = state.get("student_id")
    user = state.get("user")
    metadata_results = list(state.get("metadata_results") or [])

    if not student_id:
        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_authorized_document",
            {
                "retrieval_started": False,
                "reason": "student_id_required",
                "ipfs_retrieval_started": False,
            },
        )
        return {
            "ipfs_retrieval": {"started": False, "reason": "student_id_required"},
            "decryption": {"started": False},
            "trace": trace,
            "error": "student_id_required",
        }

    document_id = state.get("document_id") or _metadata_document_id(metadata_results)
    document_type = _document_type_from_result(metadata_results)

    if not document_id:
        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_authorized_document",
            {
                "retrieval_started": False,
                "reason": "document_reference_not_found",
                "metadata_result_count": len(metadata_results),
                "ipfs_retrieval_started": False,
            },
        )
        return {
            "ipfs_retrieval": {
                "started": False,
                "reason": "document_reference_not_found",
            },
            "decryption": {"started": False},
            "trace": trace,
            "error": None,
        }

    try:
        # ------------------------------------------------------------------
        # RAG ↔ blockchain identity consistency check
        # ------------------------------------------------------------------
        # Repository RAG is intentionally metadata-only and non-authoritative.
        # When it has identified a concrete reference for this retrieval,
        # resolve the same document from blockchain BEFORE any IPFS access and
        # compare the identity fields. A mismatch is treated as a security
        # failure and the request fails closed.
        matching_metadata = []
        normalized_target_id = str(document_id).strip().upper()
        for metadata_item in metadata_results:
            if not isinstance(metadata_item, dict):
                continue
            metadata_id = None
            for key in ("document_id", "documentId", "id"):
                value = metadata_item.get(key)
                if value:
                    metadata_id = str(value).strip().upper()
                    break
            if metadata_id == normalized_target_id:
                matching_metadata.append(metadata_item)

        if matching_metadata:
            blockchain_reference = tools.resolve_document_from_blockchain(
                document_id=document_id,
                approved_only=True,
            )
            authoritative = dict(
                blockchain_reference.get("blockchain_metadata") or {}
            )

            if not authoritative or not blockchain_reference.get("ok", False):
                trace = _add_trace_node(
                    state.get("trace", {}),
                    "retrieve_authorized_document",
                    {
                        "retrieval_started": False,
                        "found": False,
                        "document_id": document_id,
                        "reason": "blockchain_authoritative_resolution_failed",
                        "ipfs_retrieval_started": False,
                        "decryption_started": False,
                        "content_exposed_to_trace": False,
                    },
                )
                return {
                    "document_id": document_id,
                    "ipfs_retrieval": {
                        "started": False,
                        "reason": "blockchain_authoritative_resolution_failed",
                    },
                    "decryption": {"started": False},
                    "trace": trace,
                    "error": "blockchain_error",
                }

            blockchain_student_id = str(
                authoritative.get("student_id") or ""
            ).strip().upper()
            blockchain_document_id = str(
                authoritative.get("document_id") or document_id
            ).strip().upper()
            blockchain_document_type = authoritative.get("document_type")
            if blockchain_document_type is not None:
                blockchain_document_type = str(blockchain_document_type).strip().lower()

            for metadata_item in matching_metadata:
                metadata_student_id = metadata_item.get("student_id")
                if metadata_student_id is not None:
                    metadata_student_id = str(metadata_student_id).strip().upper()

                metadata_document_id = metadata_item.get("document_id")
                if metadata_document_id is None:
                    metadata_document_id = metadata_item.get("documentId")
                if metadata_document_id is None:
                    metadata_document_id = metadata_item.get("id")
                if metadata_document_id is not None:
                    metadata_document_id = str(metadata_document_id).strip().upper()

                metadata_document_type = metadata_item.get("document_type")
                if metadata_document_type is None:
                    metadata_document_type = metadata_item.get("documentType")
                if metadata_document_type is None:
                    metadata_document_type = metadata_item.get("type")
                if metadata_document_type is not None:
                    metadata_document_type = str(metadata_document_type).strip().lower()

                mismatch_fields = []
                if metadata_document_id and metadata_document_id != blockchain_document_id:
                    mismatch_fields.append("document_id")
                if metadata_student_id and metadata_student_id != blockchain_student_id:
                    mismatch_fields.append("student_id")
                if metadata_document_type and blockchain_document_type and metadata_document_type != blockchain_document_type:
                    mismatch_fields.append("document_type")

                if mismatch_fields:
                    logger.warning(
                        "RAG/blockchain identity mismatch for %s: %s",
                        document_id,
                        ", ".join(mismatch_fields),
                    )
                    trace = _add_trace_node(
                        state.get("trace", {}),
                        "retrieve_authorized_document",
                        {
                            "retrieval_started": False,
                            "found": False,
                            "document_id": document_id,
                            "reason": "rag_blockchain_identity_mismatch",
                            "mismatch_fields": mismatch_fields,
                            "rag_student_id_present": bool(metadata_student_id),
                            "blockchain_student_id_present": bool(blockchain_student_id),
                            "rag_document_type_present": bool(metadata_document_type),
                            "blockchain_document_type_present": bool(blockchain_document_type),
                            "ipfs_retrieval_started": False,
                            "decryption_started": False,
                            "content_exposed_to_trace": False,
                        },
                    )
                    return {
                        "document_id": document_id,
                        "student_id": student_id,
                        "document_type": document_type,
                        # Authorization already succeeded before this node.
                        # The failure is a retrieval-integrity consistency failure,
                        # not an RBAC denial. Preserve that distinction in state.
                        "authorized": True,
                        "version": authoritative.get("resolved_version") or authoritative.get("version"),
                        "document_version": authoritative.get("resolved_version") or authoritative.get("version"),
                        "blockchain_metadata": authoritative,
                        "ipfs_retrieval": {
                            "started": False,
                            "reason": "rag_blockchain_identity_mismatch",
                        },
                        "decryption": {"started": False},
                        "integrity_verified": False,
                        "trace": trace,
                        "error": "rag_blockchain_identity_mismatch",
                    }

        resolved = tools.get_authorized_document(
            document_id=document_id,
            user=user,
            expected_student_id=student_id,
            expected_document_type=document_type,
            approved_only=True,
            verify_integrity=True,
        )

        if not resolved:
            trace = _add_trace_node(
                state.get("trace", {}),
                "retrieve_authorized_document",
                {
                    "retrieval_started": True,
                    "found": False,
                    "document_id": document_id,
                    "ipfs_retrieval_started": False,
                    "reason": "document_not_found_or_not_authorized",
                },
            )
            return {
                "document_id": document_id,
                "ipfs_retrieval": {
                    "started": False,
                    "found": False,
                    "reason": "document_not_found_or_not_authorized",
                },
                "decryption": {"started": False},
                "trace": trace,
                "error": None,
            }

        resolved = dict(resolved)
        document = resolved.get("document")

        # Normalize the authoritative blockchain resolution into a stable
        # metadata shape. The tools layer may expose the resolved version as
        # ``version`` while the LangGraph state/test contract uses
        # ``resolved_version``. Keep both representations consistent without
        # changing the underlying blockchain data.
        resolved_version = resolved.get("resolved_version")
        if resolved_version is None:
            resolved_version = resolved.get("version")

        blockchain_metadata = {
            key: resolved[key]
            for key in (
                "document_id",
                "student_id",
                "document_type",
                "latest_version",
                "previous_version",
                "ipfs_cid",
                "content_hash",
                "approval_status",
                "approved_by",
                "timestamp",
            )
            if key in resolved
        }

        # ``resolved_version`` is authoritative for this retrieval. Add it
        # explicitly so downstream state, responses, traces, and tests do not
        # depend on whether tools.py called the field ``version`` or
        # ``resolved_version``.
        if resolved_version is not None:
            blockchain_metadata["resolved_version"] = resolved_version

        ipfs_info = dict(resolved.get("ipfs_retrieval") or {})
        ipfs_info.setdefault("started", True)
        ipfs_info.setdefault("cid", resolved.get("ipfs_cid"))
        ipfs_info.setdefault("content_retrieved", document is not None)

        decryption_info = dict(resolved.get("decryption") or {})
        decryption_info.setdefault("started", document is not None)

        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_authorized_document",
            {
                "retrieval_started": True,
                "found": document is not None,
                "document_id": document_id,
                "resolved_version": resolved.get("resolved_version"),
                "previous_version": resolved.get("previous_version"),
                "cid_source": "blockchain",
                "ipfs_retrieval_started": bool(
                    ipfs_info.get("started") or ipfs_info.get("content_retrieved")
                ),
                "decryption_started": bool(decryption_info.get("started")),
                "integrity_verified": resolved.get("integrity_verified"),
                "content_exposed_to_trace": False,
            },
        )

        # Preserve a successful integrity result even when the tool returns it
        # inside the decrypted document envelope rather than at the top level.
        integrity_verified = bool(
            resolved.get("integrity_verified")
            or (isinstance(document, dict) and document.get("integrity_verified") is True)
        )

        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_authorized_document",
            {
                "retrieval_started": True,
                "found": document is not None,
                "document_id": document_id,
                "resolved_version": resolved_version,
                "previous_version": resolved.get("previous_version"),
                "cid_source": "blockchain",
                "ipfs_retrieval_started": bool(
                    ipfs_info.get("started") or ipfs_info.get("content_retrieved")
                ),
                "decryption_started": bool(decryption_info.get("started")),
                "integrity_verified": integrity_verified,
                "content_exposed_to_trace": False,
            },
        )

        return {
            "ok": True,
            "authorized": bool(resolved.get("authorized", True)),
            "document": document,
            "document_id": resolved.get("document_id", document_id),
            "student_id": resolved.get("student_id", student_id),
            "document_type": resolved.get("document_type") or document_type,
            "document_version": resolved_version,
            "version": resolved_version,
            "previous_version": resolved.get("previous_version"),
            "resolved_cid": resolved.get("ipfs_cid"),
            "ipfs_cid": resolved.get("ipfs_cid"),
            "resolved_content_hash": resolved.get("content_hash"),
            "content_hash": resolved.get("content_hash"),
            "approval_status": resolved.get("approval_status"),
            "approved_by": resolved.get("approved_by"),
            "blockchain_timestamp": resolved.get("timestamp"),
            "blockchain_metadata": blockchain_metadata,
            "ipfs_retrieval": ipfs_info,
            "decryption": decryption_info,
            "integrity_verified": integrity_verified,
            "trace": trace,
            "error": None,
        }

    except PermissionError as exc:
        logger.warning("Authorized document retrieval denied: %s", exc)
        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_authorized_document",
            {
                "retrieval_started": False,
                "document_id": document_id,
                "reason": "permission_denied",
                "ipfs_retrieval_started": False,
            },
        )
        return {
            "document_id": document_id,
            "ipfs_retrieval": {"started": False, "reason": "permission_denied"},
            "decryption": {"started": False},
            "trace": trace,
            "error": "forbidden",
        }
    except Exception as exc:
        logger.exception("Authorized document retrieval failed")
        trace = _add_trace_node(
            state.get("trace", {}),
            "retrieve_authorized_document",
            {
                "retrieval_started": True,
                "document_id": document_id,
                "error_type": type(exc).__name__,
                "ipfs_retrieval_started": False,
            },
        )
        return {
            "document_id": document_id,
            "ipfs_retrieval": {"started": False, "error": type(exc).__name__},
            "decryption": {"started": False},
            "trace": trace,
            "error": "document_retrieval_error",
        }


def phase3_response(state: AgentState) -> Dict[str, Any]:
    """Create a safe Phase 3 response without exposing document content in the trace."""
    intent = state.get("intent", "general")
    blockchain_metadata = dict(state.get("blockchain_metadata") or {})
    document = state.get("document")
    document_id = state.get("document_id")
    version = state.get("document_version")
    cid = state.get("resolved_cid")

    if intent == "onchain_metadata":
        if not blockchain_metadata:
            answer = "No matching student record was found on the blockchain."
        else:
            student_id = blockchain_metadata.get("student_id", state.get("student_id"))
            program = blockchain_metadata.get("program")
            year = blockchain_metadata.get("year")
            status = blockchain_metadata.get("is_active")

            parts = [f"Blockchain record found for {student_id}."]
            if program is not None:
                parts.append(f"Program: {program}.")
            if year is not None:
                parts.append(f"Year: {year}.")
            if status is not None:
                parts.append(f"Active: {status}.")
            answer = " ".join(parts)

    elif intent in {"academic_document", "repository_document", "document_history", "document_retrieval"}:
        if not document_id:
            answer = (
                "The metadata repository did not identify a matching document. "
                "No confidential document content was retrieved."
            )
        elif document is None:
            error = state.get("error")
            reason = {
                "forbidden": "access was denied",
                "acl_denied": "document-level ACL authorization failed",
                "integrity_verification_failed": "integrity verification failed",
                "document_not_found": "the document was not found on the blockchain",
                "blockchain_error": "blockchain resolution failed",
                "ipfs_error": "IPFS retrieval failed",
                "rag_blockchain_identity_mismatch": (
                    "the repository metadata did not match the authoritative "
                    "blockchain document identity"
                ),
            }.get(error, "retrieval was unsuccessful")
            answer = (
                f"The document reference {document_id} was identified, but the "
                f"authoritative confidential document could not be retrieved because "
                f"{reason}."
            )
        else:
            integrity_verified = bool(state.get("integrity_verified", False))
            integrity_text = (
                "Document integrity was verified against the blockchain content hash."
                if integrity_verified
                else "Document integrity could not be confirmed."
            )
            answer = (
                f"Authorized document {document_id} was retrieved successfully. "
                f"The authoritative blockchain version is {version}, resolved to "
                f"the approved IPFS object {cid}. {integrity_text}"
            )
    else:
        answer = (
            "Phase 3 completed. No confidential document content was required "
            "for this request."
        )

    trace = _add_trace_node(
        state.get("trace", {}),
        "phase3_response",
        {
            "intent": intent,
            "document_id": document_id,
            "resolved_version": version,
            "blockchain_resolution_used": bool(blockchain_metadata),
            "document_retrieved": document is not None,
            "content_exposed_to_trace": False,
        },
    )

    return {"answer": answer, "trace": trace}


def denied_response(state: AgentState) -> Dict[str, Any]:
    """Terminal node for authorization failures."""
    reason = state.get("authorization", {}).get("reason", "forbidden")

    messages = {
        "authentication_required": (
            "Authentication is required to access confidential university "
            "document information. Please log in."
        ),
        "student_id_required_for_protected_request": (
            "Please provide the student ID for this protected document request."
        ),
        "rbac_denied": (
            "Access denied. Your current role is not authorized to access "
            "the requested confidential document information."
        ),
        "rbac_check_error": (
            "The authorization service could not verify access. "
            "No confidential document data was retrieved."
        ),
    }

    answer = messages.get(
        reason,
        "Access denied. No confidential document data was retrieved.",
    )

    trace = _add_trace_node(
        state.get("trace", {}),
        "denied_response",
        {"reason": reason},
    )

    return {
        "answer": answer,
        "error": "forbidden",
        "trace": trace,
    }


def phase1_route_response(state: AgentState) -> Dict[str, Any]:
    """Terminal node showing the Phase 1 routing decision."""
    intent = state.get("intent", "general")
    repository = state.get("repository")
    student_id = state.get("student_id")
    course_code = state.get("course_code")

    if repository:
        destination = REPOSITORY_DISPLAY_NAMES.get(
            repository, repository.title()
        )
        answer = (
            f"Phase 1 routing complete. This request is classified as "
            f"'{intent}' and routed to the {destination}. "
            f"Repository retrieval is the next implementation phase."
        )
    elif intent == "onchain_metadata":
        answer = (
            "Phase 1 routing complete. This request is classified as an "
            "on-chain metadata query. Blockchain retrieval will be added "
            "in the next phase."
        )
    elif intent == "workflow":
        answer = (
            "Phase 1 routing complete. This request is classified as a "
            "workflow query. Workflow retrieval will be integrated later."
        )
    else:
        answer = (
            "Phase 1 routing complete. The request could not be mapped to "
            "a specific document repository yet."
        )

    trace = _add_trace_node(
        state.get("trace", {}),
        "phase1_route_response",
        {
            "intent": intent,
            "repository": repository,
            "student_id": student_id,
            "course_code": course_code,
            "confidential_content_retrieved": False,
        },
    )

    return {"answer": answer, "error": None, "trace": trace}


def _authorization_router(state: AgentState) -> str:
    return (
        "allowed"
        if state.get("authorization", {}).get("allowed", False)
        else "denied"
    )


def build_graph():
    """Build and compile the current Phase 3 LangGraph."""
    graph = StateGraph(AgentState)

    graph.add_node("analyze_query", analyze_query)
    graph.add_node("authorize_query", authorize_query)
    graph.add_node("route_repository", route_repository)
    graph.add_node("retrieve_metadata", retrieve_metadata)
    graph.add_node("retrieve_onchain_metadata", retrieve_onchain_metadata)
    graph.add_node("retrieve_authorized_document", retrieve_authorized_document)
    graph.add_node("denied_response", denied_response)
    graph.add_node("phase2_metadata_response", phase2_metadata_response)
    graph.add_node("phase3_response", phase3_response)

    graph.add_edge(START, "analyze_query")
    graph.add_edge("analyze_query", "authorize_query")

    graph.add_conditional_edges(
        "authorize_query",
        _authorization_router,
        {
            "allowed": "route_repository",
            "denied": "denied_response",
        },
    )

    def retrieval_router(state: AgentState) -> str:
        intent = state.get("intent", "general")
        repositories = state.get("repositories") or []

        if intent == "onchain_metadata":
            return "blockchain"

        if intent == "document_retrieval":
            # If repository/domain metadata can be discovered, run the
            # metadata-RAG stage first so its reference can be checked against
            # the authoritative blockchain identity.  Otherwise an explicit
            # document-ID request can proceed directly to blockchain
            # resolution.
            return "metadata" if repositories else "document_direct"

        if (
            intent in {"academic_document", "repository_document", "document_history"}
            and repositories
        ):
            return "metadata"

        return "response"

    graph.add_conditional_edges(
        "route_repository",
        retrieval_router,
        {
            "blockchain": "retrieve_onchain_metadata",
            "document_direct": "retrieve_authorized_document",
            "metadata": "retrieve_metadata",
            "response": "phase2_metadata_response",
        },
    )

    def document_router(state: AgentState) -> str:
        intent = state.get("intent", "general")

        # ``metadata_only`` was introduced in Phase 3, but older AgentState
        # definitions may not preserve newly-added TypedDict fields through
        # LangGraph state handling. Therefore the router derives the decision
        # from the original question as a safe fallback.
        metadata_only = bool(state.get("metadata_only", False))
        if not metadata_only:
            metadata_only = _is_metadata_only_query(
                state.get("question", "")
            )

        # Metadata-only questions stop after repository RAG. Even when the
        # query is protected and authorization has succeeded, the agent must
        # not retrieve or decrypt the underlying IPFS document unless the user
        # explicitly requests document content.
        if metadata_only:
            return "response"

        if intent in {
            "academic_document",
            "repository_document",
            "document_history",
            "document_retrieval",
        }:
            return "document"
        return "response"

    graph.add_conditional_edges(
        "retrieve_metadata",
        document_router,
        {
            "document": "retrieve_authorized_document",
            "response": "phase2_metadata_response",
        },
    )

    graph.add_edge("retrieve_authorized_document", "phase3_response")
    graph.add_edge("retrieve_onchain_metadata", "phase3_response")
    graph.add_edge("denied_response", END)
    graph.add_edge("phase2_metadata_response", END)
    graph.add_edge("phase3_response", END)

    return graph.compile()


compiled_graph = build_graph()


def answer_question(
    question: str,
    user_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Public entry point compatible with the existing application."""
    normalized = _normalize_question(question)

    if not normalized:
        return {
            "answer": "Please enter a question.",
            "error": "empty_question",
            "trace": {
                "phase": 3,
                "graph": "university_document_agent",
                "nodes": [],
            },
        }

    initial_state: AgentState = {
        "question": normalized,
        "user": user_context,
        "trace": _initial_trace(normalized),
    }

    try:
        result = compiled_graph.invoke(initial_state)

        if not isinstance(result, dict):
            return {"answer": str(result), "trace": {}}

        result.setdefault(
            "answer",
            "The AI agent completed the request without producing an answer.",
        )
        result.setdefault("trace", {})
        return result

    except Exception as exc:
        logger.exception("LangGraph execution failed")
        return {
            "answer": (
                "The AI agent encountered an internal error while processing "
                "the request. No confidential document content was retrieved."
            ),
            "error": str(exc),
            "trace": {
                "phase": 3,
                "graph": "university_document_agent",
                "failed": True,
                "error_type": type(exc).__name__,
            },
        }


def get_graph():
    return compiled_graph


def get_graph_mermaid() -> str:
    try:
        return compiled_graph.get_graph().draw_mermaid()
    except Exception as exc:
        return f"Unable to render Mermaid graph: {exc}"
