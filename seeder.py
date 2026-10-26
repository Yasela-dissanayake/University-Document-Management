"""
Phase 5 synthetic dataset seeder.

Seeds the validated Phase 5 dataset into:
    1. IPFS      -> encrypted document content
    2. Blockchain -> immutable document-version metadata

Important design rules:
    - Existing TX-98765 is never modified.
    - Blockchain is authoritative for document identity/version/provenance.
    - IPFS stores encrypted content only.
    - The smart contract determines the next immutable version number.
    - ACL encryption is used so the stored IPFS object follows the same
      application-level encryption path as the rest of the project.
    - A missing blockchain document is a normal "new document" condition,
      not an error.
    - Dry-run is the default. Use --execute to write to IPFS/blockchain.

Usage:
    python seeder.py
    python seeder.py --execute --limit-documents 1 --limit-versions 1
    python seeder.py --execute --verify-ipfs
    python seeder.py --execute --report phase5_seed_report.json

The seeder is intentionally defensive about small API differences between
older and newer versions of the project's UniversityBlockchainClient.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from python_backend.blockchain_client import UniversityBlockchainClient
from python_backend.ipfs_client import UniversityIPFSClient
from python_backend import acl


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DATASET_DIR = PROJECT_ROOT / "phase5_dataset"

PROTECTED_DOCUMENT_IDS = {
    "TX-98765",
}

DEFAULT_APPROVED_BY = "Phase5 Seeder"

LOG = logging.getLogger("phase5_seeder")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

@dataclass
class SeedReport:
    students_registered: int = 0
    students_already_exist: int = 0
    documents_created: int = 0
    documents_existing: int = 0
    versions_created: int = 0
    versions_existing: int = 0
    ipfs_objects_created: int = 0
    ipfs_objects_skipped: int = 0
    verification_passes: int = 0
    verification_failures: int = 0
    failures: List[Dict[str, Any]] = field(default_factory=list)
    created_items: List[Dict[str, Any]] = field(default_factory=list)

    def add_failure(
        self,
        document_id: str,
        operation: str,
        error: Exception | str,
    ) -> None:
        self.verification_failures += 1
        self.failures.append(
            {
                "document_id": document_id,
                "operation": operation,
                "error": str(error),
            }
        )

    def add_success(
        self,
        document_id: str,
        version: int,
        cid: str,
        content_hash: str,
        tx_hash: Optional[str],
    ) -> None:
        self.verification_passes += 1
        self.created_items.append(
            {
                "document_id": document_id,
                "version": version,
                "ipfs_cid": cid,
                "content_hash": content_hash,
                "tx_hash": tx_hash,
            }
        )


# ---------------------------------------------------------------------------
# JSON / hashing helpers
# ---------------------------------------------------------------------------

def canonical_json_bytes(document: Dict[str, Any]) -> bytes:
    """
    Canonical representation used by acl.calculate_content_hash().

    The project ACL layer uses:
        json.dumps(doc, sort_keys=True, separators=(",", ":"))
    """
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def calculate_content_hash(document: Dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        canonical_json_bytes(document)
    ).hexdigest()


def normalize_hash(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        return "0x" + value.hex()

    text = str(value).strip().lower()

    if text.startswith("0x"):
        text = text[2:]

    return text


# ---------------------------------------------------------------------------
# Dataset loading / validation
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Required dataset file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_dataset(dataset_dir: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    required = [
        "students.json",
        "documents.json",
        "manifest.json",
    ]

    for filename in required:
        path = dataset_dir / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Missing required dataset file: {path}"
            )

    students = load_json(dataset_dir / "students.json")
    documents = load_json(dataset_dir / "documents.json")
    manifest = load_json(dataset_dir / "manifest.json")

    if not isinstance(students, list):
        raise ValueError("students.json must contain a list.")

    if not isinstance(documents, list):
        raise ValueError("documents.json must contain a list.")

    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object.")

    student_ids = set()

    for student in students:
        if not isinstance(student, dict):
            raise ValueError("Each student record must be an object.")

        for key in ("student_id", "name", "program", "year"):
            if key not in student:
                raise ValueError(
                    f"Student record is missing '{key}': {student}"
                )

        student_ids.add(str(student["student_id"]))

    document_ids = set()

    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Each document record must be an object.")

        required_keys = {
            "document_id",
            "student_id",
            "document_type",
            "repository",
            "content",
            "version_plan",
        }

        missing = required_keys.difference(document)
        if missing:
            raise ValueError(
                f"Document {document.get('document_id')} is missing "
                f"fields: {sorted(missing)}"
            )

        document_id = str(document["document_id"])
        student_id = str(document["student_id"])

        if document_id in document_ids:
            raise ValueError(
                f"Duplicate document_id in dataset: {document_id}"
            )

        if student_id not in student_ids:
            raise ValueError(
                f"Document {document_id} references unknown student "
                f"{student_id}."
            )

        if not isinstance(document["content"], dict):
            raise ValueError(
                f"Document content must be an object: {document_id}"
            )

        version_plan = document["version_plan"]

        if not isinstance(version_plan, list) or not version_plan:
            raise ValueError(
                f"Document {document_id} has an empty version_plan."
            )

        expected_version = 1

        for version in version_plan:
            if not isinstance(version, dict):
                raise ValueError(
                    f"Invalid version entry in {document_id}: {version}"
                )

            declared_version = int(version.get("version", 0))

            if declared_version != expected_version:
                raise ValueError(
                    f"{document_id}: expected version "
                    f"{expected_version}, found {declared_version}."
                )

            if not str(version.get("approval_status", "")).strip():
                raise ValueError(
                    f"{document_id} V{declared_version} has no approval_status."
                )

            expected_version += 1

        document_ids.add(document_id)

    LOG.info(
        "Dataset validation passed."
    )
    LOG.info(
        "Dataset contains %d students, %d documents, %d versions.",
        len(students),
        len(documents),
        sum(len(d["version_plan"]) for d in documents),
    )

    return students, documents, manifest


# ---------------------------------------------------------------------------
# Blockchain compatibility helpers
# ---------------------------------------------------------------------------

def _callable_accepts_parameter(
    callable_obj: Any,
    parameter_name: str,
) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False

    return parameter_name in signature.parameters


def blockchain_student_exists(
    blockchain: UniversityBlockchainClient,
    student_id: str,
) -> bool:
    """
    Prefer the project's explicit student existence method.

    Older clients expose studentRecordExists directly through the contract,
    while some clients expose only get_student_details(). This helper handles
    both without treating a missing student as an exception.
    """
    method = getattr(blockchain, "student_exists", None)

    if callable(method):
        try:
            return bool(method(student_id))
        except Exception:
            pass

    contract = getattr(blockchain, "contract", None)

    if contract is not None:
        for function_name in (
            "studentRecordExists",
            "studentExists",
        ):
            try:
                function = getattr(contract.functions, function_name)
            except AttributeError:
                continue

            try:
                return bool(function(student_id).call())
            except Exception:
                continue

    try:
        details = blockchain.get_student_details(student_id)
        return bool(details and details.get("student_id"))
    except Exception:
        return False


def blockchain_document_exists(
    blockchain: UniversityBlockchainClient,
    document_id: str,
) -> bool:
    """
    IMPORTANT:
    Do not use get_document_details() to test existence.

    The deployed contract reverts with:
        "UniversityRegistry: document not found"

    for a new document. document_exists() is therefore the correct
    existence probe.
    """
    method = getattr(blockchain, "document_exists", None)

    if not callable(method):
        raise RuntimeError(
            "UniversityBlockchainClient does not expose document_exists(). "
            "The Phase 3/4 blockchain client is required for Phase 5 seeding."
        )

    try:
        return bool(method(document_id))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to check blockchain existence for "
            f"'{document_id}': {exc}"
        ) from exc


def get_document_details_if_exists(
    blockchain: UniversityBlockchainClient,
    document_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Safely retrieve details after document_exists() has returned True.
    """
    if not blockchain_document_exists(blockchain, document_id):
        return None

    details = blockchain.get_document_details(document_id)

    if not details:
        raise RuntimeError(
            f"Blockchain reports document '{document_id}' exists, "
            "but get_document_details() returned no data."
        )

    return details


