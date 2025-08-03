from typing import Dict, Any, List, Optional
from datetime import datetime
from web3 import Web3
import os
import json
import hashlib
from dotenv import load_dotenv

load_dotenv()

class UniversityBlockchainClient:
    def __init__(self):
        """Initialize blockchain client for university records"""
        self.network_url = os.getenv("WEB3_PROVIDER")
        self.w3 = Web3(Web3.HTTPProvider(self.network_url))
        if not self.w3.is_connected():
            raise ConnectionError(f"Failed to connect to blockchain network: {self.network_url}")
        self.contract_address = os.getenv("CONTRACT_ADDRESS")
        self.private_key = os.getenv("PRIVATE_KEY")
        if self.private_key:
            self.account = self.w3.eth.account.from_key(self.private_key)
        self.contract_abi = self._load_contract_abi()
        if self.contract_address and self.contract_abi:
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.contract_address),
                abi=self.contract_abi
            )
        print(f"✅ Connected to university blockchain at {self.network_url}")

    def _load_contract_abi(self):
        """Load contract ABI from compiled artifacts"""
        try:
            abi_path = os.path.join(os.path.dirname(__file__), "../artifacts/contracts/UniversityRegistry.sol/UniversityRegistry.json")
            with open(abi_path, 'r') as f:
                contract_data = json.load(f)
                return contract_data.get("abi", [])
        except FileNotFoundError:
            print("⚠️ ABI file not found. Contract interaction may be limited.")
            return []

    def register_student_record(self, student_data: Dict[str, Any], ipfs_hash: str, content_hash: str) -> str:
        """Register a new student record (first semester) on-chain"""
        try:
            if not self.contract:
                raise Exception("Contract not initialized")
            student_wallet = self.create_student_wallet(student_data["student_id"])
            function_call = self.contract.functions.registerStudent(
                student_data["student_id"],
                student_data["name"],
                student_data["program"],
                student_data["year"],
                ipfs_hash,
                Web3.keccak(text=content_hash),  # or bytes32, as your contract expects
                student_wallet["address"]
            )
            transaction = function_call.build_transaction({
                'chainId': self.w3.eth.chain_id,
                'gas': 500_000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(self.account.address)
            })
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"✅ Student {student_data['student_id']} registered on blockchain")
            return receipt['transactionHash'].hex()
        except Exception as e:
            print(f"❌ Failed to register student on blockchain: {e}")
            raise

    def add_semester_record(self, student_id: str, ipfs_hash: str, content_hash: str) -> str:
        """
        Appends a new semester's document to the student's record.
        Assumes the contract has public addSemesterRecord(string studentId, string ipfs_hash, bytes32 contentHash)
        """
        try:
            if not self.contract:
                raise Exception("Contract not initialized")
            function_call = self.contract.functions.addSemesterRecord(
                student_id,
                ipfs_hash,
                Web3.keccak(text=content_hash)  # or as your contract requires
            )
            transaction = function_call.build_transaction({
                'chainId': self.w3.eth.chain_id,
                'gas': 200_000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': self.w3.eth.get_transaction_count(self.account.address)
            })
            signed_txn = self.w3.eth.account.sign_transaction(transaction, private_key=self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"✅ Semester record appended for {student_id}")
            return receipt['transactionHash'].hex()
        except Exception as e:
            print(f"❌ Failed to add semester record: {e}")
            raise

    def get_all_semester_hashes(self, student_id: str) -> Optional[List[str]]:
        """Get all semester document IPFS hashes for a student (latest last)"""
        try:
            if not self.contract:
                return None
            # Assumes a contract view function `getStudentSemesterHashes(string studentId) returns (string[])`
            hashes = self.contract.functions.getStudentSemesterHashes(student_id).call()
            return hashes
        except Exception as e:
            print(f"❌ Error getting semester hashes: {e}")
            return None

    def get_student_details(self, student_id: str) -> Optional[Dict[str, Any]]:
        """Get student metadata (core info, latest doc pointer, etc.)"""
        try:
            if not self.contract:
                return None
            result = self.contract.functions.getStudentDetails(student_id).call()
            return {
                'student_id': result[0],
                'name': result[1],
                'program': result[2],
                'year': result[3],
                'documents_ipfs_hash': result[4],  # latest (or last) document hash
                'content_hash': result[5].hex(),
                'timestamp': result[6],
                'is_active': result[7]
            }
        except Exception as e:
            print(f"❌ Error getting student details: {e}")
            return None

    def create_student_wallet(self, student_id: str) -> Dict[str, str]:
        """Create a deterministic wallet for a student"""
        seed = hashlib.sha256(f"university_student_{student_id}".encode()).hexdigest()
        account = self.w3.eth.account.from_key(seed)
        return {
            'student_id': student_id,
            'address': account.address,
            'private_key': account._private_key.hex()
        }
