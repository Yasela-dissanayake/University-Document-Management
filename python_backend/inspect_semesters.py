from blockchain_client import UniversityBlockchainClient

client = UniversityBlockchainClient()
hashes = client.get_all_semester_hashes("S20841")
print("All semester IPFS hashes for S20841:", hashes)
