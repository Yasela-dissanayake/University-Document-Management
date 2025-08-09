# from ipfs_client import UniversityIPFSClient

# # Initialize your client and set the encryption key
# ipfs_client = UniversityIPFSClient()
# # If the key is not auto-set, assign it like this:
# # ipfs_client.cipher_suite = Fernet(b'your_saved_key_here')

# # IPFS hash from your test
# ipfs_hash = "Qmdzg6f2bimUJkswrniG8fsMhtoVLf1SCR6JEYpJKp9iMr"
# # ipfs_hash = "QmQ4GHTkoYkSgfjnRWPepG7HkYrzJHHk786umRr6SPrRBL"

# # Retrieve encrypted content from IPFS
# encrypted_content = ipfs_client.client.cat(ipfs_hash)

# # Decrypt and print original content
# decrypted_content = ipfs_client.decrypt_document(encrypted_content)
# print("Original document data:")
# print(decrypted_content)


# ipfs_tools.py
from python_backend.ipfs_client import UniversityIPFSClient

def decrypt_ipfs_document(ipfs_hash, key=None):
    ipfs_client = UniversityIPFSClient()
    # If using a custom key:
    # if key:
    #     ipfs_client.cipher_suite = Fernet(key)

    encrypted_content = ipfs_client.client.cat(ipfs_hash)
    decrypted_content = ipfs_client.decrypt_document(encrypted_content)
    return decrypted_content
