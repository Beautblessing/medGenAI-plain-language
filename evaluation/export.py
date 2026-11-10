"""Evaluation export bundle: outputs, metrics, findings, review queue, configuration, provenance, checksums.

Reads only the evaluation runtime (CLINICAL_EVAL_RUNTIME). With ``public=True`` reviewer names and
free-text review notes are replaced by stable pseudonyms so the bundle can be published.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .batch import RunStore, aggregate, flat_row
from .profiles import PROFILES
from .provenance import environment, metric_configuration
from .review_queue import ReviewQueue


def _csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    if rows:
        fields = list(dict.fromkeys(k for r in rows for k in r))
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _json(v: Any) -> bytes:
    return json.dumps(v, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def findings_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in runs:
        for sec in ("factual_agreement_structured", "identifier_checks"):
            for f in r.get(sec, {}).get("findings", []):
                out.append({"evaluation_run_id": r["run_id"], "patient_id": r["case_id"], "language": r["language"],
                            "task": r["task"], "model_label": r.get("model_label"), "section": sec,
                            "severity": f.get("severity"), "classification": f.get("classification"),
                            "check": f.get("check"), "is_problem": f.get("is_problem"),
                            "json_pointer": f.get("json_pointer"), "source_evidence": f.get("source_evidence"),
                            "output_evidence": f.get("output_evidence"), "explanation": f.get("explanation")})
    return out


def bilingual_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"evaluation_run_id": r["run_id"], "patient_id": r["case_id"], "language": r["language"], "task": r["task"],
             "model_label": r.get("model_label"), "status": r["bilingual_equivalence"].get("status"),
             "cosine": r["bilingual_equivalence"].get("cosine")}
            for r in runs if isinstance(r.get("bilingual_equivalence"), dict)]


# Master Prompt 5.2: the submitted evaluation is plain-language adaptation (professional narrative -> output ->
# reference patient explanation). Anonymization and summarization are out of scope and excluded by default.
DEFAULT_EXCLUDED_TASKS: tuple[str, ...] = ("anonymization", "summarization")
_OUTPUT_FILES = {"anonymization": "outputs/anonymized_texts.csv", "summarization": "outputs/summaries.csv",
                 "plain_language": "outputs/plain_language.csv"}


def build_bundle(eval_runtime: str | os.PathLike, *, run_ids: list[str] | None = None, public: bool = False,
                 hash_checkpoint: bool = True, exclude_tasks: tuple[str, ...] | None = None) -> Path:
    """Build the export ZIP. Runs, transformations and review items of ``exclude_tasks`` (default: anonymization
    and summarization, out of scope for the submission) are left out; pass ``exclude_tasks=()`` to include all."""
    excluded = tuple(DEFAULT_EXCLUDED_TASKS if exclude_tasks is None else exclude_tasks)
    root = Path(eval_runtime)
    store = RunStore(root)
    runs = [r for r in store.all() if r.get("task") not in excluded]
    if run_ids:
        wanted = set(run_ids)
        runs = [r for r in runs if r["run_id"] in wanted]
    transformations = []
    for p in sorted((root / "transformations").glob("transformation_*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        if (not run_ids or t.get("evaluation_run_id") in set(run_ids)) and t.get("task") not in excluded:
            transformations.append(t)
    queue = [q for q in ReviewQueue(root).items() if q.get("task") not in excluded]
    if public:
        names: dict[str, str] = {}
        for q in queue:
            if q.get("reviewer"):
                q["reviewer"] = names.setdefault(q["reviewer"], f"reviewer-{len(names) + 1}")
            q["note"] = "[withheld in public export]" if q.get("note") else q.get("note")
    flat = [flat_row(r) for r in runs]
    registry = json.loads((root / "dataset_registry.json").read_text(encoding="utf-8")) \
        if (root / "dataset_registry.json").exists() else {"datasets": []}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files: dict[str, bytes] = {
        "outputs/transformations.json": _json(transformations),
        "metrics/patient_level_metrics.csv": _csv(flat),
        "metrics/batch_summary.csv": _csv(aggregate(flat)),
        "metrics/evaluation_runs.json": _json(runs),
        "findings/factual_consistency_findings.csv": _csv(findings_rows(runs)),
        "findings/bilingual_comparison.csv": _csv(bilingual_rows(runs)),
        "review/human_review_queue.csv": _csv(queue),
        "config/evaluation_profiles.json": _json(PROFILES),
        "config/metric_configuration.json": _json(metric_configuration(root, hash_checkpoint=hash_checkpoint)),
        "config/environment.json": _json(environment()),
        "provenance/evaluation_dataset_registry.json": _json(registry),
    }
    for task, name in _OUTPUT_FILES.items():
        if task not in excluded:
            files[name] = _csv([_out(t) for t in transformations if t["task"] == task])
    manifest = {
        "bundle": f"clinical_nlp_evaluation_export_{stamp}",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "public_export": public,
        "counts": {"evaluation_runs": len(runs), "transformations": len(transformations), "review_items": len(queue)},
        "approved_reference": sorted({str(r.get("provenance", {}).get("dataset_canonical_sha256")) for r in runs}),
        "scope_note": ("All tasks included." if not excluded else
                       f"Excluded as out of scope for this submission (Master Prompt 5.2): {', '.join(excluded)}. "
                       "The submitted evaluation compares the professional narrative, the plain-language output and "
                       "the reference patient explanation."),
        "excluded_tasks": list(excluded),
        "governance_note": "Evaluation results only. They do not approve, reject or modify the clinical reference; "
                           "a technical pass is not medical or bilingual approval.",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["provenance/provenance_manifest.json"] = _json(manifest)
    sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sorted(files.items()))
    files["SHA256SUMS"] = sums.encode()
    out = root / "exports" / f"{manifest['bundle']}{'_public' if public else ''}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return out


def _out(t: dict[str, Any]) -> dict[str, Any]:
    return {"transformation_id": t["transformation_id"], "evaluation_run_id": t.get("evaluation_run_id"),
            "evaluation_dataset_id": t.get("evaluation_dataset_id"), "patient_id": t["patient_id"],
            "language": t["language"], "method": t["method"], "execution_status": t["execution_status"],
            "source_sha256": t["source_sha256"], "output_sha256": t["output_sha256"],
            "generated_output": t["generated_output"], "error_details": t.get("error_details")}
