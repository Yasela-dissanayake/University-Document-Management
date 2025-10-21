# python_backend/blockchain_client.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from web3 import Web3, HTTPProvider
from eth_account import Account
from eth_account.signers.local import LocalAccount

# Try to load .env if available (does nothing if python-dotenv not installed)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass


def _load_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _load_abi() -> List[Dict[str, Any]]:
    """
    Find the UniversityRegistry ABI in common locations or via env var.
    Returns the ABI list directly.
    """
    here = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(here, ".."))
    candidates = [
        os.path.join(here, "artifacts", "contracts", "UniversityRegistry.sol", "UniversityRegistry.json"),
        os.path.join(repo_root, "artifacts", "contracts", "UniversityRegistry.sol", "UniversityRegistry.json"),
        os.getenv("ABI_JSON_PATH") or "",
    ]
    tried: List[str] = []
    for p in candidates:
        if not p:
            continue
        p = os.path.abspath(p)
        tried.append(p)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Hardhat artifact JSON has {"abi": [...], "bytecode": "...", ...}
            if isinstance(data, dict) and "abi" in data:
                return data["abi"]  # type: ignore
            # Some tools export ABI array directly
            if isinstance(data, list):
                return data  # type: ignore
    raise FileNotFoundError("ABI artifact not found. Tried:\n" + "\n".join(tried))


def _hex_to_bytes32(x: Optional[str]) -> bytes:
    """Return exactly 32 bytes; empty -> 32 zero bytes."""
    if not x:
        return b"\x00" * 32
    s = x.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    b = bytes.fromhex(s) if s else b""
    if len(b) >= 32:
        return b[:32]
    return b + (b"\x00" * (32 - len(b)))


def _norm_name(n: str) -> str:
    """Normalize ABI input names: strip leading underscores and lowercase."""
    return n.lstrip("_").lower()


