"""Immutable narrative-release import, validation and registry.

This module never writes to the clinical-reference ``runtime/`` tree.  Imported
evaluation artifacts live below ``CLINICAL_EVAL_RUNTIME`` and retain their
original bytes and checksums.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

EXPECTED_CASES = [f"HC{i:04d}" for i in range(1, 11)]
EXPECTED_CANONICAL_REFERENCE = "08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e"
# This application evaluates a single narrative release: the Claude Prompt 4.1 generation-only package.
# Its 40 texts are identical to the evaluated combined package 93c2dc23. Any other upload is registered as an
# external candidate and never replaces it.
KNOWN_RELEASES = {
    "374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120": (
        "Reference clinical narratives (EN/ES, from approved v9)", "4.1.0", "original_generated_candidate"
    ),
}
PRIMARY_RELEASE_SHA256 = "374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120"
NARRATIVE_FIELDS = (
    "clinical_narrative_en", "clinical_narrative_es",
    "patient_explanation_en", "patient_explanation_es",
)
CSV_FIELDS = {
    "clinical_records_english": "clinical_narrative_en",
    "clinical_records_spanish": "clinical_narrative_es",
    "patient_explanations_english": "patient_explanation_en",
    "patient_explanations_spanish": "patient_explanation_es",
}
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_MEMBERS = 200


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def _lock(path: Path):
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


def _records(document: dict[str, Any]) -> dict[str, dict[str, str]]:
    records = document.get("records", document)
    if not isinstance(records, dict) or sorted(records) != EXPECTED_CASES:
        raise ValueError("Track A narratives must contain exactly HC0001–HC0010.")
    for case_id, record in records.items():
        if not isinstance(record, dict):
            raise ValueError(f"{case_id} must be a narrative object.")
        missing = [name for name in NARRATIVE_FIELDS if not isinstance(record.get(name), str) or not record[name].strip()]
        if missing:
            raise ValueError(f"{case_id} is missing non-empty narrative fields: {', '.join(missing)}.")
    return records


def _source_identity(document: dict[str, Any], manifest: dict[str, Any] | None) -> tuple[int | None, str | None, str | None]:
    meta = document.get("generation_metadata", {})
    source = (manifest or {}).get("source", {})
    version = meta.get("dataset_version") or source.get("dataset_version")
    canonical = meta.get("source_canonical_sha256") or source.get("canonical_dataset_sha256")
    freeze_hash = meta.get("source_freeze_sha256") or source.get("zip_sha256") or source.get("approved_freeze_sha256")
    return version, canonical, freeze_hash


def _validate_internal_checksums(archive: zipfile.ZipFile) -> tuple[int, list[str]]:
    checksum_names = [name for name in archive.namelist() if PurePosixPath(name).name.upper() == "SHA256SUMS"]
    if not checksum_names:
        return 0, []
    errors: list[str] = []
    checked = 0
    base = str(PurePosixPath(checksum_names[0]).parent)
    if base == ".":
        base = ""
    for line in archive.read(checksum_names[0]).decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        expected, member = line.split(None, 1)
        member = member.lstrip("* ")
        candidate = f"{base}/{member}" if base else member
        try:
            actual = _sha_bytes(archive.read(candidate))
        except KeyError:
            errors.append(f"Checksum inventory references missing file: {member}")
            continue
        checked += 1
        if actual != expected:
            errors.append(f"Checksum mismatch: {member}")
    return checked, errors


def _validate_freeze_snapshot(archive: zipfile.ZipFile, manifest: dict[str, Any],
                              document: dict[str, Any]) -> list[str]:
    """Validate a Freeze and Download package and its selected signed-off snapshot."""
    errors: list[str] = []
    if manifest.get("freeze_type") != "narrative_release_approved_freeze":
        return ["Unsupported narrative freeze type."]
    for name, meta in (manifest.get("files") or {}).items():
        try:
            payload = archive.read(name)
        except KeyError:
            errors.append(f"Freeze manifest references missing file: {name}")
            continue
        if _sha_bytes(payload) != meta.get("sha256"):
            errors.append(f"Freeze manifest checksum mismatch: {name}")
        if len(payload) != meta.get("bytes"):
            errors.append(f"Freeze manifest byte count mismatch: {name}")
    records = _records(document)
    canonical_records = _sha_bytes(json.dumps(records, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode("utf-8"))
    if canonical_records != manifest.get("current_records_sha256"):
        errors.append("The signed-off snapshot hash does not match the freeze manifest.")
    fields = {
        ("professional", "en"): "clinical_narrative_en",
        ("professional", "es"): "clinical_narrative_es",
        ("patient", "en"): "patient_explanation_en",
        ("patient", "es"): "patient_explanation_es",
    }
    per_text = manifest.get("per_text") or []
    if len(per_text) != 40:
        errors.append("A signed-off Track A freeze must contain exactly 40 per-text records.")
    for item in per_text:
        key = (item.get("text_type"), item.get("language"))
        case_id = item.get("patient_id")
        if case_id not in records or key not in fields:
            errors.append(f"Invalid per-text identity in freeze manifest: {case_id} {key}.")
            continue
        actual = _sha_bytes(records[case_id][fields[key]].encode("utf-8"))
        if actual != item.get("text_sha256"):
            errors.append(f"Signed-off text checksum mismatch: {case_id} {key[1]} {key[0]}.")
        if item.get("decision") not in ("Approved", "Approved with comments"):
            errors.append(f"Text is not signed off in freeze manifest: {case_id} {key[1]} {key[0]}.")
    coverage = manifest.get("review_coverage") or {}
    if not coverage.get("passed") or coverage.get("accepted") != coverage.get("required"):
        errors.append("Freeze manifest does not show complete Sign-off Status.")
    return errors


class EvaluationDatasetRegistry:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.datasets = self.root / "datasets"
        self.registry_path = self.root / "dataset_registry.json"
        self.lock_path = self.root / ".dataset_registry.lock"
        self.datasets.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            _atomic_json(self.registry_path, {"registry_version": "1.0", "datasets": []})

    def all(self) -> list[dict[str, Any]]:
        try:
            items = json.loads(self.registry_path.read_text(encoding="utf-8")).get("datasets", [])
        except (OSError, json.JSONDecodeError):
            return []
        # Display-only naming: a known release is shown by what it contains. The stored registry entry is
        # immutable and keeps the name it was registered under (exposed as registered_display_name).
        for item in items:
            known = KNOWN_RELEASES.get(str(item.get("source_narrative_archive_sha256")
                                           or item.get("narrative_archive_sha256")))
            if known and item.get("display_name") != known[0]:
                item["registered_display_name"] = item.get("display_name")
                item["display_name"] = known[0]
        return items

    def get(self, dataset_id: str) -> dict[str, Any]:
        item = next((item for item in self.all() if item.get("evaluation_dataset_id") == dataset_id), None)
        if not item:
            raise KeyError(f"Unknown evaluation dataset {dataset_id!r}")
        return item

    def records(self, dataset_id: str) -> dict[str, dict[str, str]]:
        entry = self.get(dataset_id)
        return _records(json.loads((self.root / entry["narrative_json"]).read_text(encoding="utf-8")))

    def import_zip(self, source: str | os.PathLike) -> dict[str, Any]:
        path = Path(source)
        if not path.is_file():
            raise ValueError("Select a narrative package ZIP.")
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("The narrative archive exceeds the 50 MB limit.")
        archive_hash = _sha_file(path)
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("The narrative archive failed its ZIP integrity check.")
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS or sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("The narrative archive exceeds the safe extraction limit.")
            for info in infos:
                member = PurePosixPath(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if member.is_absolute() or ".." in member.parts or mode == stat.S_IFLNK:
                    raise ValueError(f"Unsafe archive member rejected: {info.filename}")
            json_docs: dict[str, Any] = {}
            for info in infos:
                if info.is_dir() or not info.filename.lower().endswith(".json"):
                    continue
                try:
                    json_docs[info.filename] = json.loads(archive.read(info).decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Invalid JSON file in archive: {info.filename}") from exc
            freeze_manifests = [(name, doc) for name, doc in json_docs.items()
                                if isinstance(doc, dict)
                                and doc.get("freeze_type") == "narrative_release_approved_freeze"]
            is_freeze = bool(freeze_manifests)
            if len(freeze_manifests) > 1:
                raise ValueError("The package contains more than one narrative freeze manifest.")
            if is_freeze:
                manifest_name, manifest = freeze_manifests[0]
                narrative_name = "snapshot/approved_narratives.json"
                document = json_docs.get(narrative_name)
                if not isinstance(document, dict):
                    raise ValueError("The narrative freeze is missing snapshot/approved_narratives.json.")
            else:
                narrative_candidates = [(name, doc) for name, doc in json_docs.items()
                                        if isinstance(doc, dict) and isinstance(doc.get("records"), dict)
                                        and set(NARRATIVE_FIELDS).issubset(next(iter(doc["records"].values()), {}))]
                if len(narrative_candidates) != 1:
                    raise ValueError("The package must contain exactly one structured bilingual narrative JSON file.")
                narrative_name, document = narrative_candidates[0]
                manifest_candidates = [(name, doc) for name, doc in json_docs.items()
                                       if "manifest" in PurePosixPath(name).name.lower() and isinstance(doc, dict)]
                manifest_name, manifest = manifest_candidates[0] if manifest_candidates else (None, None)
            records = _records(document)
            schema_candidates = [(name, doc) for name, doc in json_docs.items()
                                 if "schema" in PurePosixPath(name).name.lower() and isinstance(doc, dict)
                                 and ("$schema" in doc or "properties" in doc)]
            schema_name, schema = schema_candidates[0] if schema_candidates else (None, None)
            schema_errors: list[str] = []
            if schema:
                try:
                    Draft202012Validator.check_schema(schema)
                    schema_errors = [error.message for error in Draft202012Validator(schema).iter_errors(document)]
                except Exception as exc:
                    schema_errors = [f"Schema validation could not run: {exc}"]
            checked, checksum_errors = _validate_internal_checksums(archive)
            if is_freeze:
                csv_results = {"signed_off_snapshot": {
                    "not_applicable": True,
                    "reason": "The immutable snapshot is verified by its manifest and per-text SHA-256 inventory.",
                }}
                csv_errors = []
                freeze_errors = _validate_freeze_snapshot(archive, manifest, document)
                version = manifest.get("source_dataset_version")
                canonical = manifest.get("source_dataset_canonical_sha256")
                freeze_hash = manifest.get("source_clinical_reference_freeze_sha256")
            else:
                csv_results, csv_errors = self._validate_csvs(archive, records)
                freeze_errors = []
                version, canonical, freeze_hash = _source_identity(document, manifest)
            provenance_status = ("Verified" if canonical == EXPECTED_CANONICAL_REFERENCE
                                 and not checksum_errors and not freeze_errors else "Unverified")
            errors = [*checksum_errors, *schema_errors, *csv_errors, *freeze_errors]
            source_release_hash = manifest.get("source_archive_sha256") if is_freeze else archive_hash
            known = KNOWN_RELEASES.get(source_release_hash)
            if is_freeze:
                label = known[0] if known else f"Frozen narrative release {manifest.get('freeze_id', archive_hash[:12])}"
                release = str(manifest.get("narrative_release_version") or "unknown")
                release_type = "signed_off_freeze"
            else:
                label, release, release_type = known or (
                    f"External candidate {archive_hash[:12]}",
                    str(document.get("generation_metadata", {}).get("prompt_version") or "unknown"),
                    "uploaded_external_candidate",
                )
            if canonical != EXPECTED_CANONICAL_REFERENCE:
                errors.append("Narrative package is not bound to the approved version 9 canonical SHA-256.")
            dataset_id = f"evalds_freeze_{archive_hash[:16]}" if is_freeze else f"evalds_{archive_hash[:16]}"
            target = self.datasets / dataset_id
            with _lock(self.lock_path):
                existing = next((item for item in self.all() if item.get("evaluation_dataset_id") == dataset_id), None)
                if existing:
                    return existing
                staging = self.datasets / f".{dataset_id}.{uuid.uuid4().hex}.tmp"
                staging.mkdir(parents=True)
                try:
                    shutil.copy2(path, staging / "original.zip")
                    for name in [narrative_name, schema_name, manifest_name]:
                        if name:
                            (staging / PurePosixPath(name).name).write_bytes(archive.read(name))
                    entry = {
                        "evaluation_dataset_id": dataset_id,
                        "display_name": label,
                        "narrative_release_version": release,
                        "release_type": release_type,
                        "narrative_archive_sha256": archive_hash,
                        "source_narrative_archive_sha256": source_release_hash if is_freeze else None,
                        "narrative_json_sha256": _sha_bytes(archive.read(narrative_name)),
                        "narrative_freeze_id": manifest.get("freeze_id") if is_freeze else None,
                        "narrative_run_version_id": manifest.get("narrative_run_version_id") if is_freeze else None,
                        "signed_off_text_inventory": manifest.get("per_text") if is_freeze else None,
                        "source_clinical_reference_id": "clinical-reference-v9-08567e1be79e",
                        "source_dataset_version": version,
                        "source_dataset_canonical_sha256": canonical,
                        "source_freeze_sha256": freeze_hash,
                        "schema_version": document.get("generation_metadata", {}).get("source_schema_version"),
                        "patient_set": sorted(records),
                        "available_language_fields": list(NARRATIVE_FIELDS),
                        "generation_model_information": document.get("generation_metadata", {}),
                        "import_timestamp_utc": _utc(),
                        "import_validation_result": "Passed" if not errors else "Failed",
                        "validation_errors": errors,
                        "provenance_verification_status": provenance_status,
                        "clinical_narrative_review_status": "signed_off_frozen" if is_freeze else (
                            document.get("generation_metadata", {}).get("human_review_status")
                            or document.get("generation_metadata", {}).get("output_status")
                            or (manifest or {}).get("review_status")
                            or "Unknown"
                        ),
                        "internal_checksums_checked": checked,
                        "csv_validation": csv_results,
                        "archive_file": str((target / "original.zip").relative_to(self.root)),
                        "narrative_json": str((target / PurePosixPath(narrative_name).name).relative_to(self.root)),
                        "schema_file": str((target / PurePosixPath(schema_name).name).relative_to(self.root)) if schema_name else None,
                        "manifest_file": str((target / PurePosixPath(manifest_name).name).relative_to(self.root)) if manifest_name else None,
                    }
                    _atomic_json(staging / "import_manifest.json", entry)
                    os.replace(staging, target)
                    registry = {"registry_version": "1.0", "datasets": [*self.all(), entry]}
                    _atomic_json(self.registry_path, registry)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            return entry

    def import_json(self, narrative_json: str | os.PathLike, schema_file: str | os.PathLike | None = None,
                    manifest_file: str | os.PathLike | None = None) -> dict[str, Any]:
        source = Path(narrative_json)
        document = json.loads(source.read_text(encoding="utf-8-sig"))
        records = _records(document)
        raw = source.read_bytes()
        file_hash = _sha_bytes(raw)
        dataset_id = f"evalds_json_{file_hash[:16]}"
        version, canonical, freeze_hash = _source_identity(document, None)
        errors = [] if canonical == EXPECTED_CANONICAL_REFERENCE else ["JSON is not bound to the approved version 9 canonical SHA-256."]
        if schema_file:
            schema = json.loads(Path(schema_file).read_text(encoding="utf-8-sig"))
            Draft202012Validator.check_schema(schema)
            errors.extend(error.message for error in Draft202012Validator(schema).iter_errors(document))
        target = self.datasets / dataset_id
        with _lock(self.lock_path):
            existing = next((item for item in self.all() if item.get("evaluation_dataset_id") == dataset_id), None)
            if existing:
                return existing
            target.mkdir(parents=True, exist_ok=False)
            shutil.copy2(source, target / "narratives.json")
            if schema_file:
                shutil.copy2(schema_file, target / "schema.json")
            if manifest_file:
                shutil.copy2(manifest_file, target / "source_manifest.json")
            entry = {
                "evaluation_dataset_id": dataset_id,
                "display_name": f"Individual JSON {file_hash[:12]}",
                "narrative_release_version": document.get("generation_metadata", {}).get("prompt_version", "unknown"),
                "release_type": "uploaded_external_candidate",
                "narrative_archive_sha256": None,
                "narrative_json_sha256": file_hash,
                "source_clinical_reference_id": "clinical-reference-v9-08567e1be79e",
                "source_dataset_version": version,
                "source_dataset_canonical_sha256": canonical,
                "source_freeze_sha256": freeze_hash,
                "schema_version": document.get("generation_metadata", {}).get("source_schema_version"),
                "patient_set": sorted(records),
                "available_language_fields": list(NARRATIVE_FIELDS),
                "generation_model_information": document.get("generation_metadata", {}),
                "import_timestamp_utc": _utc(),
                "import_validation_result": "Passed" if not errors else "Failed",
                "validation_errors": errors,
                "provenance_verification_status": "Unverified provenance — individual JSON without trusted package manifest",
                "clinical_narrative_review_status": document.get("generation_metadata", {}).get("output_status", "Unknown"),
                "archive_file": None,
                "narrative_json": str((target / "narratives.json").relative_to(self.root)),
                "schema_file": str((target / "schema.json").relative_to(self.root)) if schema_file else None,
                "manifest_file": str((target / "source_manifest.json").relative_to(self.root)) if manifest_file else None,
            }
            _atomic_json(target / "import_manifest.json", entry)
            _atomic_json(self.registry_path, {"registry_version": "1.0", "datasets": [*self.all(), entry]})
        return entry

    @staticmethod
    def _validate_csvs(archive: zipfile.ZipFile, records: dict[str, dict[str, str]]) -> tuple[dict[str, Any], list[str]]:
        results: dict[str, Any] = {}
        errors: list[str] = []
        names = archive.namelist()
        for stem, field in CSV_FIELDS.items():
            matches = [name for name in names if PurePosixPath(name).stem.startswith(stem) and name.lower().endswith(".csv")]
            if not matches:
                errors.append(f"Missing narrative CSV: {stem}.csv")
                continue
            rows = list(csv.DictReader(io.StringIO(archive.read(matches[0]).decode("utf-8-sig"))))
            equal = len(rows) == 10 and all(
                row.get("id") in records and row.get("historia_clinica") == records[row["id"]][field]
                for row in rows
            )
            results[field] = {"rows": len(rows), "exact_json_text_equality": equal}
            if not equal:
                errors.append(f"{PurePosixPath(matches[0]).name} does not exactly match the narrative JSON.")
        return results, errors

    def table(self) -> list[list[Any]]:
        return [[
            item.get("evaluation_dataset_id"), item.get("display_name"), item.get("narrative_release_version"),
            item.get("import_validation_result"), item.get("provenance_verification_status"),
            item.get("clinical_narrative_review_status"), item.get("import_timestamp_utc"),
        ] for item in self.all()]
