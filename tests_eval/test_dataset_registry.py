import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from evaluation.datasets import EvaluationDatasetRegistry
from evaluation.references import EXPECTED_FREEZE_SHA256, file_sha256, load_approved_freeze
from evaluation.transforms import anonymize, plain_language, summarize
from narrative_freeze import NarrativeApprovalFreeze
from narrative_review import NarrativeReviewLog

APP = Path(__file__).resolve().parents[1]
RELEASES = APP / "reference_narratives" / "releases"
FREEZE = APP / "runtime" / "exports" / "freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip"


CLAUDE41 = RELEASES / "clinical_narratives_track_a_prompt4_1_v9_claude_candidate.zip"


def test_claude_41_release_imports_verified(tmp_path):
    registry = EvaluationDatasetRegistry(tmp_path)
    e = registry.import_zip(CLAUDE41)
    assert e["narrative_archive_sha256"].startswith("374910b8")
    assert e["display_name"] == "Reference clinical narratives (EN/ES, from approved v9)"
    assert e["import_validation_result"] == "Passed"
    assert e["provenance_verification_status"] == "Verified"
    assert e["internal_checksums_checked"] >= 10
    assert all(v["exact_json_text_equality"] for v in e["csv_validation"].values())
    assert len(registry.records(e["evaluation_dataset_id"])) == 10


def test_import_is_idempotent_and_does_not_overwrite(tmp_path):
    registry = EvaluationDatasetRegistry(tmp_path)
    path = CLAUDE41
    first = registry.import_zip(path)
    archived = tmp_path / first["archive_file"]
    before = file_sha256(archived)
    second = registry.import_zip(path)
    assert first == second
    assert len(registry.all()) == 1
    assert file_sha256(archived) == before


def test_signed_off_freeze_import_selects_snapshot_not_preserved_source(tmp_path):
    source_runtime = tmp_path / "source_eval"
    source_registry = EvaluationDatasetRegistry(source_runtime)
    source_entry = source_registry.import_zip(CLAUDE41)
    ref = load_approved_freeze(FREEZE)
    ref.narratives = source_registry.records(source_entry["evaluation_dataset_id"])
    review_root = tmp_path / "review"
    log = NarrativeReviewLog(review_root)
    for case_id in ref.case_ids:
        for language in ("en", "es"):
            for text_type in ("professional", "patient"):
                text = ref.narrative(case_id, text_type, language, original=True)
                log.record(dataset_id=source_entry["evaluation_dataset_id"],
                           release_sha256=source_entry["narrative_archive_sha256"], case_id=case_id,
                           language=language, text_type=text_type, text=text, decision="Approved",
                           reviewer="Named Reviewer", role="Clinician")
    lifecycle = NarrativeApprovalFreeze(review_root)
    frozen = lifecycle.create_freeze(dataset_id=source_entry["evaluation_dataset_id"], store=ref,
                                     entry=source_entry, eval_root=source_runtime, log=log)
    freeze_zip = review_root / frozen["zip_path"]

    imported = EvaluationDatasetRegistry(tmp_path / "target_eval").import_zip(freeze_zip)
    assert imported["import_validation_result"] == "Passed"
    assert imported["release_type"] == "signed_off_freeze"
    assert imported["display_name"] == "Reference clinical narratives (EN/ES, from approved v9)"
    assert imported["evaluation_dataset_id"].startswith("evalds_freeze_")
    assert imported["clinical_narrative_review_status"] == "signed_off_frozen"
    assert imported["source_narrative_archive_sha256"] == source_entry["narrative_archive_sha256"]
    assert imported["narrative_freeze_id"] == frozen["freeze_id"]
    assert len(imported["signed_off_text_inventory"]) == 40


def test_path_traversal_zip_is_rejected(tmp_path):
    package = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("../escape.json", "{}")
    with pytest.raises(ValueError, match="Unsafe archive member"):
        EvaluationDatasetRegistry(tmp_path / "runtime").import_zip(package)


def test_individual_json_is_labelled_unverified(tmp_path):
    with zipfile.ZipFile(CLAUDE41) as archive:
        data = archive.read("clinical_narratives_bilingual_v9.json")
    source = tmp_path / "narratives.json"
    source.write_bytes(data)
    entry = EvaluationDatasetRegistry(tmp_path / "runtime").import_json(source)
    assert entry["import_validation_result"] == "Passed"
    assert "Unverified provenance" in entry["provenance_verification_status"]


def test_approved_freeze_adapter_verifies_exact_identity():
    assert file_sha256(FREEZE) == EXPECTED_FREEZE_SHA256
    store = load_approved_freeze(FREEZE)
    assert store.case_ids == [f"HC{i:04d}" for i in range(1, 11)]
    assert store.provenance()["dataset_identity_verified"] is True
    assert store.provenance()["approved_freeze_sha256"] == EXPECTED_FREEZE_SHA256


def test_transformations_execute_with_provenance_and_preserve_source(tmp_path):
    registry = EvaluationDatasetRegistry(tmp_path)
    entry = registry.import_zip(CLAUDE41)
    records = registry.records(entry["evaluation_dataset_id"])
    ref = load_approved_freeze(FREEZE)
    source = records["HC0001"]["clinical_narrative_en"]
    original_hash = hashlib.sha256(source.encode()).hexdigest()
    a = anonymize(entry["evaluation_dataset_id"], "HC0001", "en", source, ref.record("HC0001"))
    s = summarize(entry["evaluation_dataset_id"], "HC0001", "en", source, 5)
    p = plain_language(entry["evaluation_dataset_id"], "HC0001", "en", source, 6)
    assert a["details"]["verification"]["passed"] is True
    assert len(s["generated_output"]) < len(source)
    assert p["generated_output"]
    assert all(item["source_sha256"] == original_hash for item in (a, s, p))
    assert source == records["HC0001"]["clinical_narrative_en"]


def test_evaluation_import_cannot_modify_governance_runtime(tmp_path):
    before = file_sha256(FREEZE)
    EvaluationDatasetRegistry(tmp_path).import_zip(
        CLAUDE41
    )
    assert file_sha256(FREEZE) == before
