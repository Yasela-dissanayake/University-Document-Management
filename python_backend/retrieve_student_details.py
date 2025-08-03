from blockchain_client import UniversityBlockchainClient

def main():
    blockchain_client = UniversityBlockchainClient()
    student_id = "S20841"
    student_details = blockchain_client.get_student_details(student_id)
    if student_details:
        print(f"✅ Student details retrieved: {student_details}")
    else:
        print(f"❌ No details found for student ID: {student_id}")

if __name__ == "__main__":
    main()