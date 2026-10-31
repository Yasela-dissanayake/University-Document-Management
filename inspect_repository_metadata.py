
import json

from python_backend.ai.repository_metadata import get_all_metadata


def main():
    print("Loading repository metadata...")

    records = get_all_metadata()

    print(f"\nTotal metadata records: {len(records)}\n")

    if not records:
        print("No metadata records were returned.")
        print(
            "Check your .env configuration, blockchain connection, "
            "and whether any student registration records exist."
        )
        return

    for index, record in enumerate(records, start=1):
        print(f"Record {index}")
        print(json.dumps(record, indent=2, ensure_ascii=False))
        print("-" * 70)

    print("\nMetadata source summary:")

    origins = {}
    repositories = {}

    for record in records:
        origin = record.get("metadata_origin", "unknown")
        repository = record.get("repository", "unknown")

        origins[origin] = origins.get(origin, 0) + 1
        repositories[repository] = repositories.get(repository, 0) + 1

    print("\nRecords by source:")
    print(json.dumps(origins, indent=2))

    print("\nRecords by repository:")
    print(json.dumps(repositories, indent=2))


if __name__ == "__main__":
    main()
