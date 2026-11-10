"""Explicit approval and immutable freeze lifecycle for narrative releases.

This subsystem is deliberately separate from the Clinical Reference lifecycle in
``runtime/``.  It reads imported narrative releases and append-only review
decisions, then writes only below ``CLINICAL_NARRATIVE_REVIEW_RUNTIME``.

The completed Sign-off Status is the release approval.  Freeze consumes that
exact-hash reviewer evidence directly; it does not ask for another approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from narrative_review import APPROVING, TEXT_TYPES, NarrativeReviewLog, runtime_dir


ACCEPTANCE_POLICIES = {
    "approved_or_comments": {
        "label": "All 40 current texts: Approved or Approved with comments",
        "accepted_decisions": tuple(sorted(APPROVING)),
    },
    "strict_approved_only": {
        "label": "All 40 current texts: Approved (comments do not pass)",
        "accepted_decisions": ("Approved",),
    },
}

# This requirement comes from the supplied review evidence.  It does not alter
# that evidence: the two exact text hashes remain blocked by Needs revision until
# corrected texts receive fresh decisions.
KNOWN_PENDING_FINDINGS = {
    "evalds_374910b82d732c4f": {
        ("HC0005", "en", "patient"): "QA-005",
        ("HC0005", "es", "patient"): "QA-005",
    }
}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


class NarrativeApprovalFreeze:
    """Consumed sign-off evidence plus immutable, independently verifiable ZIP freezes."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else runtime_dir()
        self.signoff_records_path = self.root / "narrative_signoff_consumptions.jsonl"
        self.registry_path = self.root / "narrative_freeze_registry.json"
        self.exports = self.root / "exports"
        self.lock_path = self.root / ".narrative_freeze.lock"

    def signoff_records(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        if not self.signoff_records_path.exists():
            return []
        rows = [json.loads(line) for line in self.signoff_records_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return [row for row in rows if dataset_id is None or row["evaluation_dataset_id"] == dataset_id]

    def freezes(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        if not self.registry_path.exists():
            return []
        try:
            rows = json.loads(self.registry_path.read_text(encoding="utf-8")).get("freezes", [])
        except (OSError, json.JSONDecodeError):
            return []
        return [row for row in rows if dataset_id is None or row["evaluation_dataset_id"] == dataset_id]

    @staticmethod
    def _source_release_sha(entry: dict[str, Any]) -> str:
        return str(entry.get("narrative_archive_sha256") or entry.get("narrative_json_sha256") or "")

    def snapshot(self, dataset_id: str, store: Any, entry: dict[str, Any],
                 log: NarrativeReviewLog | None = None) -> dict[str, Any]:
        """Build the exact current review snapshot without changing any source."""
        if entry.get("evaluation_dataset_id") != dataset_id:
            raise ValueError("The selected registry entry does not match the evaluation dataset ID.")
        if entry.get("import_validation_result") != "Passed":
            raise ValueError("Narrative approval is blocked because dataset import validation did not pass.")
        review = log or NarrativeReviewLog(self.root)
        records: dict[str, dict[str, str]] = {}
        text_inventory: list[dict[str, Any]] = []
        field = {
            ("professional", "en"): "clinical_narrative_en",
            ("professional", "es"): "clinical_narrative_es",
            ("patient", "en"): "patient_explanation_en",
            ("patient", "es"): "patient_explanation_es",
        }
        for case_id in sorted(store.case_ids):
            records[case_id] = {}
            for language in ("en", "es"):
                for text_type in TEXT_TYPES:
                    original = store.narrative(case_id, text_type, language, original=True) or ""
                    text, version = review.current_text(dataset_id, case_id, language, text_type, original)
                    decision = review.latest(dataset_id, case_id, language, text_type, text)
                    # Dataset ID and text hash are necessary but not sufficient:
                    # review evidence must also name this exact imported source release.
                    if decision and decision.get("narrative_release_sha256") != self._source_release_sha(entry):
                        decision = None
                    records[case_id][field[(text_type, language)]] = text
                    text_inventory.append({
                        "patient_id": case_id,
                        "language": language,
                        "text_type": text_type,
                        "text_version": version,
                        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "decision_id": decision.get("decision_id") if decision else None,
                        "decision": decision.get("decision") if decision else None,
                        "reviewer": decision.get("reviewer") if decision else None,
                        "reviewer_role": decision.get("reviewer_role") if decision else None,
                        "decided_utc": decision.get("decided_utc") if decision else None,
                        "pending_finding_id": KNOWN_PENDING_FINDINGS.get(dataset_id, {}).get(
                            (case_id, language, text_type)
                        ) if decision and decision.get("decision") == "Needs revision" else None,
                    })
        content_hash = _sha_bytes(_canonical(records))
        run_version_id = f"narrun_{dataset_id.removeprefix('evalds_')}_{content_hash[:16]}"
        return {
            "evaluation_dataset_id": dataset_id,
            "narrative_run_version_id": run_version_id,
            "narrative_release_version": entry.get("narrative_release_version"),
            "source_release_sha256": self._source_release_sha(entry),
            "source_archive_sha256": entry.get("narrative_archive_sha256"),
            "source_narrative_json_sha256": entry.get("narrative_json_sha256"),
            "source_clinical_reference_id": entry.get("source_clinical_reference_id"),
            "source_dataset_version": entry.get("source_dataset_version"),
            "source_dataset_canonical_sha256": entry.get("source_dataset_canonical_sha256"),
            "source_freeze_sha256": entry.get("source_freeze_sha256"),
            "provenance_verification_status": entry.get("provenance_verification_status"),
            "current_records_sha256": content_hash,
            "records": records,
            "texts": text_inventory,
        }

    def coverage(self, snapshot: dict[str, Any], policy_id: str) -> dict[str, Any]:
        if policy_id not in ACCEPTANCE_POLICIES:
            raise ValueError("Choose a supported narrative acceptance policy.")
        accepted = set(ACCEPTANCE_POLICIES[policy_id]["accepted_decisions"])
        pending = []
        for item in snapshot["texts"]:
            if item.get("decision") not in accepted:
                pending.append({key: item.get(key) for key in (
                    "patient_id", "language", "text_type", "text_version", "text_sha256", "decision",
                    "decision_id", "pending_finding_id"
                )})
        return {
            "policy_id": policy_id,
            "policy_label": ACCEPTANCE_POLICIES[policy_id]["label"],
            "accepted_decisions": sorted(accepted),
            "required": len(snapshot["texts"]),
            "accepted": len(snapshot["texts"]) - len(pending),
            "pending": pending,
            "passed": bool(snapshot["texts"]) and not pending,
        }

    def consume_signoff(self, *, dataset_id: str, store: Any, entry: dict[str, Any],
                        log: NarrativeReviewLog | None = None) -> dict[str, Any]:
        """Bind the already-completed Sign-off Status to the exact release snapshot.

        This does not ask for or invent another signer.  It consumes the named,
        exact-hash human decisions that made Sign-off Status complete.
        """
        snapshot = self.snapshot(dataset_id, store, entry, log)
        existing = self.current_signoff_record(dataset_id, snapshot)
        if existing:
            return existing
        coverage = self.coverage(snapshot, "approved_or_comments")
        if not coverage["passed"]:
            qa = sorted({p["pending_finding_id"] for p in coverage["pending"] if p.get("pending_finding_id")})
            suffix = f" Outstanding finding(s): {', '.join(qa)}." if qa else ""
            raise ValueError(
                f"Narrative freeze is blocked: Sign-off Status covers {coverage['accepted']} of "
                f"{coverage['required']} current texts.{suffix}"
            )
        reviewers = sorted({(x["reviewer"], x.get("reviewer_role") or "") for x in snapshot["texts"]
                            if x.get("reviewer")})
        decision_ids = [x["decision_id"] for x in snapshot["texts"] if x.get("decision_id")]
        approval = {
            "signoff_record_id": str(uuid.uuid4()),
            "approval_type": "completed_signoff_status_consumed",
            "evaluation_dataset_id": dataset_id,
            "narrative_run_version_id": snapshot["narrative_run_version_id"],
            "narrative_release_version": snapshot["narrative_release_version"],
            "source_release_sha256": snapshot["source_release_sha256"],
            "current_records_sha256": snapshot["current_records_sha256"],
            "per_text_sha256": {f"{x['patient_id']}:{x['language']}:{x['text_type']}": x["text_sha256"]
                                for x in snapshot["texts"]},
            "acceptance_policy": coverage,
            "signoff_source": "Completed Sign-off Status from exact current narrative-review decisions",
            "reviewers": [{"name": name, "role": role} for name, role in reviewers],
            "decision_ids": decision_ids,
            "approved_utc": max(x["decided_utc"] for x in snapshot["texts"] if x.get("decided_utc")),
            "consumed_utc": _utc(),
            "credential_verification": "Not performed by the application",
            "note": "No second approval was requested; this record binds the completed Sign-off Status to the freeze.",
        }
        with _file_lock(self.lock_path):
            self.root.mkdir(parents=True, exist_ok=True)
            with self.signoff_records_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(approval, ensure_ascii=False, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return approval

    def current_signoff_record(self, dataset_id: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        matches = [row for row in self.signoff_records(dataset_id)
                   if row.get("narrative_run_version_id") == snapshot["narrative_run_version_id"]
                   and row.get("current_records_sha256") == snapshot["current_records_sha256"]]
        return matches[-1] if matches else None

    def matching_freeze(self, dataset_id: str, current_records_sha256: str) -> dict[str, Any] | None:
        """The newest verified freeze of exactly these current texts, if any."""
        for item in reversed(self.freezes(dataset_id)):
            if item.get("current_records_sha256") == current_records_sha256:
                try:
                    if self.verify_freeze(item)["valid"]:
                        return item
                except (OSError, ValueError, zipfile.BadZipFile):
                    continue
        return None

    def freeze_status(self, dataset_id: str, store: Any, entry: dict[str, Any],
                      log: NarrativeReviewLog | None = None) -> dict[str, Any]:
        """Whether the current reference texts are the ones in a narrative freeze (read-only).

        state: "not_frozen" (no freeze yet), "matches_freeze" (current texts are frozen and still signed off),
        "changed_since_freeze" (an approved correction changed the texts after the latest freeze) or
        "signoff_changed_since_freeze" (same texts, but a later decision such as Needs revision withdrew an
        approval)."""
        freezes = self.freezes(dataset_id)
        if not freezes:
            return {"state": "not_frozen", "freeze_id": None, "zip_sha256": None}
        snapshot = self.snapshot(dataset_id, store, entry, log)
        match = self.matching_freeze(dataset_id, snapshot["current_records_sha256"])
        item = match or freezes[-1]
        state = "matches_freeze" if match else "changed_since_freeze"
        if match and not self.coverage(snapshot, "approved_or_comments")["passed"]:
            state = "signoff_changed_since_freeze"  # same texts, but a later decision withdrew an approval
        return {"state": state, "freeze_id": item["freeze_id"],
                "zip_sha256": item["zip_sha256"], "freeze_created_utc": item["created_utc"],
                "current_records_sha256": snapshot["current_records_sha256"]}

    def create_freeze(self, *, dataset_id: str, store: Any, entry: dict[str, Any], eval_root: Path | str,
                      log: NarrativeReviewLog | None = None) -> dict[str, Any]:
        snapshot = self.snapshot(dataset_id, store, entry, log)
        # Freezing unchanged texts again would only create an identical copy: return the existing freeze.
        existing = self.matching_freeze(dataset_id, snapshot["current_records_sha256"])
        if existing:
            return {**existing, "reused": True}
        approval = self.consume_signoff(dataset_id=dataset_id, store=store, entry=entry, log=log)
        # Re-check the consumed Sign-off Status immediately before packaging.
        coverage = self.coverage(snapshot, approval["acceptance_policy"]["policy_id"])
        if not coverage["passed"]:
            raise ValueError("Narrative freeze is blocked because the completed Sign-off Status no longer covers the current texts.")

        eval_root = Path(eval_root).resolve()
        source_json = (eval_root / entry["narrative_json"]).resolve()
        try:
            source_json.relative_to(eval_root)
        except ValueError as exc:
            raise ValueError("Unsafe narrative source path in the dataset registry.") from exc
        if not source_json.is_file():
            raise ValueError("The exact imported source narrative JSON is missing.")
        source_archive = None
        if entry.get("archive_file"):
            source_archive = (eval_root / entry["archive_file"]).resolve()
            try:
                source_archive.relative_to(eval_root)
            except ValueError as exc:
                raise ValueError("Unsafe source archive path in the dataset registry.") from exc
            if not source_archive.is_file() or _sha_file(source_archive) != entry.get("narrative_archive_sha256"):
                raise ValueError("The exact imported source narrative archive is missing or its SHA-256 changed.")

        review = log or NarrativeReviewLog(self.root)
        current_ids = {item["decision_id"] for item in snapshot["texts"] if item.get("decision_id")}
        decisions = [row for row in review.history(dataset_id) if row.get("decision_id") in current_ids]
        freeze_id = f"nfreeze_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"
        files: dict[str, bytes] = {
            "source/source_narratives_exact.json": source_json.read_bytes(),
            "snapshot/approved_narratives.json": _pretty({
                "evaluation_dataset_id": dataset_id,
                "narrative_run_version_id": snapshot["narrative_run_version_id"],
                "records": snapshot["records"],
            }),
            "evidence/current_review_decisions.json": _pretty(decisions),
            "evidence/consumed_signoff_status.json": _pretty(approval),
        }
        if source_archive:
            files["source/source_narrative_archive_exact.zip"] = source_archive.read_bytes()
        file_inventory = {name: {"sha256": _sha_bytes(data), "bytes": len(data)} for name, data in files.items()}
        manifest = {
            "manifest_version": "1.0",
            "freeze_id": freeze_id,
            "freeze_type": "narrative_release_approved_freeze",
            "created_utc": _utc(),
            "evaluation_dataset_id": dataset_id,
            "narrative_run_version_id": snapshot["narrative_run_version_id"],
            "narrative_release_version": snapshot["narrative_release_version"],
            "source_release_sha256": snapshot["source_release_sha256"],
            "source_archive_sha256": snapshot["source_archive_sha256"],
            "source_narrative_json_sha256": _sha_file(source_json),
            "source_clinical_reference_id": snapshot["source_clinical_reference_id"],
            "source_dataset_version": snapshot["source_dataset_version"],
            "source_dataset_canonical_sha256": snapshot["source_dataset_canonical_sha256"],
            "source_clinical_reference_freeze_sha256": snapshot["source_freeze_sha256"],
            "provenance_verification_status": snapshot["provenance_verification_status"],
            "current_records_sha256": snapshot["current_records_sha256"],
            "per_text": snapshot["texts"],
            "review_coverage": coverage,
            "consumed_signoff_record_id": approval["signoff_record_id"],
            "separation_notice": (
                "This is a Narrative Approval & Freeze artifact. It does not approve, modify, supersede, "
                "or unfreeze the immutable Clinical Reference subsystem."
            ),
            "files": file_inventory,
        }
        files["narrative_freeze_manifest.json"] = _pretty(manifest)
        files["SHA256SUMS"] = ("".join(
            f"{_sha_bytes(data)}  {name}\n" for name, data in sorted(files.items())
        )).encode("utf-8")

        with _file_lock(self.lock_path):
            self.exports.mkdir(parents=True, exist_ok=True)
            target = self.exports / f"{freeze_id}.zip"
            if target.exists():
                raise FileExistsError("Refusing to overwrite an existing narrative freeze.")
            fd, tmp_name = tempfile.mkstemp(prefix=f".{freeze_id}.", suffix=".zip", dir=self.exports)
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
                    for name, data in sorted(files.items()):
                        archive.writestr(name, data)
                with zipfile.ZipFile(tmp) as archive:
                    if archive.testzip() is not None:
                        raise ValueError("The narrative freeze failed its ZIP integrity check.")
                os.replace(tmp, target)
                frozen = {
                    "freeze_id": freeze_id,
                    "evaluation_dataset_id": dataset_id,
                    "narrative_run_version_id": snapshot["narrative_run_version_id"],
                    "narrative_release_version": snapshot["narrative_release_version"],
                    "current_records_sha256": snapshot["current_records_sha256"],
                    "consumed_signoff_record_id": approval["signoff_record_id"],
                    "created_utc": manifest["created_utc"],
                    "zip_path": str(target.relative_to(self.root)),
                    "zip_sha256": _sha_file(target),
                    "manifest_sha256": _sha_bytes(files["narrative_freeze_manifest.json"]),
                    "immutable": True,
                }
                registry = {"registry_version": "1.0", "freezes": [*self.freezes(), frozen]}
                _atomic_json(self.registry_path, registry)
            finally:
                if tmp.exists():
                    tmp.unlink()
        return frozen

    def verify_freeze(self, freeze: dict[str, Any] | str) -> dict[str, Any]:
        item = next((row for row in self.freezes() if row["freeze_id"] == freeze), None) \
            if isinstance(freeze, str) else freeze
        if not item:
            raise ValueError("Unknown narrative freeze.")
        path = (self.root / item["zip_path"]).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("Unsafe narrative freeze path in the registry.") from exc
        errors: list[str] = []
        if not path.is_file():
            return {"valid": False, "errors": ["Freeze ZIP is missing."], "path": str(path)}
        if _sha_file(path) != item["zip_sha256"]:
            errors.append("Freeze ZIP SHA-256 does not match the registry.")
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                errors.append(f"ZIP integrity failed at {bad}.")
            names = set(archive.namelist())
            required = {"narrative_freeze_manifest.json", "SHA256SUMS", "snapshot/approved_narratives.json",
                        "evidence/consumed_signoff_status.json", "evidence/current_review_decisions.json",
                        "source/source_narratives_exact.json"}
            if missing := sorted(required - names):
                errors.append(f"Missing required freeze member(s): {', '.join(missing)}")
            if "narrative_freeze_manifest.json" in names:
                manifest_bytes = archive.read("narrative_freeze_manifest.json")
                if _sha_bytes(manifest_bytes) != item["manifest_sha256"]:
                    errors.append("Manifest SHA-256 does not match the registry.")
                manifest = json.loads(manifest_bytes)
                if manifest.get("freeze_id") != item["freeze_id"]:
                    errors.append("Manifest freeze ID does not match the registry.")
                for name, meta in manifest.get("files", {}).items():
                    if name not in names or _sha_bytes(archive.read(name)) != meta.get("sha256"):
                        errors.append(f"Payload checksum mismatch: {name}")
        return {"valid": not errors, "errors": errors, "path": str(path), "zip_sha256": item["zip_sha256"]}
