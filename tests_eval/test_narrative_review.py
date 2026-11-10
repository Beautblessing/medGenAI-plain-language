"""Narrative Release Review and Sign-off (Gate C): append-only, bound to the exact text, isolated."""
import json
import hashlib
import zipfile

import pytest

from narrative_review import NarrativeReviewLog, gate_c_flag, install_override, sha256
from narrative_freeze import NarrativeApprovalFreeze


class _Store:
    case_ids = ["HC0001"]

    def __init__(self, texts):
        self.texts = texts

    def narrative(self, case, kind, lang, original=False):
        return self.texts[(kind, lang)]


TEXTS = {("professional", "en"): "Pro EN", ("patient", "en"): "Pat EN",
         ("professional", "es"): "Pro ES", ("patient", "es"): "Pat ES"}


def _rec(log, kind, lang, decision="Approved", text=None, comment=""):
    return log.record(dataset_id="ds", release_sha256="abc", case_id="HC0001", language=lang, text_type=kind,
                      text=text if text is not None else TEXTS[(kind, lang)], decision=decision,
                      reviewer="Dr Test", role="Clinician", comment=comment)


def test_decisions_are_append_only_and_bound_to_text(tmp_path):
    log = NarrativeReviewLog(tmp_path)
    _rec(log, "patient", "en", "Needs revision", comment="Dose missing")
    _rec(log, "patient", "en", "Approved")
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["decision"] == "Needs revision"
    assert log.text_status("ds", "HC0001", "en", "patient", "Pat EN") == "Approved"
    assert log.latest("ds", "HC0001", "en", "patient")["text_sha256"] == sha256("Pat EN")
    # a changed text is not covered by the earlier decision
    assert log.text_status("ds", "HC0001", "en", "patient", "Pat EN edited") == "Not reviewed (text changed)"


def test_validation_requires_reviewer_and_comment(tmp_path):
    log = NarrativeReviewLog(tmp_path)
    with pytest.raises(ValueError):
        log.record(dataset_id="ds", release_sha256="", case_id="HC0001", language="en", text_type="patient",
                   text="x", decision="Approved", reviewer=" ", role="Clinician")
    with pytest.raises(ValueError):
        _rec(log, "patient", "en", "Needs revision", comment="")
    with pytest.raises(ValueError):
        _rec(log, "patient", "en", "Maybe")
    assert not log.path.exists()


def test_gate_c_and_release_status(tmp_path):
    log = NarrativeReviewLog(tmp_path)
    run = {"evaluation_dataset_id": "ds", "case_id": "HC0001", "language": "en"}
    texts = {"professional": "Pro EN", "patient": "Pat EN"}
    assert "1 of 2" not in gate_c_flag(log, run, texts) and "0 of 2" in gate_c_flag(log, run, texts)
    _rec(log, "professional", "en")
    assert "1 of 2" in gate_c_flag(log, run, texts)
    _rec(log, "patient", "en", "Approved with comments", comment="Fine")
    assert gate_c_flag(log, run, texts).startswith("Reference narratives signed off by Dr Test")
    st = log.release_status("ds", _Store(TEXTS))
    assert (st["approved"], st["total"], st["signed_off"]) == (2, 4, False)
    _rec(log, "professional", "es")
    _rec(log, "patient", "es")
    assert log.release_status("ds", _Store(TEXTS))["signed_off"] is True
    assert "Dr Test" in log.export_csv("ds")


def test_review_log_lives_outside_both_runtimes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINICAL_NARRATIVE_REVIEW_RUNTIME", str(tmp_path / "nr"))
    log = NarrativeReviewLog()
    _rec(log, "patient", "en")
    assert log.path.parent == tmp_path / "nr"
    assert "runtime_eval" not in str(log.path) and "/runtime/" not in str(log.path)


def _corr(log, text="Pat EN corrected", **kw):
    args = dict(dataset_id="ds", case_id="HC0001", language="en", text_type="patient", original="Pat EN",
                corrected=text, editor="Dr Edit", role="Clinician", reason="Added dose")
    args.update(kw)
    return log.record_correction(**args)


