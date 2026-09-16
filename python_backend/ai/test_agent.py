"""Phase 3 + Phase 4 LangGraph test runner.

Run from project root:

    python python_backend/ai/test_agent.py

The tests validate:
    1. Authorization-before-retrieval security.
    2. Public blockchain metadata retrieval.
    3. Metadata-only repository RAG behaviour.
    4. Course-specific metadata discovery.
    5. Multi-repository metadata routing.
    6. General query routing.
    7. End-to-end Phase 3 confidential document retrieval.
    8. RAG/blockchain identity mismatch protection.
    9. Phase 3 + Phase 4 LangGraph topology.
    10. Authorized document Q&A.
    11. Course-specific grounded Q&A.
    12. Missing-information / hallucination protection.
    13. Unauthorized document never reaches the LLM.
    14. Integrity failure prevents LLM invocation.
    15. LLM context contains only the authorized plaintext document.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from python_backend.ai.agent import answer_question, get_graph_mermaid
from python_backend.ai import agent as agent_module


DOCUMENT_ID = "TX-98765"
STUDENT_ID = "STD001"
EXPECTED_VERSION = 4


def show(title: str, result: Dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(result, indent=2, default=str))


def nodes(result: Dict[str, Any]) -> List[str]:
    return [
        str(item.get("node"))
        for item in result.get("trace", {}).get("nodes", [])
        if isinstance(item, dict) and item.get("node")
    ]


def trace_node(
    result: Dict[str, Any],
    node_name: str,
) -> Dict[str, Any]:
    for item in result.get("trace", {}).get("nodes", []):
        if (
            isinstance(item, dict)
            and item.get("node") == node_name
        ):
            details = item.get("details", {})
            return details if isinstance(details, dict) else {}
    return {}


class FakeLLM:
    """Deterministic LLM double used so tests do not require a running Ollama server."""

    def __init__(self, answer: str = "MOCK ANSWER") -> None:
        self.answer = answer
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, question: str, document: Dict[str, Any]) -> str:
        self.calls.append({"question": question, "document": document})
        return self.answer


def patch_llm(fake: FakeLLM):
    original = agent_module.llm_service.answer_document_question

    def mocked_answer_document_question(question: str, document: Dict[str, Any]) -> str:
        return fake(question, document)

    agent_module.llm_service.answer_document_question = mocked_answer_document_question
    return original


def assert_phase3_success(result: Dict[str, Any]) -> None:
    """Validate the common successful Phase 3 confidential-document flow."""

    assert result.get("error") is None, json.dumps(
        result, indent=2, default=str
    )
    assert result.get("authorized") is True, json.dumps(
        result, indent=2, default=str
    )
    assert result.get("document") is not None, json.dumps(
        result, indent=2, default=str
    )
    assert result.get("document_id") == DOCUMENT_ID, result
    assert result.get("student_id") == STUDENT_ID, result
    assert result.get("version") == EXPECTED_VERSION, result
    assert result.get("document_version") == EXPECTED_VERSION, result
    assert result.get("ipfs_cid"), result
    assert result.get("content_hash"), result
    assert result.get("integrity_verified") is True, result

    # The LangGraph node is intentionally named retrieve_authorized_document.
    assert "retrieve_authorized_document" in nodes(result), result

    retrieval = trace_node(
        result,
        "retrieve_authorized_document",
    )
    assert retrieval.get("retrieval_started") is True, result
    assert retrieval.get("found") is True, result
    assert retrieval.get("resolved_version") == EXPECTED_VERSION, result
    assert retrieval.get("cid_source") == "blockchain", result
    assert retrieval.get("ipfs_retrieval_started") is True, result
    assert retrieval.get("decryption_started") is True, result
    assert retrieval.get("integrity_verified") is True, result

    phase3 = trace_node(result, "phase3_response")
    assert phase3.get("document_retrieved") is True, result
    assert phase3.get("blockchain_resolution_used") is True, result
    assert phase3.get("content_exposed_to_trace") is False, result


def run_tests() -> None:
    # ------------------------------------------------------------------
    # TEST 1
    # Protected query without authentication.
    #
    # Security requirement:
    # Authorization must fail before repository metadata retrieval and
    # before confidential blockchain/IPFS retrieval.
    # ------------------------------------------------------------------
    result = answer_question(
        "What is the grade for CO521 for student S19357?"
    )
    show(
        "TEST 1 - Protected query without authentication",
        result,
    )

    assert result.get("error") == "forbidden", result
    assert result.get("authorization", {}).get("allowed") is False, result
    assert "authorize_query" in nodes(result)
    assert nodes(result)[-1] == "denied_response"
    assert "retrieve_metadata" not in nodes(result)
    assert "retrieve_authorized_document" not in nodes(result)
    assert "retrieve_onchain_metadata" not in nodes(result)
    assert result.get("metadata_results", []) == []

    # ------------------------------------------------------------------
    # TEST 2
    # Public blockchain metadata query.
    # ------------------------------------------------------------------
    result = answer_question(
        "Is student S19357 registered on the blockchain?"
    )
    show(
        "TEST 2 - Public blockchain metadata query",
        result,
    )

    assert result.get("error") is None, result
    assert result.get("intent") == "onchain_metadata", result
    assert result.get("repository") is None, result
    assert result.get("repositories", []) == [], result

    assert "authorize_query" in nodes(result)
    assert "route_repository" in nodes(result)
    assert "retrieve_onchain_metadata" in nodes(result)
    assert "phase3_response" in nodes(result)

    assert "retrieve_metadata" not in nodes(result)
    assert "retrieve_authorized_document" not in nodes(result)

    assert result.get("metadata_results", []) == []

    # ------------------------------------------------------------------
    # TEST 3
    # Authenticated academic metadata discovery.
    #
    # This query intentionally asks for metadata, so confidential content
    # must not be retrieved or decrypted.
    # ------------------------------------------------------------------
    demo_user = {
        "username": "phase3_test_user",
        "role": "STUDENT",
        "student_id": "S19357",
    }

    result = answer_question(
        "Show the transcript metadata for student S19357.",
        user_context=demo_user,
    )
    show(
        "TEST 3 - Authenticated academic metadata query",
        result,
    )

    assert result.get("authorization", {}).get("allowed") is True, result
    assert "route_repository" in nodes(result)
    assert "retrieve_metadata" in nodes(result)
    assert "retrieve_authorized_document" not in nodes(result)
    assert "phase2_metadata_response" in nodes(result)

    assert result.get("metadata_results"), result

    forbidden = {
        "content",
        "plaintext",
        "document_content",
        "text",
        "raw_document",
        "encrypted_content",
    }

    for item in result["metadata_results"]:
        assert forbidden.isdisjoint(item.keys()), item
        assert item.get("content_retrieved") is False, item

    # ------------------------------------------------------------------
    # TEST 4
    # Course-specific academic metadata discovery.
    # ------------------------------------------------------------------
    result = answer_question(
        "Find the academic transcript metadata for CO521 of S19357.",
        user_context=demo_user,
    )
    show(
        "TEST 4 - Course-specific academic metadata query",
        result,
    )

    assert result.get("authorization", {}).get("allowed") is True, result
    assert result.get("metadata_results"), result

    assert any(
        "CO521" in item.get("course_codes", [])
        for item in result["metadata_results"]
    ), result

    assert "retrieve_authorized_document" not in nodes(result)

    for item in result["metadata_results"]:
        assert item.get("content_retrieved") is False, item

    # ------------------------------------------------------------------
    # TEST 5
    # Collaborative multi-repository metadata discovery.
    # ------------------------------------------------------------------
    result = answer_question(
        "Find registration and examination records for student S19357.",
        user_context=demo_user,
    )
    show(
        "TEST 5 - Collaborative multi-repository metadata query",
        result,
    )

    assert result.get("authorization", {}).get("allowed") is True, result

    route = trace_node(result, "route_repository")
    repos = route.get("repositories", [])

    assert "registrar" in repos, route
    assert "examination" in repos, route

    assert any(
        item.get("repository") == "registrar"
        for item in result.get("metadata_results", [])
    ), result

    assert any(
        item.get("repository") == "examination"
        for item in result.get("metadata_results", [])
    ), result

    assert "retrieve_authorized_document" not in nodes(result)

    for item in result.get("metadata_results", []):
        assert item.get("content_retrieved") is False, item

    # ------------------------------------------------------------------
    # TEST 6
    # General query.
    # ------------------------------------------------------------------
    result = answer_question(
        "What can this AI system do?"
    )
    show(
        "TEST 6 - General query",
        result,
    )

    assert result.get("error") is None, result
    assert "retrieve_metadata" not in nodes(result)
    assert "retrieve_authorized_document" not in nodes(result)
    assert "retrieve_onchain_metadata" not in nodes(result)

    # ------------------------------------------------------------------
    # TEST 7
    # End-to-end Phase 3 authoritative document retrieval.
    #
    # Current seeded test document:
    #     Document : TX-98765
    #     Student  : STD001
    #     Approved : V4
    #
    # The expected flow is:
    #
    #     Metadata RAG
    #         -> Blockchain authoritative resolution
    #         -> V4 CID
    #         -> encrypted IPFS payload
    #         -> ACL authorization/decryption
    #         -> plaintext hash verification
    #         -> Phase 3 response
    # ------------------------------------------------------------------
    phase3_user = {
        "username": "phase3_test_user",
        "role": "ADMIN",
        "student_id": STUDENT_ID,
    }

    phase3_fake_llm = FakeLLM("The document was retrieved successfully.")
    original_llm = patch_llm(phase3_fake_llm)
    try:
        result = answer_question(
            f"Retrieve document {DOCUMENT_ID} for student {STUDENT_ID}.",
            user_context=phase3_user,
        )
    finally:
        agent_module.llm_service.answer_document_question = original_llm

    show(
        "TEST 7 - Phase 3 authoritative document retrieval + Phase 4 boundary",
        result,
    )

    assert_phase3_success(result)
    assert result.get("llm_invoked") is True, result
    assert phase3_fake_llm.calls, result

    # The blockchain must be the authoritative source.
    blockchain_metadata = result.get("blockchain_metadata", {})
    assert blockchain_metadata, result
    assert blockchain_metadata.get("document_id") == DOCUMENT_ID, result
    assert blockchain_metadata.get("student_id") == STUDENT_ID, result
    assert blockchain_metadata.get("resolved_version") == EXPECTED_VERSION, result
    assert blockchain_metadata.get("approval_status") == "APPROVED", result
    assert blockchain_metadata.get("ipfs_cid") == result.get("ipfs_cid"), result
    assert blockchain_metadata.get("content_hash") == result.get("content_hash"), result

    # Verify that the retrieved plaintext belongs to the expected document.
    document = result.get("document")
    assert isinstance(document, dict), result
    assert document.get("document_id") == DOCUMENT_ID, document
    assert document.get("student_id") == STUDENT_ID, document
    assert document.get("document_type") == "Transcript", document

    # The test may inspect the returned document, but the graph trace must
    # never contain its confidential content.
    retrieval_trace = result.get("trace", {})
    assert retrieval_trace.get("nodes"), result

    for entry in retrieval_trace["nodes"]:
        if not isinstance(entry, dict):
            continue
        details = entry.get("details", {})
        if isinstance(details, dict):
            assert details.get("content_exposed_to_trace") is not True, entry

    # ------------------------------------------------------------------
    # TEST 8
    # RAG/blockchain identity mismatch protection.
    # ------------------------------------------------------------------
    original_search = agent_module.metadata_rag.search

    def mismatched_metadata_search(*args, **kwargs):
        return [
            {
                "repository": "academic",
                "document_id": DOCUMENT_ID,
                "student_id": "STD999",
                "document_type": "Transcript",
                "content_retrieved": False,
            }
        ]

    try:
        agent_module.metadata_rag.search = mismatched_metadata_search

        result = answer_question(
            f"Retrieve academic transcript document {DOCUMENT_ID} for student {STUDENT_ID}.",
            user_context={
                "username": "phase3_mismatch_test_user",
                "role": "ADMIN",
                "student_id": STUDENT_ID,
            },
        )
        show("TEST 8 - RAG/blockchain identity mismatch protection", result)

        assert result.get("authorized") is True, result
        assert result.get("document") is None, result
        assert result.get("error") == "rag_blockchain_identity_mismatch", result

        retrieval = trace_node(result, "retrieve_authorized_document")
        assert retrieval.get("retrieval_started") is False, result
        assert retrieval.get("reason") == "rag_blockchain_identity_mismatch", result
        assert retrieval.get("ipfs_retrieval_started") is False, result
        assert retrieval.get("decryption_started") is False, result
        assert retrieval.get("content_exposed_to_trace") is False, result
        assert "student_id" in retrieval.get("mismatch_fields", []), result

        blockchain_metadata = result.get("blockchain_metadata", {})
        assert blockchain_metadata.get("document_id") == DOCUMENT_ID, result
        assert blockchain_metadata.get("student_id") == STUDENT_ID, result
        assert result.get("llm_invoked") is not True, result
    finally:
        agent_module.metadata_rag.search = original_search

    # ------------------------------------------------------------------
    # TEST 9
    # Phase 3 + Phase 4 graph topology.
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("PHASE 3 + PHASE 4 GRAPH")
    print("=" * 80)

    mermaid = get_graph_mermaid()
    print(mermaid)

    required_nodes = {
        "analyze_query",
        "authorize_query",
        "route_repository",
        "retrieve_metadata",
        "retrieve_onchain_metadata",
        "retrieve_authorized_document",
        "phase3_response",
        "denied_response",
        "document_qa",
        "phase4_response",
    }

    for node_name in required_nodes:
        assert node_name in mermaid, f"Graph is missing node: {node_name}\n{mermaid}"

    # ------------------------------------------------------------------
    # TEST 10
    # Authorized document Q&A.
    # ------------------------------------------------------------------
    fake = FakeLLM("You received an A for CO521.")
    original_llm = patch_llm(fake)
    try:
        result = answer_question(
            f"What grade did I get for CO521 in document {DOCUMENT_ID} for student {STUDENT_ID}?",
            user_context={
                "username": "phase4_test_user",
                "role": "ADMIN",
                "student_id": STUDENT_ID,
            },
        )
    finally:
        agent_module.llm_service.answer_document_question = original_llm

    show("TEST 10 - Authorized document Q&A", result)

    assert result.get("error") is None, result
    assert result.get("document") is not None, result
    assert result.get("integrity_verified") is True, result
    assert result.get("llm_invoked") is True, result
    assert result.get("llm_context_authorized") is True, result
    assert result.get("llm_answer") == "You received an A for CO521.", result
    assert result.get("answer") == "You received an A for CO521.", result
    assert nodes(result)[-2:] == ["document_qa", "phase4_response"], result

    # The mock must receive exactly the authorized plaintext returned by Phase 3.
    assert len(fake.calls) == 1, fake.calls
    assert fake.calls[0]["document"] == result["document"], result
    assert fake.calls[0]["document"]["document_id"] == DOCUMENT_ID, result
    assert fake.calls[0]["document"]["student_id"] == STUDENT_ID, result

    # ------------------------------------------------------------------
    # TEST 11
    # Course-specific grounded Q&A.
    # ------------------------------------------------------------------
    fake = FakeLLM("CO521 has grade A.")
    original_llm = patch_llm(fake)
    try:
        result = answer_question(
            f"What grade did I get for CO521 in {DOCUMENT_ID}?",
            user_context={
                "username": "phase4_course_test_user",
                "role": "ADMIN",
                "student_id": STUDENT_ID,
            },
        )
    finally:
        agent_module.llm_service.answer_document_question = original_llm

    show("TEST 11 - Course-specific grounded Q&A", result)

    assert result.get("error") is None, result
    assert result.get("llm_invoked") is True, result
    assert result.get("answer") == "CO521 has grade A.", result
    assert fake.calls[0]["question"].find("CO521") >= 0, fake.calls

    # ------------------------------------------------------------------
    # TEST 12
    # Missing information / hallucination protection.
    # The model double returns the safe response expected from the grounded
    # prompt; the important graph-level property is that it receives only the
    # authorized document and no repository metadata content.
    # ------------------------------------------------------------------
    fake = FakeLLM("The requested CO999 information cannot be determined from the document.")
    original_llm = patch_llm(fake)
    try:
        result = answer_question(
            f"What grade did I get for CO999 in {DOCUMENT_ID}?",
            user_context={
                "username": "phase4_missing_test_user",
                "role": "ADMIN",
                "student_id": STUDENT_ID,
            },
        )
    finally:
        agent_module.llm_service.answer_document_question = original_llm

    show("TEST 12 - Missing information / hallucination protection", result)

    assert result.get("error") is None, result
    assert result.get("llm_invoked") is True, result
    assert "cannot be determined" in result.get("answer", "").lower(), result
    assert fake.calls[0]["document"] == result["document"], result

    # ------------------------------------------------------------------
    # TEST 13
    # Unauthorized document must never reach the LLM.
    # ------------------------------------------------------------------
    unauthorized_fake = FakeLLM("THIS MUST NEVER BE RETURNED")
    original_llm = patch_llm(unauthorized_fake)
    try:
        result = answer_question(
            f"Retrieve document {DOCUMENT_ID} for student {STUDENT_ID}.",
            user_context={
                "username": "phase4_unauthorized_user",
                "role": "STUDENT",
                "student_id": "STD999",
            },
        )
    finally:
        agent_module.llm_service.answer_document_question = original_llm

    show("TEST 13 - Unauthorized document never reaches LLM", result)

    assert result.get("authorized") is False, result
    assert result.get("document") is None, result
    assert result.get("llm_invoked") is not True, result
    assert unauthorized_fake.calls == [], unauthorized_fake.calls
    assert "document_qa" not in nodes(result)

    # ------------------------------------------------------------------
    # TEST 14
    # Integrity failure prevents LLM invocation.
    # We force the authorized-document node to report an integrity failure and
    # verify that the LLM mock is never called.
    # ------------------------------------------------------------------
    original_retrieve = agent_module.retrieve_authorized_document
    original_graph = agent_module.compiled_graph

    def forced_integrity_failure(state: Dict[str, Any]) -> Dict[str, Any]:
        trace = agent_module._add_trace_node(
            state.get("trace", {}),
            "retrieve_authorized_document",
            {
                "retrieval_started": True,
                "found": True,
                "resolved_version": EXPECTED_VERSION,
                "ipfs_retrieval_started": True,
                "decryption_started": True,
                "integrity_verified": False,
                "content_exposed_to_trace": False,
            },
        )
        return {
            "document_id": DOCUMENT_ID,
            "student_id": STUDENT_ID,
            "document": {
                "document_id": DOCUMENT_ID,
                "student_id": STUDENT_ID,
                "document_type": "Transcript",
                "courses": [{"code": "CO521", "grade": "A"}],
            },
            "authorized": True,
            "integrity_verified": False,
            "error": "integrity_verification_failed",
            "trace": trace,
        }

    integrity_fake = FakeLLM("THIS MUST NEVER BE CALLED")
    original_llm = patch_llm(integrity_fake)
    try:
        agent_module.retrieve_authorized_document = forced_integrity_failure
        # Recompile so the temporary node implementation is captured by the
        # graph used by answer_question().
        agent_module.compiled_graph = agent_module.build_graph()
        result = answer_question(
            f"Retrieve document {DOCUMENT_ID} for student {STUDENT_ID}.",
            user_context={
                "username": "phase4_integrity_test_user",
                "role": "ADMIN",
                "student_id": STUDENT_ID,
            },
        )
    finally:
        agent_module.retrieve_authorized_document = original_retrieve
        agent_module.compiled_graph = original_graph
        agent_module.llm_service.answer_document_question = original_llm

    show("TEST 14 - Integrity failure prevents LLM invocation", result)

    assert result.get("error") == "integrity_verification_failed", result
    assert result.get("llm_invoked") is not True, result
    assert integrity_fake.calls == [], integrity_fake.calls
    assert "document_qa" not in nodes(result), result

    # ------------------------------------------------------------------
    # TEST 15
    # LLM context must be exactly the authorized plaintext document and must
    # not be sourced from metadata-RAG results.
    # ------------------------------------------------------------------
    captured = FakeLLM("Grounded answer.")
    original_llm = patch_llm(captured)
    original_search = agent_module.metadata_rag.search

    def controlled_metadata_search(*args, **kwargs):
        return [
            {
                "repository": "academic",
                "document_id": DOCUMENT_ID,
                "student_id": STUDENT_ID,
                "document_type": "Transcript",
                "course_codes": ["CO521", "CO523"],
                "content_retrieved": False,
            }
        ]

    try:
        agent_module.metadata_rag.search = controlled_metadata_search
        result = answer_question(
            f"Retrieve academic transcript document {DOCUMENT_ID} for student {STUDENT_ID}.",
            user_context={
                "username": "phase4_context_test_user",
                "role": "ADMIN",
                "student_id": STUDENT_ID,
            },
        )
    finally:
        agent_module.metadata_rag.search = original_search
        agent_module.llm_service.answer_document_question = original_llm

    show("TEST 15 - LLM receives only authorized plaintext", result)

    assert result.get("error") is None, result
    assert result.get("llm_invoked") is True, result
    assert len(captured.calls) == 1, captured.calls
    assert captured.calls[0]["document"] == result["document"], result
    assert captured.calls[0]["document"] != result.get("metadata_results"), result
    assert captured.calls[0]["document"]["student_id"] == STUDENT_ID, result
    assert captured.calls[0]["document"]["document_id"] == DOCUMENT_ID, result

    # No trace entry may contain the actual document body.
    forbidden_trace_keys = {
        "content",
        "plaintext",
        "document_content",
        "raw_document",
        "encrypted_content",
        "document_data",
        "context",
    }
    for entry in result.get("trace", {}).get("nodes", []):
        if not isinstance(entry, dict):
            continue
        details = entry.get("details", {})
        if not isinstance(details, dict):
            continue
        assert forbidden_trace_keys.isdisjoint(details.keys()), entry
        assert details.get("content_exposed_to_trace") is not True, entry

    print("\n" + "=" * 80)
    print("ALL PHASE 3 + PHASE 4 LANGGRAPH TESTS PASSED")
    print("=" * 80)
    print("PASS - Authorization-before-retrieval")
    print("PASS - Public blockchain metadata retrieval")
    print("PASS - Metadata-only repository RAG")
    print("PASS - Course-specific metadata discovery")
    print("PASS - Multi-repository metadata routing")
    print("PASS - General query routing")
    print("PASS - Blockchain authoritative V4 resolution")
    print("PASS - Encrypted IPFS retrieval")
    print("PASS - ACL authorization/decryption")
    print("PASS - Plaintext integrity verification")
    print("PASS - RAG/blockchain identity mismatch protection")
    print("PASS - Authorized document Q&A")
    print("PASS - Course-specific grounded Q&A")
    print("PASS - Missing-information protection")
    print("PASS - Unauthorized document blocked from LLM")
    print("PASS - Integrity failure blocks LLM")
    print("PASS - LLM context security boundary")
    print("PASS - Phase 3 + Phase 4 LangGraph topology")


if __name__ == "__main__":
    run_tests()
