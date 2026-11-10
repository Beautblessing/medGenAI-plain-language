"""The single evaluation entry point used by every Gradio tab and by batch runs.

Result sections are deliberately separate:
  factual_agreement_structured  – agreement with the approved JSON record (authority)
  identifier_checks             – residual PII
  source_faithfulness           – AlignScore / NLI of the output against its input text
  source_preservation           – BERTScore / embeddings of the output against its input text
  reference_similarity          – BERTScore / embeddings against a reference narrative
  bilingual_equivalence         – EN<->ES embedding similarity when both outputs exist
  readability
A high similarity score never offsets a structured-fact failure: the verdict is driven by
facts, identifiers, contradictions and task-specific thresholds only.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from . import models
from .checks.clinical_facts import check_structured_facts, summarize_facts
from .checks.pii import check_pii, summarize_pii
from .checks.readability import readability_delta
from .profiles import get_profile, profile_hash
from .references import ReferenceStore

ENGINE_VERSION = "0.1.0"


def _sha(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluate(store: ReferenceStore, *, task: str, case_id: str, language: str, output_text: str,
             source_text: str | None = None, reference_text: str | None = None,
             other_language_output: str | None = None, model_label: str = "", use_models: bool = True,
             metrics: set[str] | None = None) -> dict[str, Any]:
    """Evaluate one transformed text.

    source_text     defaults to the V9 professional narrative of the case in `language`.
    reference_text  defaults to the profile's reference narrative (plain language only).
    metrics         optional subset of {"bertscore","alignscore","embeddings","nli"} to run.
    """
    if language not in ("en", "es"):
        raise ValueError("language must be 'en' or 'es'")
    if not output_text or not output_text.strip():
        raise ValueError("output_text is empty")
    prof = get_profile(task)
    record = store.record(case_id)

    source_origin = "supplied"
    if source_text is None and prof["source_kind"]:
        source_text = store.narrative(case_id, prof["source_kind"], language)
        source_origin = f"V9 {prof['source_kind']} narrative ({store.narratives_status})" if source_text else "none"
    reference_origin = "supplied" if reference_text else "none"
    if reference_text is None and prof["reference_kind"]:
        reference_text = store.narrative(case_id, prof["reference_kind"], language)
        reference_origin = f"V9 {prof['reference_kind']} narrative ({store.narratives_status})" if reference_text else "none"

    run = {
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "engine_version": ENGINE_VERSION,
        "task": task, "task_label": prof["label"], "case_id": case_id, "language": language,
        "model_label": model_label,
        "profile_hash": profile_hash(task),
        "inputs": {
            "output_sha256": _sha(output_text), "source_origin": source_origin, "source_sha256": _sha(source_text),
            "reference_origin": reference_origin, "reference_sha256": _sha(reference_text),
        },
        "texts": {"output": output_text, "source": source_text, "reference": reference_text},
        "provenance": store.provenance(),
    }

    # ---- authority: structured JSON
    facts = check_structured_facts(record, case_id, output_text, language, task, source_text=source_text,
                                   all_case_ids=store.case_ids)
    run["factual_agreement_structured"] = {"summary": summarize_facts(facts), "findings": [f.to_dict() for f in facts]}
    want0 = metrics or {"bertscore", "alignscore", "embeddings", "nli"}
    run["factual_agreement_structured"]["record_nli"] = (
        models.fact_nli(case_id, record, output_text) if use_models and "nli" in want0
        else {"status": "skipped", "reason": "model metrics disabled for this run"})

    pii = check_pii(record, output_text, include_quasi=True)
    run["identifier_checks"] = {"role": prof["roles"]["pii"], "summary": summarize_pii(pii, record, output_text),
                                "findings": [f.to_dict() for f in pii]}

    run["readability"] = {"role": prof["roles"]["readability"], **readability_delta(source_text, output_text, language)}
    if task == "plain_language":
        from .checks.plain_style import check_plain_style
        run["plain_language_checks"] = check_plain_style(output_text, language)

    # ---- model-based metrics
    want = metrics or {"bertscore", "alignscore", "embeddings", "nli"}
    skipped = {"status": "skipped", "reason": "model metrics disabled for this run"}
    sf: dict[str, Any] = {}
    sp: dict[str, Any] = {}
    rs: dict[str, Any] = {"reference_origin": reference_origin}
    if source_text:
        sf["alignscore"] = (models.alignscore(source_text, output_text, language)
                            if use_models and "alignscore" in want else skipped)
        for d in prof["nli_directions"]:
            if not (use_models and "nli" in want):
                sf[f"nli_{d}"] = skipped
            elif d == "output_vs_source":
                sf[f"nli_{d}"] = models.nli_check(source_text, output_text, direction=d)
            else:
                sf[f"nli_{d}"] = models.nli_check(output_text, source_text, direction=d)
        sp["bertscore_vs_source"] = (models.bertscore(output_text, source_text, language)
                                     if use_models and "bertscore" in want else skipped)
        sp["embedding_vs_source"] = (models.embedding_similarity(output_text, source_text)
                                     if use_models and "embeddings" in want else skipped)
    if reference_text:
        rs["bertscore_vs_reference"] = (models.bertscore(output_text, reference_text, language)
                                        if use_models and "bertscore" in want else skipped)
        rs["embedding_vs_reference"] = (models.embedding_similarity(output_text, reference_text)
                                        if use_models and "embeddings" in want else skipped)
    run["source_faithfulness"] = sf
    run["source_preservation"] = sp
    run["reference_similarity"] = rs
    if other_language_output:
        run["bilingual_equivalence"] = (models.embedding_similarity(output_text, other_language_output)
                                        if use_models and "embeddings" in want else skipped)

    run["flags"], run["verdict"] = _flags(prof, run, language)
    return run


def _ok(m: Any) -> bool:
    return isinstance(m, dict) and m.get("status") == "ok"


def _flags(prof: dict[str, Any], run: dict[str, Any], language: str) -> tuple[list[dict[str, str]], str]:
    th = prof["flags"]
    flags: list[dict[str, str]] = []

    def flag(level: str, reason: str):
        flags.append({"level": level, "reason": reason})

    fs = run["factual_agreement_structured"]["summary"]
    if fs["critical_problems"]:
        flag("fail", f"{fs['critical_problems']} critical clinical problem(s) against the structured record")
    if fs["contradictions"] and not fs["critical_problems"]:
        flag("review", f"{fs['contradictions']} contradiction(s) against the structured record")
    if fs["fact_agreement"] is not None and fs["fact_agreement"] < th["min_fact_agreement"]:
        na = fs.get("required_not_assessed") or 0
        flag("review", f"Required facts supported {fs['required_supported']}/{fs['required_items']}"
             + (f" ({na} not assessed)" if na else ""))
    if fs["unsupported_additions"]:
        flag("review", f"{fs['unsupported_additions']} unsupported addition(s)")

    pi = run["identifier_checks"]["summary"]
    if pi["direct_leaks"]:
        flag("fail" if th.get("any_direct_identifier_leak") else "review",
             f"{pi['direct_leaks']} direct identifier(s) remain in the text")

    rn = run["factual_agreement_structured"].get("record_nli")
    if _ok(rn) and rn["contradiction_count"]:
        flag("review", f"NLI against the structured record: {rn['contradiction_count']} contradicting sentence(s)")

    sf = run["source_faithfulness"]
    a = sf.get("alignscore")
    if _ok(a) and th.get("min_alignscore") is not None and a["score"] < th["min_alignscore"]:
        flag("review", f"AlignScore (source → output consistency) {a['score']:.2f}; target {th['min_alignscore']:g} or higher")
    for k, v in sf.items():
        if k.startswith("nli_") and _ok(v) and v["contradiction_count"] > th.get("max_nli_contradictions", 0):
            flag("review", f"NLI ({v['direction']}): {v['contradiction_count']} contradicting sentence(s)")

    rd = run["readability"]
    tgt = (th.get("readability_target") or {}).get(language)
    score = rd["output"]["primary_score"]
    if tgt is not None and score is not None and score < tgt:
        name = {"flesch_reading_ease": "Flesch reading ease", "fernandez_huerta": "Fernández-Huerta"}.get(
            rd["output"]["primary_score_name"], rd["output"]["primary_score_name"])
        flag("review", f"Readability ({name}) {score:.0f}; target {tgt:g} or higher (higher is easier)")
    lr = th.get("max_length_ratio")
    if lr and rd.get("length_ratio") and rd["length_ratio"] > lr:
        flag("review", f"Output is {rd['length_ratio']:.0%} of the source length; not concise")

    pl = run.get("plain_language_checks") or {}
    if pl.get("unexplained_terms"):
        flag("review", "Medical terms not explained in everyday words: "
             + ", ".join(t["term"] for t in pl["unexplained_terms"]))
    if pl.get("warning_signs_repeated_in_follow_up"):
        flag("review", f"Follow-up repeats {len(pl['warning_signs_repeated_in_follow_up'])} warning sign(s); "
             "they belong only under Warning signs")
    if pl.get("patient_blaming_sentences"):
        flag("review", "Sentence suggests the patient failed to give information: "
             + pl["patient_blaming_sentences"][0][:120])
    nli_src = sf.get("nli_output_vs_source")
    if run.get("task") == "plain_language" and _ok(nli_src):
        weak = [r for r in nli_src.get("sentences", [])
                if r["label"] == "not_entailed" and r["entailment"] < th.get("max_unsupported_entailment", 0.02)]
        if weak:
            flag("review", f"{len(weak)} sentence(s) not supported by the source text (NLI); check for added "
                 f"content, e.g. \"{weak[0]['sentence'][:100]}\"")

    unavailable = [k for sec in ("source_faithfulness", "source_preservation", "reference_similarity")
                   for k, v in run[sec].items() if isinstance(v, dict) and v.get("status") == "unavailable"]
    if unavailable:
        flag("info", f"Metrics unavailable: {', '.join(unavailable)}")
    if "candidate" in (run["inputs"]["reference_origin"] + run["inputs"]["source_origin"]):
        flag("info", "Reference narratives are candidates and have not been signed off yet (narrative release review)")

    verdict = "fail" if any(f["level"] == "fail" for f in flags) else \
        "needs_human_review" if any(f["level"] == "review" for f in flags) else "pass_automated_checks"
    return flags, verdict