def get_existing_history(
    blockchain: UniversityBlockchainClient,
    document_id: str,
) -> List[Dict[str, Any]]:
    history_method = getattr(
        blockchain,
        "get_document_history",
        None,
    )

    if not callable(history_method):
        raise RuntimeError(
            "UniversityBlockchainClient does not expose "
            "get_document_history()."
        )

    history = history_method(document_id)

    if history is None:
        return []

    if not isinstance(history, list):
        history = list(history)

    normalized: List[Dict[str, Any]] = []

    for entry in history:
        if not isinstance(entry, dict):
            raise RuntimeError(
                "Unexpected blockchain history entry type: "
                f"{type(entry).__name__}"
            )

        normalized.append(entry)

    normalized.sort(
        key=lambda item: int(item.get("version", 0))
    )

    return normalized


def get_existing_version(
    blockchain: UniversityBlockchainClient,
    document_id: str,
    version: int,
) -> Optional[Dict[str, Any]]:
    method = getattr(
        blockchain,
        "get_document_version",
        None,
    )

    if callable(method):
        try:
            result = method(document_id, version)
            if result:
                return result
        except Exception:
            pass

    history = get_existing_history(
        blockchain,
        document_id,
    )

    for item in history:
        if int(item.get("version", 0)) == int(version):
            return item

    return None


