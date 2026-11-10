"""Batch evaluation, flat result rows for CSV, and append-only run storage."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .engine import evaluate
from .references import ReferenceStore

REQUIRED_COLUMNS = ("case_id", "task", "language", "output_text")
OPTIONAL_COLUMNS = ("model_label", "source_text", "reference_text")


def _g(d: dict, *path, default=None):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return default
        d = d[p]
    return d


def flat_row(run: dict[str, Any]) -> dict[str, Any]:
    fs = run["factual_agreement_structured"]["summary"]
    pi = run["identifier_checks"]["summary"]
    rd = run["readability"]["output"]
    sf, sp, rs = run["source_faithfulness"], run["source_preservation"], run["reference_similarity"]
    nli = _g(sf, "nli_output_vs_source") or {}
    return {
        "run_id": run["run_id"], "timestamp_utc": run["timestamp_utc"], "model_label": run["model_label"],
        "task": run["task"], "case_id": run["case_id"], "language": run["language"], "verdict": run["verdict"],
        # authority: structured JSON
        "fact_agreement": fs["fact_agreement"], "required_supported": fs["required_supported"],
        "required_items": fs["required_items"], "omissions": fs["omissions"],
        "contradictions": fs["contradictions"], "unsupported_additions": fs["unsupported_additions"],
        "critical_problems": fs["critical_problems"],
        # identifiers
        "identifier_removal_rate": pi["identifier_removal_rate"], "direct_identifier_leaks": pi["direct_leaks"],
        "quasi_identifier_hits": pi["quasi_identifier_hits"],
        # faithfulness to source
        "alignscore": _g(sf, "alignscore", "score"), "nli_contradictions": nli.get("contradiction_count"),
        "nli_entailed_rate": nli.get("entailed_rate"),
        # similarity (never used in the verdict)
        "bertscore_f1_vs_source": _g(sp, "bertscore_vs_source", "f1"),
        "embedding_cos_vs_source": _g(sp, "embedding_vs_source", "cosine"),
        "bertscore_f1_vs_reference": _g(rs, "bertscore_vs_reference", "f1"),
        "embedding_cos_vs_reference": _g(rs, "embedding_vs_reference", "cosine"),
        "bilingual_cosine": _g(run, "bilingual_equivalence", "cosine"),
        # readability
        "readability_metric": rd["primary_score_name"], "readability_score": rd["primary_score"],
        "words": rd["words"], "length_ratio_vs_source": run["readability"].get("length_ratio"),
        "flags": " | ".join(f"{f['level']}: {f['reason']}" for f in run["flags"]),
        "reference_status": run["provenance"]["reference_narratives_status"],
        "dataset_canonical_sha256": run["provenance"]["dataset_canonical_sha256"],
        "profile_hash": run["profile_hash"],
        "evaluation_dataset_id": run.get("evaluation_dataset_id"),
        "transformation_id": run.get("transformation_id"),
        "missing_metrics": ",".join(sorted(_missing(run))),
    }


def _missing(run: dict[str, Any]) -> set[str]:
    """Model metrics that were requested but did not produce a score (unavailable or error)."""
    out = set()
    blocks = {"record_nli": run["factual_agreement_structured"].get("record_nli"), **run["source_faithfulness"],
              **run["source_preservation"], **{k: v for k, v in run["reference_similarity"].items() if isinstance(v, dict)}}
    if isinstance(run.get("bilingual_equivalence"), dict):
        blocks["bilingual_equivalence"] = run["bilingual_equivalence"]
    for k, v in blocks.items():
        if isinstance(v, dict) and v.get("status") in ("unavailable", "error"):
            out.add(k)
    return out


def read_batch_csv(path_or_text: str | os.PathLike) -> list[dict[str, str]]:
    p = Path(str(path_or_text))
    text = p.read_text(encoding="utf-8-sig") if p.exists() else str(path_or_text)
    rows = list(csv.DictReader(io.StringIO(text)))
    missing = [c for c in REQUIRED_COLUMNS if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"Batch file is missing columns: {missing}. Required: {REQUIRED_COLUMNS}")
    return rows


def run_batch(store: ReferenceStore, rows: Iterable[dict[str, str]], *, use_models: bool = True,
              progress=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = list(rows)
    # pair EN/ES outputs of the same case/task/model for bilingual equivalence
    by_key = {(r["case_id"], r["task"], r.get("model_label", ""), r["language"]): r["output_text"] for r in rows}
    runs, errors = [], []
    for i, r in enumerate(rows):
        if progress:
            progress(i / max(1, len(rows)), desc=f"{r.get('case_id')} {r.get('task')} {r.get('language')}")
        other = "es" if r["language"] == "en" else "en"
        try:
            runs.append(evaluate(store, task=r["task"], case_id=r["case_id"], language=r["language"],
                                 output_text=r["output_text"], source_text=r.get("source_text") or None,
                                 reference_text=r.get("reference_text") or None,
                                 other_language_output=by_key.get((r["case_id"], r["task"], r.get("model_label", ""), other)),
                                 model_label=r.get("model_label", ""), use_models=use_models))
        except Exception as e:  # noqa: BLE001
            errors.append({"row": i + 1, "case_id": r.get("case_id"), "error": f"{type(e).__name__}: {e}"})
    return runs, errors


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per model x task x language: counts, and mean and median of each numeric column over completed runs.

    n counts outputs, not independent patients: the same 10 synthetic cases appear in every group.
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["model_label"], r["task"], r["language"]), []).append(r)
    numeric = ["fact_agreement", "critical_problems", "contradictions", "direct_identifier_leaks", "alignscore",
               "nli_contradictions", "bertscore_f1_vs_source", "bertscore_f1_vs_reference",
               "embedding_cos_vs_reference", "bilingual_cosine", "readability_score", "length_ratio_vs_source"]
    out = []
    for (m, t, l), rs in sorted(groups.items()):
        row = {"model_label": m, "task": t, "language": l, "n": len(rs),
               "distinct_patients": len({r["case_id"] for r in rs}),
               "passed": sum(r["verdict"] == "pass_automated_checks" for r in rs),
               "needs_review": sum(r["verdict"] == "needs_human_review" for r in rs),
               "failed": sum(r["verdict"] == "fail" for r in rs),
               "runs_with_missing_metrics": sum(bool(r.get("missing_metrics")) for r in rs)}
        for c in numeric:
            vals = sorted(r[c] for r in rs if isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool))
            row[f"mean_{c}"] = round(sum(vals) / len(vals), 4) if vals else None
            row[f"median_{c}"] = round(_median(vals), 4) if vals else None
        out.append(row)
    return out


def _median(vals: list[float]) -> float:
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def generate_and_evaluate(store: ReferenceStore, *, dataset_id: str, task: str, languages: Iterable[str],
                          method: str, params: dict[str, Any] | None = None, case_ids: Iterable[str] | None = None,
                          use_models: bool = True, model_label: str | None = None, progress=None
                          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Transform each case's professional narrative with one method, then evaluate each output.

    Returns (runs, transformations, failures). A failed case is recorded and the batch continues.
    """
    from .transforms import transform
    languages = list(languages)
    cases = list(case_ids or store.case_ids)
    jobs = [(c, l) for c in cases for l in languages]
    runs, transformations, failures = [], [], []
    outputs: dict[tuple[str, str], str] = {}
    for i, (case, lang) in enumerate(jobs):
        if progress:
            progress(i / max(1, len(jobs)), desc=f"{task} {case} {lang} ({method})")
        source = store.narrative(case, "professional", lang)
        t = transform(task, dataset_id, case, lang, source or "", reference_record=store.record(case),
                      method=method, params=params)
        transformations.append(t)
        if t["execution_status"] != "Completed":
            failures.append({"case_id": case, "language": lang, "stage": "transformation", "error": t["error_details"]})
            continue
        outputs[(case, lang)] = t["generated_output"]
    label = model_label or method
    for t in transformations:
        if t["execution_status"] != "Completed":
            continue
        case, lang = t["patient_id"], t["language"]
        other = outputs.get((case, "es" if lang == "en" else "en"))
        try:
            run = evaluate(store, task=task, case_id=case, language=lang, output_text=t["generated_output"],
                           source_text=t["source_text"], other_language_output=other, model_label=label,
                           use_models=use_models)
        except Exception as e:  # noqa: BLE001
            failures.append({"case_id": case, "language": lang, "stage": "evaluation", "error": f"{type(e).__name__}: {e}"})
            continue
        run["evaluation_dataset_id"] = dataset_id
        run["transformation_id"] = t["transformation_id"]
        run["transformation_provenance"] = {k: v for k, v in t.items() if k not in ("source_text", "generated_output")}
        t["evaluation_run_id"] = run["run_id"]
        runs.append(run)
    return runs, transformations, failures


class RunStore:
    """Append-only JSONL storage under its own directory (never the governance runtime/)."""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "evaluation_runs.jsonl"

    def append(self, run: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(run, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def get(self, run_id: str) -> dict[str, Any] | None:
        return next((r for r in reversed(self.all()) if r["run_id"] == run_id), None)

    def export(self, runs: list[dict[str, Any]], stem: str) -> tuple[Path, Path]:
        exp = self.root / "exports"
        exp.mkdir(exist_ok=True)
        rows = [flat_row(r) for r in runs]
        csv_path, json_path = exp / f"{stem}.csv", exp / f"{stem}.json"
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        json_path.write_text(json.dumps({"summary": aggregate(rows), "runs": runs}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        return csv_path, json_path
