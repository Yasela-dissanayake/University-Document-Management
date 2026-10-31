
import os
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

RPC_URL = os.getenv("WEB3_PROVIDER")
CONTRACT_ADDRESS = os.getenv(
    "CONTRACT_ADDRESS",
    "0x28234Bcb8625184d8bD1A3bdFbAa787CCd73e57D"
)

if not RPC_URL:
    raise ValueError("WEB3_PROVIDER is missing from .env")

web3 = Web3(Web3.HTTPProvider(RPC_URL))

if not web3.is_connected():
    raise ConnectionError("Could not connect to Tenderly RPC")

address = Web3.to_checksum_address(CONTRACT_ADDRESS)

latest_block = web3.eth.block_number

print("=" * 60)
print("TENDERLY CONTRACT DEPLOYMENT BLOCK DISCOVERY")
print("=" * 60)
print(f"Contract: {address}")
print(f"Chain ID: {web3.eth.chain_id}")
print(f"Latest block: {latest_block}")

# Confirm that the contract exists at the latest block.
latest_code = web3.eth.get_code(address, latest_block)

if not latest_code:
    raise RuntimeError(
        "No contract bytecode found at this address "
        "in the latest block."
    )

print(f"Contract bytecode found: {len(latest_code)} bytes")
print("\nSearching for the first block containing contract code...")

# Binary search for the first block with non-empty bytecode.
low = 0
high = latest_block
checks = 0

while low < high:
    mid = (low + high) // 2
    code = web3.eth.get_code(address, mid)
    checks += 1

    print(
        f"\rChecking block {mid:,} "
        f"({checks} checks)...",
        end="",
        flush=True
    )

    if code:
        high = mid
    else:
        low = mid + 1

deployment_block = low

print("\n")
print("=" * 60)
print("RESULT")
print("=" * 60)
print(f"First block containing contract code: {deployment_block:,}")
print(f"Latest block: {latest_block:,}")
print(f"Blocks to scan: {latest_block - deployment_block + 1:,}")
print(f"Binary search checks: {checks}")

block = web3.eth.get_block(deployment_block)

print(f"Deployment block hash: {block['hash'].hex()}")
print(f"Block timestamp: {block['timestamp']}")

print("\nUse this block in your discovery script:")
print(f"--from-block {deployment_block}")