def create_document_version(
    blockchain: UniversityBlockchainClient,
    *,
    document_id: str,
    student_id: str,
    document_type: str,
    ipfs_cid: str,
    content_hash: str,
    approval_status: str,
    approved_by: str,
) -> str:
    method = getattr(
        blockchain,
        "create_document_version",
        None,
    )

    if not callable(method):
        raise RuntimeError(
            "UniversityBlockchainClient does not expose "
            "create_document_version()."
        )

    tx_hash = method(
        document_id=document_id,
        student_id=student_id,
        document_type=document_type,
        ipfs_cid=ipfs_cid,
        content_hash=content_hash,
        approval_status=approval_status,
        approved_by=approved_by,
    )

    return str(tx_hash)


# ---------------------------------------------------------------------------
# Student registration
# ---------------------------------------------------------------------------

def register_student(
    blockchain: UniversityBlockchainClient,
    student: Dict[str, Any],
    first_cid: str,
    first_content_hash: str,
) -> str:
    """
    Register a Phase 5 student using the current blockchain client API.

    The current ``UniversityBlockchainClient.register_student_record()``
    method is keyword-only and accepts:

        student_id
        name
        program
        year
        documents_ipfs_hash
        content_hash

    The adapter deliberately uses that explicit contract instead of falling
    back to the older positional API, which is no longer supported by the
    current client.
    """
    method = getattr(
        blockchain,
        "register_student_record",
        None,
    )

    if not callable(method):
        raise RuntimeError(
            "UniversityBlockchainClient does not expose "
            "register_student_record()."
        )

    return str(
        method(
            student_id=str(student["student_id"]),
            name=str(student["name"]),
            program=str(student["program"]),
            year=int(student["year"]),
            documents_ipfs_hash=str(first_cid),
            content_hash=normalize_hash(first_content_hash),
        )
    )


# ---------------------------------------------------------------------------
# IPFS / ACL helpers
# ---------------------------------------------------------------------------

def store_encrypted_document(
    ipfs: UniversityIPFSClient,
    document: Dict[str, Any],
    student_id: str,
) -> Tuple[str, str]:
    """
    Encrypt using the project's ACL mechanism and store using the existing
    UniversityIPFSClient.

    ACL principals intentionally include:
        role:ADMIN
        student:<student_id>

    This matches the project's existing ACL design and allows authorized
    student retrieval later.
    """
    plaintext_hash = calculate_content_hash(document)

    principals = [
        "role:ADMIN",
        f"student:{student_id}",
    ]

    result = acl.encrypt_and_store(
        document,
        principals,
        ipfs,
    )

    if (
        not isinstance(result, tuple)
        or len(result) != 2
    ):
        raise RuntimeError(
            "acl.encrypt_and_store() must return "
            "(cid, content_hash)."
        )

    cid, returned_hash = result

    cid = str(cid).strip()
    returned_hash = normalize_hash(returned_hash)

    if not cid:
        raise RuntimeError(
            "ACL/IPFS storage returned an empty CID."
        )

    if returned_hash != normalize_hash(plaintext_hash):
        raise RuntimeError(
            "ACL content hash does not match the independently "
            "calculated plaintext hash.\n"
            f"ACL:       {returned_hash}\n"
            f"Plaintext: {plaintext_hash}"
        )

    return cid, plaintext_hash


