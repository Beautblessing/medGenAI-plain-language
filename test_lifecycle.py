import json
import tempfile
import threading
import unittest
from pathlib import Path

from review_core import (
    DEFAULT_DATASET,
    DEFAULT_ISSUES,
    DEFAULT_SCHEMA,
    ReviewError,
    ReviewStore,
    atomic_json,
)


REVIEWER = {
    "full_name": "Automated Test Reviewer",
    "professional_role": "Test-only clinical reviewer",
}


class DatasetLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ReviewStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def archive_default(self):
        return self.store.archive_and_validate_bundle(DEFAULT_DATASET, DEFAULT_SCHEMA, DEFAULT_ISSUES)

    def approve_active(self):
        for issue in self.store.issues():
            self.store.record_issue_decision(
                issue["issue_id"],
                "Approve existing information",
                "Automated synthetic-data test rationale.",
                "",
                REVIEWER,
            )
        return self.store.record_clinical_approval("Clinically Approved", "", REVIEWER, True)

    def test_01_active_does_not_imply_unfrozen(self):
        self.store.freeze("provisional")
        self.assertTrue(self.store.has_active_dataset())
        self.assertEqual("Active — provisional freeze available", self.store.lifecycle_label())

    def test_02_freezing_does_not_deactivate_active_version(self):
        before = self.store.dataset_identity()
        self.store.freeze("provisional")
        self.assertEqual(before, self.store.dataset_identity())

    def test_03_active_version_can_have_no_freeze(self):
        self.assertTrue(self.store.has_active_dataset())
        self.assertEqual([], self.store.freezes(version=1))

    def test_04_active_version_can_have_both_freeze_types(self):
        self.store.freeze("provisional")
        self.approve_active()
        self.store.freeze("approved")
        self.assertEqual({"provisional", "approved"}, {item["freeze_type"] for item in self.store.freezes(version=1)})

    def test_05_latest_freeze_types_can_reference_different_versions(self):
        self.store.freeze("provisional")
        upload = self.archive_default()
        self.store.activate_uploaded_bundle(upload["bundle_id"], True)
        self.approve_active()
        self.store.freeze("approved")
        self.assertEqual(1, self.store.latest_freeze("provisional")["dataset_version"])
        self.assertEqual(2, self.store.latest_freeze("approved")["dataset_version"])

    def test_06_latest_freeze_is_selected_by_timestamp(self):
        self.store.freeze("provisional")
        self.store.freeze("provisional")
        registry = json.loads(self.store.freeze_registry_path.read_text())
        registry["freezes"][0]["created_at_utc"] = "2099-01-01T00:00:00Z"
        registry["freezes"][1]["created_at_utc"] = "2000-01-01T00:00:00Z"
        atomic_json(self.store.freeze_registry_path, registry)
        self.assertEqual(registry["freezes"][0]["freeze_id"], self.store.latest_freeze("provisional")["freeze_id"])

    def test_07_no_active_dataset_dashboard_is_safe(self):
        atomic_json(self.store.state_path, {})
        dashboard = self.store.dashboard()
        self.assertEqual("No active dataset", dashboard["lifecycle_label"])
        self.assertIsNone(dashboard["active_dataset"])

    def test_08_no_frozen_dataset_dashboard_is_safe(self):
        dashboard = self.store.dashboard()
        self.assertEqual("No approved freeze has been created.", dashboard["latest_approved_freeze"])
        self.assertEqual("No provisional freeze has been created.", dashboard["latest_provisional_freeze"])

    def test_09_dashboard_reports_validation_review_approval_and_freeze(self):
        issue = self.store.issues()[0]
        self.store.record_issue_decision(issue["issue_id"], "Request additional information", "Test evidence remains pending.", "", REVIEWER)
        self.store.freeze("provisional")
        active = self.store.dashboard()["active_dataset"]
        self.assertEqual("Passed", active["technical_validation"]["technical_validation"])
        self.assertTrue(active["review_progress"].startswith("1/"))
        self.assertEqual("No current hash-bound approval", active["clinical_approval"])
        self.assertEqual(1, len(active["provisional_freezes"]))

    def test_10_candidate_validation_does_not_activate(self):
        before = self.store.dataset_identity()
        report = self.archive_default()
        self.assertEqual("Passed", report["validation_status"])
        self.assertEqual(before, self.store.dataset_identity())

    def test_11_failed_upload_remains_archived_and_downloadable(self):
        invalid = self.root / "invalid.json"
        invalid.write_text("{not valid json", encoding="utf-8")
        report = self.store.archive_and_validate_bundle(invalid, DEFAULT_SCHEMA, DEFAULT_ISSUES)
        detail = self.store.upload_detail(report["bundle_id"])
        self.assertEqual("Failed", detail["validation_status"])
        self.assertTrue(detail["downloadable_files"])
        with self.assertRaises(ReviewError):
            self.store.activate_uploaded_bundle(report["bundle_id"], True)

    def test_12_validated_nonactivated_upload_is_retained(self):
        report = self.archive_default()
        detail = self.store.upload_detail(report["bundle_id"])
        self.assertEqual("Passed", detail["validation_status"])
        self.assertEqual("Not activated", detail["activation_status"])

    def test_13_activation_creates_new_immutable_version(self):
        report = self.archive_default()
        self.store.activate_uploaded_bundle(report["bundle_id"], True)
        self.assertEqual(2, self.store.dataset_identity()[0])
        self.assertTrue((self.store.version_bundles / "v0001" / "dataset.json").exists())
        self.assertTrue((self.store.version_bundles / "v0002" / "dataset.json").exists())

    def test_14_historical_decisions_and_approvals_do_not_carry_forward(self):
        self.approve_active()
        report = self.archive_default()
        self.store.activate_uploaded_bundle(report["bundle_id"], True)
        self.assertEqual({}, self.store.latest_decisions())
        self.assertIsNone(self.store.current_approval())

    def test_15_historical_downloads_do_not_change_active_version(self):
        report = self.archive_default()
        self.store.activate_uploaded_bundle(report["bundle_id"], True)
        before = self.store.dataset_identity()
        detail = self.store.version_detail(1)
        self.assertTrue(detail["downloadable_files"])
        self.assertEqual(before, self.store.dataset_identity())

    def test_16_reactivation_creates_new_version_without_repointing(self):
        report = self.archive_default()
        self.store.activate_uploaded_bundle(report["bundle_id"], True)
        result = self.store.reactivate_version(1, True)
        self.assertEqual(3, result["new_dataset_version"])
        self.assertEqual(3, self.store.dataset_identity()[0])
        self.assertFalse(self.store.version_detail(1)["active"])

    def test_17_freeze_manifest_verifies_every_payload_file(self):
        package, _ = self.store.freeze("provisional")
        verification = self.store.verify_freeze_package(package)
        self.assertEqual("Passed", verification["status"], verification)

    def test_18_migration_reconciliation_is_idempotent(self):
        first = self.store.reconcile_runtime()
        counts = (len(self.store.version_rows()), len(self.store.upload_rows()), len(self.store.freezes()))
        second = self.store.reconcile_runtime()
        self.assertEqual(counts, (len(self.store.version_rows()), len(self.store.upload_rows()), len(self.store.freezes())))
        self.assertTrue(first["idempotent"] and second["idempotent"])

    def test_19_concurrent_version_creation_allocates_unique_numbers(self):
        first = self.archive_default()["bundle_id"]
        second = self.archive_default()["bundle_id"]
        results = []
        errors = []

        def activate(bundle_id):
            try:
                results.append(ReviewStore(self.root).activate_uploaded_bundle(bundle_id, True)["new_dataset_version"])
            except Exception as exc:  # Captured so the assertion reports thread failures.
                errors.append(exc)

        threads = [threading.Thread(target=activate, args=(bundle,)) for bundle in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        self.assertEqual([2, 3], sorted(results))

    def test_20_each_version_remains_linked_to_exact_artifacts(self):
        detail = self.store.version_detail(1)
        required = {"dataset", "source_schema", "effective_schema", "qa_issues", "validation_report", "version_manifest"}
        self.assertEqual(required, set(detail["artifacts"]))
        self.assertTrue(all((self.root / path).exists() for path in detail["artifacts"].values()))

    def test_21_qa_register_may_use_a_named_issue_array(self):
        wrapped = self.root / "wrapped_issues.json"
        wrapped.write_text(json.dumps({"qa_issues": json.loads(DEFAULT_ISSUES.read_text())}), encoding="utf-8")
        report = self.store.archive_and_validate_bundle(DEFAULT_DATASET, DEFAULT_SCHEMA, wrapped)
        self.assertEqual("Passed", report["validation_status"])

    def test_22_dataset_selected_as_qa_register_has_specific_error(self):
        report = self.store.archive_and_validate_bundle(DEFAULT_DATASET, DEFAULT_SCHEMA, DEFAULT_DATASET)
        errors = report["technical_validation"]["structural_errors"]
        self.assertTrue(any("appears to be a clinical dataset" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
