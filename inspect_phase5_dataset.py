
import json
from pathlib import Path


DATASET_DIR = Path("phase5_dataset")
FILES = [
    DATASET_DIR / "documents.json",
    DATASET_DIR / "metadata_records.json",
]


def load_json(path):
    if not path.is_file():
        print(f"\n[MISSING] {path}")
        return None

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    print(f"\n{'=' * 70}")
    print(f"FILE: {path}")
    print(f"{'=' * 70}")

    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        print("Top-level keys:", list(data.keys()))
        records = data.get("documents", data.get("records", []))
        if not isinstance(records, list):
            print("This file does not contain a recognized records list.")
            return data
    else:
        print("Unexpected JSON structure:", type(data).__name__)
        return data

    print("Record count:", len(records))

    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            print(f"Record {index}: unexpected type {type(record).__name__}")
            continue

        print(f"\nRecord {index}")
        print("Available fields:", sorted(record.keys()))

        # Print identifiers and metadata only, never document plaintext.
        for field in (
            "document_id",
            "student_id",
            "document_type",
            "repository",
            "latest_version",
            "latest_approved_version",
            "approval_status",
            "metadata_origin",
        ):
            if field in record:
                print(f"  {field}: {record[field]}")

        version_plan = record.get("version_plan")
        if isinstance(version_plan, list):
            print("  version_plan entries:", len(version_plan))
            for version in version_plan:
                if isinstance(version, dict):
                    print(
                        "   - version:",
                        version.get("version", version.get("version_number")),
                        "| approval:",
                        version.get("approval_status"),
                    )

    return records


def main():
    loaded = {}

    for path in FILES:
        loaded[path.name] = load_json(path)

    documents = loaded.get("documents.json")
    metadata = loaded.get("metadata_records.json")

    if isinstance(documents, list) and isinstance(metadata, list):
        document_ids = {
            str(item.get("document_id", "")).strip()
            for item in documents
            if isinstance(item, dict) and item.get("document_id")
        }
        metadata_ids = {
            str(item.get("document_id", "")).strip()
            for item in metadata
            if isinstance(item, dict) and item.get("document_id")
        }

        print(f"\n{'=' * 70}")
        print("DATASET CONSISTENCY SUMMARY")
        print(f"{'=' * 70}")
        print("Unique document IDs in documents.json:", len(document_ids))
        print("Unique document IDs in metadata_records.json:", len(metadata_ids))
        print("Documents missing from metadata:", sorted(document_ids - metadata_ids))
        print("Metadata without a matching document:", sorted(metadata_ids - document_ids))

    print(
        "\nNote: matching dataset files do not prove that records exist "
        "on the deployed blockchain."
    )


if __name__ == "__main__":
    main()
