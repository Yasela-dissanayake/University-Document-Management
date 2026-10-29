# Metadata-only Repository RAG benchmark

## What is the metadata source?

The default is the deployed `UniversityRegistry` contract via `WEB3_PROVIDER`
and `CONTRACT_ADDRESS`. The adapter enumerates `studentIds` and reads the
student registration getter. It does not read IPFS or decrypt anything.

There is an important limitation in the current Solidity contract:
`documents` is a mapping without an enumerable document-ID list. The
`DocumentVersionCreated` event indexes document and student IDs as strings;
for indexed dynamic strings, the event topic contains a hash, not the original
string. Therefore the current contract cannot be used to enumerate all
`createDocumentVersion` records through a simple read-only scan. For complete
versioned-document metadata, use a trusted off-chain indexer/snapshot that
observes the contract's document-version creation and update workflow. Do not
pretend registration records cover all document types.

## Configure source

The root `.env` is loaded by the adapter when `python-dotenv` is installed.
Alternatively, export the variables in your shell.

- Default: `METADATA_SOURCE=blockchain`
- JSON snapshot: set `METADATA_REGISTRY_PATH=/absolute/path/to/metadata_records.json`.
  A configured path takes precedence over `METADATA_SOURCE`.
- Development fixtures: `METADATA_SOURCE=demo` (explicit opt-in only).

The JSON file may be a list of records or an object with a `documents` list.
Each record contains metadata only. Never place document plaintext, decryption
keys, or encrypted file contents in this registry. Keep student identifiers
and reports access-controlled; use an approved/anonymized evaluation dataset
where possible.

## Build and verify the benchmark

From the repository root, using the project's Python environment:

```bash
python -m python_backend.ai.test_repository_metadata_source
python -m python_backend.ai.evaluate_repository_rag --prepare-benchmark
```

This writes provisional query cases to `python_backend/ai/rag_benchmark.json`
using the configured source. The proposed relevant IDs are only candidate
labels. Review each query and independently confirm the complete set of
relevant documents. Correct queries and IDs as needed, then set:

```json
"ground_truth_status": "verified"
```

Only set that value after independent review. Run the evaluation:

```bash
python -m python_backend.ai.evaluate_repository_rag
```

The evaluator refuses unverified labels by default. You may run
`--allow-unverified-ground-truth` for debugging, but the report is explicitly
marked `diagnostic_only` and must not be presented as final retrieval quality.

The evaluator writes `rag_evaluation_report.json` by default. Keep this report
local if it contains real student identifiers or other sensitive metadata.
The benchmark measures metadata retrieval only; it does not establish
authorization correctness, blockchain version resolution, IPFS availability,
decryption success, or answer quality.
