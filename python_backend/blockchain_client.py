# blockchain_client.py

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from web3 import Web3, HTTPProvider
from eth_account import Account
from eth_account.signers.local import LocalAccount

# Try to load .env if available
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    pass


def _load_env(name: str) -> str:
    val = os.getenv(name)

    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return val


def _load_abi() -> List[Dict[str, Any]]:
    """
    Find the UniversityRegistry ABI in common locations or via
    ABI_JSON_PATH.

    Returns the ABI list directly.
    """

    here = os.path.dirname(__file__)
    repo_root = os.path.abspath(
        os.path.join(here, "..")
    )

    candidates = [
        os.path.join(
            here,
            "artifacts",
            "contracts",
            "UniversityRegistry.sol",
            "UniversityRegistry.json",
        ),
        os.path.join(
            repo_root,
            "artifacts",
            "contracts",
            "UniversityRegistry.sol",
            "UniversityRegistry.json",
        ),
        os.getenv("ABI_JSON_PATH") or "",
    ]

    tried: List[str] = []

    for path in candidates:
        if not path:
            continue

        path = os.path.abspath(path)
        tried.append(path)

        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Hardhat artifact
            if isinstance(data, dict) and "abi" in data:
                return data["abi"]  # type: ignore

            # ABI exported directly
            if isinstance(data, list):
                return data  # type: ignore

    raise FileNotFoundError(
        "ABI artifact not found. Tried:\n"
        + "\n".join(tried)
    )


# ABI entries required by the Phase 3 document-version workflow.
# These are merged into the loaded artifact so an older local Hardhat ABI
# cannot silently break read-only document resolution.
PHASE3_DOCUMENT_ABI: List[Dict[str, Any]] = [
    {
        "inputs": [{"internalType": "string", "name": "_documentId", "type": "string"}],
        "name": "documentExists",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "string", "name": "_documentId", "type": "string"}],
        "name": "getDocumentDetails",
        "outputs": [
            {"internalType": "string", "name": "documentId", "type": "string"},
            {"internalType": "string", "name": "studentId", "type": "string"},
            {"internalType": "string", "name": "documentType", "type": "string"},
            {"internalType": "uint256", "name": "latestVersion", "type": "uint256"},
            {"internalType": "bool", "name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "string", "name": "_documentId", "type": "string"},
            {"internalType": "uint256", "name": "_version", "type": "uint256"},
        ],
        "name": "getDocumentVersion",
        "outputs": [
            {"internalType": "uint256", "name": "version", "type": "uint256"},
            {"internalType": "uint256", "name": "previousVersion", "type": "uint256"},
            {"internalType": "string", "name": "ipfsCID", "type": "string"},
            {"internalType": "bytes32", "name": "contentHash", "type": "bytes32"},
            {"internalType": "string", "name": "approvalStatus", "type": "string"},
            {"internalType": "string", "name": "approvedBy", "type": "string"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "string", "name": "_documentId", "type": "string"}],
        "name": "getLatestDocumentVersion",
        "outputs": [
            {"internalType": "uint256", "name": "version", "type": "uint256"},
            {"internalType": "uint256", "name": "previousVersion", "type": "uint256"},
            {"internalType": "string", "name": "ipfsCID", "type": "string"},
            {"internalType": "bytes32", "name": "contentHash", "type": "bytes32"},
            {"internalType": "string", "name": "approvalStatus", "type": "string"},
            {"internalType": "string", "name": "approvedBy", "type": "string"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "string", "name": "_documentId", "type": "string"}],
        "name": "getLatestApprovedDocumentVersion",
        "outputs": [
            {"internalType": "uint256", "name": "version", "type": "uint256"},
            {"internalType": "uint256", "name": "previousVersion", "type": "uint256"},
            {"internalType": "string", "name": "ipfsCID", "type": "string"},
            {"internalType": "bytes32", "name": "contentHash", "type": "bytes32"},
            {"internalType": "string", "name": "approvalStatus", "type": "string"},
            {"internalType": "string", "name": "approvedBy", "type": "string"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "string", "name": "_documentId", "type": "string"}],
        "name": "getDocumentHistory",
        "outputs": [{
            "components": [
                {"internalType": "uint256", "name": "version", "type": "uint256"},
                {"internalType": "uint256", "name": "previousVersion", "type": "uint256"},
                {"internalType": "string", "name": "ipfsCID", "type": "string"},
                {"internalType": "bytes32", "name": "contentHash", "type": "bytes32"},
                {"internalType": "string", "name": "approvalStatus", "type": "string"},
                {"internalType": "string", "name": "approvedBy", "type": "string"},
                {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
            ],
            "internalType": "struct UniversityRegistry.DocumentVersion[]",
            "name": "",
            "type": "tuple[]",
        }],
        "stateMutability": "view",
        "type": "function",
    },
]

