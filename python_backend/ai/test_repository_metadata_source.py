"""Unit tests for explicit metadata-source selection.

Run from the repository root:
    python -m unittest python_backend.ai.test_repository_metadata_source
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from python_backend.ai import repository_metadata as metadata


class MetadataSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("METADATA_REGISTRY_PATH", None)
        os.environ.pop("METADATA_SOURCE", None)

    def tearDown(self) -> None:
        self.env.stop()

    def test_demo_source_requires_explicit_opt_in(self) -> None:
        os.environ["METADATA_SOURCE"] = "demo"
        records = metadata.load_metadata_records()
        self.assertTrue(records)
        self.assertTrue(all(r.get("metadata_origin") == "demo_fixture" for r in records))

    def test_default_source_uses_blockchain_not_demo(self) -> None:
        expected = [{
            "document_id": "STUDENT-S123-REGISTRATION",
            "student_id": "S123",
            "document_type": "Student Registration Record",
            "semester": None,
            "course_codes": [],
            "keywords": ["registration"],
            "repository": "registrar",
            "latest_version": 0,
            "latest_approved_cid": "",
            "latest_cid": "bafy-example",
            "metadata_origin": "blockchain_student_registry",
            "approval_status": "NOT_TRACKED",
        }]
        with patch.object(metadata, "load_blockchain_metadata_records", return_value=expected) as loader:
            records = metadata.load_metadata_records()
        loader.assert_called_once_with()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["metadata_origin"], "blockchain_student_registry")
        self.assertEqual(records[0]["latest_cid"], "bafy-example")
        self.assertEqual(records[0]["latest_approved_cid"], "")

    def test_json_mode_requires_snapshot_path(self) -> None:
        os.environ["METADATA_SOURCE"] = "json"
        with self.assertRaisesRegex(RuntimeError, "requires METADATA_REGISTRY_PATH"):
            metadata.load_metadata_records()

    def test_unknown_source_fails_instead_of_falling_back(self) -> None:
        os.environ["METADATA_SOURCE"] = "typo"
        with self.assertRaisesRegex(ValueError, "Unsupported METADATA_SOURCE"):
            metadata.load_metadata_records()

    def test_explicit_records_override_environment_source(self) -> None:
        os.environ["METADATA_SOURCE"] = "invalid"
        records = [{
            "document_id": "DOC-1",
            "student_id": "S1",
            "document_type": "Transcript",
            "repository": "academic",
        }]
        loaded = metadata.load_metadata_records(records)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["document_id"], "DOC-1")


if __name__ == "__main__":
    unittest.main()
