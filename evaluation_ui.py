"""Gradio tabs for the shared evaluation engine.

Call add_evaluation_tabs() inside an existing gr.Blocks. Callbacks here only collect inputs,
call evaluation.engine.evaluate / evaluation.batch, and format results; no metric is computed
in this file. Evaluation runs are written to their own append-only folder (CLINICAL_EVAL_RUNTIME,
default runtime_eval/) and never to the governance runtime/.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gradio as gr

from evaluation import evaluate, load_references
from evaluation.batch import RunStore, aggregate, flat_row, generate_and_evaluate, read_batch_csv, run_batch
from evaluation import export as eval_export
from evaluation import generators, provenance
from evaluation.checks.common import SEVERITY_ORDER
from evaluation.review_queue import DECISIONS, HEADERS as QUEUE_HEADERS, ReviewQueue
from evaluation.datasets import KNOWN_RELEASES, EvaluationDatasetRegistry
KNOWN_RELEASE_SHAS = set(KNOWN_RELEASES)
from evaluation.profiles import PROFILES
from evaluation.references import load_approved_freeze
from evaluation import transforms

APP_DIR = Path(__file__).resolve().parent
DATASET_PATH = os.environ.get("CLINICAL_EVAL_DATASET",
                              str(APP_DIR / "runtime" / "dataset_versions" / "clinical_records_reference_v0009.json"))
NARRATIVES_PATH = os.environ.get("CLINICAL_EVAL_NARRATIVES",
                                 str(APP_DIR / "reference_narratives" / "clinical_narratives_bilingual_v9_prompt4_1.json"))
EVAL_RUNTIME = Path(os.environ.get("CLINICAL_EVAL_RUNTIME", str(APP_DIR / "runtime_eval")))
FREEZE_PATH = Path(os.environ.get(
    "CLINICAL_EVAL_FREEZE",
    str(APP_DIR / "runtime" / "exports" / "freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip"),
))
# Master Prompt 5.2: the submitted evaluation is plain-language adaptation, comparing the professional narrative,
# the generated output and the reference patient explanation. Anonymization and summarization are out of scope; their
# code and tests are kept and can be shown again with CLINICAL_EVAL_ENABLE_ANONYMIZATION=1 / _SUMMARIZATION=1.
ANONYMIZATION_ENABLED = os.environ.get("CLINICAL_EVAL_ENABLE_ANONYMIZATION") == "1"
SUMMARIZATION_ENABLED = os.environ.get("CLINICAL_EVAL_ENABLE_SUMMARIZATION") == "1"
HIDDEN_TASKS = tuple(t for t, on in (("anonymization", ANONYMIZATION_ENABLED), ("summarization", SUMMARIZATION_ENABLED))
                     if not on)
VISIBLE_TASKS = [(label, t) for label, t in (("Anonymization", "anonymization"), ("Summarization", "summarization"),
                                            ("Plain-language adaptation", "plain_language")) if t not in HIDDEN_TASKS]
BUNDLED_RELEASES = [
    APP_DIR / "reference_narratives" / "releases" / "clinical_narratives_track_a_prompt4_1_v9_claude_candidate.zip",
]

_STORE = None
_RUNS = None
_REGISTRY = None
_QUEUE = None
_RELEASE_STORES = {}
LANGS = {"English": "en", "Spanish": "es"}
PATIENT_CHOICES = [("Select patient", "")] + [(f"HC{i:04d}", f"HC{i:04d}") for i in range(1, 11)]
VERDICT_STYLE = {
    "fail": ("#B91C1C", "Fail: critical problem found"),
    "needs_human_review": ("#B45309", "Needs human review"),
    "pass_automated_checks": ("#15803D", "Passed automated checks (not a clinical approval)"),
}


def store():
    global _STORE
    if _STORE is None:
        _STORE = load_references(DATASET_PATH, NARRATIVES_PATH if os.path.exists(NARRATIVES_PATH) else None)
    return _STORE


def runs() -> RunStore:
    global _RUNS
    if _RUNS is None:
        _RUNS = RunStore(EVAL_RUNTIME)
    return _RUNS


def registry() -> EvaluationDatasetRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = EvaluationDatasetRegistry(EVAL_RUNTIME)
    return _REGISTRY


def queue() -> ReviewQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = ReviewQueue(EVAL_RUNTIME)
    return _QUEUE


def _gate_c(run: dict[str, Any]) -> None:
    """Report the narrative sign-off (Gate C) of the texts this run used; read-only, info level only."""
    try:
        entry = registry().get(run.get("evaluation_dataset_id") or "")
    except Exception:  # noqa: BLE001
        entry = {}
    if entry.get("release_type") == "signed_off_freeze" and entry.get("import_validation_result") == "Passed":
        text = f"Reference narratives signed off in verified freeze {entry.get('narrative_freeze_id')}"
        for flag in run.get("flags", []):
            if flag.get("reason", "").startswith("Reference narratives") or "Gate C" in flag.get("reason", ""):
                flag["reason"] = text
        run["narrative_sign_off"] = text
        inventory = entry.get("signed_off_text_inventory") or []
        run["reference_text_versions"] = {
            item["text_type"]: {"version": item.get("text_version", 0), "sha256": item["text_sha256"]}
            for item in inventory
            if item.get("patient_id") == run.get("case_id") and item.get("language") == run.get("language")
        }
        run["narrative_freeze_id"] = entry.get("narrative_freeze_id")
        run["narrative_run_version_id"] = entry.get("narrative_run_version_id")
        _signoff_status(run, "frozen")
        return
    try:
        from narrative_review import NarrativeReviewLog, gate_c_flag
        ref = release_store(run.get("evaluation_dataset_id") or "")
        texts = {t: ref.narrative(run["case_id"], t, run["language"]) or "" for t in ("professional", "patient")}
        text = gate_c_flag(NarrativeReviewLog(), run, texts)
    except Exception:  # noqa: BLE001  (no release or no log: keep the engine's wording)
        return
    for f in run.get("flags", []):
        if f.get("reason", "").startswith("Reference narratives") or "Gate C" in f.get("reason", ""):
            f["reason"] = text
    run["narrative_sign_off"] = text
    try:
        log = NarrativeReviewLog()
        run["reference_text_versions"] = {
            t: {"version": v, "sha256": __import__("hashlib").sha256(tx.encode()).hexdigest()}
            for t in ("professional", "patient")
            for tx, v in [log.approved_text(run.get("evaluation_dataset_id") or "", run["case_id"], run["language"], t,
                                            ref.narrative(run["case_id"], t, run["language"], original=True) or "")]}
    except Exception:  # noqa: BLE001
        pass
    _narrative_freeze(run, ref)
    if text.startswith("Reference narratives signed off"):
        _signoff_status(run, "frozen" if (run.get("narrative_freeze") or {}).get("state") == "matches_freeze"
                        else "signed_off")


def _signoff_status(run: dict[str, Any], status: str) -> None:
    """Record the sign-off state of the reference narratives at run time; the status the release had when it
    was imported is kept next to it."""
    prov = run.get("provenance")
    if isinstance(prov, dict):
        prov.setdefault("reference_narratives_status_at_import", prov.get("reference_narratives_status"))
        prov["reference_narratives_status"] = status


def _narrative_freeze(run: dict[str, Any], ref) -> None:
    """Record which narrative freeze the reference texts of this run belong to (read-only)."""
    try:
        from narrative_freeze import NarrativeApprovalFreeze
        from narrative_review import NarrativeReviewLog
        dataset_id = run.get("evaluation_dataset_id") or ""
        log = NarrativeReviewLog()
        st = NarrativeApprovalFreeze(log.root).freeze_status(dataset_id, ref, registry().get(dataset_id), log)
    except Exception:  # noqa: BLE001  (no release, no freeze lifecycle: nothing to record)
        return
    run["narrative_freeze"] = st
    if st["state"] == "matches_freeze":
        run["narrative_sign_off"] = f'{run.get("narrative_sign_off", "")} · frozen as {st["freeze_id"]}'.lstrip(" ·")
        for f in run.get("flags", []):
            if f.get("reason", "").startswith("Reference narratives"):
                f["reason"] = run["narrative_sign_off"]
    elif st["state"] == "signoff_changed_since_freeze":
        run.setdefault("flags", []).append({
            "level": "review",
            "reason": (f"A review decision changed after the latest narrative freeze ({st['freeze_id']}): the frozen "
                       "release is no longer fully signed off. Check the Sign-off Status."),
        })
    elif st["state"] == "changed_since_freeze":
        run.setdefault("flags", []).append({
            "level": "review",
            "reason": (f"Reference narratives changed after the latest narrative freeze ({st['freeze_id']}): "
                       "this run does not use the frozen release. Create a new freeze or check the change."),
        })


def _store_run(run: dict[str, Any]) -> dict[str, Any]:
    """Stamp metric provenance, append the run, and queue its flags for human review."""
    provenance.stamp(run, EVAL_RUNTIME)
    _gate_c(run)
    runs().append(run)
    queue().add_run(run)
    return run


def release_store(dataset_id: str):
    if not dataset_id:
        return store()
    if dataset_id not in _RELEASE_STORES:
        ref = load_approved_freeze(FREEZE_PATH)
        entry = registry().get(dataset_id)
        ref.narratives = registry().records(dataset_id)
        ref.narratives_path = str(EVAL_RUNTIME / entry["narrative_json"])
        ref.narratives_sha256 = entry.get("narrative_archive_sha256") or entry.get("narrative_json_sha256")
        ref.narratives_status = entry.get("clinical_narrative_review_status", "Unknown")
        ref.warnings.extend(entry.get("validation_errors", []))
        try:  # approved Gate C corrections replace the release text for this release (read-only here)
            from narrative_review import install_override
            install_override(ref, dataset_id)
        except Exception:  # noqa: BLE001
            pass
        _RELEASE_STORES[dataset_id] = ref
    return _RELEASE_STORES[dataset_id]


def _release_dropdown(label: str, **kwargs):
    """Release selector. Choices are filled at build time (so a page left open across an app restart still sends a
    value the server lists) and refreshed on tab select; callbacks also check that the release is registered."""
    initial = _release_choices()
    return gr.Dropdown(choices=initial["choices"], value=initial["value"], label=label, **kwargs)


def _known_release(dataset_id) -> bool:
    return bool(dataset_id) and any(item.get("evaluation_dataset_id") == dataset_id for item in registry().all())


def _release_choices(selected: str | None = None):
    choices = [(f"{item['display_name']} · {item['provenance_verification_status']}", item["evaluation_dataset_id"])
               for item in registry().all() if item.get("import_validation_result") == "Passed"]
    values = {value for _, value in choices}
    value = selected if selected in values else (choices[0][1] if choices else None)
    return gr.update(choices=choices, value=value)


def _registry_table():
    return registry().table()


def _import_zip_tracked(package):
    """Import a ZIP and report whether it was newly imported or was already in the registry."""
    before = {item["evaluation_dataset_id"] for item in registry().all()}
    entry = registry().import_zip(package)
    return entry, entry["evaluation_dataset_id"] in before


def _release_details_md(entry: dict) -> str:
    """Release details for any registry entry, in the same layout as the registered reference narratives."""
    gen = entry.get("generation_model_information") or {}
    cases = entry.get("patient_set") or []
    span = f"{cases[0]}–{cases[-1]}" if cases else "no cases"
    fields = entry.get("available_language_fields") or []
    who = " ".join(str(x) for x in (gen.get("generating_model_provider"), gen.get("generating_model_identifier")) if x)
    produced = (f"generated by {who}; configuration version recorded in package metadata" if who
                else "not recorded in the package")
    sha = entry.get("narrative_archive_sha256") or entry.get("narrative_json_sha256") or entry.get("source_file_sha256")
    lines = [
        f"**Contents:** {len(cases) * len(fields)} narratives for {span} ({len(cases)} cases × {len(fields)} text fields), "
        f"bound to approved v{entry.get('source_dataset_version', '?')}.",
        f"**How produced:** {produced} (release version {entry.get('narrative_release_version') or 'unknown'}).",
        f"**Evaluation dataset ID:** `{entry.get('evaluation_dataset_id')}`",
        f"**SHA-256:** `{sha or 'not recorded'}`",
        f"**Provenance:** {entry.get('provenance_verification_status', 'unknown')}",
        f"**Validation:** {entry.get('import_validation_result')}"
        + ("" if not entry.get("validation_errors") else ": " + "; ".join(map(str, entry["validation_errors"]))),
        f"**Status:** {str(entry.get('clinical_narrative_review_status') or 'unknown').replace('_', ' ')}.",
    ]
    return "  \n".join(lines)


def _upload_result(entry: dict, existed: bool):
    name = html.escape(str(entry["display_name"]).split(" (")[0])
    if entry["import_validation_result"] != "Passed":
        banner = _import_banner("rejected", "❌ Validation failed", name, "Not ready")
    elif existed:
        known = entry.get("narrative_archive_sha256") in KNOWN_RELEASE_SHAS
        note = ("This file is the registered reference narratives package; nothing was changed." if known
                else "This package was uploaded before; nothing was changed.")
        banner = _import_banner("approved", "ℹ️ Already registered", name, "Ready", note)
    else:
        banner = _import_banner("approved", "✅ Imported", name, "Ready")
    return banner, _release_details_md(entry), gr.update(visible=True), entry, _registry_table()


def _upload_error(message: str):
    return (_import_banner("rejected", "❌ Import rejected", html.escape(message), "Not ready"), "",
            gr.update(visible=False), {}, _registry_table())


def _import_release(package):
    if not package:
        return "Choose a narrative package ZIP first.", "", gr.update(visible=False), {}, _registry_table()
    try:
        entry, existed = _import_zip_tracked(package)
        _RELEASE_STORES.pop(entry["evaluation_dataset_id"], None)
        return _upload_result(entry, existed)
    except Exception as exc:
        return _upload_error(str(exc))


def _import_individual(narrative, schema, manifest):
    if not narrative:
        return "Choose a narrative JSON file first.", "", gr.update(visible=False), {}, _registry_table()
    try:
        before = {item["evaluation_dataset_id"] for item in registry().all()}
        entry = registry().import_json(narrative, schema, manifest)
        _RELEASE_STORES.pop(entry["evaluation_dataset_id"], None)
        return _upload_result(entry, entry["evaluation_dataset_id"] in before)
    except Exception as exc:
        return _upload_error(str(exc))


def _import_bundled():
    results = []
    for package in BUNDLED_RELEASES:
        if not package.exists():
            results.append(_import_banner("rejected", "❌ Missing", html.escape(package.name), "Not ready"))
            continue
        try:
            entry, existed = _import_zip_tracked(package)
            name = html.escape(str(entry["display_name"]).split(" (")[0])
            if entry["import_validation_result"] != "Passed":
                results.append(_import_banner("rejected", "❌ Validation failed", name, "Not ready"))
            elif existed:
                results.append(_import_banner("approved", "ℹ️ Already registered", name, "Ready"))
            else:
                results.append(_import_banner("approved", "✅ Imported", name, "Ready"))
        except Exception as exc:
            results.append(_import_banner("rejected", "❌ Import rejected", html.escape(f"{package.name}: {exc}"), "Not ready"))
    _RELEASE_STORES.clear()
    return "".join(results), _registry_table()


_NEXT_STEPS = {
    "Ready": "Next step: open <strong>Explore Clinical Narratives</strong> in the menu on the left and select a patient.",
    "Not ready": "Check the registry below; narratives cannot be explored until a valid package is imported.",
}


def _import_banner(kind: str, title: str, message: str, badge: str, note: str = "") -> str:
    step = " ".join(x for x in (note, _NEXT_STEPS.get(badge)) if x)
    return (f'<div class="status-banner status-{kind}" role="status"><span><strong>{title}:</strong> {message}</span>'
            f'<span class="status-badge">{badge}</span></div>'
            + (f'<p class="next-step">{step}</p>' if step else ""))


def _release_view(dataset_id, case_id, lang_label, comparison_id=None):
    if not case_id or not _known_release(dataset_id):
        return "", "", {}, {}, {}, "", "", "", ""
    language = LANGS[lang_label]
    current = release_store(dataset_id)
    professional = current.narrative(case_id, "professional", language) or ""
    patient = current.narrative(case_id, "patient", language) or ""
    entry = registry().get(dataset_id)
    comparison_prof = comparison_patient = ""
    if comparison_id:
        other = release_store(comparison_id)
        comparison_prof = other.narrative(case_id, "professional", language) or ""
        comparison_patient = other.narrative(case_id, "patient", language) or ""
    limitations = _case_limitations(case_id)
    other_lang = "es" if language == "en" else "en"
    other_prof = current.narrative(case_id, "professional", other_lang) or ""
    other_patient = current.narrative(case_id, "patient", other_lang) or ""
    return (professional, patient, entry, current.record(case_id), limitations,
            comparison_prof, comparison_patient, other_prof, other_patient)


def _case_limitations(case_id: str) -> dict:
    """Limitations accepted in the approved freeze that apply to this patient (read-only)."""
    try:
        with zipfile.ZipFile(FREEZE_PATH) as archive:
            manifest = json.loads(archive.read("clinical_reference_freeze_manifest.json"))
            issues = json.loads(archive.read("clinical_reference_qa_issues.json"))
    except Exception as exc:  # noqa: BLE001
        return {"patient_id": case_id, "status": f"Approved freeze could not be read: {exc}"}
    issues = issues if isinstance(issues, list) else issues.get("issues", [])
    case_of = {i.get("issue_id"): i.get("patient_id") for i in issues}
    items = manifest.get("outstanding_limitations") or manifest.get("documented_limitations") or []
    mine = [{**item, "json_field_path": next((i.get("json_field_path") for i in issues
                                              if i.get("issue_id") == item.get("issue_id")), None)}
            for item in items if case_of.get(item.get("issue_id")) == case_id]
    return {"patient_id": case_id,
            "documented_limitations": mine or "None documented for this patient in the approved freeze."}


def _source_release(dataset_id, case_id, lang_label):
    if not case_id or not _known_release(dataset_id):
        return ""
    return release_store(dataset_id).narrative(case_id, "professional", LANGS[lang_label]) or ""


def _write_transformation(record: dict[str, Any]) -> str:
    folder = EVAL_RUNTIME / "transformations"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"transformation_{record['transformation_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _run_transformation(task, dataset_id, case_id, lang_label, source, method, max_sentences, target_words,
                        max_new_tokens, use_models, progress=gr.Progress()):
    if not _known_release(dataset_id):
        raise gr.Error("Import and select an evaluation dataset first (reload the page if the app was restarted).")
    if not case_id:
        raise gr.Error("Select a patient first.")
    language = LANGS[lang_label]
    ref = release_store(dataset_id)
    source = source or ref.narrative(case_id, "professional", language)
    method = method or "rule_baseline"
    params = ({"max_sentences": int(max_sentences or 6)} if method == "rule_baseline"
              else {"target_words": int(target_words or 180), "max_new_tokens": int(max_new_tokens or 400)})
    progress(0.1, desc="Loading model and generating" if method != "rule_baseline" else "Running transformation")
    transformed = transforms.transform(task, dataset_id, case_id, language, source, reference_record=ref.record(case_id),
                                       method=method, params=params)
    if transformed["execution_status"] != "Completed":
        path = _write_transformation(transformed)
        msg = (f"<div style='border-left:6px solid #B91C1C;padding:8px 14px'><b>Transformation failed; nothing was "
               f"scored.</b><br>{html.escape(str(transformed['error_details']))}</div>")
        return ("", transformed, msg, [], [], [], [], [], path, None)
    progress(0.4, desc="Running clinical and language checks")
    run = evaluate(ref, task=task, case_id=case_id, language=language,
                   output_text=transformed["generated_output"], source_text=transformed["source_text"],
                   model_label=transformed["method"], use_models=bool(use_models))
    run["evaluation_dataset_id"] = dataset_id
    run["transformation_id"] = transformed["transformation_id"]
    run["transformation_provenance"] = {k: v for k, v in transformed.items() if k not in ("source_text", "generated_output")}
    _store_run(run)
    transformed["evaluation_run_id"] = run["run_id"]
    path = _write_transformation(transformed)
    table = [[r["identifier_category"], r["original_synthetic_value"] or "", r["replacement"], r["occurrences"]]
             for r in transformed.get("details", {}).get("replacement_table", [])]
    return (transformed["generated_output"], transformed, verdict_html(run), three_way_rows(run), authority_rows(run),
            similarity_rows(run), problem_rows(run), table, path, run["run_id"])


def _model_status_html(st: dict[str, Any]) -> str:
    """One-line model status for the transformation tabs (full details stay in each run's provenance)."""
    name = html.escape(str(st.get("model") or "").split("/")[-1])
    if st.get("status") == "loaded":
        from evaluation.models import DEVICE_NAMES
        dev = str(st.get("device", ""))
        head = (f"✅ <b>Loaded:</b> {name} · {html.escape(DEVICE_NAMES.get(dev, dev.upper()))} · "
                f"{html.escape(str(st.get('dtype', '')))} · {float(st.get('load_seconds') or 0):.1f} s")
        sub = "Ready to run."
    elif st.get("status") == "error":
        head = f"⛔ <b>Could not load:</b> {name}"
        sub = html.escape(str(st.get("reason") or ""))
    else:
        head = f"○ <b>Not loaded:</b> {name}"
        from evaluation.models import DEVICE_NAMES, device
        size = float(st.get("approx_size_gb") or 0)
        slow = (" ⚠ This model is large: without a GPU it needs about "
                f"{size * 2:.0f} GB of memory and is very slow." if size >= 6 and device() == "cpu" else "")
        sub = (f"Will run on: {DEVICE_NAMES.get(device(), device())}"
               + (f" · about {size:.0f} GB" if size else "") + f".{slow} Press “Load selected model”, or it loads "
               "on the first run (from the local Hugging Face cache).")
    rows = "".join(f"<tr><td style='padding:1px 12px 1px 0;color:var(--body-text-color-subdued)'>{html.escape(k)}</td>"
                   f"<td style='padding:1px 0;word-break:break-all'><code>{html.escape(str(v))}</code></td></tr>"
                   for k, v in st.items() if v not in (None, ""))
    return (f"<div style='padding:6px 10px;border-radius:6px;background:var(--block-background-fill)'>{head}"
            f"<div style='font-size:0.85em;color:var(--body-text-color-subdued)'>{sub}</div>"
            f"<details style='font-size:0.85em;margin-top:4px'><summary style='cursor:pointer'>Full details</summary>"
            f"<table style='margin-top:4px'>{rows}</table></details></div>")


def _load_generation_model(method):
    if not method or not method.startswith("hf_local:"):
        return ""
    return _model_status_html(generators.load_model(method.split(":", 1)[1]))


# ------------------------------------------------------------------ formatting helpers
def verdict_html(run: dict[str, Any] | None) -> str:
    if not run:
        return ""
    color, label = VERDICT_STYLE[run["verdict"]]
    items = "".join(f"<li><b>{html.escape(f['level'])}</b>: {html.escape(f['reason'])}</li>" for f in run["flags"])
    return (f"<div style='border-left:6px solid {color};padding:8px 14px;border-radius:6px;background:rgba(0,0,0,0.03)'>"
            f"<div style='font-weight:700;color:{color}'>{label}</div>"
            f"<div style='font-size:0.9em'>Run {run['run_id'][:8]} · {run['task_label']} · {run['case_id']} · "
            f"{run['language'].upper()} · {html.escape(run['model_label'] or 'unlabelled model')}</div>"
            f"<ul style='margin:6px 0 0 0'>{items}</ul></div>")


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _metric(m: Any, key: str) -> str:
    if not isinstance(m, dict):
        return "—"
    if m.get("status") == "ok":
        return _fmt(m.get(key))
    return m.get("status", "—") + (f" ({m.get('reason', '')[:80]})" if m.get("reason") else "")


SCORE_NAMES = {"flesch_reading_ease": "Flesch reading ease", "fernandez_huerta": "Fernández-Huerta"}


def _score_name(name: str | None) -> str:
    return SCORE_NAMES.get(name or "", (name or "readability").replace("_", " "))


def _facts(fs: dict[str, Any] | None) -> str:
    """Required facts supported out of a denominator that is fixed per case."""
    if not fs:
        return "—"
    na = fs.get("required_not_assessed") or 0
    return f"{fs['required_supported']}/{fs['required_items']}" + (f" ({na} not assessed)" if na else "")


def _bertscore_label(m: Any, what: str) -> str:
    """English BERTScore is rescaled against a baseline, Spanish is raw; the two are not comparable."""
    scale = ("rescaled" if m.get("rescaled_with_baseline") else "raw, not rescaled") if isinstance(m, dict) \
        and m.get("status") == "ok" else None
    return f"BERTScore F1 vs {what}" + (f" ({scale})" if scale else "")


def _nli_unsupported(nli: Any, limit: float) -> str:
    if not (isinstance(nli, dict) and nli.get("status") == "ok"):
        return _metric(nli, "contradiction_count")
    return str(sum(r["label"] == "not_entailed" and r["entailment"] < limit for r in nli.get("sentences", [])))


def _unsupported_limit(run: dict[str, Any]) -> float:
    return (PROFILES.get(run.get("task"), {}).get("flags") or {}).get("max_unsupported_entailment", 0.02)


def authority_rows(run: dict[str, Any]) -> list[list[str]]:
    fs = run["factual_agreement_structured"]["summary"]
    pi = run["identifier_checks"]["summary"]
    sf = run["source_faithfulness"]
    nli = sf.get("nli_output_vs_source")
    rows = [
        ["Required facts supported (structured JSON)", _facts(fs), "primary"],
        ["Critical problems", str(fs["critical_problems"]), "primary"],
        ["Contradictions / unsupported additions (structured checks)",
         f"{fs['contradictions']} / {fs['unsupported_additions']}", "primary"],
        ["Sentences not supported by the source (NLI)", _nli_unsupported(nli, _unsupported_limit(run)), "screening"],
        ["Direct identifiers remaining", str(pi["direct_leaks"]), run["identifier_checks"]["role"]],
        ["AlignScore, source → output", _metric(sf.get("alignscore"), "score"), PROFILES[run["task"]]["roles"]["alignscore"]],
        ["NLI vs structured record: contradicting sentences (screening only)",
         _metric(run["factual_agreement_structured"].get("record_nli"), "contradiction_count"), "screening"],
        ["NLI vs input text: contradicting sentences", _metric(nli, "contradiction_count"), PROFILES[run["task"]]["roles"]["nli"]],
    ]
    if "nli_source_vs_output" in sf:
        rows.append(["NLI source sentences contradicted by output", _metric(sf["nli_source_vs_output"], "contradiction_count"),
                     "changed facts"])
    return rows


def similarity_rows(run: dict[str, Any]) -> list[list[str]]:
    sp, rs = run["source_preservation"], run["reference_similarity"]
    rd = run["readability"]
    rows = [
        [_bertscore_label(sp.get("bertscore_vs_source"), "source"), _metric(sp.get("bertscore_vs_source"), "f1")],
        ["Embedding cosine vs source", _metric(sp.get("embedding_vs_source"), "cosine")],
    ]
    if rs.get("reference_origin", "none") != "none":
        rows += [[_bertscore_label(rs.get("bertscore_vs_reference"), "reference"),
                  _metric(rs.get("bertscore_vs_reference"), "f1")],
                 ["Embedding cosine vs reference", _metric(rs.get("embedding_vs_reference"), "cosine")],
                 ["Reference used", rs["reference_origin"]]]
    if "bilingual_equivalence" in run:
        rows.append(["EN↔ES embedding cosine", _metric(run["bilingual_equivalence"], "cosine")])
    o = rd["output"]
    rows.append([f"Readability ({_score_name(o['primary_score_name'])}, higher = easier)", _fmt(o["primary_score"])])
    if rd.get("easier_than_source_by") is not None:
        rows.append(["Readability change vs source", f"{rd['easier_than_source_by']:+.1f}"])
    if rd.get("length_ratio") is not None:
        rows.append(["Length vs source", f"{rd['length_ratio']:.0%}"])
    return rows


def problem_rows(run: dict[str, Any], include_supported: bool = False) -> list[list[str]]:
    out = []
    for sec in ("factual_agreement_structured", "identifier_checks"):
        for f in run[sec]["findings"]:
            if include_supported or f["is_problem"]:
                cls = f["classification"].replace("_", " ")
                if sec == "identifier_checks" and f["classification"] == "supported":
                    cls = "passed (not in output)"
                expl = f["explanation"].replace(" not present", " not found in the output") if sec == "identifier_checks" \
                    else f["explanation"]
                out.append([f["severity"], cls, f["check"], expl, f.get("json_pointer") or "",
                            (f.get("source_evidence") or "")[:200], (f.get("output_evidence") or "—")[:200]])
    order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    return sorted(out, key=lambda r: order.get(r[0], 9))


THREE_WAY_HEADERS = ["Measure", "Professional narrative", "Plain-language (PL) output", "Reference explanation"]
THREE_WAY_WIDTHS = ["28%", "24%", "24%", "24%"]
_BASELINE_CACHE: dict[tuple, dict[str, Any]] = {}


def _reference_baseline(run: dict[str, Any]) -> dict[str, Any] | None:
    """Deterministic checks of the reference patient explanation against the same structured record (not stored)."""
    ds, ref_text = run.get("evaluation_dataset_id"), (run.get("texts") or {}).get("reference")
    if not ds or not ref_text or not _known_release(ds):
        return None
    key = (ds, run["case_id"], run["language"])
    if key not in _BASELINE_CACHE:
        try:
            _BASELINE_CACHE[key] = evaluate(release_store(ds), task="plain_language", case_id=run["case_id"],
                                            language=run["language"], output_text=ref_text,
                                            source_text=run["texts"].get("source"), use_models=False)
        except Exception:  # noqa: BLE001
            return None
    return _BASELINE_CACHE[key]


def _pair(a: str, b: str) -> str:
    return a if a == b else f"{a} / {b}"


def _style(run: dict[str, Any] | None) -> str:
    pl = (run or {}).get("plain_language_checks")
    if not pl:
        return "—"
    return f"{len(pl['unexplained_terms'])} / {len(pl['warning_signs_repeated_in_follow_up'])}"


READABILITY_HEADERS = ["Measure", "Professional narrative", "Plain-language (PL) output", "Reference explanation",
                       "PL vs professional", "Reference vs professional", "PL vs reference"]
READABILITY_WIDTHS = ["16%", "13.5%", "13.5%", "13.5%", "14.5%", "14.5%", "14.5%"]


def readability_rows(run: dict[str, Any]) -> list[list[str]]:
    """Readability of the source and the output as a table (the raw values stay in the downloaded run JSON)."""
    rd = run.get("readability") or {}
    src, out = rd.get("source") or {}, rd.get("output") or {}
    ref_text = (run.get("texts") or {}).get("reference")
    ref = {}
    if ref_text:
        from evaluation.checks.readability import readability as _readability
        ref = _readability(ref_text, run.get("language") or "en")
    name = _score_name(out.get("primary_score_name"))
    target = ((PROFILES.get(run.get("task"), {}).get("flags") or {}).get("readability_target") or {}).get(run.get("language"))

    def row(label, key, better):
        a, b = src.get(key), out.get(key)
        if a is None and b is None:
            return None
        def change(x, y):
            if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                return "—"
            d = y - x
            word = "" if not better else (" easier" if (d > 0) == (better == "up") and d else " harder" if d else "")
            return (f"{d:+.1f}" if isinstance(d, float) else f"{d:+d}") + word
        c = ref.get(key) if ref else None
        return [label, _fmt(a), _fmt(b), _fmt(c) if ref else "—", change(a, b), change(a, c), change(c, b)]

    rows = [row("Words", "words", None), row("Sentences", "sentences", None),
            row("Words per sentence (lower = easier)", "words_per_sentence", "down"),
            row(f"{name} (higher = easier)" + (f"; target {target:g} or higher" if target else ""),
                "primary_score", "up"),
            row("Flesch–Kincaid grade (lower = easier)", "flesch_kincaid_grade", "down")]
    return [r for r in rows if r]


def _relative_paths(value: Any) -> Any:
    """Show paths inside the project folder relative to it (no user name or home folder on screen)."""
    root = str(APP_DIR) + os.sep
    if isinstance(value, dict):
        return {k: _relative_paths(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_relative_paths(v) for v in value]
    if isinstance(value, str) and root in value:
        return value.replace(root, "")
    return value


def three_way_rows(run: dict[str, Any] | None) -> list[list[str]]:
    """Professional narrative vs generated output vs reference patient explanation, side by side."""
    if not run or run.get("task") != "plain_language":
        return []
    from evaluation.checks.readability import readability as _readability
    lang, texts = run["language"], run["texts"]
    src = run["readability"].get("source") or _readability(texts.get("source") or "", lang)
    out = run["readability"]["output"]
    ref = _readability(texts["reference"], lang) if texts.get("reference") else {}
    base = _reference_baseline(run)

    def r(d, k):
        return _fmt(d.get(k)) if d else "—"

    facts = _facts

    fs_out = run["factual_agreement_structured"]["summary"]
    fs_ref = base["factual_agreement_structured"]["summary"] if base else None
    score = _score_name(out.get("primary_score_name"))
    rows = [
        ["Words", r(src, "words"), r(out, "words"), r(ref, "words")],
        ["Words per sentence (lower = easier)", r(src, "words_per_sentence"), r(out, "words_per_sentence"),
         r(ref, "words_per_sentence")],
        [f"Readability: {score} (higher = easier)", r(src, "primary_score"), r(out, "primary_score"), r(ref, "primary_score")],
    ]
    if lang == "en":
        rows.append(["Flesch–Kincaid grade (lower = easier)", r(src, "flesch_kincaid_grade"),
                     r(out, "flesch_kincaid_grade"), r(ref, "flesch_kincaid_grade")])
    rows += [
        ["Required structured facts supported", "source", facts(fs_out), facts(fs_ref)],
        ["Critical problems", "—", str(fs_out["critical_problems"]), str(fs_ref["critical_problems"]) if fs_ref else "—"],
        ["Contradictions / unsupported additions (structured checks)", "—",
         f"{fs_out['contradictions']} / {fs_out['unsupported_additions']}",
         f"{fs_ref['contradictions']} / {fs_ref['unsupported_additions']}" if fs_ref else "—"],
        ["Sentences not supported by the source (NLI)", "—",
         _nli_unsupported(run["source_faithfulness"].get("nli_output_vs_source"), _unsupported_limit(run)),
         _nli_unsupported(base["source_faithfulness"].get("nli_output_vs_source"), _unsupported_limit(run)) if base else "—"],
        ["Medical terms not explained / warning signs repeated in Follow-up", "—", _style(run), _style(base)],
        ["Embedding cosine with the professional narrative", "—",
         _metric(run["source_preservation"].get("embedding_vs_source"), "cosine"), "—"],
        ["Similarity of output to the reference: BERTScore F1 "
         + ("(rescaled)" if run["language"] == "en" else "(raw, not rescaled)") + " / embedding cosine", "—",
         _pair(_metric(run['reference_similarity'].get('bertscore_vs_reference'), 'f1'),
               _metric(run['reference_similarity'].get('embedding_vs_reference'), 'cosine')), "(target)"],
    ]
    return rows


FINDING_HEADERS = ["Severity", "Classification", "Check", "Explanation", "JSON Pointer", "Structured record", "Output evidence"]
FINDING_WIDTHS = ["10%", "15%", "14%", "21%", "13%", "14%", "13%"]   # fits the tab width, no sideways scroll
NLI_HEADERS = ["Label", "Output sentence", "Entailment", "Contradiction", "Closest premise"]
NLI_WIDTHS = ["125px", "auto", "110px", "135px", "auto"]   # label/score columns just fit "not entailed", "Contradiction"


def highlighted(run: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Output text with identifier leaks and contradiction evidence marked (section labels on their own line)."""
    from evaluation.transforms import layout_sections
    text = layout_sections(run["texts"]["output"])[0] if run.get("task") == "plain_language" else run["texts"]["output"]
    spans = []
    import re as _re
    for f in run["identifier_checks"]["findings"]:
        if not f["is_problem"]:
            continue
        # direct identifiers (name, id, phone, email, address) in red; dates and age are quasi-identifiers (amber)
        quasi = f["severity"] not in ("critical", "major")
        lab = "identifier" if not quasi else ("age (quasi-identifier)" if f["check"] == "pii::age"
                                              else "date (quasi-identifier)")
        if f["check"] == "pii::age" or f["check"].startswith("pii_pattern::"):
            ev = f.get("output_evidence") or ""
            hit = _re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", ev) if "date" in f["check"] else None
            v = hit.group(0) if hit else (ev if f["check"] == "pii::age" else "")
        else:
            v = f.get("source_evidence") or ""
        i = text.lower().find(v.lower()) if v else -1
        if i >= 0:
            spans.append((i, i + len(v), lab))
    rs = run["source_faithfulness"].get("nli_output_vs_source") or {}
    for r in rs.get("sentences", []) if rs.get("status") == "ok" else []:
        if r["label"] == "contradiction":
            i = text.find(r["sentence"])
            if i >= 0:
                spans.append((i, i + len(r["sentence"]), "NLI contradiction"))
    spans.sort()
    out, pos = [], 0
    for a, b, lab in spans:
        if a < pos:
            continue
        out.append((text[pos:a], None))
        out.append((text[a:b], lab))
        pos = b
    out.append((text[pos:], None))
    return out


# ------------------------------------------------------------------ release upload and exploration
REGISTRY_HEADERS = [
    "Evaluation dataset ID", "Release", "Version", "Validation", "Provenance", "Narrative review status", "Imported UTC",
]


SOURCE_REGISTERED = "Use the registered reference narratives"
SOURCE_UPLOAD = "Upload another narrative package"


def upload_tab():
    with gr.Tab("Upload Evaluation Dataset", id="eval_upload") as tab:
        gr.Markdown(
            "Choose where the narratives come from. Uploads are handled independently of the clinical-reference "
            "uploader; ZIP members are inspected without executing code, and imports are immutable and cannot alter "
            "reference approval or freeze state."
        )
        source = gr.Radio([SOURCE_REGISTERED, SOURCE_UPLOAD], value=SOURCE_REGISTERED, label="Narrative source")
        with gr.Column(visible=True) as registered_panel:
            bundled = gr.Button("Import narratives", variant="primary")
            with gr.Accordion("Release details", open=False):
                gr.Markdown(
                    "**Contents:** 40 reference clinical narratives for HC0001–HC0010: a professional clinical narrative "
                    "and a patient explanation per case, in English and Spanish, written from the approved v9 reference.  \n"
                    "**How produced:** generated by Claude with narrative prompt 4.1 (generation-only package, version 4.1.0).  \n"
                    "**Evaluation dataset ID:** `evalds_374910b82d732c4f`  \n"
                    "**ZIP SHA-256:** `374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120`, verified on import.  \n"
                    "**Status:** candidate pending independent clinical and bilingual review."
                )
            bundled_status = gr.HTML()
        with gr.Column(visible=False) as upload_panel:
            gr.Markdown("An uploaded package is registered separately as an external candidate and never replaces "
                        "the registered reference narratives.")
            with gr.Row():
                package = gr.File(label="Complete narrative package ZIP", file_types=[".zip"], type="filepath")
                import_package = gr.Button("Validate and import ZIP", variant="primary", interactive=False)
            gr.Markdown("#### Alternative: individual narrative JSON (provenance remains unverified without a trusted package)")
            with gr.Row():
                narrative = gr.File(label="Narrative JSON", file_types=[".json"], type="filepath")
                schema = gr.File(label="Optional output schema", file_types=[".json"], type="filepath")
                manifest = gr.File(label="Optional source manifest", file_types=[".json"], type="filepath")
            import_json_btn = gr.Button("Validate and import individual JSON", interactive=False)
            status = gr.HTML()
            with gr.Accordion("Release details", open=False, visible=False) as upload_details:
                upload_details_md = gr.Markdown()
                detail = gr.JSON(label="Full import record and provenance")
        table = gr.Dataframe(value=_registry_table(), headers=REGISTRY_HEADERS, interactive=False,
                             label="Immutable evaluation dataset registry")
        source.change(lambda v: (gr.update(visible=v == SOURCE_REGISTERED), gr.update(visible=v == SOURCE_UPLOAD)),
                      source, [registered_panel, upload_panel])
        # Import buttons stay disabled until a file is chosen.
        package.change(lambda f: gr.update(interactive=bool(f)), package, import_package)
        narrative.change(lambda f: gr.update(interactive=bool(f)), narrative, import_json_btn)
        upload_outputs = [status, upload_details_md, upload_details, detail, table]
        import_package.click(_import_release, package, upload_outputs)
        import_json_btn.click(_import_individual, [narrative, schema, manifest], upload_outputs)
        bundled.click(_import_bundled, outputs=[bundled_status, table])
        tab.select(_registry_table, outputs=table)


def explore_tab():
    with gr.Tab("Explore Clinical Narratives", id="eval_explore") as tab:
        gr.Markdown("Read-only exploration. Narrative candidates are not treated as approved clinical facts.")
        with gr.Row():
            release = _release_dropdown("Narrative release", scale=3)
            case = gr.Dropdown(PATIENT_CHOICES, value="", label="Patient", scale=1)
            language = gr.Radio(list(LANGS), value="English", label="Language", scale=1)
        with gr.Row():
            professional = gr.Textbox(lines=16, label="Professional clinical narrative", interactive=False, autoscroll=False)
            patient = gr.Textbox(lines=16, label="Patient explanation", interactive=False, autoscroll=False)
        with gr.Accordion("Provenance, authoritative facts and approved-reference limitations", open=False):
            with gr.Row():
                release_prov = gr.JSON(label="Narrative release")
                facts = gr.JSON(label="Authoritative structured record")
                limitations = gr.JSON(label="Documented approved-reference limitations")
        gr.Markdown("#### Other language (English ↔ Spanish), same release")
        with gr.Row():
            other_prof = gr.Textbox(lines=10, label="Professional narrative, other language", interactive=False, autoscroll=False)
            other_patient = gr.Textbox(lines=10, label="Patient explanation, other language", interactive=False, autoscroll=False)
        comparison = gr.State(None)
        comparison_prof = gr.State("")
        comparison_patient = gr.State("")
        outputs = [professional, patient, release_prov, facts, limitations, comparison_prof, comparison_patient,
                   other_prof, other_patient]
        for component in (release, case, language):
            component.change(_release_view, [release, case, language, comparison], outputs)
        # A pre-selected release fires no change event, so fill the texts right after the choices load.
        tab.select(_release_choices, outputs=release).then(_release_view, [release, case, language, comparison], outputs)


def _method_controls(method: str | None):
    """Show only the controls of the chosen method: the rule baseline needs no model and has its own setting."""
    model = bool(method) and method != "rule_baseline"
    status = _model_status_html(generators.model_status(method.split(":", 1)[1])) if model else ""
    return (gr.update(visible=model), gr.update(visible=model, value=status), gr.update(visible=not model),
            gr.update(visible=model), gr.update(visible=model))


def transformation_tab(task: str, title: str, intro: str, default_sentences: int, last_run: gr.State):
    with gr.Tab(title, id=f"eval_transform_{task}") as tab:
        gr.Markdown(intro)
        with gr.Row():
            release = _release_dropdown("Evaluation dataset release", scale=3)
            case = gr.Dropdown(PATIENT_CHOICES, value="", label="Patient", scale=1)
            language = gr.Radio(list(LANGS), value="English", label="Language", scale=1)
        generative = task != "anonymization"
        with gr.Row(visible=generative):
            method = gr.Dropdown(generators.methods(task), value="rule_baseline", label="Transformation method / model")
            load_btn = gr.Button("Load selected model", scale=0, visible=False)
            model_state = gr.HTML(visible=False)
        with gr.Row(visible=generative):
            max_sentences = gr.Slider(2, 12, value=default_sentences, step=1, label="Rule baseline: maximum sentences")
            target_words = gr.Slider(60, 400, value=150 if task == "summarization" else 320, step=10,
                                     label="Model: target length (words)", visible=False)
            max_new_tokens = gr.Slider(100, 1000, value=700, step=50, label="Model: max new tokens (greedy decoding)",
                                       visible=False)
        source = gr.Textbox(lines=12, label="Selected imported professional narrative (editable)", autoscroll=False)
        use_models = gr.Checkbox(
            value=False,
            label="Include locally available model-based metrics (missing checkpoints are reported as unavailable)",
        )
        run_btn = gr.Button(f"Run {title.lower()} and evaluate", variant="primary")
        with gr.Column(visible=False) as results:   # shown once a run has finished, hidden again when the input changes
            output = gr.Textbox(lines=12, label="Generated transformation", interactive=False, autoscroll=False)
            verdict = gr.HTML()
            three_way = gr.Dataframe(headers=THREE_WAY_HEADERS, interactive=False, wrap=True, visible=task == "plain_language",
                                     column_widths=THREE_WAY_WIDTHS, max_height=900,
                                     label="Three-way comparison: professional narrative → generated output → reference "
                                           "patient explanation")
            replacements = gr.Dataframe(headers=["Identifier category", "Original synthetic value", "Replacement", "Occurrences"],
                                        interactive=False, label="Identifier replacements", visible=task == "anonymization",
                                        wrap=True, column_widths=["28%", "42%", "18%", "12%"])
            authority = gr.Dataframe(headers=["Agreement with structured reference", "Value", "Role"], interactive=False,
                                     wrap=True, column_widths=["45%", "35%", "20%"])
            similarity = gr.Dataframe(headers=["Similarity and readability", "Value"], interactive=False, wrap=True,
                                      column_widths=["55%", "45%"])
            findings = gr.Dataframe(headers=FINDING_HEADERS, interactive=False, wrap=True,
                                    column_widths=FINDING_WIDTHS, elem_classes=["ev-find"],
                                    label="Clinical findings requiring review")
            with gr.Accordion("Transformation provenance and verification", open=False):
                transform_provenance = gr.JSON(label="Immutable transformation record")
            download = gr.File(label="Download immutable transformation record")
        for component in (release, case, language):
            component.change(_source_release, [release, case, language], source)
            component.change(lambda: gr.update(visible=False), None, results)
        method.change(_method_controls, method, [load_btn, model_state, max_sentences, target_words, max_new_tokens])
        load_btn.click(_load_generation_model, method, model_state)
        run_btn.click(lambda c: gr.update(visible=bool(c)), case, results).then(   # open the results area so progress shows
            lambda ds, c, l, s, m, n, w, t, um, progress=gr.Progress():
                _run_transformation(task, ds, c, l, s, m, n, w, t, um, progress),
            [release, case, language, source, method, max_sentences, target_words, max_new_tokens, use_models],
            [output, transform_provenance, verdict, three_way, authority, similarity, findings, replacements, download,
             last_run],
        )
        tab.select(_release_choices, outputs=release).then(_source_release, [release, case, language], source)


# ------------------------------------------------------------------ task tab
def _source_for(case_id: str, lang_label: str) -> str:
    try:
        return store().narrative(case_id, "professional", LANGS[lang_label]) or ""
    except Exception:  # noqa: BLE001
        return ""


def _run_task(task, case_id, lang_label, model_label, source_text, output_text, output_file, use_models, progress):
    if output_file:
        output_text = Path(output_file).read_text(encoding="utf-8")
    if not output_text or not output_text.strip():
        raise gr.Error("Paste the model output or upload a .txt file first.")
    progress(0.1, desc="Running checks and metrics")
    run = evaluate(store(), task=task, case_id=case_id, language=LANGS[lang_label], output_text=output_text,
                   source_text=source_text or None, model_label=model_label or "", use_models=bool(use_models))
    _store_run(run)
    return (verdict_html(run), authority_rows(run), similarity_rows(run), problem_rows(run), highlighted(run),
            run["run_id"])


def task_tab(task: str, intro: str, last_run: gr.State):
    with gr.Tab(PROFILES[task]["label"], id=f"eval_{task}"):
        gr.Markdown(intro)
        ids = store().case_ids
        with gr.Row():
            case = gr.Dropdown(ids, value=ids[0], label="Reference case")
            lang = gr.Radio(list(LANGS), value="English", label="Language")
            model_label = gr.Textbox(label="Model that produced the output", placeholder="e.g. llama-3.1-8b, prompt v2")
        with gr.Row():
            source = gr.Textbox(value=_source_for(ids[0], "English"), lines=12, max_lines=24,
                                label="Input text (V9 professional narrative by default; editable)")
            with gr.Column():
                output = gr.Textbox(lines=12, max_lines=24, label="Model output to evaluate")
                output_file = gr.File(label="…or upload the output as .txt", file_types=[".txt"], type="filepath")
        use_models = gr.Checkbox(value=True, label="Include model-based metrics (BERTScore, AlignScore, embeddings, NLI; slower)")
        btn = gr.Button("Evaluate", variant="primary")
        verdict = gr.HTML()
        with gr.Row():
            auth = gr.Dataframe(headers=["Agreement with the structured record and source", "Value", "Role"],
                                interactive=False, wrap=True)
            sim = gr.Dataframe(headers=["Similarity and readability (never decides the verdict)", "Value"],
                               interactive=False, wrap=True)
        marked = gr.HighlightedText(label="Output with identifiers and contradicting sentences marked",
                                    combine_adjacent=False, show_legend=True,
                                    color_map={"identifier": "red", "date (quasi-identifier)": "yellow",
                                               "age (quasi-identifier)": "yellow", "NLI contradiction": "orange"})
        probs = gr.Dataframe(headers=FINDING_HEADERS, interactive=False, wrap=True,
                             column_widths=FINDING_WIDTHS, elem_classes=["ev-find"],
                             label="Detected problems (full evidence in the Evaluation tab)")
        case.change(_source_for, [case, lang], source)
        lang.change(_source_for, [case, lang], source)
        def go(c, l, m, src, out, f, um, progress=gr.Progress()):
            return _run_task(task, c, l, m, src, out, f, um, progress)

        btn.click(go, [case, lang, model_label, source, output, output_file, use_models],
                  [verdict, auth, sim, probs, marked, last_run])


# ------------------------------------------------------------------ evaluation tab
def _run_choices(selected: str | None = None):
    rs = [r for r in runs().all() if r.get("task") not in HIDDEN_TASKS]
    ch = [(f"{r['timestamp_utc'][:19].replace('T', ' ')} · {r['task_label']} · {r['case_id']} · {r['language'].upper()} · "
           f"{r['model_label'] or '—'} · {r['verdict']}", r["run_id"]) for r in reversed(rs)]
    value = selected if selected and any(v == selected for _, v in ch) else (ch[0][1] if ch else None)
    return gr.update(choices=ch, value=value)


def html_table(headers: list[str], rows: list[list[Any]], widths: list[str], cls: str = "", title: str = "",
               mono: tuple[int, ...] = ()) -> str:
    """A plain HTML table: it is exactly as tall as its rows (no scroll box, no hidden sizing copy), wraps text in
    every cell and copies cleanly into a document."""
    def cell(v: Any) -> str:
        if isinstance(v, float):
            v = f"{v:.4f}"
        return html.escape("" if v is None else str(v)).replace("\n", "<br>")
    cols = "".join(f'<col style="width:{w}">' if w != "auto" else "<col>" for w in widths)
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f'<td class="ev-mono">{cell(v)}</td>' if i in mono else f"<td>{cell(v)}</td>"
                                    for i, v in enumerate(r)) + "</tr>" for r in rows) or \
        f'<tr><td colspan="{len(headers)}" class="ev-empty">No rows.</td></tr>'
    cap = f'<div class="ev-title">{html.escape(title)}</div>' if title else ""
    return f'{cap}<div class="ev-table {cls}"><table><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead>' \
           f"<tbody>{body}</tbody></table></div>"


FINDING_SEVERITIES = ["All"] + [s.capitalize() for s in sorted(SEVERITY_ORDER, key=SEVERITY_ORDER.get, reverse=True)]


FINDING_PAGE_SIZE = 5
CHECK_VIEWS = ["Structured facts and identifiers", "NLI: professional narrative", "NLI: record facts"]


def _severity_choices(rows: list[list[str]]):
    """Severity dropdown options with the number of checks at each level for this run."""
    return gr.update(choices=[(f"All ({len(rows)})", "All")] +
                     [(f"{s} ({sum(r[0] == s.lower() for r in rows)})", s) for s in FINDING_SEVERITIES[1:]], value="All")


def _finding_page(rows: list[list[str]], page: int, size: int, severity: str = "All") -> tuple[str, str, int]:
    """One page of the findings table (most severe first, optionally one severity) and its 'Rows a–b of n' line."""
    total = len(rows)
    if severity and severity != "All":
        rows = [r for r in rows if r[0] == severity.lower()]
    size = int(size or 10)
    pages = max(1, -(-len(rows) // size))
    page = min(max(0, int(page or 0)), pages - 1)
    chunk = rows[page * size:(page + 1) * size]
    of = f"{len(rows)} {severity.lower()}" if severity and severity != "All" else str(len(rows))
    where = (f"Rows {page * size + 1}–{page * size + len(chunk)} of {of} · page {page + 1} of {pages}"
             if rows else ("No checks for this run." if not total else f"No {severity.lower()} checks in this run."))
    return html_table(FINDING_HEADERS, chunk, FINDING_WIDTHS, "ev-find"), where, page



def _nli_page(rows: list[list[Any]], page: int) -> tuple[str, str, int]:
    """One page (5 sentences) of an NLI sentence table and its 'Rows a–b of n' line."""
    size = FINDING_PAGE_SIZE
    pages = max(1, -(-len(rows) // size))
    page = min(max(0, int(page or 0)), pages - 1)
    chunk = rows[page * size:(page + 1) * size]
    where = (f"Rows {page * size + 1}–{page * size + len(chunk)} of {len(rows)} · page {page + 1} of {pages}"
             if rows else "No sentences checked for this run.")
    return html_table(NLI_HEADERS, chunk, NLI_WIDTHS, "ev-nli"), where, page


def _show_run(run_id):
    run = runs().get(run_id) if run_id else None
    if not run:
        return "", "", [], [], [], [], "", {}, None
    sf = run["source_faithfulness"]

    def nli_rows(v):
        if not (isinstance(v, dict) and v.get("status") == "ok"):
            return []
        return [[r["label"].replace("_", " "), r["sentence"], r["entailment"], r["contradiction"],
                 r.get("closest_premise") or ""] for r in v["sentences"]]

    # every output sentence is checked twice, automatically: against the structured record's facts and against
    # the input text (the professional narrative for plain-language runs)
    nli_record = nli_rows(run["factual_agreement_structured"].get("record_nli"))
    nli_source = [row for k, v in sf.items() if k.startswith("nli_") for row in nli_rows(v)]
    path = EVAL_RUNTIME / "exports" / f"run_{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    return (verdict_html(run),
            html_table(THREE_WAY_HEADERS, three_way_rows(run), THREE_WAY_WIDTHS, "ev-3way",
                       "Three-way comparison (plain-language runs)"),
            problem_rows(run, include_supported=True),
            nli_record, nli_source,
            highlighted(run),
            html_table(READABILITY_HEADERS, readability_rows(run), READABILITY_WIDTHS, "ev-nli ev-read", "Readability"),
            _relative_paths({**run["provenance"], **run.get("metric_provenance", {})}), str(path))


def evaluation_tab(last_run: gr.State):
    with gr.Tab("Evaluation and Metrics", id="eval_detail") as tab:
        gr.Markdown("Complete evidence for one run. Agreement with the structured JSON record is reported separately from "
                    "similarity to reference or source texts; similarity scores never establish factual correctness.")
        with gr.Row():
            pick = gr.Dropdown(label="Evaluation run", choices=[], scale=4, allow_custom_value=True)
            refresh = gr.Button("Refresh", scale=1)
        verdict = gr.HTML()
        three_way_eval = gr.HTML()
        find_rows, find_pos = gr.State([]), gr.State(0)
        gr.Markdown("### Fact and evidence checks", elem_classes=["ev-section"])
        # a radio styled as tabs (gr.Tabs nested in the side menu hides tabs in an overflow menu on some screens)
        check_view = gr.Radio(CHECK_VIEWS, value=CHECK_VIEWS[0], show_label=False, container=False,
                              elem_classes=["ev-switch"])
        with gr.Column(visible=True) as view_facts:
            gr.Markdown("Every structured-fact and identifier check, with JSON Pointer evidence. This is the "
                        "authority on clinical content. Most severe first, 5 rows per page.",
                        elem_classes=["ev-read-note"])
            with gr.Row(elem_classes=["ev-pager"]):
                find_sev = gr.Dropdown([("All", "All")] + [(s, s) for s in FINDING_SEVERITIES[1:]], value="All",
                                       label="Severity", show_label=False, scale=0, min_width=170,
                                       container=False, elem_classes=["ev-sev"])
                find_prev = gr.Button("◀ Previous", size="sm", scale=0, min_width=110)
                find_where = gr.Markdown("", elem_classes=["ev-page"])
                find_next = gr.Button("Next ▶", size="sm", scale=0, min_width=110)
            findings = gr.HTML()
        with gr.Column(visible=False) as view_src:
            gr.Markdown("Checked against the professional narrative: the NLI model tests whether the input text "
                        "supports (entails) or contradicts each output sentence.", elem_classes=["ev-read-note"])
            src_rows, src_pos = gr.State([]), gr.State(0)
            with gr.Row(elem_classes=["ev-pager"]):
                src_prev = gr.Button("◀ Previous", size="sm", scale=0, min_width=110)
                src_where = gr.Markdown("", elem_classes=["ev-page"])
                src_next = gr.Button("Next ▶", size="sm", scale=0, min_width=110)
            nli_src = gr.HTML()
        with gr.Column(visible=False) as view_rec:
            gr.Markdown("Screening only. Checked against the structured record facts: each sentence is compared with the single "
                        "closest fact, so a faithful sentence that says more than that one fact is often labelled "
                        "*not entailed*. Use these rows to decide what to read; they are not a score.",
                        elem_classes=["ev-read-note"])
            rec_rows, rec_pos = gr.State([]), gr.State(0)
            with gr.Row(elem_classes=["ev-pager"]):
                rec_prev = gr.Button("◀ Previous", size="sm", scale=0, min_width=110)
                rec_where = gr.Markdown("", elem_classes=["ev-page"])
                rec_next = gr.Button("Next ▶", size="sm", scale=0, min_width=110)
            nli = gr.HTML()
        gr.Markdown("**Output text.** Marked in red: a direct identifier left in the text (name, ID, phone, email, "
                    "address). Marked in yellow: a date or age from the record (a quasi-identifier, minor, for review; "
                    "it does not affect the verdict). Marked in orange: a sentence the NLI model finds contradicting the "
                    "source. Unmarked text has no such finding.")
        marked = gr.HighlightedText(show_label=False, combine_adjacent=False, show_legend=False,
                                    color_map={"identifier": "red", "date (quasi-identifier)": "yellow",
                                               "age (quasi-identifier)": "yellow", "NLI contradiction": "orange"},
                                    elem_classes=["ev-output"])
        read = gr.HTML()
        gr.Markdown("Blue: the PL output and its change from the professional narrative. Green: the "
                    "reference explanation and its change. The last column compares the output with the reference.",
                    elem_classes=["ev-read-note"])
        dl = gr.File(label="Download this run (JSON)")
        with gr.Accordion("Technical details (dataset, checksums, environment, metric settings)", open=False):
            prov = gr.JSON(label="Provenance", show_label=False)
        gr.Markdown("Flags and problem findings from every run are queued in **Evaluation Review Queue** for human review.")
        outs = [verdict, three_way_eval, find_rows, rec_rows, src_rows, marked, read, prov, dl]
        tab.select(lambda rid: _run_choices(rid), last_run, pick)
        refresh.click(lambda rid: _run_choices(rid), last_run, pick)
        page_outs = [findings, find_where, find_pos]
        n = FINDING_PAGE_SIZE
        check_view.change(lambda v: [gr.update(visible=v == c) for c in CHECK_VIEWS], check_view,
                          [view_facts, view_src, view_rec])
        pick.change(_show_run, pick, outs).then(_severity_choices, find_rows, find_sev).then(
            lambda r: _finding_page(r, 0, n, "All"), find_rows, page_outs).then(
            lambda a, b: (*_nli_page(a, 0), *_nli_page(b, 0)), [src_rows, rec_rows],
            [nli_src, src_where, src_pos, nli, rec_where, rec_pos])
        for rows_, pos_, prev_, next_, out_ in ((src_rows, src_pos, src_prev, src_next, [nli_src, src_where, src_pos]),
                                                (rec_rows, rec_pos, rec_prev, rec_next, [nli, rec_where, rec_pos])):
            prev_.click(lambda r, p: _nli_page(r, p - 1), [rows_, pos_], out_)
            next_.click(lambda r, p: _nli_page(r, p + 1), [rows_, pos_], out_)
        find_sev.input(lambda r, v: _finding_page(r, 0, n, v), [find_rows, find_sev], page_outs)
        find_prev.click(lambda r, p, v: _finding_page(r, p - 1, n, v), [find_rows, find_pos, find_sev], page_outs)
        find_next.click(lambda r, p, v: _finding_page(r, p + 1, n, v), [find_rows, find_pos, find_sev], page_outs)


# ------------------------------------------------------------------ batch tab
TEMPLATE_COLUMNS = ["case_id", "task", "language", "model_label", "output_text", "source_text", "reference_text"]
BASELINE_METHOD = "reference_baseline"
BASELINE_LABEL = "Reference explanation (baseline)"
VERDICT_SHORT = {"pass_automated_checks": "passed", "needs_human_review": "needs review", "fail": "failed"}
VERDICT_FILTERS = ["All", "Failed", "Needs review", "Passed"]
COMPARE_HEADERS = ["Method", "Lang.", "Outputs", "Pass / review / fail", "Required facts", "Critical",
                   "ID leaks", "NLI contra­dictions", "Readability", "Words", "BERTScore vs reference"]
COMPARE_WIDTHS = ["16%", "6%", "8%", "9%", "11%", "8%", "7%", "9%", "10%", "7%", "9%"]
OUTPUT_HEADERS = ["Method", "Case", "Lang.", "Verdict", "Facts", "Critical", "ID leaks", "Readability", "Words",
                  "Main flag"]
OUTPUT_WIDTHS = ["16%", "8%", "6%", "9%", "6%", "8%", "7%", "10%", "7%", "23%"]


def _template() -> str:
    path = EVAL_RUNTIME / "exports" / "batch_template.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(TEMPLATE_COLUMNS)
        w.writerow(["HC0001", "plain_language", "en", "my-model", "Plain-language text…", "", ""])
    return str(path)


def _batch_methods(task: str) -> list[tuple[str, str]]:
    extra = [(BASELINE_LABEL, BASELINE_METHOD)] if task == "plain_language" else []
    return extra + generators.methods(task)


def _method_name(label: str | None, task: str = "plain_language") -> str:
    """Readable method name from a stored model_label ('hf_local:qwen… · <release name>' → the model's label)."""
    if not label:
        return "Unlabelled"
    if label.startswith("V9 reference") or label == BASELINE_LABEL:
        return BASELINE_LABEL
    base = label.split(" · ")[0]
    names = {v: l for l, v in generators.methods(task)}
    return names.get(base, base)


def _batch_result(res, failures, stem, requested):
    """Status line, failures table and the two downloads for the batch that just ran."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_p, json_p = runs().export(res, f"{stem}_{stamp}") if res else (None, None)
    line = (f"**Last batch:** {requested} requested · {len(res)} evaluated · {len(failures)} could not be evaluated. "
            "Outputs of the same 10 synthetic patients; not independent evidence of clinical performance.")
    fail_rows = [[f.get("case_id") or "—", (f.get("language") or "").upper() or "—",
                  f.get("error") or f.get("reason") or json.dumps(f, ensure_ascii=False)[:200]] for f in failures]
    fail_html = html_table(["Case", "Language", "Why it could not be evaluated"], fail_rows, ["12%", "10%", "78%"],
                           "ev-nli", "Outputs that could not be evaluated (never scored)")
    return (gr.update(value=line, visible=True), gr.update(value=fail_html, visible=bool(failures)),
            gr.update(value=str(csv_p) if csv_p else None, visible=bool(csv_p)),
            gr.update(value=str(json_p) if json_p else None, visible=bool(json_p)))


def _store_batch(res, dataset_id):
    for r in res:
        r["evaluation_dataset_id"] = dataset_id
        _store_run(r)


def _do_batch(file, dataset_id, use_models, progress=gr.Progress()):
    if not file:
        raise gr.Error("Upload a CSV with columns: case_id, task, language, output_text (model_label optional).")
    if not _known_release(dataset_id):
        raise gr.Error("Select an imported evaluation dataset release (reload the page if the app was restarted).")
    rows = read_batch_csv(file)
    hidden = sorted({r.get("task") for r in rows} & set(HIDDEN_TASKS))
    if hidden:
        raise gr.Error(f"Out of scope for this submission (Master Prompt 5.2): {', '.join(hidden)}. "
                       "Remove those rows or start the app with the matching CLINICAL_EVAL_ENABLE_… setting.")
    res, errors = run_batch(release_store(dataset_id), rows, use_models=bool(use_models), progress=progress)
    _store_batch(res, dataset_id)
    return _batch_result(res, errors, "batch", len(rows))


def _generate_batch(dataset_id, task, lang_choice, method, target_words, use_models, progress=gr.Progress()):
    if not _known_release(dataset_id):
        raise gr.Error("Import and select an evaluation dataset first (reload the page if the app was restarted).")
    langs = {"English": ["en"], "Spanish": ["es"], "Both": ["en", "es"]}[lang_choice]
    store = release_store(dataset_id)
    if method == BASELINE_METHOD:
        rows = [{"case_id": c, "task": "plain_language", "language": l, "model_label": BASELINE_LABEL,
                 "output_text": store.narrative(c, "patient", l)} for c in store.case_ids for l in langs]
        res, errors = run_batch(store, rows, use_models=bool(use_models), progress=progress)
        _store_batch(res, dataset_id)
        return _batch_result(res, errors, "release_baseline", len(rows))
    params = ({} if method == "rule_baseline" or task == "anonymization"
              else {"target_words": int(target_words), "max_new_tokens": 700})
    label = f"{method} · {registry().get(dataset_id)['display_name']}"
    res, transformations, failures = generate_and_evaluate(
        store, dataset_id=dataset_id, task=task, languages=langs,
        method=method if task != "anonymization" else "rule_baseline", params=params,
        use_models=bool(use_models), model_label=label, progress=progress)
    for t in transformations:
        _write_transformation(t)
    _store_batch(res, dataset_id)
    return _batch_result(res, failures, f"generated_{task}", len(transformations))


def _latest_runs(dataset_id, task) -> list[dict[str, Any]]:
    """Stored runs for one release and task, keeping only the latest run per method, case and language
    (a method run twice is counted once, with its newest result)."""
    latest: dict[tuple, dict[str, Any]] = {}
    for r in sorted(runs().all(), key=lambda r: r.get("timestamp_utc") or ""):
        if r.get("task") == task and r.get("evaluation_dataset_id") == dataset_id:
            latest[(_method_name(r.get("model_label"), task), r["case_id"], r["language"])] = r
    return list(latest.values())


def _method_choices(dataset_id, task, selected=None, known=None):
    """All methods with stored runs; keeps the user's selection and adds any method that is new since last time."""
    names = sorted({_method_name(r.get("model_label"), task) for r in _latest_runs(dataset_id, task)},
                   key=lambda m: (m != BASELINE_LABEL, m))
    keep = [m for m in names if m in (selected or []) or m not in (known or [])]
    return gr.update(choices=names, value=keep or names), names


def _num(vals):
    vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return vals


def _compare(dataset_id, task, chosen):
    rs = [r for r in _latest_runs(dataset_id, task) if _method_name(r.get("model_label"), task) in (chosen or [])]
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for r in rs:
        groups.setdefault((_method_name(r.get("model_label"), task), r["language"]), []).append(flat_row(r))
    order = sorted(groups, key=lambda k: (k[0] != BASELINE_LABEL, k[0], k[1]))
    rows, csv_rows = [], []
    for m, lang in order:
        g = groups[(m, lang)]
        sup, req = sum(_num(x["required_supported"] for x in g)), sum(_num(x["required_items"] for x in g))
        nli = _num(x["nli_contradictions"] for x in g)
        rd = _num(x["readability_score"] for x in g)
        words = _num(x["words"] for x in g)
        bs = _num(x["bertscore_f1_vs_reference"] for x in g)
        v = [sum(x["verdict"] == k for x in g) for k in ("pass_automated_checks", "needs_human_review", "fail")]
        patients = len({x["case_id"] for x in g})
        row = [m, lang.upper(), f"{len(g)}" + ("" if patients == len(g) else f" ({patients} patients)"),
               f"{v[0]} / {v[1]} / {v[2]}", f"{100 * sup / req:.0f}% ({sup}/{req})" if req else "—",
               str(sum(_num(x["critical_problems"] for x in g))), str(sum(_num(x["direct_identifier_leaks"] for x in g))),
               str(sum(nli)) if nli else "—",
               f"{sum(rd) / len(rd):.1f}" if rd else "—",
               f"{sum(words) / len(words):.0f}" if words else "—",
               f"{sum(bs) / len(bs):.3f}" if bs else "—"]
        rows.append(row)
        csv_rows.append(dict(zip(COMPARE_HEADERS, row)))
    table = html_table(COMPARE_HEADERS, rows, COMPARE_WIDTHS, "ev-nli",
                       "Comparison by method and language (latest run of each method, case and language)")
    if not rows:
        table = html_table(COMPARE_HEADERS, [], COMPARE_WIDTHS, "ev-nli",
                           "Comparison by method and language: no runs yet for this release")
    path = EVAL_RUNTIME / "exports" / "comparison_by_method_language.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COMPARE_HEADERS)
        w.writeheader()
        w.writerows(csv_rows)
    sev = {"fail": 0, "needs_human_review": 1, "pass_automated_checks": 2}
    out_rows = []
    for r in sorted(rs, key=lambda r: (sev.get(r["verdict"], 3), _method_name(r.get("model_label"), task), r["case_id"],
                                       r["language"])):
        f = flat_row(r)
        main = next((x["reason"] for x in r["flags"] if x["level"] == "fail"), None) or \
            next((x["reason"] for x in r["flags"] if x["level"] == "review"), None) or "—"
        out_rows.append({"run_id": r["run_id"], "verdict": r["verdict"], "cells": [
            _method_name(r.get("model_label"), task), r["case_id"], r["language"].upper(),
            VERDICT_SHORT.get(r["verdict"], r["verdict"]),
            f"{f['required_supported']}/{f['required_items']}", f["critical_problems"], f["direct_identifier_leaks"],
            f"{f['readability_score']:.1f}" if isinstance(f["readability_score"], (int, float)) else "—",
            f["words"], main]})
    return table, (str(path) if rows else None), out_rows


def _output_page(rows, page, verdict):
    sel = rows if verdict in (None, "All") else \
        [r for r in rows if VERDICT_SHORT.get(r["verdict"]) == {"Failed": "failed", "Needs review": "needs review",
                                                                 "Passed": "passed"}[verdict]]
    size = FINDING_PAGE_SIZE
    pages = max(1, -(-len(sel) // size))
    page = min(max(0, int(page or 0)), pages - 1)
    chunk = sel[page * size:(page + 1) * size]
    where = (f"Rows {page * size + 1}–{page * size + len(chunk)} of {len(sel)} · page {page + 1} of {pages}"
             if sel else "No outputs to show.")
    opts = [(f"{' · '.join(str(c) for c in r['cells'][:4])}", r["run_id"]) for r in sel]
    return (html_table(OUTPUT_HEADERS, [r["cells"] for r in chunk], OUTPUT_WIDTHS, "ev-nli"), where, page,
            gr.update(choices=opts, value=opts[page * size][1] if opts else None))


def _verdict_choices(rows):
    n = {k: sum(VERDICT_SHORT.get(r["verdict"]) == v for r in rows)
         for k, v in (("Failed", "failed"), ("Needs review", "needs review"), ("Passed", "passed"))}
    return gr.update(choices=[(f"All ({len(rows)})", "All")] + [(f"{k} ({n[k]})", k) for k in VERDICT_FILTERS[1:]],
                     value="All")


def _export_bundle(public):
    path = eval_export.build_bundle(EVAL_RUNTIME, public=bool(public), exclude_tasks=HIDDEN_TASKS)
    return gr.update(value=str(path), visible=True), f"Bundle written: `{path.name}`"


_OPEN_EVAL_JS = """() => { const t = [...document.querySelectorAll('[role=tab]')]
    .find(b => b.innerText.trim() === 'Evaluation and Metrics'); if (t) { t.click(); window.scrollTo(0, 0); } }"""


def batch_tab(last_run: gr.State):
    with gr.Tab("Batch Comparison and Export", id="eval_batch") as tab:
        gr.Markdown("Compare methods over the whole release: produce outputs for all 10 patients, compare the "
                    "results by method and language, and export everything with provenance.")
        gr.Markdown("### 1. Produce outputs", elem_classes=["ev-section"])
        with gr.Row():
            release = _release_dropdown("Evaluation dataset release", scale=3)
            task = gr.Dropdown(VISIBLE_TASKS, value="plain_language", label="Task", visible=len(VISIBLE_TASKS) > 1)
            lang = gr.Radio(["English", "Spanish", "Both"], value="Both", label="Languages", scale=2)
        with gr.Row():
            method = gr.Dropdown(_batch_methods("plain_language"), value="rule_baseline", label="Method", scale=3)
            target_words = gr.Slider(60, 400, value=320, step=10, label="Target length (words)", visible=False, scale=2)
            use_models = gr.Checkbox(value=True, label="Include model-based metrics (BERTScore, AlignScore, NLI; slower)",
                                     scale=2)
        gen_btn = gr.Button("Generate and evaluate all cases", variant="primary")
        with gr.Accordion("Or evaluate outputs produced elsewhere (CSV upload)", open=False):
            gr.Markdown("One row per output: `case_id`, `task` (" + " or ".join(t for _, t in VISIBLE_TASKS) + "), "
                        "`language` (en or es), `output_text`, and optionally `model_label` (the method name shown in "
                        "the comparison), `source_text`, `reference_text`. Uses the release and the model-metrics "
                        "setting above.")
            with gr.Row():
                file = gr.File(label="Batch CSV", file_types=[".csv"], type="filepath")
                gr.File(label="Template", value=_template())
            run_btn = gr.Button("Evaluate CSV")
        # always visible: the progress bar of a running batch is drawn over this box
        status = gr.Markdown("No batch run yet in this session. While a batch runs, its progress shows here.",
                             elem_classes=["ev-status"], min_height=60)
        failures = gr.HTML(visible=False)
        with gr.Row():
            out_csv = gr.File(label="Per-output CSV (last batch)", visible=False)
            out_json = gr.File(label="Full results JSON with provenance (last batch)", visible=False)

        gr.Markdown("### 2. Compare results", elem_classes=["ev-section"])
        gr.Markdown("Built from every stored run for this release, keeping the latest run of each method, case and "
                    "language. Pass means the automated checks passed; it is not a clinical approval. Required facts: "
                    "supported out of required, pooled over the outputs. Readability is the mean Flesch reading ease "
                    "(EN) or Fernández-Huerta (ES); the target is 60 or higher. Words is the mean length. "
                    "— means not measured (model metrics were off).", elem_classes=["ev-read-note"])
        with gr.Row():
            chosen = gr.CheckboxGroup([], label="Methods to compare", scale=5)
            refresh = gr.Button("Refresh", size="sm", scale=0, min_width=110)
        compare = gr.HTML()
        compare_csv = gr.File(label="Comparison table (CSV)", visible=False)
        gr.Markdown("**Per output.** Failed first, then needs review, then passed; 5 rows per page.",
                    elem_classes=["ev-read-note"])
        out_rows, out_pos = gr.State([]), gr.State(0)
        with gr.Row(elem_classes=["ev-pager"]):
            vfilter = gr.Dropdown([("All", "All")] + [(v, v) for v in VERDICT_FILTERS[1:]], value="All",
                                  show_label=False, container=False, scale=0, min_width=170)
            prev_b = gr.Button("◀ Previous", size="sm", scale=0, min_width=110)
            where = gr.Markdown("", elem_classes=["ev-page"])
            next_b = gr.Button("Next ▶", size="sm", scale=0, min_width=110)
        outputs_tbl = gr.HTML()
        with gr.Row(elem_classes=["ev-pager"]):
            open_pick = gr.Dropdown([], label="Open one output in Evaluation and Metrics", scale=4)
            open_btn = gr.Button("Open in Evaluation and Metrics", size="sm", scale=1)

        gr.Markdown("### 3. Export", elem_classes=["ev-section"])
        gr.Markdown("The whole evaluation workspace: outputs, patient-level and batch metrics, findings, bilingual "
                    "comparison, review queue, configuration, provenance manifest and SHA256SUMS. The public option "
                    "pseudonymises reviewer names and withholds review notes.", elem_classes=["ev-read-note"])
        with gr.Row():
            public = gr.Checkbox(value=True, label="Public (GitHub-safe) export")
            bundle_btn = gr.Button("Build export bundle")
        bundle_file = gr.File(label="Export bundle (ZIP)", visible=False)
        bundle_msg = gr.Markdown()

        # ---- wiring
        def refresh_all(ds, t, sel, known):
            choices, names = _method_choices(ds, t, sel, known)
            table, path, rows = _compare(ds, t, choices["value"])
            return (choices, names, table, gr.update(value=path, visible=bool(path)), rows, _verdict_choices(rows),
                    *_output_page(rows, 0, "All"))

        def recompute(ds, t, sel):
            table, path, rows = _compare(ds, t, sel)
            return (table, gr.update(value=path, visible=bool(path)), rows, _verdict_choices(rows),
                    *_output_page(rows, 0, "All"))

        page_outs = [outputs_tbl, where, out_pos, open_pick]
        known = gr.State([])
        full = [chosen, known, compare, compare_csv, out_rows, vfilter] + page_outs
        task.change(lambda t: gr.update(choices=_batch_methods(t), value="rule_baseline"), task, method)
        method.change(lambda m: gr.update(visible=str(m).startswith("hf_local:")), method, target_words)
        batch_outs = [status, failures, out_csv, out_json]
        gen_btn.click(_generate_batch, [release, task, lang, method, target_words, use_models], batch_outs).then(
            refresh_all, [release, task, chosen, known], full)
        run_btn.click(_do_batch, [file, release, use_models], batch_outs).then(refresh_all, [release, task, chosen, known], full)
        for ev in (tab.select, refresh.click):
            ev(refresh_all, [release, task, chosen, known], full)
        release.change(refresh_all, [release, task, chosen, known], full)
        chosen.input(recompute, [release, task, chosen], [compare, compare_csv, out_rows, vfilter] + page_outs)
        vfilter.input(lambda r, v: _output_page(r, 0, v), [out_rows, vfilter], page_outs)
        prev_b.click(lambda r, p, v: _output_page(r, p - 1, v), [out_rows, out_pos, vfilter], page_outs)
        next_b.click(lambda r, p, v: _output_page(r, p + 1, v), [out_rows, out_pos, vfilter], page_outs)
        open_btn.click(lambda rid: rid, open_pick, last_run).then(None, js=_OPEN_EVAL_JS)
        bundle_btn.click(_export_bundle, public, [bundle_file, bundle_msg])
        tab.select(_release_choices, release, release)


# ------------------------------------------------------------------ review queue tab
RQ_HEADERS = ["Method", "Case", "Lang.", "Severity", "Check", "Finding", "Status"]
RQ_WIDTHS = ["16%", "8%", "6%", "8%", "15%", "35%", "12%"]
RQ_FAMILIES = ["Structured facts", "Identifiers", "Faithfulness (NLI, AlignScore)", "Readability and length",
               "Plain style"]
RQ_SEVERITY_ORDER = {"critical": 0, "fail": 1, "major": 2, "minor": 3, "review": 4, "info": 5}
# flags that only summarise findings already in the queue (or are informational) are folded away
_RQ_FOLDED = re.compile(r"critical clinical problem|against the structured record$|Required facts supported|"
                        r"unsupported addition|direct identifier|Metrics unavailable|Reference narratives are candidates",
                        re.I)


def _flag_family(reason: str) -> str:
    if re.search(r"NLI|AlignScore|not supported by the source", reason):
        return "Faithfulness (NLI, AlignScore)"
    if re.search(r"Readability|source length", reason):
        return "Readability and length"
    if re.search(r"Medical terms|Follow-up repeats|patient failed", reason):
        return "Plain style"
    return "Other"


def _rq_items(dataset_id, include_tests=False) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Queue items for one release, from the latest run of each method, case and language (as in the Batch tab),
    with method, check and check family attached. Summary flags are folded into their findings."""
    from evaluation.review_queue import _item_id
    if not _known_release(dataset_id):
        return [], {}
    n_cases = len(release_store(dataset_id).case_ids)
    latest = [r for _, t in VISIBLE_TASKS for r in _latest_runs(dataset_id, t)]
    if not include_tests:
        groups: dict[tuple, set] = {}
        for r in latest:
            groups.setdefault((_method_name(r.get("model_label"), r["task"]), r["task"], r["language"]), set()).add(
                r["case_id"])
        complete = {k for k, v in groups.items() if len(v) >= n_cases}
        latest = [r for r in latest if (_method_name(r.get("model_label"), r["task"]), r["task"], r["language"])
                  in complete]
    run_map = {r["run_id"]: r for r in latest}
    checks: dict[str, dict[str, Any]] = {}
    for r in latest:
        for sec in ("factual_agreement_structured", "identifier_checks"):
            for f in r.get(sec, {}).get("findings", []):
                if f.get("is_problem"):
                    text = f"{f['classification']}: {f['explanation']}"
                    ev = " | ".join(x for x in (f.get("json_pointer"), f.get("source_evidence"),
                                                f.get("output_evidence")) if x)
                    checks[_item_id(r["run_id"], sec, text + ev)] = f
    out = []
    for i in queue().items("All"):
        run = run_map.get(i["evaluation_run_id"])
        if not run:
            continue
        f = checks.get(i["item_id"])
        if i["source"] == "automated flag":
            if i["severity"] == "info" or _RQ_FOLDED.search(i["finding"]):
                continue
            family, check = _flag_family(i["finding"]), "flag"
        else:
            check = f["check"] if f else i["source"]
            family = "Identifiers" if check.startswith("pii") else "Structured facts"
        out.append({**i, "method": _method_name(run.get("model_label"), run["task"]), "family": family,
                    "check": check, "finding_obj": f})
    out.sort(key=lambda x: (x["review_status"] != "Pending", RQ_SEVERITY_ORDER.get(x["severity"], 9), x["method"],
                            x["patient_id"], x["language"]))
    return out, run_map


def _rq_filter(items, method, lang, sev, family, status):
    return [i for i in items if (method in (None, "All") or i["method"] == method)
            and (lang in (None, "All") or i["language"] == lang.lower())
            and (sev in (None, "All") or i["severity"] == sev)
            and (family in (None, "All") or i["family"] == family)
            and (status in (None, "All") or i["review_status"] == status)]


def _rq_table(sel, page, selected_id):
    size = FINDING_PAGE_SIZE
    pages = max(1, -(-len(sel) // size))
    page = min(max(0, int(page or 0)), pages - 1)
    chunk = sel[page * size:(page + 1) * size]
    where = (f"Rows {page * size + 1}–{page * size + len(chunk)} of {len(sel)} · page {page + 1} of {pages}"
             if sel else "Nothing to review with these filters.")
    head = "".join(f"<th>{html.escape(h)}</th>" for h in RQ_HEADERS)
    cols = "".join(f'<col style="width:{w}">' for w in RQ_WIDTHS)
    body = ""
    for i in chunk:
        status = i["decision"] or "Pending"
        cells = [i["method"], i["patient_id"], i["language"].upper(), i["severity"], i["check"], i["finding"], status]
        js = ("const t=document.querySelector('#rq-pick textarea');t.value='" + i["item_id"] +
              "';t.dispatchEvent(new Event('input',{bubbles:true}));")
        cls = ' class="rq-sel"' if i["item_id"] == selected_id else ""
        body += f'<tr{cls} onclick="{html.escape(js)}">' + "".join(
            f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>"
    if not body:
        body = f'<tr><td colspan="{len(RQ_HEADERS)}" class="ev-empty">No items.</td></tr>'
    table = (f'<div class="ev-table ev-nli rq-table"><table><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead>'
             f"<tbody>{body}</tbody></table></div>")
    return table, where, page


def _closest_sentence(text: str, value: str) -> str:
    words = set(re.findall(r"\w{3,}", (value or "").lower()))
    best, score = "", 0.0
    for s in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        w = set(re.findall(r"\w{3,}", s.lower()))
        if w and words:
            sc = len(w & words) / len(words)
            if sc > score:
                best, score = s.strip(), sc
    return best if score >= 0.3 else ""


def _rq_panel(item, run) -> str:
    if not item:
        return "<div class='rq-panel ev-empty'>Click a row in the table to review it.</div>"
    rows = [("Finding", item["finding"]), ("Method · case · language",
            f"{item['method']} · {item['patient_id']} · {item['language'].upper()}"),
            ("Severity · check", f"{item['severity']} · {item['check']} ({item['family']})")]
    f = item.get("finding_obj")
    if f:
        rows += [("Structured record", f"{f.get('json_pointer') or '—'}: {f.get('source_evidence') or '—'}"),
                 ("In the output", f.get("output_evidence") or "— (not found in the output)")]
        src = _closest_sentence((run or {}).get("texts", {}).get("source"), f.get("source_evidence") or "")
        if src:
            rows.append(("Professional narrative (closest sentence)", src))
    elif run and "NLI" in item["finding"]:
        nli = run["source_faithfulness"].get("nli_output_vs_source") or {}
        bad = [s for s in nli.get("sentences", []) if s["label"] != "entailed"][:3]
        for s in bad:
            rows.append((f"Output sentence ({s['label'].replace('_', ' ')})", s["sentence"]))
            rows.append(("Closest source text", s.get("closest_premise") or "—"))
    if item["decision"]:
        rows.append(("Current decision", f"{item['decision']} by {item['reviewer']} ({item['decided_at_utc'][:16]})"
                     + (f" · note: {item['note']}" if item.get("note") else "")))
    body = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    return f'<div class="ev-table rq-panel"><table><colgroup><col style="width:24%"><col></colgroup>{body}</table></div>'


def _rq_summary(items) -> tuple[str, str, str | None]:
    dec = list(DECISIONS)
    fam_rows, csv_rows = [], []
    for fam in RQ_FAMILIES + ["Other"]:
        g = [i for i in items if i["family"] == fam]
        if not g:
            continue
        rev = [i for i in g if i["decision"]]
        counts = [sum(i["decision"] == d for i in rev) for d in dec]
        fp = f"{100 * counts[1] / len(rev):.0f}%" if rev else "—"
        fam_rows.append([fam, len(g), len(rev), *counts, fp])
    fam_html = html_table(["Check family", "Items", "Reviewed", *dec, "False-positive share"], fam_rows,
                          ["22%", "8%", "9%", "11%", "10%", "11%", "11%", "18%"], "ev-nli",
                          "By check family (how often the automated checks were right)")
    meth_rows = []
    for m, lang in sorted({(i["method"], i["language"]) for i in items}, key=lambda k: (k[0] != BASELINE_LABEL, k)):
        g = [i for i in items if i["method"] == m and i["language"] == lang]
        rev = [i for i in g if i["decision"]]
        conf = [i for i in rev if i["decision"] == "Confirmed problem"]
        outputs = len({i["evaluation_run_id"] for i in g})
        meth_rows.append([m, lang.upper(), outputs, len(g), len(rev), len(conf),
                          sum(i["severity"] in ("critical", "fail") for i in conf),
                          sum(i["decision"] == "Needs clinical input" for i in rev),
                          f"{len(conf) / outputs:.2f}" if outputs and rev else "—"])
    heads = ["Method", "Lang.", "Outputs with items", "Items", "Reviewed", "Confirmed problems", "of which critical",
             "Needs clinical input", "Confirmed per output"]
    meth_html = html_table(heads, meth_rows, ["20%", "6%", "10%", "8%", "9%", "11%", "10%", "12%", "14%"], "ev-nli",
                           "By method and language (human-confirmed problems)")
    for r in fam_rows:
        csv_rows.append({"table": "check_family", "group": r[0], "items": r[1], "reviewed": r[2],
                         **dict(zip(dec, r[3:7])), "false_positive_share": r[7]})
    for r in meth_rows:
        csv_rows.append({"table": "method", "group": f"{r[0]} · {r[1]}", "outputs_with_items": r[2], "items": r[3],
                         "reviewed": r[4], "confirmed_problems": r[5], "confirmed_critical": r[6],
                         "needs_clinical_input": r[7], "confirmed_per_output": r[8]})
    path = None
    if csv_rows:
        path = EVAL_RUNTIME / "exports" / "review_results.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = list(dict.fromkeys(k for r in csv_rows for k in r))
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(csv_rows)
    return fam_html, meth_html, (str(path) if path else None)


def review_queue_tab(last_run: gr.State):
    with gr.Tab("Evaluation Review Queue", id="eval_review") as tab:
        gr.Markdown("Human review of the automated findings. Decisions are stored separately and never change the "
                    "clinical reference, its sign-off or its freeze. The reviewer name is recorded as typed.")
        with gr.Row():
            release = _release_dropdown("Evaluation dataset release", scale=3)
            include_tests = gr.Checkbox(value=False, label="Include single test runs and incomplete batches", scale=2)
        with gr.Row():
            f_method = gr.Dropdown(["All"], value="All", label="Method", scale=3)
            f_lang = gr.Dropdown(["All", "EN", "ES"], value="All", label="Language", scale=1, min_width=100)
            f_sev = gr.Dropdown(["All"], value="All", label="Severity", scale=1, min_width=120)
            f_family = gr.Dropdown(["All"] + RQ_FAMILIES, value="All", label="Check family", scale=2)
            f_status = gr.Dropdown(["Pending", "Reviewed", "All"], value="Pending", label="Status", scale=1,
                                   min_width=120)
        with gr.Row(elem_classes=["ev-pager"]):
            prev_b = gr.Button("◀ Previous", size="sm", scale=0, min_width=110)
            where = gr.Markdown("", elem_classes=["ev-page"])
            next_b = gr.Button("Next ▶", size="sm", scale=0, min_width=110)
        table = gr.HTML()
        pick = gr.Textbox(elem_id="rq-pick", elem_classes=["rq-hidden"], show_label=False, container=False)
        gr.Markdown("### Review the selected item", elem_classes=["ev-section"])
        panel = gr.HTML()
        with gr.Row():
            decision = gr.Radio(list(DECISIONS), value=None, label="Decision", scale=3)
            reviewer = gr.Textbox(label="Reviewer (kept for this session)", scale=1)
        note = gr.Textbox(label="Note (optional)", lines=2)
        with gr.Row():
            save = gr.Button("Save and next", variant="primary")
            open_btn = gr.Button("Open this run in Evaluation and Metrics")
        msg = gr.Markdown()
        gr.Markdown("### Review results", elem_classes=["ev-section"])
        gr.Markdown("From the decisions recorded so far, for the release and runs selected above (all statuses).",
                    elem_classes=["ev-read-note"])
        fam_tbl = gr.HTML()
        meth_tbl = gr.HTML()
        res_csv = gr.File(label="Review results (CSV)", visible=False)
        pos = gr.State(0)
        sel_id = gr.State(None)

        filters = [release, include_tests, f_method, f_lang, f_sev, f_family, f_status]

        def render(ds, inc, m, l, s, fam, st, page, chosen, keep_choices=True):
            items, run_map = _rq_items(ds, inc)
            sel = _rq_filter(items, m, l, s, fam, st)
            ids = [i["item_id"] for i in sel]
            if page is None:
                page = ids.index(chosen) // FINDING_PAGE_SIZE if chosen in ids else 0
            page = min(max(0, int(page)), max(0, (len(ids) - 1) // FINDING_PAGE_SIZE))
            on_page = ids[page * FINDING_PAGE_SIZE:(page + 1) * FINDING_PAGE_SIZE]
            if chosen not in on_page:
                chosen = on_page[0] if on_page else None
            tbl, wh, page = _rq_table(sel, page, chosen)
            item = next((i for i in sel if i["item_id"] == chosen), None)
            fam_html, meth_html, path = _rq_summary(items)
            methods = ["All"] + sorted({i["method"] for i in items})
            sevs = ["All"] + sorted({i["severity"] for i in items}, key=lambda x: RQ_SEVERITY_ORDER.get(x, 9))
            return (tbl, wh, page, chosen, _rq_panel(item, run_map.get(item["evaluation_run_id"]) if item else None),
                    gr.update(value=(item or {}).get("decision")), gr.update(value=(item or {}).get("note") or ""),
                    fam_html, meth_html, gr.update(value=path, visible=bool(path)),
                    gr.update(choices=methods, value=m if m in methods else "All"),
                    gr.update(choices=sevs, value=s if s in sevs else "All"))

        outs = [table, where, pos, sel_id, panel, decision, note, fam_tbl, meth_tbl, res_csv, f_method, f_sev]

        def first_page(*a):
            return render(*a, 0, None)

        for comp in filters:
            comp.input(first_page, filters, outs)
        tab.select(first_page, filters, outs)
        prev_b.click(lambda *a: render(*a[:-2], a[-2] - 1, None), filters + [pos, sel_id], outs)
        next_b.click(lambda *a: render(*a[:-2], a[-2] + 1, None), filters + [pos, sel_id], outs)
        pick.input(lambda *a: render(*a[:-2], a[-2], a[-1]), filters + [pos, pick], outs)

        def save_next(ds, inc, m, l, s, fam, st, page, item_id, dec, who, txt):
            if not item_id:
                raise gr.Error("Click a row in the table first.")
            if not dec:
                raise gr.Error("Choose a decision.")
            before = [i["item_id"] for i in _rq_filter(_rq_items(ds, inc)[0], m, l, s, fam, st)]
            try:
                rec = queue().decide(item_id, dec, who, txt)
            except (KeyError, ValueError) as e:
                raise gr.Error(str(e))
            ids = [i["item_id"] for i in _rq_filter(_rq_items(ds, inc)[0], m, l, s, fam, st)]
            # next item: the one after the saved item in the list as it was (the saved one may have left the list)
            after = before[before.index(item_id) + 1:] if item_id in before else []
            nxt = next((x for x in after if x in ids), ids[0] if ids else None)
            out = render(ds, inc, m, l, s, fam, st, None, nxt)
            return (*out, f"Saved **{rec['decision']}** by {html.escape(rec['reviewer'])}.")

        save.click(save_next, filters + [pos, sel_id, decision, reviewer, note], outs + [msg])

        def open_run(item_id, ds, inc):
            items, _ = _rq_items(ds, inc)
            return next((i["evaluation_run_id"] for i in items if i["item_id"] == item_id), None)

        open_btn.click(open_run, [sel_id, release, include_tests], last_run).then(None, js=_OPEN_EVAL_JS)
        tab.select(_release_choices, release, release)


# ------------------------------------------------------------------ entry point
INTROS = {
    "anonymization": "### Anonymization\nReplace identifying details while keeping every clinical fact. "
                     "**Primary checks:** identifier removal and residual PII, and survival of every structured fact "
                     "present in the input. BERTScore here measures preservation of the input text.",
    "summarization": "### Summarization\nConcise clinical summary. **Primary check:** critical facts from the structured "
                     "record (diagnoses, medications and doses, warning signs, follow-up). Less important details may be "
                     "omitted. AlignScore (English) and NLI check that nothing contradicts the input.",
    "plain_language": "### Plain language\nPatient-friendly adaptation. **Primary checks:** structured facts, contradictions "
                      "and readability. Similarity is measured against the V9 patient explanation, which is a candidate "
                      "reference that has not yet been clinically reviewed.",
}


def add_evaluation_tabs():
    """Render the Clinical NLP Dashboard tabs inside the current gr.Blocks context."""
    try:
        load_approved_freeze(FREEZE_PATH)
        registry()
    except Exception as e:  # noqa: BLE001
        with gr.Tab("Clinical NLP evaluation"):
            gr.Markdown(f"**Evaluation workspace unavailable:** {html.escape(str(e))}\n\n"
                        f"Approved freeze: `{FREEZE_PATH}`")
        return
    try:  # import torch on the main thread; a first import inside a worker thread has crashed oneMKL
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001
        pass
    last_run = gr.State(None)
    upload_tab()
    explore_tab()
    if ANONYMIZATION_ENABLED:
        transformation_tab("anonymization", "Anonymization", INTROS["anonymization"], 8, last_run)
    if SUMMARIZATION_ENABLED:
        transformation_tab("summarization", "Summarization", INTROS["summarization"], 6, last_run)
    transformation_tab("plain_language", "Plain-Language Adaptation", INTROS["plain_language"], 8, last_run)
    evaluation_tab(last_run)
    batch_tab(last_run)
    review_queue_tab(last_run)


if __name__ == "__main__":
    with gr.Blocks(title="Clinical NLP Dashboard") as demo:
        gr.Markdown("# Clinical NLP Dashboard")
        add_evaluation_tabs()
    demo.launch(show_api=False, allowed_paths=[str(EVAL_RUNTIME)])
