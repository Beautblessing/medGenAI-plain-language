"""Evaluation human-review queue (Workspace B only).

Items come from automated flags and problem findings of stored evaluation runs. Decisions are
appended to their own JSONL file under the evaluation runtime. Nothing here reads or writes the
clinical-reference runtime/, approvals or freezes, and no reviewer identity is ever invented:
the reviewer field is whatever the person typed.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECISIONS = ("Confirmed problem", "False positive", "Needs clinical input", "Acceptable as is")
HEADERS = ["Item ID", "Run", "Patient", "Language", "Task", "Output", "Severity", "Finding", "Source evidence",
           "Review status", "Decision", "Reviewer", "Decided UTC"]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _item_id(run_id: str, kind: str, text: str) -> str:
    return "rq_" + hashlib.sha256(f"{run_id}|{kind}|{text}".encode()).hexdigest()[:16]


def items_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    base = {"evaluation_run_id": run["run_id"], "patient_id": run["case_id"], "language": run["language"],
            "task": run["task"], "output_identifier": run.get("transformation_id") or run["inputs"]["output_sha256"],
            "evaluation_dataset_id": run.get("evaluation_dataset_id")}
    for f in run.get("flags", []):
        out.append({**base, "item_id": _item_id(run["run_id"], "flag", f["reason"]), "source": "automated flag",
                    "severity": f["level"], "finding": f["reason"], "source_evidence": ""})
    for sec in ("factual_agreement_structured", "identifier_checks"):
        for f in run.get(sec, {}).get("findings", []):
            if not f.get("is_problem"):
                continue
            text = f"{f['classification']}: {f['explanation']}"
            evidence = " | ".join(x for x in (f.get("json_pointer"), f.get("source_evidence"), f.get("output_evidence")) if x)
            out.append({**base, "item_id": _item_id(run["run_id"], sec, text + evidence), "source": sec,
                        "severity": f["severity"], "finding": text, "source_evidence": evidence[:500]})
    return out


class ReviewQueue:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.items_path = self.root / "review_queue_items.jsonl"
        self.decisions_path = self.root / "review_queue_decisions.jsonl"

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    @staticmethod
    def _append(path: Path, rows: list[dict[str, Any]]) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def add_run(self, run: dict[str, Any]) -> int:
        known = {i["item_id"] for i in self._read(self.items_path)}
        new = [{**i, "queued_at_utc": _utc()} for i in items_from_run(run) if i["item_id"] not in known]
        self._append(self.items_path, new)
        return len(new)

    def decide(self, item_id: str, decision: str, reviewer: str, note: str = "") -> dict[str, Any]:
        if decision not in DECISIONS:
            raise ValueError(f"Decision must be one of {DECISIONS}")
        if not reviewer or not reviewer.strip():
            raise ValueError("Reviewer name is required (it is recorded exactly as typed).")
        if item_id not in {i["item_id"] for i in self._read(self.items_path)}:
            raise KeyError(f"Unknown review item {item_id!r}")
        rec = {"item_id": item_id, "decision": decision, "reviewer": reviewer.strip(), "note": note or "",
               "decided_at_utc": _utc(), "scope": "evaluation finding only; not a clinical-reference approval"}
        self._append(self.decisions_path, [rec])
        return rec

    def items(self, status: str | None = None) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for d in self._read(self.decisions_path):
            latest[d["item_id"]] = d
        out = []
        for i in self._read(self.items_path):
            d = latest.get(i["item_id"])
            row = {**i, "review_status": "Reviewed" if d else "Pending", "decision": (d or {}).get("decision"),
                   "reviewer": (d or {}).get("reviewer"), "decided_at_utc": (d or {}).get("decided_at_utc"),
                   "note": (d or {}).get("note")}
            if status in (None, "All") or row["review_status"] == status:
                out.append(row)
        return out

    def table(self, status: str | None = None, exclude_tasks: tuple[str, ...] = ()) -> list[list[Any]]:
        return [[i["item_id"], i["evaluation_run_id"][:8], i["patient_id"], i["language"], i["task"],
                 (i["output_identifier"] or "")[:12], i["severity"], i["finding"], i["source_evidence"],
                 i["review_status"], i["decision"] or "", i["reviewer"] or "", i["decided_at_utc"] or ""]
                for i in self.items(status) if i.get("task") not in exclude_tasks]
