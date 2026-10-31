
import os
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

rpc_url = os.getenv("WEB3_PROVIDER")

if not rpc_url:
    raise ValueError("WEB3_PROVIDER is missing from your .env file")

web3 = Web3(Web3.HTTPProvider(rpc_url))

if not web3.is_connected():
    raise ConnectionError("Could not connect to Tenderly RPC")

print(f"Connected: {web3.is_connected()}")
print(f"Chain ID: {web3.eth.chain_id}")
print(f"Latest block number: {web3.eth.block_number}")

latest_block = web3.eth.get_block("latest")

print(f"Latest block hash: {latest_block['hash'].hex()}")
print(f"Latest block timestamp: {latest_block['timestamp']}")