def _merge_phase3_abi(abi: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure required Phase 3 read functions exist in the local ABI."""
    merged = list(abi)
    existing = {item.get("name") for item in merged if isinstance(item, dict)}
    for entry in PHASE3_DOCUMENT_ABI:
        if entry["name"] not in existing:
            merged.append(entry)
    return merged


def _hex_to_bytes32(x: Optional[str]) -> bytes:
    """
    Convert a hexadecimal string into exactly 32 bytes.

    Empty input becomes 32 zero bytes.
    """

    if not x:
        return b"\x00" * 32

    s = x.strip().lower()

    if s.startswith("0x"):
        s = s[2:]

    try:
        data = bytes.fromhex(s) if s else b""
    except ValueError as exc:
        raise ValueError(
            f"Invalid hexadecimal content hash: {x}"
        ) from exc

    if len(data) >= 32:
        return data[:32]

    return data + (b"\x00" * (32 - len(data)))


def _bytes32_to_hex(value: Any) -> str:
    """
    Convert bytes/HexBytes/hexadecimal-like values into
    a normalized 0x-prefixed hexadecimal string.
    """

    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()

    value_str = str(value)

    if value_str.startswith("0x"):
        return value_str

    return "0x" + value_str


def _norm_name(name: str) -> str:
    """
    Normalize ABI input names.

    Example:
        _studentId -> studentid
        _ipfsHash  -> ipfshash
    """

    return name.lstrip("_").lower()


class UniversityBlockchainClient:
    """
    Client for the UniversityRegistry smart contract.

    Environment variables:

        WEB3_PROVIDER
            RPC URL

        CONTRACT_ADDRESS
            Deployed UniversityRegistry address

        PRIVATE_KEY
            Backend/service wallet private key

        ABI_JSON_PATH
            Optional absolute path to the contract ABI artifact.
    """

    def __init__(self) -> None:

        self.rpc_url: str = _load_env(
            "WEB3_PROVIDER"
        )

        self.contract_address: str = (
            Web3.to_checksum_address(
                _load_env("CONTRACT_ADDRESS")
            )
        )

        private_key = _load_env(
            "PRIVATE_KEY"
        )

        self.w3: Web3 = Web3(
            HTTPProvider(self.rpc_url)
        )

        self.account: LocalAccount = (
            Account.from_key(private_key)
        )

        self.chain_id: int = (
            self.w3.eth.chain_id
        )

        abi = _merge_phase3_abi(_load_abi())

        self.contract = self.w3.eth.contract(
            address=self.contract_address,
            abi=abi,
        )

        print(
            "✅ Connected to university blockchain "
            f"at {self.rpc_url}"
        )

        # --------------------------------------------------------
        # Cache ABI input names
        # --------------------------------------------------------

        self._register_inputs_norm = (
            self._get_fn_inputs_norm(
                "registerStudent"
            )
        )

        self._add_semester_inputs_norm = (
            self._get_fn_inputs_norm(
                "addSemesterRecord"
            )
        )

        self._create_document_version_inputs_norm = (
            self._get_fn_inputs_norm(
                "createDocumentVersion"
            )
        )

    # ============================================================
    # ABI Helpers
    # ============================================================

    def _get_fn_inputs_norm(
        self,
        fn_name: str,
    ) -> List[str]:
        """
        Return normalized input names for the first
        ABI function matching fn_name.
        """

        inputs: List[str] = []

        try:
            for item in self.contract.abi:

                if (
                    item.get("type") == "function"
                    and item.get("name") == fn_name
                ):
                    abi_inputs = (
                        item.get("inputs", [])
                        or []
                    )

                    inputs = [
                        _norm_name(
                            i.get("name", "")
                        )
                        for i in abi_inputs
                    ]

                    break

        except Exception:
            inputs = []

        return inputs

    # ============================================================
    # Gas / Transaction Helpers
    # ============================================================

    def _supports_eip1559(self) -> bool:
        try:

            latest = self.w3.eth.get_block(
                "latest"
            )

            return (
                "baseFeePerGas" in latest
                and latest["baseFeePerGas"]
                is not None
            )

        except Exception:
            return False

    def _gas_price_fields(self) -> Dict[str, int]:

        if self._supports_eip1559():

            latest = self.w3.eth.get_block(
                "latest"
            )

            base_fee = int(
                latest.get(
                    "baseFeePerGas",
                    self.w3.to_wei(
                        1,
                        "gwei",
                    ),
                )
            )

            try:
                priority_fee = int(
                    self.w3.eth.max_priority_fee
                )

            except Exception:
                priority_fee = self.w3.to_wei(
                    2,
                    "gwei",
                )

            return {
                "maxFeePerGas": int(
                    base_fee * 2
                    + priority_fee
                ),
                "maxPriorityFeePerGas": (
                    priority_fee
                ),
            }

        return {
            "gasPrice": self.w3.eth.gas_price
        }

    def _estimate_gas(
        self,
        fn,
        tx_from: str,
        value: int = 0,
    ) -> int:

        try:

            estimated = fn.estimate_gas(
                {
                    "from": tx_from,
                    "value": value,
                }
            )

        except Exception:

            estimated = 600_000

        return int(
            estimated * 1.5
        )

    def _send_fn(
        self,
        fn,
        value: int = 0,
    ) -> str:

        tx_from = self.account.address

        gas = self._estimate_gas(
            fn,
            tx_from,
            value=value,
        )

        gas_fields = (
            self._gas_price_fields()
        )

        nonce = (
            self.w3.eth.get_transaction_count(
                tx_from
            )
        )

        transaction = fn.build_transaction(
            {
                "from": tx_from,
                "chainId": self.chain_id,
                "nonce": nonce,
                "gas": gas,
                **gas_fields,
                "value": value,
            }
        )

        signed = (
            self.account.sign_transaction(
                transaction
            )
        )

        raw = getattr(
            signed,
            "rawTransaction",
            None,
        )

        if raw is None:
            raw = getattr(
                signed,
                "raw_transaction",
                None,
            )

        if raw is None:

            try:
                raw = signed.raw  # type: ignore

            except Exception as exc:

                raise RuntimeError(
                    "Cannot extract raw transaction "
                    f"bytes from SignedTransaction: {exc}"
                )

        tx_hash = (
            self.w3.eth.send_raw_transaction(
                raw
            )
        )

        tx_hash_hex = (
            tx_hash.hex()
            if hasattr(tx_hash, "hex")
            else str(tx_hash)
        )

        # Wait for transaction confirmation
        try:

            receipt = (
                self.w3.eth.wait_for_transaction_receipt(
                    tx_hash,
                    timeout=120,
                )
            )

            if receipt.get("status") == 0:

                raise RuntimeError(
                    "Transaction reverted on-chain. "
                    f"Hash: {tx_hash_hex}"
                )

        except Exception as wait_error:

            if (
                "reverted"
                in str(wait_error).lower()
            ):
                raise

            print(
                "[blockchain_client] Warning: "
                f"receipt wait error: {wait_error}"
            )

        return tx_hash_hex

    # ============================================================
    # Existing Student Write Functions
    # ============================================================

    def register_student_record(
        self,
        *,
        student_id: str,
        name: str,
        program: str,
        year: int,
        documents_ipfs_hash: str = "",
        content_hash: str = "",
        timestamp: int = 0,
        student_wallet: Optional[str] = None,
    ) -> str:
        """
        Register a new student.

        The timestamp parameter is retained for compatibility
        with the existing Python API.

        The current Solidity contract does not accept a timestamp
        parameter; the blockchain uses block.timestamp.
        """

        names = (
            self._register_inputs_norm
            or []
        )

        cid = (
            documents_ipfs_hash
            or ""
        )

        values: Dict[str, Any] = {

            "studentid": student_id,

            "name": name,

            "program": program,

            "year": int(year),

            "documentsipfshash": cid,

            "ipfshash": cid,

            "cid": cid,

            "contenthash": _hex_to_bytes32(
                content_hash
            ),

            "timestamp": int(
                timestamp or 0
            ),

            "studentwallet": (
                Web3.to_checksum_address(
                    student_wallet
                    or self.account.address
                )
            ),
        }

        args: List[Any] = []

        for name_key in names:

            if name_key not in values:

                raise TypeError(
                    "Unknown ABI input "
                    f"'{name_key}' for "
                    "registerStudent"
                )

            args.append(
                values[name_key]
            )

        if len(args) != len(names):

            raise TypeError(
                "registerStudent ABI expects "
                f"{len(names)} args {names}, "
                f"but client built "
                f"{len(args)} args {args}"
            )

        fn = (
            self.contract.functions
            .registerStudent(*args)
        )

        return self._send_fn(fn)

    # ============================================================
    # Existing Semester Function
    # ============================================================

    def add_semester_record(
        self,
        *,
        student_id: str,
        documents_ipfs_hash: str,
        content_hash: str,
        timestamp: int,
    ) -> str:
        """
        Append a semester record using the existing
        addSemesterRecord() contract function.

        The timestamp parameter is retained for compatibility.
        The current Solidity contract records block.timestamp.
        """

        names = (
            self._add_semester_inputs_norm
            or []
        )

        cid = documents_ipfs_hash

        values: Dict[str, Any] = {

            "studentid": student_id,

            "documentsipfshash": cid,

            "ipfshash": cid,

            "cid": cid,

            "contenthash": _hex_to_bytes32(
                content_hash
            ),

            "timestamp": int(
                timestamp
            ),
        }

        args: List[Any] = []

        for name_key in names:

            if name_key not in values:

                raise TypeError(
                    "Unknown ABI input "
                    f"'{name_key}' for "
                    "addSemesterRecord"
                )

            args.append(
                values[name_key]
            )

        if len(args) != len(names):

            raise TypeError(
                "addSemesterRecord ABI expects "
                f"{len(names)} args {names}, "
                f"but client built "
                f"{len(args)} args {args}"
            )

        fn = (
            self.contract.functions
            .addSemesterRecord(*args)
        )

        return self._send_fn(fn)

    # ============================================================
    # NEW DOCUMENT VERSIONING FUNCTIONS
    # ============================================================

    def create_document_version(
        self,
        *,
        document_id: str,
        student_id: str,
        document_type: str,
        ipfs_cid: str,
        content_hash: str,
        approval_status: str,
        approved_by: str,
    ) -> str:
        """
        Create a new immutable document version.

        The Solidity contract automatically determines:

            Version 1:
                previousVersion = 0

            Version 2:
                previousVersion = 1

            Version 3:
                previousVersion = 2

        The Python client does NOT determine the version number.
        The blockchain is the authoritative source.
        """

        if not document_id.strip():
            raise ValueError(
                "document_id cannot be empty"
            )

        if not student_id.strip():
            raise ValueError(
                "student_id cannot be empty"
            )

        if not document_type.strip():
            raise ValueError(
                "document_type cannot be empty"
            )

        if not ipfs_cid.strip():
            raise ValueError(
                "ipfs_cid cannot be empty"
            )

        if not approval_status.strip():
            raise ValueError(
                "approval_status cannot be empty"
            )

        names = (
            self._create_document_version_inputs_norm
            or []
        )

        values: Dict[str, Any] = {

            "documentid": document_id,

            "studentid": student_id,

            "documenttype": document_type,

            "ipfscid": ipfs_cid,

            "ipfshash": ipfs_cid,

            "cid": ipfs_cid,

            "contenthash": _hex_to_bytes32(
                content_hash
            ),

            "approvalstatus": approval_status,

            "approvedby": approved_by,
        }

        args: List[Any] = []

        for name_key in names:

            if name_key not in values:

                raise TypeError(
                    "Unknown ABI input "
                    f"'{name_key}' for "
                    "createDocumentVersion"
                )

            args.append(
                values[name_key]
            )

        if len(args) != len(names):

            raise TypeError(
                "createDocumentVersion ABI expects "
                f"{len(names)} args {names}, "
                f"but client built "
                f"{len(args)} args {args}"
            )

        fn = (
            self.contract.functions
            .createDocumentVersion(*args)
        )

        return self._send_fn(fn)

    # ============================================================
    # Document Existence
    # ============================================================

    def document_exists(
        self,
        document_id: str,
    ) -> bool:
        """
        Check whether a logical document exists
        on the blockchain.
        """

        try:

            return bool(
                self.contract.functions
                .documentExists(
                    document_id
                )
                .call()
            )

        except Exception as exc:

            raise RuntimeError(
                "Failed to check document existence "
                f"for '{document_id}': {exc}"
            ) from exc

    # ============================================================
    # Document Details
    # ============================================================

    def get_document_details(
        self,
        document_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve logical document metadata.

        Returns:

            {
                "document_id": ...,
                "student_id": ...,
                "document_type": ...,
                "latest_version": ...,
                "exists": ...
            }

        Returns None when the document does not exist.
        """

        try:

            data = (
                self.contract.functions
                .getDocumentDetails(
                    document_id
                )
                .call()
            )

        except Exception as exc:
            raise RuntimeError(
                "Failed to read document details for "
                f"'{document_id}' from blockchain: {exc}"
            ) from exc

        if isinstance(data, dict):

            return {
                "document_id": data.get(
                    "documentId",
                    data.get(
                        "document_id"
                    ),
                ),
                "student_id": data.get(
                    "studentId",
                    data.get(
                        "student_id"
                    ),
                ),
                "document_type": data.get(
                    "documentType",
                    data.get(
                        "document_type"
                    ),
                ),
                "latest_version": int(
                    data.get(
                        "latestVersion",
                        data.get(
                            "latest_version",
                            0,
                        ),
                    )
                ),
                "exists": bool(
                    data.get(
                        "exists",
                        False,
                    )
                ),
            }

        try:

            return {
                "document_id": data[0],
                "student_id": data[1],
                "document_type": data[2],
                "latest_version": int(
                    data[3]
                ),
                "exists": bool(
                    data[4]
                ),
            }

        except Exception as exc:

            raise RuntimeError(
                "Could not parse document details: "
                f"{exc}"
            ) from exc

    # ============================================================
    # Authoritative Document Resolution
    # ============================================================

    def resolve_authoritative_document(
        self,
        document_id: str,
        *,
        expected_student_id: Optional[str] = None,
        expected_document_type: Optional[str] = None,
        approved_only: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve the authoritative immutable document version.

        The blockchain is the sole source of truth for the logical
        document identity, version, CID, content hash, approval state,
        and provenance metadata. Repository metadata is never consulted.

        When ``approved_only`` is True, the most recent blockchain
        version whose approval status is exactly ``APPROVED`` is used.
        """

        normalized_id = str(document_id or "").strip()
        if not normalized_id:
            raise ValueError("document_id cannot be empty")

        details = self.get_document_details(normalized_id)
        if not details or not details.get("exists", False):
            return None

        blockchain_student_id = str(
            details.get("student_id", "") or ""
        ).strip()
        blockchain_document_type = str(
            details.get("document_type", "") or ""
        ).strip()

        if expected_student_id:
            expected_sid = str(expected_student_id).strip()
            if (
                blockchain_student_id
                and blockchain_student_id.upper() != expected_sid.upper()
            ):
                raise PermissionError(
                    "Blockchain document belongs to student "
                    f"{blockchain_student_id}, not {expected_sid}."
                )

        if expected_document_type:
            expected_type = str(expected_document_type).strip()
            if (
                blockchain_document_type
                and blockchain_document_type.casefold()
                != expected_type.casefold()
            ):
                raise ValueError(
                    "Blockchain document type is "
                    f"'{blockchain_document_type}', not '{expected_type}'."
                )

        # Prefer the dedicated Solidity view when available.  Some local ABI
        # artifacts can lag behind the deployed contract, so fall back to the
        # already-supported latest/history views instead of silently returning
        # None.
        if approved_only:
            version_data = self.get_latest_approved_document_version(
                normalized_id
            )

            if not version_data:
                latest = self.get_latest_document_version(normalized_id)
                if latest and str(latest.get("approval_status", "")).strip().upper() == "APPROVED":
                    version_data = latest

            if not version_data:
                try:
                    history = self.get_document_history(normalized_id)
                except Exception:
                    history = []

                for candidate in reversed(history):
                    if str(candidate.get("approval_status", "")).strip().upper() == "APPROVED":
                        version_data = candidate
                        break
        else:
            version_data = self.get_latest_document_version(
                normalized_id
            )

            if not version_data:
                try:
                    history = self.get_document_history(normalized_id)
                except Exception:
                    history = []
                if history:
                    version_data = history[-1]

        if not version_data:
            return None

        # Normalize the client-facing schema once, at the blockchain boundary.
        resolved = dict(version_data)
        resolved["document_id"] = normalized_id
        resolved["student_id"] = blockchain_student_id
        resolved["document_type"] = blockchain_document_type
        resolved["resolved_version"] = int(
            resolved.get("version", 0)
        )

        return resolved

    # ============================================================
    # Specific Document Version
    # ============================================================

    def get_document_version(
        self,
        document_id: str,
        version: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific immutable document version.
        """

        if version <= 0:
            raise ValueError(
                "version must be greater than 0"
            )

        try:

            data = (
                self.contract.functions
                .getDocumentVersion(
                    document_id,
                    int(version),
                )
                .call()
            )

        except Exception as exc:
            raise RuntimeError(
                "Failed to read blockchain document version for "
                f"'{document_id}' using get_document_version(): {exc}"
            ) from exc

        return self._normalize_document_version(
            data
        )

    # ============================================================
    # Latest Document Version
    # ============================================================

    def get_latest_document_version(
        self,
        document_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve the latest recorded version.

        Note:
            This may be an unapproved version.

        Use get_latest_approved_document_version()
        when only an approved version should be returned.
        """

        try:

            data = (
                self.contract.functions
                .getLatestDocumentVersion(
                    document_id
                )
                .call()
            )

        except Exception as exc:
            raise RuntimeError(
                "Failed to read blockchain document version for "
                f"'{document_id}' using get_latest_document_version(): {exc}"
            ) from exc

        return self._normalize_document_version(
            data
        )

    # ============================================================
    # Latest Approved Document Version
    # ============================================================

    def get_latest_approved_document_version(
        self,
        document_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve the most recent approved document version.

        The Solidity contract searches backwards from the
        latest version until it finds:

            approvalStatus == "APPROVED"
        """

        try:

            data = (
                self.contract.functions
                .getLatestApprovedDocumentVersion(
                    document_id
                )
                .call()
            )

        except Exception as exc:
            raise RuntimeError(
                "Failed to read blockchain document version for "
                f"'{document_id}' using get_latest_approved_document_version(): {exc}"
            ) from exc

        return self._normalize_document_version(
            data
        )

    # ============================================================
    # Complete Document History
    # ============================================================

    def get_document_history(
        self,
        document_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all immutable versions of a document.

        Example:

            [
                {
                    "version": 1,
                    "previous_version": 0,
                    ...
                },
                {
                    "version": 2,
                    "previous_version": 1,
                    ...
                },
                {
                    "version": 3,
                    "previous_version": 2,
                    ...
                }
            ]
        """

        try:

            history = (
                self.contract.functions
                .getDocumentHistory(
                    document_id
                )
                .call()
            )

        except Exception as exc:

            raise RuntimeError(
                "Failed to retrieve document history "
                f"for '{document_id}': {exc}"
            ) from exc

        result: List[
            Dict[str, Any]
        ] = []

        for version_data in history:

            result.append(
                self._normalize_document_version(
                    version_data
                )
            )

        return result

    # ============================================================
    # Document Version Normalization
    # ============================================================

    def _normalize_document_version(
        self,
        data: Any,
    ) -> Dict[str, Any]:
        """
        Normalize a DocumentVersion tuple returned by Web3.

        Solidity return order:

            version
            previousVersion
            ipfsCID
            contentHash
            approvalStatus
            approvedBy
            timestamp
        """

        if isinstance(data, dict):

            version = data.get(
                "version",
                0,
            )

            previous_version = data.get(
                "previousVersion",
                data.get(
                    "previous_version",
                    0,
                ),
            )

            ipfs_cid = data.get(
                "ipfsCID",
                data.get(
                    "ipfs_cid",
                    "",
                ),
            )

            content_hash = data.get(
                "contentHash",
                data.get(
                    "content_hash",
                    b"",
                ),
            )

            approval_status = data.get(
                "approvalStatus",
                data.get(
                    "approval_status",
                    "",
                ),
            )

            approved_by = data.get(
                "approvedBy",
                data.get(
                    "approved_by",
                    "",
                ),
            )

            timestamp = data.get(
                "timestamp",
                0,
            )

        else:

            try:

                version = data[0]

                previous_version = data[1]

                ipfs_cid = data[2]

                content_hash = data[3]

                approval_status = data[4]

                approved_by = data[5]

                timestamp = data[6]

            except Exception as exc:

                raise RuntimeError(
                    "Could not parse document version "
                    f"tuple: {exc}"
                ) from exc

        return {
            "version": int(version),

            "previous_version": int(
                previous_version
            ),

            "ipfs_cid": str(
                ipfs_cid
            ),

            "content_hash": (
                _bytes32_to_hex(
                    content_hash
                )
            ),

            "approval_status": str(
                approval_status
            ),

            "approved_by": str(
                approved_by
            ),

            "timestamp": int(
                timestamp
            ),
        }

    # ============================================================
    # Existing Student Reads
    # ============================================================

    def get_student_details(
        self,
        student_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve student details.

        Tries the explicit getter first, then the public
        students mapping.
        """

        for view_name in (
            "getStudent",
            "getStudentDetails",
        ):

            try:

                fn = getattr(
                    self.contract.functions,
                    view_name,
                )

                data = fn(
                    student_id
                ).call()

                return (
                    self._normalize_student_tuple(
                        data
                    )
                )

            except Exception:
                pass

        try:

            data = (
                self.contract.functions
                .students(
                    student_id
                )
                .call()
            )

            return (
                self._normalize_student_tuple(
                    data
                )
            )

        except Exception:

            return None

    def get_all_semester_hashes(
        self,
        student_id: str,
    ) -> List[str]:
        """
        Return all historical semester IPFS CIDs
        for a student.
        """

        try:

            cids = (
                self.contract.functions
                .getStudentSemesterHashes(
                    student_id
                )
                .call()
            )

            return [
                str(cid)
                for cid in cids
            ]

        except Exception:

            return []

    # ============================================================
    # Student Tuple Normalization
    # ============================================================

    def _normalize_student_tuple(
        self,
        data: Any,
    ) -> Dict[str, Any]:

        if isinstance(data, dict):
            return data

        result: Dict[str, Any] = {}

        try:

            result["student_id"] = data[0]

            result["name"] = data[1]

            result["program"] = data[2]

            result["year"] = int(
                data[3]
            )

            result[
                "documents_ipfs_hash"
            ] = data[4]

            result[
                "content_hash"
            ] = _bytes32_to_hex(
                data[5]
            )

            result["timestamp"] = int(
                data[6]
            )

            result["is_active"] = bool(
                data[7]
            )

            result["student_wallet"] = (
                data[8]
                if len(data) > 8
                else None
            )

        except Exception as exc:

            print(
                "[blockchain_client] Warning: "
                "could not fully parse student tuple: "
                f"{exc}. Partial data: {result}"
            )

        return result

    # ============================================================
    # Existing Letter Function
    # ============================================================

    def add_letter_record(
        self,
        *,
        student_id: str,
        documents_ipfs_hash: str,
        content_hash: str,
        timestamp: int,
    ) -> str:
        """
        Existing compatibility method.

        Currently stores the letter using addSemesterRecord().
        """

        print(
            "📝 Adding letter record for "
            f"{student_id} via addSemesterRecord()"
        )

        return self.add_semester_record(
            student_id=student_id,
            documents_ipfs_hash=documents_ipfs_hash,
            content_hash=content_hash,
            timestamp=timestamp,
        )


# End of python_backend/blockchain_client.py