from blockchain_client import UniversityBlockchainClient

client = UniversityBlockchainClient()
hashes = client.get_all_semester_hashes("S19357")
print("All semester IPFS hashes for S19357:", hashes)
