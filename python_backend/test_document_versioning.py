from blockchain_client import UniversityBlockchainClient


def main():
    client = UniversityBlockchainClient()

    student_id = "STD001"
    document_id = "TX-STD001-001"

    print("=" * 72)
    print("PHASE 5 BLOCKCHAIN VERSION VERIFICATION")
    print("=" * 72)

    # ------------------------------------------------------------------
    # 1. Check student
    # ------------------------------------------------------------------
    print("\n1. Checking student...")

    student = client.get_student_details(student_id)

    print(student)

    if not student:
        raise RuntimeError(
            f"Student {student_id} does not exist on the deployed contract."
        )

    print("PASS: Student exists.")

    # ------------------------------------------------------------------
    # 2. Check document
    # ------------------------------------------------------------------
    print("\n2. Checking document...")

    exists = client.document_exists(document_id)

    print("Document exists:", exists)

    if not exists:
        raise RuntimeError(
            f"Document {document_id} does not exist on the deployed contract."
        )

    print("PASS: Document exists.")

    # ------------------------------------------------------------------
    # 3. Read document details
    # ------------------------------------------------------------------
    print("\n3. Reading document details...")

    details = client.get_document_details(document_id)

    print(details)

    expected_latest_version = details["latest_version"]

    if expected_latest_version < 1:
        raise RuntimeError(
            f"Invalid latest version: {expected_latest_version}"
        )

    print(
        f"PASS: Blockchain reports latest version = "
        f"{expected_latest_version}"
    )

    # ------------------------------------------------------------------
    # 4. Read latest version
    # ------------------------------------------------------------------
    print("\n4. Reading latest version...")

    latest = client.get_latest_document_version(document_id)

    print(latest)

    if latest["version"] != expected_latest_version:
        raise RuntimeError(
            "Latest-version mismatch: "
            f"document details says V{expected_latest_version}, "
            f"but get_latest_document_version() returned "
            f"V{latest['version']}."
        )

    print("PASS: Latest version is consistent.")

    # ------------------------------------------------------------------
    # 5. Read latest approved version
    # ------------------------------------------------------------------
    print("\n5. Reading latest approved version...")

    latest_approved = client.get_latest_approved_document_version(
        document_id
    )

    print(latest_approved)

    if latest_approved["version"] > expected_latest_version:
        raise RuntimeError(
            "Invalid approved version: approved version is newer "
            "than the latest document version."
        )

    print("PASS: Latest approved version is valid.")

    # ------------------------------------------------------------------
    # 6. Read complete history
    # ------------------------------------------------------------------
    print("\n6. Reading complete version history...")

    history = client.get_document_history(document_id)

    if not history:
        raise RuntimeError(
            f"No version history found for document {document_id}."
        )

    for record in history:
        print(record)

    # ------------------------------------------------------------------
    # 7. Validate version sequence
    # ------------------------------------------------------------------
    print("\n7. Validating version chain...")

    expected_version = 1

    for record in history:
        version = record["version"]
        previous_version = record["previous_version"]

        print(
            f"V{version}: previous_version={previous_version}"
        )

        if version != expected_version:
            raise RuntimeError(
                "Version sequence error: "
                f"expected V{expected_version}, found V{version}."
            )

        expected_previous_version = version - 1

        if previous_version != expected_previous_version:
            raise RuntimeError(
                f"Invalid previous_version for V{version}: "
                f"expected {expected_previous_version}, "
                f"found {previous_version}."
            )

        expected_version += 1

    history_latest_version = expected_version - 1

    if history_latest_version != expected_latest_version:
        raise RuntimeError(
            "History/latest mismatch: "
            f"history ends at V{history_latest_version}, "
            f"but document details reports V{expected_latest_version}."
        )

    print("PASS: Version chain is sequential and consistent.")

    # ------------------------------------------------------------------
    # 8. Verify Phase 5 V1/V2 data
    # ------------------------------------------------------------------
    print("\n8. Checking Phase 5 seeded versions...")

    phase5_versions = {
        1: {
            "ipfs_cid": (
                "QmR1cscZnmu99u2k1fdBgWcmsxgiJR4Cp2rrMm7dwXrvoL"
            ),
            "approval_status": "APPROVED",
            "approved_by": "Phase5 Seeder",
        },
        2: {
            "ipfs_cid": (
                "QmX4S5M8pdiAMZPPdpPzF8rWZAcFtzsifNE35c8N4PGYj6"
            ),
            "approval_status": "APPROVED",
            "approved_by": "Phase5 Seeder",
        },
    }

    for version_number, expected in phase5_versions.items():
        record = client.get_document_version(
            document_id,
            version_number,
        )

        print(f"\nV{version_number}:")
        print(record)

        if record["ipfs_cid"] != expected["ipfs_cid"]:
            raise RuntimeError(
                f"V{version_number} CID mismatch:\n"
                f"Expected: {expected['ipfs_cid']}\n"
                f"Actual:   {record['ipfs_cid']}"
            )

        if record["approval_status"] != expected["approval_status"]:
            raise RuntimeError(
                f"V{version_number} approval status mismatch:\n"
                f"Expected: {expected['approval_status']}\n"
                f"Actual:   {record['approval_status']}"
            )

        if record["approved_by"] != expected["approved_by"]:
            raise RuntimeError(
                f"V{version_number} approved_by mismatch:\n"
                f"Expected: {expected['approved_by']}\n"
                f"Actual:   {record['approved_by']}"
            )

    print("PASS: Phase 5 V1/V2 records match the seeded data.")

    # ------------------------------------------------------------------
    # 9. Final summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("RESULT: BLOCKCHAIN VERSION VERIFICATION PASSED")
    print("=" * 72)

    print(f"Document:       {document_id}")
    print(f"Student:        {student_id}")
    print(f"Latest version: V{expected_latest_version}")
    print(f"History records: {len(history)}")
    print("Version chain:  VALID")
    print("Phase 5 V1/V2:  VERIFIED")
    print("=" * 72)


if __name__ == "__main__":
    main()