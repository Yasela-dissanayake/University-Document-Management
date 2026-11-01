"""Unit tests for document-aware RBAC policy.

Run:
    python -m unittest python_backend.tests.test_document_access_policy
"""
import unittest

from python_backend import rbac


class DocumentAccessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.exam_user = {"id": 2, "username": "exam_officer", "role": "EXAM_DIVISION"}
        self.admin = {"id": 1, "username": "admin", "role": "ADMIN"}
        self.student = {"id": 7, "username": "STD001", "role": "STUDENT", "student_id": "STD001"}

    def test_exam_division_can_access_examination_result_sheet(self):
        self.assertTrue(rbac.can_view_document(
            self.exam_user, "STD001", "Examination Result Sheet"
        ))

    def test_exam_division_cannot_access_transcript(self):
        self.assertFalse(rbac.can_view_document(
            self.exam_user, "STD001", "Transcript"
        ))

    def test_exam_division_cannot_access_registration_certificate(self):
        self.assertFalse(rbac.can_view_document(
            self.exam_user, "STD001", "Registration Certificate"
        ))

    def test_exam_division_cannot_access_unknown_type(self):
        self.assertFalse(rbac.can_view_document(
            self.exam_user, "STD001", ""
        ))

    def test_existing_admin_access_is_preserved(self):
        self.assertTrue(rbac.can_view_document(
            self.admin, "STD001", "Transcript"
        ))

    def test_student_can_access_own_document(self):
        self.assertTrue(rbac.can_view_document(
            self.student, "STD001", "Transcript"
        ))

    def test_student_cannot_access_another_student(self):
        self.assertFalse(rbac.can_view_document(
            self.student, "STD002", "Examination Result Sheet"
        ))

    def test_exam_division_only_gets_provisional_explicit_lookup(self):
        self.assertTrue(rbac.can_attempt_document_lookup(
            self.exam_user, "document_retrieval"
        ))
        self.assertFalse(rbac.can_attempt_document_lookup(
            self.exam_user, "academic_document"
        ))
        self.assertFalse(rbac.can_attempt_document_lookup(
            self.exam_user, "general"
        ))


if __name__ == "__main__":
    unittest.main()