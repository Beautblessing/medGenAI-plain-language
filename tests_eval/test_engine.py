"""Engine tests without model downloads (model metrics are stubbed or disabled)."""
import os
import re

import pytest

from evaluation import evaluate, load_references, models
from evaluation.batch import RunStore, aggregate, flat_row, run_batch

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.environ.get("CLINICAL_EVAL_DATASET",
                         os.path.join(APP, "runtime", "dataset_versions", "clinical_records_reference_v0009.json"))
NARR = os.environ.get("CLINICAL_EVAL_NARRATIVES",
                      os.path.join(APP, "reference_narratives", "clinical_narratives_bilingual_v9_prompt4_1.json"))


@pytest.fixture(scope="module")
def store():
    if not (os.path.exists(DATASET) and os.path.exists(NARR)):
        pytest.skip("reference files not available")
    return load_references(DATASET, NARR)


def anonymize(store, cid, text):
    info = store.record(cid)["patient_information"]
    for k, tag in [("full_name", "[NAME]"), ("fictional_identification_number", "[ID]"), ("phone_number", "[PHONE]"),
                   ("email", "[EMAIL]"), ("address", "[ADDRESS]"), ("id", "[RECORD]")]:
        text = text.replace(str(info[k]), tag)
    for part in info["full_name"].split():
        text = re.sub(rf"\b{re.escape(part)}\b", "[NAME]", text)
    return text


def test_dataset_identity_is_verified(store):
    assert store.provenance()["dataset_identity_verified"]
    assert "candidate" in store.narratives_status


def test_v9_references_agree_with_structured_record(store):
    for cid in store.case_ids:
        for lang in ("en", "es"):
            r = evaluate(store, task="plain_language", case_id=cid, language=lang,
                         output_text=store.narrative(cid, "patient", lang), use_models=False)
            fs = r["factual_agreement_structured"]["summary"]
            assert fs["critical_problems"] == 0, (cid, lang)
            open_ = {f["check"] for f in r["factual_agreement_structured"]["findings"] if f["required"] and f["is_problem"]}
            # The Prompt 4.1 package itself reports this one omission (FC-0217/FC-0218): the patient
            # explanations name metformin without "500 mg" in the same sentence.
            assert open_ <= ({"medication::met"} if cid == "HC0003" else set()), (cid, lang, open_)


def test_unanonymized_text_fails_anonymization(store):
    r = evaluate(store, task="anonymization", case_id="HC0001", language="en",
                 output_text=store.narrative("HC0001", "professional", "en"), use_models=False)
    assert r["verdict"] == "fail"
    assert r["identifier_checks"]["summary"]["direct_leaks"] >= 5


@pytest.mark.parametrize("lang", ["en", "es"])
def test_clean_anonymization_keeps_facts_and_removes_identifiers(store, lang):
    for cid in store.case_ids:
        src = store.narrative(cid, "professional", lang)
        r = evaluate(store, task="anonymization", case_id=cid, language=lang, output_text=anonymize(store, cid, src),
                     use_models=False)
        assert r["identifier_checks"]["summary"]["direct_leaks"] == 0, (cid, r["identifier_checks"]["findings"])
        fs = r["factual_agreement_structured"]["summary"]
        assert fs["fact_agreement"] == 1.0 and fs["critical_problems"] == 0, cid
        assert r["verdict"] != "fail"


def test_changed_dose_is_critical_even_when_text_is_almost_identical(store, monkeypatch):
    # Similarity metrics are stubbed to near-perfect: the verdict must still fail.
    monkeypatch.setattr(models, "bertscore", lambda *a, **k: {"status": "ok", "precision": .99, "recall": .99, "f1": .99})
    monkeypatch.setattr(models, "embedding_similarity", lambda *a, **k: {"status": "ok", "cosine": .99})
    monkeypatch.setattr(models, "alignscore", lambda *a, **k: {"status": "not_applicable"})
    monkeypatch.setattr(models, "nli_check", lambda *a, **k: {"status": "skipped"})
    src = anonymize(store, "HC0001", store.narrative("HC0001", "professional", "en"))
    wrong = re.sub(r"(?i)amoxicillin 500 mg", "amoxicillin 250 mg", src, count=1)
    assert wrong != src
    r = evaluate(store, task="anonymization", case_id="HC0001", language="en", output_text=wrong)
    assert r["source_preservation"]["bertscore_vs_source"]["f1"] == .99
    assert any(f["check"] == "engine::medication_dose_changed" for f in r["factual_agreement_structured"]["findings"])
    assert r["verdict"] == "fail"


def test_suspected_diagnosis_called_confirmed_is_flagged(store):
    txt = store.narrative("HC0005", "patient", "en") + "\nThe reflux disease is confirmed."
    r = evaluate(store, task="plain_language", case_id="HC0005", language="en", output_text=txt, use_models=False)
    assert any(f["check"].startswith("certainty::") and f["is_problem"]
               for f in r["factual_agreement_structured"]["findings"])
    assert r["verdict"] == "fail"


