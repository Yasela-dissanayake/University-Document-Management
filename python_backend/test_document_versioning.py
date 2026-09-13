from blockchain_client import UniversityBlockchainClient


def main():
    client = UniversityBlockchainClient()

    student_id = "STD001"
    document_id = "STD001-TRANSCRIPT"

    print("\n1. Checking student...")
    student = client.get_student_details(student_id)

    print(student)

    if not student:
        raise RuntimeError(
            f"Student {student_id} does not exist on the deployed contract."
        )

    print("\n2. Checking document...")
    exists = client.document_exists(document_id)

    print("Document exists:", exists)

    print("\n3. Creating Version 1...")

    tx1 = client.create_document_version(
        document_id=document_id,
        student_id=student_id,
        document_type="Transcript",
        ipfs_cid="bafy-test-version-1",
        content_hash=(
            "1111111111111111111111111111111111111111111111111111111111111111"
        ),
        approval_status="APPROVED",
        approved_by="Registrar",
    )

    print("Version 1 transaction:", tx1)

    print("\n4. Reading Version 1...")

    version1 = client.get_document_version(
        document_id,
        1,
    )

    print(version1)

    print("\n5. Creating Version 2...")

    tx2 = client.create_document_version(
        document_id=document_id,
        student_id=student_id,
        document_type="Transcript",
        ipfs_cid="bafy-test-version-2",
        content_hash=(
            "2222222222222222222222222222222222222222222222222222222222222222"
        ),
        approval_status="APPROVED",
        approved_by="Registrar",
    )

    print("Version 2 transaction:", tx2)

    print("\n6. Creating Version 3...")

    tx3 = client.create_document_version(
        document_id=document_id,
        student_id=student_id,
        document_type="Transcript",
        ipfs_cid="bafy-test-version-3",
        content_hash=(
            "3333333333333333333333333333333333333333333333333333333333333333"
        ),
        approval_status="APPROVED",
        approved_by="Registrar",
    )

    print("Version 3 transaction:", tx3)

    print("\n7. Reading latest version...")

    latest = client.get_latest_document_version(
        document_id
    )

    print(latest)

    print("\n8. Reading latest approved version...")

    latest_approved = (
        client.get_latest_approved_document_version(
            document_id
        )
    )

    print(latest_approved)

    print("\n9. Reading complete history...")

    history = client.get_document_history(
        document_id
    )

    for record in history:
        print(record)

    print("\n10. Reading document details...")

    details = client.get_document_details(
        document_id
    )

    print(details)


if __name__ == "__main__":
    main()