def test_correction_needs_revision_decision_and_valid_fields(tmp_path):
    log = NarrativeReviewLog(tmp_path)
    with pytest.raises(ValueError):          # no decision yet
        _corr(log)
    _rec(log, "patient", "en", "Approved")
    with pytest.raises(ValueError):          # approved texts cannot be corrected
        _corr(log)
    _rec(log, "patient", "en", "Needs revision", comment="Dose missing")
    with pytest.raises(ValueError):
        _corr(log, editor=" ")
    with pytest.raises(ValueError):
        _corr(log, reason="")
    with pytest.raises(ValueError):
        _corr(log, text="Pat EN")            # unchanged
    assert not log.corrections_path.exists()
    c = _corr(log)
    assert c["version"] == 1 and c["original_sha256"] == sha256("Pat EN")
    assert c["corrected_sha256"] == sha256("Pat EN corrected") and c["revision_instruction"] == "Dose missing"
    assert "{+corrected+}" in c["word_diff"]


def test_corrected_text_needs_fresh_review_and_is_used_once_approved(tmp_path):
    log = NarrativeReviewLog(tmp_path)
    _rec(log, "patient", "en", "Needs revision", comment="Dose missing")
    _corr(log)
    assert log.current_text("ds", "HC0001", "en", "patient", "Pat EN") == ("Pat EN corrected", 1)
    assert log.text_status("ds", "HC0001", "en", "patient", "Pat EN corrected") == "Not reviewed (text changed)"
    # not approved yet: the evaluation keeps the release text
    assert log.approved_text("ds", "HC0001", "en", "patient", "Pat EN") == ("Pat EN", 0)
    rows = log.release_status("ds", _Store(TEXTS))["rows"]
    assert any("(corrected v1)" in r[3] for r in rows)
    # second round: needs revision again, correction v2, then approval
    _rec(log, "patient", "en", "Needs revision", text="Pat EN corrected", comment="Still unclear")
    _corr(log, text="Pat EN corrected twice")
    assert log.current_text("ds", "HC0001", "en", "patient", "Pat EN")[1] == 2
    _rec(log, "patient", "en", "Approved", text="Pat EN corrected twice")
    assert log.approved_text("ds", "HC0001", "en", "patient", "Pat EN") == ("Pat EN corrected twice", 2)
    store = _Store(TEXTS)
    install_override(store, "ds", log)
    assert store.text_override("HC0001", "patient", "en", "Pat EN") == "Pat EN corrected twice"
    assert store.text_override("HC0001", "professional", "en", "Pro EN") == "Pro EN"
    st = log.release_status("ds", store)
    assert "Approved (corrected v2)" in [r[3] for r in st["rows"]] and st["approved"] == 1
    # the release decision log itself is untouched by corrections
    assert all("corrected_text" not in d for d in log.all())


def test_reference_store_override_keeps_original_available():
    from evaluation.references import ReferenceStore
    import inspect
    assert "original" in inspect.signature(ReferenceStore.narrative).parameters


def test_locate_issues_points_to_the_passages_an_instruction_names():
    from narrative_review import locate_issues
    text = ("Why you came\nYou came with heartburn.\n\nWhat this means\nYou have reflux. A camera test (endoscopy) "
            "is not needed.\n\nAbout your headache medicine\nUse paracetamol.\n\nWarning signs\nCall emergency "
            "services if you vomit blood or pass black stools and you feel faint.")
    loc = locate_issues(text, 'Vomiting blood/black stools is always an emergency. Endoscopy statement not in the '
                              'record. Extra seventh heading. "heartburn" is fine.', "en")
    marked = {text[a:b]: ns for a, b, ns in loc["spans"]}
    assert marked["A camera test (endoscopy) is not needed."] == [2]
    assert marked["About your headache medicine"] == [3]
    assert 1 in next(ns for s, ns in marked.items() if s.startswith("Call emergency"))
    assert marked["You came with heartburn."] == [4]          # quoted passage
    assert "Warning signs" not in marked and "Why you came" not in marked
    assert [i["matches"] > 0 for i in loc["issues"]] == [True] * 4


def test_locate_issues_ignores_negated_mentions():
    from narrative_review import locate_issues
    text = ("What we found\nYou have no vomiting, no blood in vomit and no black stools.\n\nWarning signs\n"
            "Call emergency services if you vomit blood or pass black stools and you feel faint.")
    spans = [text[a:b] for a, b, _ in locate_issues(text, "Vomiting blood or black stools is always an emergency.",
                                                      "en")["spans"]]
    assert spans == ["Call emergency services if you vomit blood or pass black stools and you feel faint."]


