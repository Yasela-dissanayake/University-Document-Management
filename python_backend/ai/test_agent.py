"""Phase 3 LangGraph test runner.

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
    8. LangGraph topology.
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

    result = answer_question(
        f"Retrieve document {DOCUMENT_ID} for student {STUDENT_ID}.",
        user_context=phase3_user,
    )
    show(
        "TEST 7 - Phase 3 authoritative document retrieval",
        result,
    )

    assert_phase3_success(result)

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
    #
    # The RAG result is deliberately forged for the test so that it points
    # TX-98765 at a different student. The blockchain remains authoritative
    # and says TX-98765 belongs to STD001. The agent must fail closed before
    # IPFS retrieval/decryption.
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
        show(
            "TEST 8 - RAG/blockchain identity mismatch protection",
            result,
        )

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

        # The authoritative blockchain record must still identify STD001.
        blockchain_metadata = result.get("blockchain_metadata", {})
        assert blockchain_metadata.get("document_id") == DOCUMENT_ID, result
        assert blockchain_metadata.get("student_id") == STUDENT_ID, result

        # No trace entry may expose confidential document content.
        for entry in result.get("trace", {}).get("nodes", []):
            if not isinstance(entry, dict):
                continue
            details = entry.get("details", {})
            if isinstance(details, dict):
                assert details.get("content_exposed_to_trace") is not True, entry
    finally:
        agent_module.metadata_rag.search = original_search

    # ------------------------------------------------------------------
    # TEST 9
    # Verify that the Phase 3 graph exposes the required stages.
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("PHASE 3 GRAPH")
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
    }

    for node_name in required_nodes:
        assert node_name in mermaid, (
            f"Graph is missing node: {node_name}\n{mermaid}"
        )

    # ------------------------------------------------------------------
    # Final result
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("ALL PHASE 3 LANGGRAPH TESTS PASSED")
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
    print("PASS - Phase 3 LangGraph topology")


if __name__ == "__main__":
    run_tests()
