import json
import tempfile
import unittest
from pathlib import Path

from review_core import DEFAULT_DATASET, DEFAULT_ISSUES, DEFAULT_SCHEMA, ReviewError, ReviewStore


REVIEWER = {
    "full_name": "Synthetic Reviewer",
    "professional_role": "Clinical reviewer",
    "clinical_specialty": "Test specialty",
    "organization": "Synthetic institution",
    "registration_reference": "TEST-ONLY",
    "declared_qualifications": "Self-declared test qualification",
    "scope_of_review": "All synthetic QA findings",
    "reviewer_comments": "Automated test identity; not a real sign-off.",
    "self_declaration_acknowledged": True,
}


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_default_dataset_validates(self):
        result, schema = self.store.technical_validation()
        self.assertTrue(result.passed, result.as_dict())
        self.assertIn("HC0001", schema["properties"])

    def test_provisional_freeze_allowed_before_review(self):
        package, manifest = self.store.freeze("provisional")
        self.assertTrue(package.exists())
        self.assertEqual("Provisionally frozen", manifest["dataset_freeze"])
        self.assertFalse(manifest["approved_for_downstream_narrative_generation"])

    def test_approved_freeze_blocked_without_human_approval(self):
        with self.assertRaises(ReviewError):
            self.store.freeze("approved")

    def test_correction_versions_and_stales_decisions(self):
        issue = self.store.issues()[0]
        pointer = issue["json_field_path"]
        original = self.store._get_pointer(self.store.dataset(), pointer)
        replacement = json.dumps(original)
        self.store.record_issue_decision(issue["issue_id"], "Request correction", "Test rationale", replacement, REVIEWER)
        self.store.apply_requested_correction(issue["issue_id"], REVIEWER, True)
        version, _ = self.store.dataset_identity()
        self.assertEqual(2, version)
        self.assertEqual({}, self.store.latest_decisions())

    def test_complete_human_review_allows_hash_bound_approved_freeze(self):
        for issue in self.store.issues():
            self.store.record_issue_decision(
                issue["issue_id"],
                "Approve existing information",
                "Explicit synthetic-test acceptance rationale.",
                "",
                REVIEWER,
            )
        approval = self.store.record_clinical_approval(
            "Clinically Approved",
            "",
            REVIEWER,
            True,
        )
        package, manifest = self.store.freeze("approved")
        self.assertTrue(package.exists())
        self.assertEqual(approval["dataset_sha256"], manifest["approval_dataset_sha256"])
        self.assertEqual("Approved frozen", manifest["dataset_freeze"])
        self.assertTrue(manifest["approved_for_downstream_narrative_generation"])

    def test_uploaded_bundle_is_archived_and_activated_as_new_version(self):
        report = self.store.import_reference_bundle(
            DEFAULT_DATASET,
            DEFAULT_SCHEMA,
            DEFAULT_ISSUES,
            True,
        )
        version, checksum = self.store.dataset_identity()
        self.assertEqual(2, version)
        self.assertEqual(report["dataset_sha256"], checksum)
        self.assertTrue((self.store.uploads / report["bundle_id"] / "upload_report.json").exists())
        self.assertEqual({}, self.store.latest_decisions())

    def test_uploaded_bundle_requires_explicit_activation_confirmation(self):
        with self.assertRaises(ReviewError):
            self.store.import_reference_bundle(DEFAULT_DATASET, DEFAULT_SCHEMA, DEFAULT_ISSUES, False)

    def test_issue_view_includes_current_value_context_and_dataset_identity(self):
        view = self.store.issue_view("QA-001")
        self.assertEqual(["evening", "morning"], view["current_dataset_value"])
        self.assertEqual("Metformin", view["current_dataset_context"]["name"])
        self.assertEqual(1, view["dataset_under_review"]["version"])
        self.assertEqual(self.store.dataset_identity()[1], view["dataset_under_review"]["sha256"])

    def test_reused_issue_id_does_not_leak_review_history_between_bundles(self):
        old = self.store.record_issue_decision(
            "QA-002",
            "Approve existing information",
            "Old metformin rationale that belongs only to the original QA-002.",
            "",
            REVIEWER,
        )
        dataset_file = Path(self.temp.name) / "uploaded_dataset.json"
        schema_file = Path(self.temp.name) / "uploaded_schema.json"
        issues_file = Path(self.temp.name) / "uploaded_issues.json"
        dataset_file.write_text(DEFAULT_DATASET.read_text(encoding="utf-8"), encoding="utf-8")
        schema_file.write_text(DEFAULT_SCHEMA.read_text(encoding="utf-8"), encoding="utf-8")
        issues = json.loads(DEFAULT_ISSUES.read_text(encoding="utf-8"))
        replacement_issue = next(issue for issue in issues if issue["issue_id"] == "QA-002")
        replacement_issue["patient_id"] = "HC0006"
        replacement_issue["json_field_path"] = "/HC0006/medication_management/medications/0"
        issues_file.write_text(json.dumps(issues), encoding="utf-8")

        self.store.import_reference_bundle(dataset_file, schema_file, issues_file, True)
        view = self.store.issue_view("QA-002")
        self.assertEqual("HC0006", view["patient_id"])
        self.assertEqual([], view["previous_reviewer_comments"])
        self.assertEqual("Awaiting human decision for current dataset", view["current_resolution_status"])

        new = self.store.record_issue_decision(
            "QA-002",
            "Request additional information",
            "New naproxen rationale for the active uploaded bundle.",
            "",
            REVIEWER,
        )
        self.assertIsNone(new["previous_decision_reference"])
        self.assertNotEqual(old["decision_id"], new["previous_decision_reference"])
        self.assertEqual(
            ["New naproxen rationale for the active uploaded bundle."],
            self.store.issue_view("QA-002")["previous_reviewer_comments"],
        )

    def test_only_reviewer_name_and_role_are_required(self):
        minimal = {
            "full_name": "Synthetic Reviewer",
            "professional_role": "Clinical reviewer",
        }
        record = self.store.record_issue_decision(
            "QA-003",
            "Approve existing information",
            "Synthetic test acceptance.",
            "",
            minimal,
        )
        self.assertEqual("Synthetic Reviewer", record["reviewer"]["full_name"])
        self.assertIsNone(record["reviewer"]["declared_qualifications"])
        self.assertEqual("Not provided", record["reviewer"]["qualification_status"])


if __name__ == "__main__":
    unittest.main()