def test_issue_actions_from_wording_or_prefix():
    from narrative_review import issue_action
    assert issue_action("Endoscopy statement not in the record", 1) == "Remove"
    assert issue_action("Only advises action if heavy or faint", 1) == "Replace"
    assert issue_action("Extra seventh heading", 1, heading=True) == "Merge"
    assert issue_action("The omeprazole dose is missing") == "Add"
    assert issue_action("Vomiting blood is an emergency", 1) == "Replace"      # 'Vomiting' is not 'omit'
    assert issue_action("Vomiting blood is an emergency", 0) == "Add"
    assert issue_action("Remove: the BMI sentence is fine but unnecessary", 1) == "Remove"
    assert issue_action("Añadir: control en 8 semanas") == "Add"


def _entry(eval_root, dataset_id="ds"):
    folder = eval_root / "datasets" / dataset_id
    folder.mkdir(parents=True)
    source_json = folder / "narratives.json"
    source_json.write_text(json.dumps({"records": {"HC0001": {
        "clinical_narrative_en": "Pro EN", "patient_explanation_en": "Pat EN",
        "clinical_narrative_es": "Pro ES", "patient_explanation_es": "Pat ES",
    }}}), encoding="utf-8")
    archive = folder / "original.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("source.txt", "exact source archive")
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    return {
        "evaluation_dataset_id": dataset_id,
        "narrative_release_version": "4.1-test",
        "narrative_archive_sha256": archive_sha,
        "narrative_json_sha256": hashlib.sha256(source_json.read_bytes()).hexdigest(),
        "source_clinical_reference_id": "clinical-reference-v9-test",
        "source_dataset_version": 9,
        "source_dataset_canonical_sha256": "canonical",
        "source_freeze_sha256": "reference-freeze",
        "provenance_verification_status": "Verified",
        "import_validation_result": "Passed",
        "archive_file": f"datasets/{dataset_id}/original.zip",
        "narrative_json": f"datasets/{dataset_id}/narratives.json",
    }


def _approve_all(log, release_sha="abc", patient_en="Approved", patient_es="Approved"):
    for kind, lang, decision in (("professional", "en", "Approved"), ("patient", "en", patient_en),
                                 ("professional", "es", "Approved"), ("patient", "es", patient_es)):
        log.record(dataset_id="ds", release_sha256=release_sha, case_id="HC0001", language=lang, text_type=kind,
                   text=TEXTS[(kind, lang)], decision=decision, reviewer="Dr Test", role="Clinician",
                   comment="Accepted with note" if decision != "Approved" else "")


def test_completed_signoff_status_is_consumed_without_second_approval(tmp_path):
    log = NarrativeReviewLog(tmp_path / "review")
    lifecycle = NarrativeApprovalFreeze(tmp_path / "review")
    entry = _entry(tmp_path / "eval")
    store = _Store(TEXTS)
    _approve_all(log, entry["narrative_archive_sha256"], patient_en="Approved with comments")
    snapshot = lifecycle.snapshot("ds", store, entry, log)
    assert lifecycle.coverage(snapshot, "approved_or_comments")["passed"] is True
    assert lifecycle.coverage(snapshot, "strict_approved_only")["passed"] is False
    assert lifecycle.current_signoff_record("ds", snapshot) is None
    consumed = lifecycle.consume_signoff(dataset_id="ds", store=store, entry=entry, log=log)
    assert lifecycle.current_signoff_record("ds", snapshot)["signoff_record_id"] == consumed["signoff_record_id"]
    assert consumed["approval_type"] == "completed_signoff_status_consumed"
    assert consumed["reviewers"] == [{"name": "Dr Test", "role": "Clinician"}]
    assert len(consumed["decision_ids"]) == 4
    # Consuming the same completed status is idempotent, not a second approval.
    assert lifecycle.consume_signoff(dataset_id="ds", store=store, entry=entry, log=log) == consumed
    assert len(lifecycle.signoff_records("ds")) == 1


