from web3 import Web3
import os
from dotenv import load_dotenv

def test_web3_connection():
    # Load environment variables
    load_dotenv()
    
    # Get provider URL
    provider_url = os.getenv('WEB3_PROVIDER')
    contract_address = os.getenv('CONTRACT_ADDRESS')
    
    print(f"Testing Web3 connection with provider: {provider_url}")
    
    try:
        # Initialize Web3
        w3 = Web3(Web3.HTTPProvider(provider_url))
        
        # Test connection
        if w3.is_connected():
            print("✅ Successfully connected to the Web3 provider")
            
            # Get network info
            chain_id = w3.eth.chain_id
            print(f"✅ Connected to network with Chain ID: {chain_id}")
            
            # Check if contract exists
            code = w3.eth.get_code(contract_address)
            if len(code) > 0:
                print(f"✅ Contract exists at {contract_address}")
                print(f"Contract code size: {len(code)} bytes")
            else:
                print(f"❌ No contract found at {contract_address}")
        else:
            print("❌ Failed to connect to Web3 provider")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    test_web3_connection()
