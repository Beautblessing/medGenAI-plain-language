"""Section layout, plain-language screening, fixed fact denominators and medication rules."""
import os

import pytest

from evaluation import load_references
from evaluation.checks.plain_style import check_plain_style
from evaluation.transforms import layout_sections

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


def test_section_labels_get_their_own_line():
    text = ("Why You Came\n\nYou came because of a sore throat.\nWhat We Found\n\nYour temperature was 38.6 °C.\n"
            "**Treatment:**\nTake amoxicillin. Follow-Up\nWe will check again in 72 hours.")
    out, changed = layout_sections(text)
    assert changed
    assert out == ("Why You Came\nYou came because of a sore throat.\n\nWhat We Found\nYour temperature was 38.6 °C.\n\n"
                   "Treatment\nTake amoxicillin.\n\nFollow-Up\nWe will check again in 72 hours.")
    assert layout_sections(out) == (out, False)


def test_plain_style_screening_finds_jargon_repeats_and_blame():
    text = ("Why you came\nYour SpO2 was 98%. You did not provide your weight.\n\n"
            "What we found\nYour erythematous tonsils, the two soft lumps at the back of the throat (tonsils), were red.\n"
            "There was cervical lymphadenopathy.\n\n"
            "Warning signs\nCall emergency services if you cannot breathe.\n\n"
            "Follow-up\nWe will check you in 72 hours. Seek emergency care if your throat swells.")
    r = check_plain_style(text, "en")
    terms = {t["term"] for t in r["unexplained_terms"]}
    assert {"SpO2", "erythematous", "cervical", "lymphadenopathy"} <= terms
    explained = check_plain_style("You have tonsillopharyngitis (an infection of the tonsils and throat). It is "
                                  "called streptococcal pharyngitis.", "en")
    assert explained["unexplained_terms"] == []
    assert r["warning_signs_repeated_in_follow_up"] == ["Seek emergency care if your throat swells."]
    assert r["patient_blaming_sentences"] == ["You did not provide your weight."]


def test_reference_explanations_pass_the_screening(store):
    for cid in store.case_ids:
        for lang in ("en", "es"):
            text = store.narrative(cid, "patient", lang)
            if text:
                r = check_plain_style(text, lang)
                assert not r["warning_signs_repeated_in_follow_up"] and not r["patient_blaming_sentences"], (cid, lang)


def _facts(store, cid, lang, text):
    from evaluation.engine import evaluate
    run = evaluate(store, task="plain_language", case_id=cid, language=lang, output_text=text,
                   source_text=store.narrative(cid, "professional", lang), use_models=False)
    return run


def test_required_fact_denominator_is_fixed_per_case(store):
    for cid in store.case_ids:
        for lang in ("en", "es"):
            pat = store.narrative(cid, "patient", lang)
            if not pat:
                continue
            totals = {_facts(store, cid, lang, t)["factual_agreement_structured"]["summary"]["required_items"]
                      for t in (pat, store.narrative(cid, "professional", lang), pat[: len(pat) // 2])}
            assert len(totals) == 1, (cid, lang, totals)


def test_wrong_frequency_and_as_needed_on_a_scheduled_antibiotic_are_critical(store):
    text = ("Your treatment\n- Amoxicillin: take 500 mg by mouth twice a day (every 8 hours) for 10 days. "
            "Only take it when you need it.\n- Paracetamol 1 g by mouth every 8 hours only when needed, at most 3 g a day.")
    run = _facts(store, "HC0001", "en", text)
    checks = {f["check"] for f in run["factual_agreement_structured"]["findings"] if f["origin"].startswith("engine")}
    assert {"engine::medication_frequency_changed", "engine::medication_made_as_needed"} <= checks
    assert run["verdict"] == "fail"
    assert not any("Paracetamol" in f["explanation"] for f in run["factual_agreement_structured"]["findings"]
                   if f["origin"].startswith("engine"))


def test_rapid_test_that_confirms_counts_as_positive(store):
    run = _facts(store, "HC0001", "en", "The rapid antigen test confirms this diagnosis.")
    strep = [f for f in run["factual_agreement_structured"]["findings"] if f["check"] == "investigation::strep_test"]
    assert strep and strep[0]["classification"] == "supported"