def test_pending_revision_blocks_freeze_and_reports_qa005(tmp_path):
    log = NarrativeReviewLog(tmp_path / "review")
    lifecycle = NarrativeApprovalFreeze(tmp_path / "review")
    entry = _entry(tmp_path / "eval", "evalds_374910b82d732c4f")
    store = _Store(TEXTS)
    store.case_ids = ["HC0005"]
    for kind, lang in (("professional", "en"), ("professional", "es"), ("patient", "es")):
        log.record(dataset_id=entry["evaluation_dataset_id"], release_sha256=entry["narrative_archive_sha256"],
                   case_id="HC0005", language=lang, text_type=kind, text=TEXTS[(kind, lang)],
                   decision="Approved", reviewer="Reviewer", role="Clinician")
    log.record(dataset_id=entry["evaluation_dataset_id"], release_sha256=entry["narrative_archive_sha256"],
               case_id="HC0005", language="en", text_type="patient", text=TEXTS[("patient", "en")],
               decision="Needs revision", reviewer="Reviewer", role="Clinician", comment="Fix before release")
    with pytest.raises(ValueError, match="QA-005"):
        lifecycle.consume_signoff(dataset_id=entry["evaluation_dataset_id"], store=store, entry=entry, log=log)


def test_freeze_archives_exact_source_and_is_checksum_verified(tmp_path):
    review_root = tmp_path / "review"
    eval_root = tmp_path / "eval"
    log = NarrativeReviewLog(review_root)
    lifecycle = NarrativeApprovalFreeze(review_root)
    entry = _entry(eval_root)
    store = _Store(TEXTS)
    _approve_all(log, entry["narrative_archive_sha256"])
    frozen = lifecycle.create_freeze(dataset_id="ds", store=store, entry=entry, eval_root=eval_root, log=log)
    checked = lifecycle.verify_freeze(frozen)
    assert checked["valid"] is True
    path = review_root / frozen["zip_path"]
    with zipfile.ZipFile(path) as zf:
        assert zf.read("source/source_narrative_archive_exact.zip") == (eval_root / entry["archive_file"]).read_bytes()
        manifest = json.loads(zf.read("narrative_freeze_manifest.json"))
        assert manifest["consumed_signoff_record_id"] == frozen["consumed_signoff_record_id"]
        assert len(manifest["per_text"]) == 4
        assert manifest["separation_notice"].startswith("This is a Narrative Approval")
        signoff = json.loads(zf.read("evidence/consumed_signoff_status.json"))
        assert len(signoff["decision_ids"]) == 4 and "signer" not in signoff
    # Freezing the same texts again reuses the verified freeze instead of making an identical copy.
    again = lifecycle.create_freeze(dataset_id="ds", store=store, entry=entry, eval_root=eval_root, log=log)
    assert again["freeze_id"] == frozen["freeze_id"] and again["reused"] is True
    assert len(lifecycle.signoff_records("ds")) == 1 and len(lifecycle.freezes("ds")) == 1
    assert lifecycle.freeze_status("ds", store, entry, log)["state"] == "matches_freeze"
    # A later "Needs revision" on a frozen text withdraws the sign-off without changing the text ...
    log.record(dataset_id="ds", release_sha256=entry["narrative_archive_sha256"], case_id="HC0001", language="en",
               text_type="patient", text="Pat EN", decision="Needs revision", reviewer="Dr Test", role="Clinician",
               comment="Add the dose")
    assert lifecycle.freeze_status("ds", store, entry, log)["state"] == "signoff_changed_since_freeze"
    # ... and a correction approved after the freeze changes the texts: the freeze no longer matches ...
    _corr(log, original="Pat EN", corrected="Pat EN with dose", reason="Added dose")
    log.record(dataset_id="ds", release_sha256=entry["narrative_archive_sha256"], case_id="HC0001", language="en",
               text_type="patient", text="Pat EN with dose", decision="Approved", reviewer="Dr Test", role="Clinician")
    st = lifecycle.freeze_status("ds", store, entry, log)
    assert st["state"] == "changed_since_freeze" and st["freeze_id"] == frozen["freeze_id"]
    # ... and a new freeze is a new immutable artifact that never overwrites the first.
    second = lifecycle.create_freeze(dataset_id="ds", store=store, entry=entry, eval_root=eval_root, log=log)
    assert second["freeze_id"] != frozen["freeze_id"] and path.exists() and not second.get("reused")
    assert len(lifecycle.freezes("ds")) == 2
    st = lifecycle.freeze_status("ds", store, entry, log)
    assert st["state"] == "matches_freeze" and st["freeze_id"] == second["freeze_id"]


def test_freeze_status_before_any_freeze(tmp_path):
    lifecycle = NarrativeApprovalFreeze(tmp_path / "review")
    entry = _entry(tmp_path / "eval")
    assert lifecycle.freeze_status("ds", _Store(TEXTS), entry, NarrativeReviewLog(tmp_path / "review"))["state"] \
        == "not_frozen"