def verify_ipfs_document(
    ipfs: UniversityIPFSClient,
    cid: str,
    expected_content_hash: str,
) -> bool:
    """
    Verify the stored encrypted document using the project's current
    IPFS retrieval/decryption path.

    For ACL-encrypted payloads, UniversityIPFSClient alone returns the
    encrypted envelope. Therefore ACL verification is attempted first.
    """
    try:
        payload = ipfs.retrieve_academic_document(cid)

        if not isinstance(payload, dict):
            return False

        # ACL envelope path.
        if payload.get("enc") == "fernet-v1":
            # We cannot claim a user-level ACL verification here without
            # inventing a user object. The project's IPFS client can at least
            # confirm that the outer encrypted object is valid JSON and that
            # it contains the expected encrypted envelope fields.
            return (
                payload.get("alg") == "Fernet"
                and bool(payload.get("ciphertext"))
                and bool(payload.get("kid"))
            )

        # Plain IPFS document path, retained for compatibility.
        actual_hash = calculate_content_hash(payload)
        return normalize_hash(actual_hash) == normalize_hash(
            expected_content_hash
        )

    except Exception as exc:
        LOG.error(
            "IPFS verification failed for %s: %s",
            cid,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Blockchain verification
# ---------------------------------------------------------------------------

def verify_blockchain_version(
    blockchain: UniversityBlockchainClient,
    document: Dict[str, Any],
    expected_version: int,
    expected_cid: str,
    expected_hash: str,
    expected_status: str,
) -> Dict[str, Any]:
    document_id = str(document["document_id"])

    details = get_document_details_if_exists(
        blockchain,
        document_id,
    )

    if details is None:
        raise RuntimeError(
            f"Document '{document_id}' does not exist after "
            "successful version creation."
        )

    latest_version = int(
        details.get("latest_version", 0)
    )

    if latest_version < expected_version:
        raise RuntimeError(
            f"{document_id}: blockchain latest version is V"
            f"{latest_version}, but V{expected_version} was expected."
        )

    version_data = get_existing_version(
        blockchain,
        document_id,
        expected_version,
    )

    if version_data is None:
        raise RuntimeError(
            f"{document_id}: V{expected_version} was not found "
            "in blockchain history."
        )

    actual_cid = str(
        version_data.get("ipfs_cid", "")
        or ""
    ).strip()

    actual_hash = normalize_hash(
        version_data.get("content_hash")
    )

    actual_status = str(
        version_data.get("approval_status", "")
        or ""
    ).strip()

    if actual_cid != expected_cid:
        raise RuntimeError(
            f"{document_id} V{expected_version}: CID mismatch.\n"
            f"Expected: {expected_cid}\n"
            f"Found:    {actual_cid}"
        )

    if actual_hash != normalize_hash(expected_hash):
        raise RuntimeError(
            f"{document_id} V{expected_version}: content hash mismatch.\n"
            f"Expected: {normalize_hash(expected_hash)}\n"
            f"Found:    {actual_hash}"
        )

    if actual_status != expected_status:
        raise RuntimeError(
            f"{document_id} V{expected_version}: approval status mismatch.\n"
            f"Expected: {expected_status}\n"
            f"Found:    {actual_status}"
        )

    return version_data


def validate_version_chain(
    blockchain: UniversityBlockchainClient,
    document_id: str,
) -> None:
    history = get_existing_history(
        blockchain,
        document_id,
    )

    if not history:
        raise RuntimeError(
            f"{document_id}: blockchain history is empty."
        )

    for index, item in enumerate(history, start=1):
        version = int(item.get("version", 0))
        previous = int(
            item.get("previous_version", 0)
        )

        if version != index:
            raise RuntimeError(
                f"{document_id}: expected V{index}, found V{version}."
            )

        expected_previous = (
            0 if version == 1 else version - 1
        )

        if previous != expected_previous:
            raise RuntimeError(
                f"{document_id} V{version}: expected "
                f"previous_version={expected_previous}, "
                f"found {previous}."
            )


# ---------------------------------------------------------------------------
# Document seeding
# ---------------------------------------------------------------------------

def process_document(
    blockchain: UniversityBlockchainClient,
    ipfs: UniversityIPFSClient,
    document: Dict[str, Any],
    students_by_id: Dict[str, Dict[str, Any]],
    report: SeedReport,
    *,
    execute: bool,
    verify_ipfs: bool,
    limit_versions: Optional[int],
    approved_by: str,
) -> None:
    document_id = str(document["document_id"])
    student_id = str(document["student_id"])
    document_type = str(document["document_type"])

    if document_id in PROTECTED_DOCUMENT_IDS:
        raise RuntimeError(
            f"Protected document '{document_id}' is intentionally excluded "
            "from Phase 5 seeding."
        )

    LOG.info(
        "Processing %s (%s / %s)",
        document_id,
        student_id,
        document.get("repository"),
    )

    if student_id not in students_by_id:
        raise RuntimeError(
            f"Student '{student_id}' is missing from students.json."
        )

    planned_versions = list(
        document["version_plan"]
    )

    if limit_versions is not None:
        planned_versions = planned_versions[
            :limit_versions
        ]

    # ------------------------------------------------------------------
    # Check existing logical document.
    # ------------------------------------------------------------------

    exists = blockchain_document_exists(
        blockchain,
        document_id,
    )

    if exists:
        report.documents_existing += 1

        details = get_document_details_if_exists(
            blockchain,
            document_id,
        )

        current_version = int(
            details.get("latest_version", 0)
            if details
            else 0
        )

        LOG.info(
            "%s already exists on blockchain at V%d.",
            document_id,
            current_version,
        )
    else:
        report.documents_created += 1
        current_version = 0

        LOG.info(
            "%s is new; blockchain version chain will start at V1.",
            document_id,
        )

    # ------------------------------------------------------------------
    # Process planned versions sequentially.
    # ------------------------------------------------------------------

    for version_plan in planned_versions:
        planned_version = int(
            version_plan["version"]
        )
        approval_status = str(
            version_plan["approval_status"]
        ).strip()

        # Existing blockchain version:
        # verify it rather than creating a duplicate.
        if current_version >= planned_version:
            existing_version = get_existing_version(
                blockchain,
                document_id,
                planned_version,
            )

            if existing_version is None:
                raise RuntimeError(
                    f"{document_id}: blockchain reports latest version "
                    f"V{current_version}, but V{planned_version} "
                    "could not be retrieved."
                )

            existing_cid = str(
                existing_version.get("ipfs_cid", "")
                or ""
            ).strip()

            existing_hash = normalize_hash(
                existing_version.get("content_hash")
            )

            if execute:
                report.versions_existing += 1

            LOG.info(
                "%s V%d already exists; no duplicate version created.",
                document_id,
                planned_version,
            )

            if verify_ipfs and existing_cid:
                # Only verify IPFS objects that belong to the current Phase 5
                # seeding run.  This project already contains development/test
                # blockchain fixtures whose CIDs intentionally do not point to
                # real IPFS objects (for example, ``bafy-test-version-1``).
                # Those immutable historical records must remain untouched,
                # but they should not make an otherwise idempotent Phase 5
                # seeding run fail.
                existing_approved_by = str(
                    existing_version.get("approved_by", "") or ""
                ).strip()

                if existing_approved_by == approved_by:
                    if verify_ipfs_document(
                        ipfs,
                        existing_cid,
                        existing_hash,
                    ):
                        report.verification_passes += 1
                    else:
                        report.add_failure(
                            document_id,
                            f"verify_existing_v{planned_version}_ipfs",
                            "Existing Phase 5 IPFS object failed envelope/integrity verification.",
                        )
                else:
                    LOG.info(
                        "%s V%d is an existing non-Phase-5 fixture "
                        "(approved_by=%s); skipping IPFS verification.",
                        document_id,
                        planned_version,
                        existing_approved_by or "<unknown>",
                    )

            continue

        # The contract should create versions strictly sequentially.
        expected_new_version = current_version + 1

        if planned_version != expected_new_version:
            raise RuntimeError(
                f"{document_id}: dataset requests V{planned_version}, "
                f"but blockchain is at V{current_version}; "
                f"next allowed version is V{expected_new_version}."
            )

        # ------------------------------------------------------------------
        # Build the plaintext document.
        #
        # The synthetic dataset's version plan currently describes approval
        # state rather than separate content mutations, so the document
        # content is the dataset's canonical content for every planned
        # version. Fernet/ACL encryption produces a distinct IPFS object.
        # ------------------------------------------------------------------

        document_content = dict(
            document["content"]
        )

        # Ensure the immutable document identity in content remains aligned
        # with the blockchain request.
        document_content["document_id"] = document_id
        document_content["student_id"] = student_id
        document_content["document_type"] = document_type

        plaintext_hash = calculate_content_hash(
            document_content
        )

        # ------------------------------------------------------------------
        # Dry-run.
        # ------------------------------------------------------------------

        if not execute:
            LOG.info(
                "[DRY-RUN] Would create %s V%d | status=%s | hash=%s",
                document_id,
                planned_version,
                approval_status,
                plaintext_hash,
            )
            current_version = planned_version
            continue

        # ------------------------------------------------------------------
        # Encrypt + IPFS.
        # ------------------------------------------------------------------

        cid, returned_hash = store_encrypted_document(
            ipfs,
            document_content,
            student_id,
        )

        report.ipfs_objects_created += 1

        LOG.info(
            "IPFS stored %s V%d -> %s",
            document_id,
            planned_version,
            cid,
        )

        if verify_ipfs:
            if verify_ipfs_document(
                ipfs,
                cid,
                returned_hash,
            ):
                LOG.info(
                    "IPFS envelope verification passed for %s V%d.",
                    document_id,
                    planned_version,
                )
            else:
                raise RuntimeError(
                    f"{document_id} V{planned_version}: "
                    "IPFS verification failed before blockchain write."
                )

        # ------------------------------------------------------------------
        # Register student if this is a new student.
        #
        # We do this after obtaining the first encrypted document because
        # the project's existing register_student_record API requires an
        # initial IPFS/content hash.
        # ------------------------------------------------------------------

        if planned_version == 1:
            if blockchain_student_exists(
                blockchain,
                student_id,
            ):
                report.students_already_exist += 1
            else:
                student = students_by_id[student_id]

                tx_student = register_student(
                    blockchain,
                    student,
                    cid,
                    returned_hash,
                )

                report.students_registered += 1

                LOG.info(
                    "Registered student %s. Tx: %s",
                    student_id,
                    tx_student,
                )

        # ------------------------------------------------------------------
        # Create immutable blockchain version.
        # ------------------------------------------------------------------

        tx_hash = create_document_version(
            blockchain,
            document_id=document_id,
            student_id=student_id,
            document_type=document_type,
            ipfs_cid=cid,
            content_hash=returned_hash,
            approval_status=approval_status,
            approved_by=approved_by,
        )

        report.versions_created += 1

        LOG.info(
            "Created %s V%d. Tx: %s",
            document_id,
            planned_version,
            tx_hash,
        )

        # ------------------------------------------------------------------
        # Verify the exact version and immutable chain.
        # ------------------------------------------------------------------

        verify_blockchain_version(
            blockchain,
            document,
            planned_version,
            cid,
            returned_hash,
            approval_status,
        )

        validate_version_chain(
            blockchain,
            document_id,
        )

        report.add_success(
            document_id=document_id,
            version=planned_version,
            cid=cid,
            content_hash=returned_hash,
            tx_hash=tx_hash,
        )

        current_version = planned_version

        # Small pause to avoid overly aggressive RPC sequencing.
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# Main seeding operation
# ---------------------------------------------------------------------------

def seed_dataset(
    *,
    dataset_dir: Path,
    execute: bool,
    verify_ipfs: bool,
    limit_documents: Optional[int],
    limit_versions: Optional[int],
    report_path: Optional[Path],
    approved_by: str,
) -> SeedReport:
    students, documents, _manifest = validate_dataset(
        dataset_dir
    )

    blockchain = UniversityBlockchainClient()

    ipfs = UniversityIPFSClient()

    students_by_id = {
        str(student["student_id"]): student
        for student in students
    }

    selected_documents = [
        document
        for document in documents
        if str(document["document_id"])
        not in PROTECTED_DOCUMENT_IDS
    ]

    if limit_documents is not None:
        selected_documents = selected_documents[
            :limit_documents
        ]

    report = SeedReport()

    LOG.info(
        "Seeding mode: %s",
        "EXECUTE" if execute else "DRY-RUN",
    )

    LOG.info(
        "Selected documents: %d",
        len(selected_documents),
    )

    for document in selected_documents:
        document_id = str(
            document["document_id"]
        )

        try:
            process_document(
                blockchain,
                ipfs,
                document,
                students_by_id,
                report,
                execute=execute,
                verify_ipfs=verify_ipfs,
                limit_versions=limit_versions,
                approved_by=approved_by,
            )

        except Exception as exc:
            LOG.exception(
                "Failed to seed %s",
                document_id,
            )
            report.add_failure(
                document_id,
                "seed_document",
                exc,
            )

    if report_path:
        report_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = asdict(report)
        payload["mode"] = (
            "EXECUTE"
            if execute
            else "DRY-RUN"
        )
        payload["dataset"] = str(
            dataset_dir.resolve()
        )

        report_path.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        LOG.info(
            "Report written to %s",
            report_path,
        )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_report(
    report: SeedReport,
    *,
    dataset_dir: Path,
    execute: bool,
) -> None:
    print()
    print("=" * 72)
    print("PHASE 5 DATASET SEEDING REPORT")
    print("=" * 72)
    print(f"Dataset: {dataset_dir.resolve()}")
    print(
        f"Mode: {'EXECUTE' if execute else 'DRY-RUN'}"
    )
    print()
    print(
        f"Students registered:   {report.students_registered}"
    )
    print(
        f"Students already exist: {report.students_already_exist}"
    )
    print(
        f"Documents created:     {report.documents_created}"
    )
    print(
        f"Documents existing:    {report.documents_existing}"
    )
    print(
        f"Versions created:      {report.versions_created}"
    )
    print(
        f"Versions existing:     {report.versions_existing}"
    )
    print(
        f"IPFS objects created:  {report.ipfs_objects_created}"
    )
    print(
        f"IPFS objects skipped:  {report.ipfs_objects_skipped}"
    )
    print(
        f"Verification passes:   {report.verification_passes}"
    )
    print(
        f"Verification failures: {report.verification_failures}"
    )

    if report.created_items:
        print()
        print("CREATED / VERIFIED")
        print("-" * 72)

        for item in report.created_items:
            print(
                f"- {item['document_id']} "
                f"V{item['version']} | "
                f"CID={item['ipfs_cid']} | "
                f"tx={item['tx_hash']}"
            )

    if report.failures:
        print()
        print("FAILURES")
        print("-" * 72)

        for failure in report.failures:
            print(
                f"- {failure['document_id']} | "
                f"{failure['operation']} | "
                f"{failure['error']}"
            )

    print("=" * 72)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed the Phase 5 synthetic evaluation dataset into "
            "encrypted IPFS and the UniversityRegistry blockchain."
        )
    )

    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATASET_DIR,
        help=(
            "Phase 5 dataset directory "
            "(default: phase5_dataset)"
        ),
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually write to IPFS and blockchain. "
            "Without this flag the command is a dry-run."
        ),
    )

    parser.add_argument(
        "--verify-ipfs",
        action="store_true",
        help=(
            "Verify each IPFS object after storing it. "
            "For ACL envelopes this verifies the encrypted envelope."
        ),
    )

    parser.add_argument(
        "--limit-documents",
        type=int,
        default=None,
        help=(
            "Process only the first N non-protected documents."
        ),
    )

    parser.add_argument(
        "--limit-versions",
        type=int,
        default=None,
        help=(
            "Process only the first N planned versions per document."
        ),
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional JSON report output path.",
    )

    parser.add_argument(
        "--approved-by",
        default=DEFAULT_APPROVED_BY,
        help=(
            "approvedBy value written to blockchain versions "
            f"(default: {DEFAULT_APPROVED_BY!r})."
        ),
    )

    parser.add_argument(
        "--log-level",
        choices=(
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
        ),
        default="INFO",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(
            logging,
            args.log_level,
        ),
        format=(
            "%(asctime)s | %(levelname)s | %(message)s"
        ),
    )

    if args.limit_documents is not None and args.limit_documents < 1:
        parser.error(
            "--limit-documents must be at least 1."
        )

    if args.limit_versions is not None and args.limit_versions < 1:
        parser.error(
            "--limit-versions must be at least 1."
        )

    try:
        report = seed_dataset(
            dataset_dir=args.dataset,
            execute=args.execute,
            verify_ipfs=args.verify_ipfs,
            limit_documents=args.limit_documents,
            limit_versions=args.limit_versions,
            report_path=args.report,
            approved_by=args.approved_by,
        )

        print_report(
            report,
            dataset_dir=args.dataset,
            execute=args.execute,
        )

        return 1 if report.failures else 0

    except KeyboardInterrupt:
        LOG.error("Seeding interrupted by user.")
        return 130

    except Exception:
        LOG.exception(
            "Fatal seeder error."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
