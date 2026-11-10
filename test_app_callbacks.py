import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import app
from review_core import DEFAULT_DATASET, DEFAULT_ISSUES, DEFAULT_SCHEMA, ReviewStore


class DataframeSelectionCallbackTests(unittest.TestCase):
    def test_version_row_selection_accepts_gradio_pandas_dataframe(self):
        table = pd.DataFrame([["v0004", "Yes"], ["v0003", "No"]])
        with patch.object(app, "version_detail_values", return_value=({"version": 3}, ["dataset.json"])) as detail:
            _, metadata, files = app.select_version_row(table, SimpleNamespace(index=(1, 0)))
        detail.assert_called_once_with("v0003")
        self.assertEqual({"version": 3}, metadata)
        self.assertEqual(["dataset.json"], files)

    def test_upload_row_selection_accepts_gradio_pandas_dataframe(self):
        table = pd.DataFrame([["bundle-a", "Passed"], ["bundle-b", "Failed"]])
        with patch.object(app, "upload_detail_values", return_value=({"bundle_id": "bundle-b"}, ["report.json"])) as detail:
            _, metadata, files = app.select_upload_row(table, SimpleNamespace(index=(1, 0)))
        detail.assert_called_once_with("bundle-b")
        self.assertEqual({"bundle_id": "bundle-b"}, metadata)
        self.assertEqual(["report.json"], files)

    def test_selecting_an_issue_clears_prior_issue_inputs(self):
        with patch.object(app, "select_issue", return_value={"issue_id": "QA-002"}) as select:
            detail, decision_update, rationale, replacement, operation_update, correction_path, correction_confirm, status = app.select_issue_for_review("QA-002")
        self.assertGreaterEqual(select.call_count, 1)
        self.assertEqual({"issue_id": "QA-002"}, detail)
        self.assertIsNone(decision_update["value"])
        self.assertEqual("", rationale)
        self.assertEqual("", replacement)
        self.assertEqual("replace", operation_update["value"])
        self.assertEqual("", correction_path)
        self.assertFalse(correction_confirm)
        self.assertEqual("", status)


class UploadRejectionCallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.temp.name))
        self.active_before = self.store.dataset_identity()

    def tearDown(self):
        self.temp.cleanup()

    def assert_rejected_without_activation(self, dataset, schema, issues):
        with patch.object(app, "STORE", self.store):
            status, report, _, rows = app.preview_upload_bundle(dataset, schema, issues)
        self.assertEqual("Failed", report["validation_status"])
        self.assertIn("Action blocked", status)
        self.assertIn("The active dataset was not changed", status)
        self.assertEqual(self.active_before, self.store.dataset_identity())
        self.assertEqual("Not activated", rows[0][3])

    def test_dataset_file_in_qa_slot_is_rejected_without_crashing(self):
        self.assert_rejected_without_activation(DEFAULT_DATASET, DEFAULT_SCHEMA, DEFAULT_DATASET)

    def test_qa_file_in_dataset_slot_is_rejected_without_crashing(self):
        self.assert_rejected_without_activation(DEFAULT_ISSUES, DEFAULT_SCHEMA, DEFAULT_ISSUES)

    def test_dataset_file_in_schema_slot_is_rejected_without_crashing(self):
        self.assert_rejected_without_activation(DEFAULT_DATASET, DEFAULT_DATASET, DEFAULT_ISSUES)


if __name__ == "__main__":
    unittest.main()