class UniversityBlockchainClient:
    """
    Thin helper for your UniversityRegistry contract.

    Env:
      - WEB3_PROVIDER: RPC URL
      - CONTRACT_ADDRESS: deployed UniversityRegistry
      - PRIVATE_KEY: service wallet key (gas payer)
      - (optional) ABI_JSON_PATH: absolute path to UniversityRegistry.json
    """

    def __init__(self) -> None:
        self.rpc_url: str = _load_env("WEB3_PROVIDER")
        self.contract_address: str = Web3.to_checksum_address(_load_env("CONTRACT_ADDRESS"))
        pk = _load_env("PRIVATE_KEY")

        self.w3: Web3 = Web3(HTTPProvider(self.rpc_url))
        self.account: LocalAccount = Account.from_key(pk)
        self.chain_id: int = self.w3.eth.chain_id

        abi = _load_abi()
        self.contract = self.w3.eth.contract(address=self.contract_address, abi=abi)

        print(f"✅ Connected to university blockchain at {self.rpc_url}")

        # Cache normalized input-name lists for functions we care about
        self._register_inputs_norm: List[str] = self._get_fn_inputs_norm("registerStudent")
        self._add_semester_inputs_norm: List[str] = self._get_fn_inputs_norm("addSemesterRecord")
        # Debug prints (optional):
        print(f"[ABI] registerStudent inputs: {self._register_inputs_norm}")
        print(f"[ABI] addSemesterRecord inputs: {self._add_semester_inputs_norm}")

    # ---------- ABI helpers ----------

    def _get_fn_inputs_norm(self, fn_name: str) -> List[str]:
        """
        Return normalized input names (lowercased, underscore stripped)
        for the *first* ABI entry with the given function name.
        If multiple overloads exist, picks the first.
        """
        inputs: List[str] = []
        try:
            # Scan ABI directly to avoid get_function_by_name overload issues.
            for item in self.contract.abi:
                if item.get("type") == "function" and item.get("name") == fn_name:
                    ins = item.get("inputs", []) or []
                    inputs = [_norm_name(i.get("name", "")) for i in ins]
                    break
        except Exception:
            inputs = []
        return inputs

    # ---------- gas helpers ----------

    def _supports_eip1559(self) -> bool:
        try:
            latest = self.w3.eth.get_block("latest")
            return "baseFeePerGas" in latest and latest["baseFeePerGas"] is not None
        except Exception:
            return False

    def _gas_price_fields(self) -> Dict[str, int]:
        if self._supports_eip1559():
            latest = self.w3.eth.get_block("latest")
            base = int(latest.get("baseFeePerGas", self.w3.to_wei(1, "gwei")))
            try:
                tip = int(self.w3.eth.max_priority_fee)  # type: ignore[attr-defined]
            except Exception:
                tip = self.w3.to_wei(2, "gwei")
            return {
                "maxFeePerGas": int(base * 2 + tip),
                "maxPriorityFeePerGas": tip,
            }
        else:
            return {"gasPrice": self.w3.eth.gas_price}

    def _estimate_gas(self, fn, tx_from: str, value: int = 0) -> int:
        try:
            est = fn.estimate_gas({"from": tx_from, "value": value})
        except Exception:
            est = 600_000
        return int(est * 1.5)

    def _send_fn(self, fn, value: int = 0) -> str:
        tx_from = self.account.address
        gas = self._estimate_gas(fn, tx_from, value=value)
        gas_fields = self._gas_price_fields()
        nonce = self.w3.eth.get_transaction_count(tx_from)

        tx = fn.build_transaction(
            {
                "from": tx_from,
                "chainId": self.chain_id,
                "nonce": nonce,
                "gas": gas,
                **gas_fields,
                "value": value,
            }
        )
        signed = self.account.sign_transaction(tx)

        # Support both eth-account variants:
        raw = getattr(signed, "rawTransaction", None)
        if raw is None:
            raw = getattr(signed, "raw_transaction", None)
        if raw is None:
            # Very defensive fallback (rarely needed)
            try:
                raw = signed.raw  # type: ignore[attr-defined]
            except Exception as e:
                raise RuntimeError(f"Cannot extract raw tx bytes from SignedTransaction: {e}")

        tx_hash = self.w3.eth.send_raw_transaction(raw)
        # web3 returns HexBytes; normalize to hex string
        return tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)

    # ---------- writes ----------

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
        Register a new student. Supports contract variants:
          - id,name,program,year,docCID,bytes32,timestamp
          - id,name,program,year,docCID,bytes32,studentWallet
          - id,name,program,year,docCID,bytes32,timestamp,studentWallet
          - id,name,program,year,docCID,bytes32
        We build args by ABI input names, normalized (leading '_', case ignored).
        """
        names = self._register_inputs_norm or []

        # Prepare values by normalized key (include aliases for CID)
        cid = documents_ipfs_hash or ""
        values: Dict[str, Any] = {
            "studentid": student_id,
            "name": name,
            "program": program,
            "year": int(year),
            "documentsipfshash": cid,                            # common
            "ipfshash": cid,                                     # alias
            "cid": cid,                                          # alias
            "contenthash": _hex_to_bytes32(content_hash),
            "timestamp": int(timestamp or 0),
            "studentwallet": Web3.to_checksum_address(student_wallet or self.account.address),
        }

        # Assemble args in the order defined by the ABI we detected
        args: List[Any] = []
        for n in names:
            if n not in values:
                raise TypeError(f"Unknown ABI input '{n}' for registerStudent")
            args.append(values[n])

        if len(args) != len(names):
            raise TypeError(
                f"registerStudent ABI expects {len(names)} args {names}, but client built {len(args)} args {args}"
            )

        fn = self.contract.functions.registerStudent(*args)
        return self._send_fn(fn)

    def add_semester_record(
        self,
        *,
        student_id: str,
        documents_ipfs_hash: str,
        content_hash: str,
        timestamp: int,
    ) -> str:
        """
        Append a new semester/version. Supports variants:
          - addSemesterRecord(id, docCID, bytes32, timestamp)
          - addSemesterRecord(id, docCID, bytes32)
        Built by ABI name order. Accepts CID name aliases (_documentsIPFSHash, _ipfsHash, _cid).
        """
        names = self._add_semester_inputs_norm or []

        cid = documents_ipfs_hash
        values: Dict[str, Any] = {
            "studentid": student_id,
            "documentsipfshash": cid,                             # common
            "ipfshash": cid,                                      # alias
            "cid": cid,                                           # alias
            "contenthash": _hex_to_bytes32(content_hash),
            "timestamp": int(timestamp),
        }

        args: List[Any] = []
        for n in names:
            if n not in values:
                raise TypeError(f"Unknown ABI input '{n}' for addSemesterRecord")
            args.append(values[n])

        if len(args) != len(names):
            raise TypeError(
                f"addSemesterRecord ABI expects {len(names)} args {names}, but client built {len(args)} args {args}"
            )

        fn = self.contract.functions.addSemesterRecord(*args)
        return self._send_fn(fn)

    # ---------- reads ----------

    def get_student_details(self, student_id: str) -> Optional[Dict[str, Any]]:
        """
        Try helper getters first; fall back to mapping getter.
        Returns a dict or None if not found.
        """
        # Preferred helpers if your contract exposes them
        for view_name in ("getStudent", "getStudentDetails"):
            try:
                fn = getattr(self.contract.functions, view_name)
                data = fn(student_id).call()
                return self._normalize_student_tuple(data)
            except Exception:
                pass

        # Fallback to public mapping getter
        try:
            data = self.contract.functions.students(student_id).call()
            return self._normalize_student_tuple(data)
        except Exception:
            return None

    def get_all_semester_hashes(self, student_id: str) -> Optional[List[str]]:
        """
        Returns list of CIDs via getStudentSemesterHashes if available.
        """
        try:
            cids = self.contract.functions.getStudentSemesterHashes(student_id).call()
            return [str(x) for x in cids]
        except Exception:
            return None

    # ---------- normalization ----------

    def _normalize_student_tuple(self, t: Any) -> Dict[str, Any]:
        """
        Convert tuple returned by contract into a dict with stable keys.
        Order assumed per your contract's StudentRecord.
        """
        if isinstance(t, dict):
            return t

        d: Dict[str, Any] = {}
        try:
            d["student_id"] = t[0]
            d["name"] = t[1]
            d["program"] = t[2]
            d["year"] = int(t[3])
            d["documents_ipfs_hash"] = t[4]
            # bytes32 -> hex
            d["content_hash"] = t[5].hex() if isinstance(t[5], (bytes, bytearray)) else str(t[5])
            d["timestamp"] = int(t[6])
            d["is_active"] = bool(t[7])
            d["student_wallet"] = t[8] if len(t) > 8 else None
        except Exception:
            # best-effort fallback
            pass
        return d

    def add_letter_record(
        self,
        *,
        student_id: str,
        documents_ipfs_hash: str,
        content_hash: str,
        timestamp: int,
    ) -> str:
        """
        Reuse addSemesterRecord() to store letter documents.
        This creates a new document version on-chain tagged as a 'letter'.
        """
        print(f"📝 Adding letter record for {student_id} via addSemesterRecord()")
        return self.add_semester_record(
            student_id=student_id,
            documents_ipfs_hash=documents_ipfs_hash,
            content_hash=content_hash,
            timestamp=timestamp,
        )
# End of python_backend/blockchain_client.py