def test_spanish_omitted_warning_sign_is_an_omission(store):
    txt = store.narrative("HC0001", "patient", "es")
    cut = re.sub(r"(?i)babe\w*", "", txt)
    r = evaluate(store, task="plain_language", case_id="HC0001", language="es", output_text=cut, use_models=False)
    bad = [f for f in r["factual_agreement_structured"]["findings"] if f["check"] == "warning::w_drool"]
    assert bad and bad[0]["classification"] == "omission"


def test_summary_may_drop_noncritical_facts_but_not_critical(store):
    full = store.narrative("HC0001", "professional", "en")
    short = re.sub(r"(?i)centor[^.]{0,40}4", "score recorded", anonymize(store, "HC0001", full))
    r = evaluate(store, task="summarization", case_id="HC0001", language="en", output_text=short, use_models=False)
    centor = [f for f in r["factual_agreement_structured"]["findings"] if f["check"] == "investigation::centor"]
    assert centor and centor[0]["required"] is False and centor[0]["classification"] == "requires_human_review"
    cut = re.sub(r"(?i)drool\w*", "", short)
    r = evaluate(store, task="summarization", case_id="HC0001", language="en", output_text=cut, use_models=False)
    drool = [f for f in r["factual_agreement_structured"]["findings"] if f["check"] == "warning::w_drool"]
    assert drool[0]["classification"] == "omission" and r["verdict"] != "pass_automated_checks"


def test_number_words_count_as_numbers(store):
    txt = "Amoxicillin 500 mg every eight hours for ten days."
    r = evaluate(store, task="summarization", case_id="HC0001", language="en", output_text=txt, use_models=False)
    got = {f["check"]: f["classification"] for f in r["factual_agreement_structured"]["findings"]}
    assert got["medication::amox_freq"] == "supported" and got["medication::amox_dur"] == "supported", got


def test_changed_frequency_is_critical(store):
    txt = store.narrative("HC0002", "patient", "en")
    bad = re.sub(r"(?i)(ramipril 2\.5 mg[^.]{0,40}?)once (a day|daily)", r"\1twice a day", txt, count=1)
    assert bad != txt
    r = evaluate(store, task="plain_language", case_id="HC0002", language="en", output_text=bad, use_models=False)
    assert any(f["check"] == "engine::medication_frequency_changed" for f in r["factual_agreement_structured"]["findings"])
    assert r["verdict"] == "fail"


def test_spanish_changed_dose_is_critical(store):
    txt = store.narrative("HC0001", "patient", "es")
    bad = re.sub(r"(?i)(amoxicilina, un antibiótico, )500 mg", r"\g<1>750 mg", txt, count=1)
    assert bad != txt
    r = evaluate(store, task="plain_language", case_id="HC0001", language="es", output_text=bad, use_models=False)
    assert r["verdict"] == "fail"


def test_alignscore_is_not_applied_to_spanish():
    assert models.alignscore("a", "b", "es")["status"] == "not_applicable"


def test_fernandez_huerta_is_not_flesch_kincaid():
    from evaluation.checks.readability import readability
    r = readability("El paciente tiene fiebre. Debe tomar el medicamento cada ocho horas.", "es")
    assert r["primary_score_name"] == "fernandez_huerta" and r["fernandez_huerta"] > 30


def test_batch_and_store(store, tmp_path):
    rows = [{"case_id": c, "task": "plain_language", "language": l, "model_label": "v9-reference",
             "output_text": store.narrative(c, "patient", l)} for c in store.case_ids[:3] for l in ("en", "es")]
    runs, errors = run_batch(store, rows, use_models=False)
    assert not errors and len(runs) == 6
    rs = RunStore(tmp_path)
    for r in runs:
        rs.append(r)
    assert len(rs.all()) == 6
    csv_p, json_p = rs.export(runs, "t")
    assert csv_p.exists() and json_p.exists()
    agg = aggregate([flat_row(r) for r in runs])
    assert {a["language"] for a in agg} == {"en", "es"} and all(a["n"] == 3 for a in agg)


def test_evaluation_never_writes_to_governance_runtime(store, tmp_path):
    from evaluation.references import file_sha256
    before = file_sha256(store.dataset_path)
    r = evaluate(store, task="summarization", case_id="HC0002", language="es",
                 output_text=store.narrative("HC0002", "professional", "es"), use_models=False)
    rs = RunStore(tmp_path / "runtime_eval")
    rs.append(r)
    assert file_sha256(store.dataset_path) == before
    assert "runtime_eval" in str(rs.path) and "dataset_versions" not in str(rs.path)


def _models_ready():
    try:
        from narrative_eval.models import snapshot_path
        snapshot_path("semantic"); snapshot_path("nli_multilingual")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _models_ready(), reason="pinned NLI/embedding models not downloaded")
def test_record_nli_flags_a_flipped_test_result(store):
    r = models.fact_nli("HC0001", store.record("HC0001"), "The rapid strep test was negative.")
    assert r["status"] == "ok" and r["contradiction_count"] == 1


@pytest.mark.skipif(not _models_ready(), reason="pinned NLI/embedding models not downloaded")
def test_source_nli_flags_withdrawn_prescription(store):
    src = store.narrative("HC0001", "professional", "en")
    r = models.nli_check(src, "Amoxicillin is not prescribed.")
    assert r["status"] == "ok" and r["contradiction_count"] == 1
