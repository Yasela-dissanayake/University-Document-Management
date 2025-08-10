from blockchain_client import UniversityBlockchainClient

client = UniversityBlockchainClient()
student = client.get_student_details("S19357")
if student:
    for k, v in student.items():
        print(f"{k}: {v}")
else:
    print("No on-chain data for student S19357.")
