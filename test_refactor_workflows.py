import json
import shutil
import tempfile
import unittest
from pathlib import Path

from review_core import DEFAULT_DATASET, DEFAULT_ISSUES, DEFAULT_SCHEMA, ReviewError, ReviewStore


REVIEWER = {
    "full_name": "Automated Test Reviewer",
    "professional_role": "Test-only reviewer",
}


class RefactorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ReviewStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def request_replace(self, issue_id):
        issue = self.store.issue(issue_id)
        value = self.store._get_pointer(self.store.dataset(), issue["json_field_path"])
        return self.store.record_issue_decision(
            issue_id,
            "Request correction",
            "Automated batch-correction test rationale.",
            json.dumps(value),
            REVIEWER,
            "replace",
            issue["json_field_path"],
        )

    def approve_all(self):
        for issue in self.store.issues():
            self.store.record_issue_decision(
                issue["issue_id"],
                "Approve existing information",
                "Automated acceptance rationale.",
                "",
                REVIEWER,
            )

    def test_multiple_corrections_create_one_version(self):
        self.request_replace("QA-001")
        self.request_replace("QA-002")
        preview = self.store.preview_correction_batch(["QA-001", "QA-002"])
        snapshot, _ = self.store.create_safety_snapshot()
        result = self.store.apply_correction_batch(preview["preview_id"], snapshot, REVIEWER, True)
        self.assertEqual(2, result["version"]["version"])
        self.assertEqual(2, self.store.dataset_identity()[0])
        self.assertEqual({}, self.store.latest_decisions())

    def test_failed_validation_creates_no_version(self):
        issue = self.store.issue("QA-001")
        self.store.record_issue_decision(
            "QA-001", "Request correction", "Invalid schema test.", "null", REVIEWER,
            "replace", issue["json_field_path"],
        )
        preview = self.store.preview_correction_batch(["QA-001"])
        self.assertFalse(preview["eligible_for_commit"])
        snapshot, _ = self.store.create_safety_snapshot()
        with self.assertRaises(ReviewError):
            self.store.apply_correction_batch(preview["preview_id"], snapshot, REVIEWER, True)
        self.assertEqual(1, self.store.dataset_identity()[0])
        self.assertFalse((self.store.version_bundles / "v0002").exists())

    def test_replace_add_remove_operations(self):
        document = {"object": {"old": 1}, "items": ["a", "b"]}
        self.store._apply_json_operation(document, {"operation": "replace", "path": "/object/old", "value": 2})
        self.store._apply_json_operation(document, {"operation": "add", "path": "/object/new", "value": 3})
        self.store._apply_json_operation(document, {"operation": "add", "path": "/items/1", "value": "x"})
        removed = self.store._apply_json_operation(document, {"operation": "remove", "path": "/items/0"})
        self.store._apply_json_operation(document, {"operation": "remove", "path": "/object/old"})
        self.assertEqual({"new": 3}, document["object"])
        self.assertEqual(["x", "b"], document["items"])
        self.assertEqual("a", removed["original_value"])
        self.assertIsNone(removed["resulting_value"])

    def test_required_property_remove_fails_candidate_validation(self):
        issue = self.store.issue("QA-001")
        self.store.record_issue_decision(
            "QA-001", "Request correction", "Remove required field test.", "", REVIEWER,
            "remove", "/HC0003/patient_information/id",
        )
        preview = self.store.preview_correction_batch([issue["issue_id"]])
        self.assertFalse(preview["eligible_for_commit"])
        self.assertTrue(preview["technical_validation"]["structural_errors"])

    def test_duplicate_and_parent_child_conflicts_are_rejected(self):
        requests = [
            {"issue_id": "A", "operation": {"path": "/a"}},
            {"issue_id": "B", "operation": {"path": "/a"}},
            {"issue_id": "C", "operation": {"path": "/a/b"}},
        ]
        errors = self.store._operation_conflicts(requests)
        self.assertTrue(any("duplicates" in error for error in errors))
        self.assertTrue(any("overlapping" in error for error in errors))

    def test_active_change_invalidates_preview(self):
        self.request_replace("QA-001")
        preview = self.store.preview_correction_batch(["QA-001"])
        snapshot, _ = self.store.create_safety_snapshot()
        upload = self.store.archive_and_validate_bundle(DEFAULT_DATASET, DEFAULT_SCHEMA, DEFAULT_ISSUES)
        self.store.activate_uploaded_bundle(upload["bundle_id"], True)
        with self.assertRaisesRegex(ReviewError, "changed after preview"):
            self.store.apply_correction_batch(preview["preview_id"], snapshot, REVIEWER, True)

    def test_commit_requires_safety_snapshot(self):
        self.request_replace("QA-001")
        preview = self.store.preview_correction_batch(["QA-001"])
        with self.assertRaisesRegex(ReviewError, "safety snapshot"):
            self.store.apply_correction_batch(preview["preview_id"], "", REVIEWER, True)
        self.assertEqual(1, self.store.dataset_identity()[0])

    def test_safety_snapshot_does_not_change_identity_or_approval(self):
        before = self.store.dataset_identity()
        snapshot, manifest = self.store.create_safety_snapshot()
        self.assertTrue(snapshot.exists())
        self.assertEqual(before, self.store.dataset_identity())
        self.assertFalse(manifest["changes_active_state"])

    def test_gradio_temporary_copy_of_snapshot_is_accepted(self):
        self.request_replace("QA-001")
        preview = self.store.preview_correction_batch(["QA-001"])
        snapshot, _ = self.store.create_safety_snapshot()
        copied_snapshot = self.root / "gradio-temporary-upload.zip"
        shutil.copy2(snapshot, copied_snapshot)
        result = self.store.apply_correction_batch(preview["preview_id"], copied_snapshot, REVIEWER, True)
        self.assertEqual(2, result["version"]["version"])

    def test_contradictory_approved_justification_is_blocked(self):
        with self.assertRaisesRegex(ReviewError, "conflicts with the justification"):
            self.store.record_clinical_approval(
                "Clinically Approved with Limitations",
                "Final clinical approval is deferred and approval is withheld.",
                REVIEWER,
                True,
            )

    def test_approval_history_marks_controlling_and_superseded(self):
        self.approve_all()
        first = self.store.record_clinical_approval("Clinically Approved", "", REVIEWER, True)
        second = self.store.record_clinical_approval("Clinically Approved", "", REVIEWER, True)
        history = self.store.approval_history()
        self.assertEqual("Historical — superseded", history[-2]["classification"])
        self.assertEqual(second["approval_id"], history[-2]["superseded_by"])
        self.assertEqual("Controlling approval", history[-1]["classification"])
        self.assertEqual(first["approval_id"], history[-1]["supersedes"])

    def test_inconsistent_historical_approval_is_labelled(self):
        self.approve_all()
        # Preserve a legacy inconsistent record as append-only evidence.
        gate = self.store.gate_report()
        legacy = {
            "approval_id": "legacy-inconsistent",
            "dataset_version": gate["dataset_version"],
            "dataset_sha256": gate["dataset_sha256"],
            "reviewer": REVIEWER,
            "approval_decision": "Clinically Approved with Limitations",
            "justification": "Approval is deferred.",
            "outstanding_limitations": [],
            "review_timestamp_utc": "2026-01-01T00:00:00Z",
        }
        from review_core import append_jsonl
        append_jsonl(self.store.approvals_path, legacy)
        self.store.record_clinical_approval("Clinically Approved", "", REVIEWER, True)
        history = self.store.approval_history()
        self.assertEqual("Historical — superseded and internally inconsistent", history[-2]["classification"])

    def test_approval_for_non_active_version_is_non_controlling(self):
        self.approve_all()
        approval = self.store.record_clinical_approval("Clinically Approved", "", REVIEWER, True)
        upload = self.store.archive_and_validate_bundle(DEFAULT_DATASET, DEFAULT_SCHEMA, DEFAULT_ISSUES)
        self.store.activate_uploaded_bundle(upload["bundle_id"], True)
        history = self.store.approval_history(1, approval["dataset_sha256"])
        self.assertEqual("Historical — non-controlling", history[-1]["classification"])
        self.assertFalse(history[-1]["is_controlling"])

    def test_freeze_classification_tracks_superseding_approval(self):
        self.approve_all()
        self.store.record_clinical_approval("Clinically Approved", "", REVIEWER, True)
        package, _ = self.store.freeze("approved")
        self.assertEqual("Current controlling approved freeze", self.store.freezes()[-1]["classification"])
        self.store.record_clinical_approval("Clinically Approved", "", REVIEWER, True)
        historical = self.store.freezes()[-1]
        self.assertEqual("Historical approved freeze — controlling approval superseded", historical["classification"])
        verification = self.store.verify_freeze_package(package)
        self.assertEqual("Passed with warnings", verification["status"])

    def test_correction_plan_is_read_only(self):
        self.request_replace("QA-001")
        before = self.store.dataset_identity()
        plan = self.store.export_correction_plan()
        self.assertTrue(plan.exists())
        self.assertEqual(before, self.store.dataset_identity())


if __name__ == "__main__":
    unittest.main()
