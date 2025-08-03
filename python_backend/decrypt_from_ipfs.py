from ipfs_client import UniversityIPFSClient

# Initialize your client and set the encryption key
ipfs_client = UniversityIPFSClient()
# If the key is not auto-set, assign it like this:
# ipfs_client.cipher_suite = Fernet(b'your_saved_key_here')

# IPFS hash from your test
ipfs_hash = "QmTJrhkq9LyXwDnngu9nFfyibvni61d6YQfx7YZ69P9B5Q"
# ipfs_hash = "QmQ4GHTkoYkSgfjnRWPepG7HkYrzJHHk786umRr6SPrRBL"

# Retrieve encrypted content from IPFS
encrypted_content = ipfs_client.client.cat(ipfs_hash)

# Decrypt and print original content
decrypted_content = ipfs_client.decrypt_document(encrypted_content)
print("Original document data:")
print(decrypted_content)
