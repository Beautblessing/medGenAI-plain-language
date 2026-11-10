from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


APP_ROOT = Path(__file__).resolve().parent
INPUTS = APP_ROOT / "inputs"
DEFAULT_DATASET = INPUTS / "clinical_records_reference_reviewed_v1.2.1.json"
DEFAULT_SCHEMA = INPUTS / "clinical_records_schema_v1.2.1.json"
DEFAULT_ISSUES = INPUTS / "clinical_reference_qa_issues.json"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_IN_PROCESS_LOCKS: dict[str, threading.RLock] = {}

ISSUE_DECISIONS = (
    "Approve existing information",
    "Approve with documented limitation",
    "Request correction",
    "Request additional information",
    "Reject clinical content",
)
APPROVAL_DECISIONS = (
    "Clinically Approved",
    "Clinically Approved with Limitations",
    "Further Clinical Review Required",
    "Clinically Rejected",
)
SIGNOFF_DECLARATION = (
    "I have reviewed the clinical content and outstanding quality-assurance "
    "findings within my declared scope of expertise. My decision is based on "
    "the information available in the synthetic reference dataset. I understand "
    "that automated structural validation does not establish clinical accuracy, "
    "and that my approval decision and any documented limitations will be "
    "retained in the audit trail."
)


class ReviewError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def physical_json_sha256(value: Any) -> str:
    """Hash JSON exactly as ``atomic_json`` writes it."""
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def effective_schema(source: dict[str, Any], dataset: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Adapt only the ten explicit root IDs when an older schema uses HC001 style IDs."""
    schema = copy.deepcopy(source)
    old_ids = [f"HC{i:03d}" for i in range(1, 11)]
    new_ids = [f"HC{i:04d}" for i in range(1, 11)]
    if set(dataset) != set(new_ids) or set(schema.get("properties", {})) != set(old_ids):
        return schema, None
    properties: dict[str, Any] = {}
    for old, new in zip(old_ids, new_ids):
        spec = copy.deepcopy(schema["properties"][old])
        try:
            spec["allOf"][1]["properties"]["patient_information"]["properties"]["id"]["const"] = new
        except (KeyError, IndexError, TypeError):
            raise ReviewError("The source schema's root patient-ID constraints could not be adapted safely.")
        properties[new] = spec
    schema["properties"] = properties
    schema["required"] = new_ids
    patient_info = schema.get("$defs", {}).get("patient_information", {}).get("properties", {})
    for field, pattern in {
        "id": r"^HC\d{4}$",
        "fictional_identification_number": r"^SYN-ID-\d{4}$",
        "phone_number": r"^SYN-PHONE-\d{4}$",
    }.items():
        if field not in patient_info:
            raise ReviewError(f"The source schema is missing the expected patient_information.{field} definition.")
        patient_info[field]["pattern"] = pattern
    source_id = str(schema.get("$id", "https://example.invalid/clinical-schema.json")).split("#", 1)[0]
    schema["$id"] = source_id.removesuffix(".json") + ".effective-hc0001-ids.json"
    schema["description"] = str(schema.get("description", "")) + (
        " Effective application schema: explicit root IDs and the three related synthetic-ID "
        "patterns were mapped from three digits to four digits to match the reviewed V1.2.1 dataset."
    )
    return schema, (
        "Mapped explicit root IDs HC001-HC010 to HC0001-HC0010 and aligned the patient ID, "
        "synthetic identification number, and synthetic phone number patterns to four digits; "
        "all clinical record definitions are unchanged."
    )


def deterministic_checks(dataset: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for record_id, patient in dataset.items():
        try:
            info = patient["patient_information"]
            if info.get("id") != record_id:
                problems.append(f"{record_id}: nested patient ID does not match the root ID")
            born = datetime.strptime(info["date_of_birth"], "%d/%m/%Y").date()
            seen = datetime.strptime(info["encounter_date"], "%d/%m/%Y").date()
            expected = seen.year - born.year - ((seen.month, seen.day) < (born.month, born.day))
            if seen < born:
                problems.append(f"{record_id}: encounter date precedes date of birth")
            elif info.get("age") != expected:
                problems.append(f"{record_id}: documented age {info.get('age')} differs from calculated age {expected}")
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"{record_id}: deterministic demographic check failed: {exc}")
            continue
        try:
            medications = patient["medication_management"]["medications"]
            for index, medication in enumerate(medications):
                if medication.get("lifecycle_status") == "planned" and medication.get("administration_events"):
                    problems.append(f"{record_id}: medication {index} is planned but has administration events")
                for event_index, event in enumerate(medication.get("administration_events", [])):
                    if event.get("status") == "not_given" and not event.get("reason"):
                        problems.append(f"{record_id}: medication {index} event {event_index} is not_given without a reason")
        except (KeyError, TypeError):
            pass  # The structural validator reports missing/wrongly typed sections.
    return problems


@dataclass
class ValidationResult:
    passed: bool
    structural_errors: list[str]
    deterministic_errors: list[str]
    schema_note: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "technical_validation": "Passed" if self.passed else "Failed",
            "structural_errors": self.structural_errors,
            "deterministic_errors": self.deterministic_errors,
            "effective_schema_note": self.schema_note,
        }


def validate(dataset: dict[str, Any], source_schema: dict[str, Any]) -> tuple[ValidationResult, dict[str, Any]]:
    schema, note = effective_schema(source_schema, dataset)
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(dataset), key=lambda e: list(e.absolute_path))
    structural = [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]
    deterministic = deterministic_checks(dataset) if not structural else []
    result = ValidationResult(not structural and not deterministic, structural, deterministic, note)
    return result, schema


def normalize_reviewer(values: dict[str, Any]) -> dict[str, Any]:
    reviewer = {
        "full_name": str(values.get("full_name", "")).strip(),
        "professional_role": str(values.get("professional_role", "")).strip(),
        "clinical_specialty": str(values.get("clinical_specialty", "")).strip() or None,
        "organization": str(values.get("organization", "")).strip() or None,
        "registration_reference": str(values.get("registration_reference", "")).strip() or None,
        "declared_qualifications": str(values.get("declared_qualifications", "")).strip() or None,
        "scope_of_review": str(values.get("scope_of_review", "")).strip() or None,
        "reviewer_comments": str(values.get("reviewer_comments", "")).strip() or None,
        "qualification_status": (
            "Self-declared; not independently verified by this application"
            if values.get("declared_qualifications") or values.get("clinical_specialty")
            or values.get("registration_reference") or values.get("scope_of_review")
            else "Not provided"
        ),
    }
    missing = [key for key in ("full_name", "professional_role") if not reviewer[key]]
    if missing:
        raise ReviewError("Complete the required reviewer fields: " + ", ".join(missing) + ".")
    reviewer["self_declaration_acknowledged"] = bool(values.get("self_declaration_acknowledged"))
    return reviewer


class ReviewStore:
    def __init__(self, root: Path | str = APP_ROOT / "runtime") -> None:
        self.root = Path(root)
        self.versions = self.root / "dataset_versions"
        self.uploads = self.root / "uploads"
        self.exports = self.root / "exports"
        self.correction_previews = self.root / "correction_previews"
        self.safety_snapshots = self.root / "safety_snapshots"
        self.state_path = self.root / "state.json"
        self.decisions_path = self.root / "human_review_decisions.jsonl"
        self.approvals_path = self.root / "clinical_approvals.jsonl"
        self.audit_path = self.root / "audit_log.jsonl"
        self.issues_path = self.root / "issues.json"
        self.source_schema_path = self.root / "source_schema.json"
        self.initialize()

    def initialize(self) -> None:
        self.versions.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.exports.mkdir(parents=True, exist_ok=True)
        if not self.issues_path.exists():
            shutil.copy2(DEFAULT_ISSUES, self.issues_path)
        if not self.source_schema_path.exists():
            shutil.copy2(DEFAULT_SCHEMA, self.source_schema_path)
        if not self.state_path.exists():
            dataset = load_json(DEFAULT_DATASET)
            version_path = self.versions / "clinical_records_reference_v0001.json"
            atomic_json(version_path, dataset)
            state = {"dataset_version": 1, "dataset_file": version_path.name, "created_at_utc": utc_now()}
            atomic_json(self.state_path, state)
            self.audit("workspace_initialized", {"dataset_version": 1, "dataset_sha256": sha256_value(dataset)})

    def audit(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        prior = read_jsonl(self.audit_path)
        previous_hash = prior[-1]["event_hash"] if prior else None
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "timestamp_utc": utc_now(),
            "payload": payload,
            "previous_event_hash": previous_hash,
        }
        event["event_hash"] = sha256_value(event)
        append_jsonl(self.audit_path, event)
        return event

    def state(self) -> dict[str, Any]:
        return load_json(self.state_path)

    def dataset_path(self) -> Path:
        return self.versions / self.state()["dataset_file"]

    def dataset(self) -> dict[str, Any]:
        return load_json(self.dataset_path())

    def dataset_identity(self) -> tuple[int, str]:
        return int(self.state()["dataset_version"]), sha256_value(self.dataset())

    def issues(self) -> list[dict[str, Any]]:
        return load_json(self.issues_path)

    @staticmethod
    def _uploaded_json(path_value: str | Path | None, label: str) -> tuple[Path, Any]:
        if not path_value:
            raise ReviewError(f"Upload the {label} JSON file.")
        path = Path(path_value)
        if not path.is_file():
            raise ReviewError(f"The uploaded {label} file is unavailable.")
        if path.suffix.lower() != ".json":
            raise ReviewError(f"The {label} must be a .json file.")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ReviewError(f"The {label} exceeds the 25 MB upload limit.")
        try:
            return path, load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            raise ReviewError(f"The {label} is not readable valid JSON: {exc}") from exc

    @staticmethod
    def _validate_issue_register(issues: Any) -> list[dict[str, Any]]:
        if isinstance(issues, dict):
            for key in ("qa_issues", "issues", "findings"):
                if isinstance(issues.get(key), list):
                    issues = issues[key]
                    break
            else:
                keys = [str(key) for key in issues]
                if keys and all(key.startswith("HC") for key in keys):
                    raise ReviewError(
                        "The selected QA issues file appears to be a clinical dataset keyed by patient IDs, "
                        "not a QA issue register. Upload the matching QA issues JSON in the QA issues field."
                    )
        if not isinstance(issues, list):
            raise ReviewError(
                "The QA issues JSON must contain an array of issue objects, either at the top level "
                "or under 'qa_issues', 'issues', or 'findings'."
            )
        required = {
            "issue_id", "patient_id", "json_field_path", "severity",
            "explanation", "suggested_correction_or_review_action",
        }
        accepted_severities = {"critical", "major", "minor", "informational"}
        identifiers: list[str] = []
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                raise ReviewError(f"QA issue {index + 1} is not an object.")
            missing = sorted(key for key in required if not issue.get(key))
            if missing:
                raise ReviewError(f"QA issue {index + 1} is missing required fields: {', '.join(missing)}.")
            if str(issue["severity"]).lower() not in accepted_severities:
                raise ReviewError(f"QA issue {issue['issue_id']} has an unsupported severity.")
            if not str(issue["json_field_path"]).startswith("/"):
                raise ReviewError(f"QA issue {issue['issue_id']} does not contain a JSON Pointer field path.")
            identifiers.append(str(issue["issue_id"]))
        duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
        if duplicates:
            raise ReviewError("QA issue IDs must be unique; duplicates: " + ", ".join(duplicates) + ".")
        return issues

    def inspect_reference_bundle(
        self,
        dataset_file: str | Path | None,
        schema_file: str | Path | None,
        issues_file: str | Path | None,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        dataset_path, dataset = self._uploaded_json(dataset_file, "reference dataset")
        schema_path, schema = self._uploaded_json(schema_file, "JSON Schema")
        issues_path, issues_value = self._uploaded_json(issues_file, "QA issues")
        if not isinstance(dataset, dict):
            raise ReviewError("The reference dataset must be a JSON object keyed by patient ID.")
        if not isinstance(schema, dict):
            raise ReviewError("The JSON Schema must be a JSON object.")
        schema_keys = {str(key) for key in schema}
        if schema_keys and all(key.startswith("HC") for key in schema_keys):
            raise ReviewError(
                "The selected JSON Schema file appears to be a clinical dataset keyed by patient IDs, "
                "not an authoritative JSON Schema. Upload the matching schema in the JSON Schema field."
            )
        schema_markers = {"$schema", "$id", "$ref", "$defs", "definitions", "type", "properties", "allOf", "anyOf", "oneOf"}
        if not schema_keys.intersection(schema_markers):
            raise ReviewError(
                "The selected JSON Schema file does not contain recognizable JSON Schema keywords. "
                "Upload the matching authoritative schema in the JSON Schema field."
            )
        issues = self._validate_issue_register(issues_value)
        try:
            validation, effective = validate(dataset, schema)
        except Exception as exc:
            raise ReviewError(f"The uploaded schema could not be compiled or applied: {exc}") from exc
        patient_ids = set(dataset)
        unknown_patients = sorted({str(issue["patient_id"]) for issue in issues if str(issue["patient_id"]) not in patient_ids})
        if unknown_patients:
            raise ReviewError("QA issues reference patient IDs absent from the dataset: " + ", ".join(unknown_patients) + ".")
        for issue in issues:
            pointer = str(issue["json_field_path"])
            expected_prefix = "/" + str(issue["patient_id"])
            if pointer != expected_prefix and not pointer.startswith(expected_prefix + "/"):
                raise ReviewError(
                    f"QA issue {issue['issue_id']} field path does not belong to patient {issue['patient_id']}."
                )
            try:
                self._get_pointer(dataset, pointer)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ReviewError(f"QA issue {issue['issue_id']} points to a missing dataset value: {exc}") from exc
        report = {
            "ready_for_activation": True,
            "technical_validation": validation.as_dict(),
            "technical_validation_passed": validation.passed,
            "note": (
                "A technically failing bundle may be activated for correction and provisional freezing, "
                "but it cannot receive an approved freeze until validation passes."
            ),
            "dataset_records": len(dataset),
            "qa_issues": len(issues),
            "dataset_sha256": sha256_value(dataset),
            "source_schema_sha256": sha256_value(schema),
            "effective_schema_sha256": sha256_value(effective),
            "qa_issues_sha256": sha256_value(issues),
            "uploaded_filenames": {
                "dataset": dataset_path.name,
                "schema": schema_path.name,
                "issues": issues_path.name,
            },
        }
        return dataset, schema, issues, report

    def import_reference_bundle(
        self,
        dataset_file: str | Path | None,
        schema_file: str | Path | None,
        issues_file: str | Path | None,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ReviewError("Explicitly confirm activation of the uploaded reference bundle.")
        dataset, schema, issues, report = self.inspect_reference_bundle(dataset_file, schema_file, issues_file)
        old_version, old_checksum = self.dataset_identity()
        bundle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        bundle_dir = self.uploads / bundle_id
        bundle_dir.mkdir(parents=True, exist_ok=False)
        atomic_json(bundle_dir / "reference_dataset.json", dataset)
        atomic_json(bundle_dir / "reference_schema_source.json", schema)
        atomic_json(bundle_dir / "qa_issues.json", issues)
        atomic_json(bundle_dir / "upload_report.json", report)
        new_version = old_version + 1
        new_path = self.versions / f"clinical_records_reference_v{new_version:04d}.json"
        if new_path.exists():
            raise ReviewError(f"Dataset version target already exists and will not be overwritten: {new_path.name}")
        atomic_json(new_path, dataset)
        atomic_json(self.source_schema_path, schema)
        atomic_json(self.issues_path, issues)
        state = {
            "dataset_version": new_version,
            "dataset_file": new_path.name,
            "created_at_utc": utc_now(),
            "supersedes_version": old_version,
            "active_upload_bundle": bundle_id,
        }
        atomic_json(self.state_path, state)
        event = self.audit("reference_bundle_uploaded_and_activated", {
            "bundle_id": bundle_id,
            "uploaded_filenames": report["uploaded_filenames"],
            "old_dataset_version": old_version,
            "old_dataset_sha256": old_checksum,
            "new_dataset_version": new_version,
            "new_dataset_sha256": report["dataset_sha256"],
            "source_schema_sha256": report["source_schema_sha256"],
            "qa_issues_sha256": report["qa_issues_sha256"],
            "technical_validation": report["technical_validation"],
            "review_effect": "All decisions and approvals from earlier dataset versions are stale for this uploaded bundle.",
        })
        return {**report, "bundle_id": bundle_id, "new_dataset_version": new_version, "audit_event_id": event["event_id"]}

    def issue(self, issue_id: str) -> dict[str, Any]:
        for issue in self.issues():
            if issue.get("issue_id") == issue_id:
                return issue
        raise ReviewError(f"Unknown issue ID: {issue_id}")

    def technical_validation(self, record_audit: bool = False) -> tuple[ValidationResult, dict[str, Any]]:
        result, schema = validate(self.dataset(), load_json(self.source_schema_path))
        if record_audit:
            version, checksum = self.dataset_identity()
            self.audit("technical_validation_run", {"dataset_version": version, "dataset_sha256": checksum, **result.as_dict()})
        return result, schema

    @staticmethod
    def _decision_matches_issue(record: dict[str, Any], issue: dict[str, Any]) -> bool:
        """Match history to the full issue identity, not a reusable issue ID alone."""
        return all(
            str(record.get(field, "")) == str(issue.get(field, ""))
            for field in ("issue_id", "patient_id", "json_field_path")
        )

    def _issue_decision_history(
        self,
        issue: dict[str, Any],
        exact_dataset: bool = True,
    ) -> list[dict[str, Any]]:
        records = read_jsonl(self.decisions_path)
        if exact_dataset:
            version, checksum = self.dataset_identity()
            records = [
                record for record in records
                if record.get("dataset_version") == version
                and record.get("dataset_sha256") == checksum
            ]
        return [record for record in records if self._decision_matches_issue(record, issue)]

    def latest_decisions(self, exact_dataset: bool = True) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for issue in self.issues():
            history = self._issue_decision_history(issue, exact_dataset=exact_dataset)
            if history:
                latest[issue["issue_id"]] = history[-1]
        return latest

    def issue_view(self, issue_id: str) -> dict[str, Any]:
        issue = copy.deepcopy(self.issue(issue_id))
        decision = self.latest_decisions().get(issue_id)
        dataset = self.dataset()
        version, checksum = self.dataset_identity()
        pointer = str(issue.get("json_field_path", ""))
        try:
            current_value = copy.deepcopy(self._get_pointer(dataset, pointer))
            tokens = self._pointer_tokens(pointer)
            context_tokens = tokens[:-1]
            # For array-backed findings, show the complete containing item (for
            # example, the full medication) rather than only the leaf property.
            for index, token in enumerate(tokens):
                if token.isdigit():
                    context_tokens = tokens[: index + 1]
            context_pointer = "/" + "/".join(
                token.replace("~", "~0").replace("/", "~1") for token in context_tokens
            ) if context_tokens else ""
            current_context = copy.deepcopy(self._get_pointer(dataset, context_pointer)) if context_pointer else dataset
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            current_value = {"error": f"Current value could not be resolved: {exc}"}
            current_context = None
        issue["original_clinical_information"] = issue.get("observed_value_or_evidence")
        issue["explanation_of_concern"] = issue.get("explanation")
        issue["suggested_review_action"] = issue.get("suggested_correction_or_review_action")
        issue["current_dataset_value"] = current_value
        issue["current_dataset_context"] = current_context
        issue["dataset_under_review"] = {
            "version": version,
            "sha256": checksum,
            "field_path": pointer,
        }
        issue["review_instruction"] = (
            "Compare the original finding with current_dataset_value and current_dataset_context. "
            "Record a human decision only after verifying the active dataset content within the declared review scope."
        )
        issue["current_resolution_status"] = decision["decision"] if decision else "Awaiting human decision for current dataset"
        issue["previous_reviewer_comments"] = [
            record.get("rationale")
            for record in self._issue_decision_history(issue, exact_dataset=True)
            if record.get("rationale")
        ]
        return issue

    def record_issue_decision(
        self,
        issue_id: str,
        decision: str,
        rationale: str,
        proposed_replacement_text: str,
        reviewer_values: dict[str, Any],
        correction_operation: str | None = None,
        correction_path: str | None = None,
    ) -> dict[str, Any]:
        reviewer = normalize_reviewer(reviewer_values)
        if decision not in ISSUE_DECISIONS:
            raise ReviewError("Select a human decision; no option is preselected.")
        issue = self.issue(issue_id)
        rationale = (rationale or "").strip()
        if decision in ISSUE_DECISIONS[1:] and not rationale:
            raise ReviewError("A written rationale is mandatory for this decision.")
        if issue.get("severity", "").lower() in {"critical", "major"} and decision == ISSUE_DECISIONS[0] and not rationale:
            raise ReviewError("Document the clinical acceptance rationale for a critical or major issue.")
        proposed: Any = None
        operation: dict[str, Any] | None = None
        if decision == "Request correction":
            operation_name = (correction_operation or "replace").strip().lower()
            if operation_name not in {"replace", "add", "remove"}:
                raise ReviewError("Correction operation must be replace, add, or remove.")
            operation_path = (correction_path or issue.get("json_field_path") or "").strip()
            self._pointer_tokens(operation_path, allow_append=operation_name == "add")
            if operation_name != "remove" and not (proposed_replacement_text or "").strip():
                raise ReviewError("Record the proposed replacement before requesting correction.")
            if operation_name != "remove":
                try:
                    proposed = json.loads(proposed_replacement_text)
                except json.JSONDecodeError:
                    proposed = proposed_replacement_text
            expected = None
            try:
                expected = copy.deepcopy(self._get_pointer(self.dataset(), operation_path))
            except (KeyError, IndexError, TypeError, ValueError):
                if operation_name != "add":
                    raise ReviewError(f"The correction path does not currently exist: {operation_path}")
            operation = {
                "operation": operation_name,
                "path": operation_path,
                "expected_current_value": expected,
            }
            if operation_name != "remove":
                operation["value"] = proposed
        version, checksum = self.dataset_identity()
        prior = self.latest_decisions(exact_dataset=False).get(issue_id)
        record = {
            "decision_id": str(uuid.uuid4()),
            "issue_id": issue_id,
            "patient_id": issue.get("patient_id"),
            "json_field_path": issue.get("json_field_path"),
            "severity": issue.get("severity"),
            "dataset_version": version,
            "dataset_sha256": checksum,
            "reviewer": reviewer,
            "decision": decision,
            "rationale": rationale or None,
            "proposed_replacement": proposed,
            "correction_operation": operation,
            "review_timestamp_utc": utc_now(),
            "previous_decision_reference": prior.get("decision_id") if prior else None,
            "additional_review_required": decision in {"Request correction", "Request additional information", "Reject clinical content"},
        }
        append_jsonl(self.decisions_path, record)
        self.audit("human_issue_decision_recorded", {"decision_id": record["decision_id"], "issue_id": issue_id, "dataset_version": version, "dataset_sha256": checksum, "decision": decision})
        return record

    @staticmethod
    def _pointer_tokens(pointer: str, allow_append: bool = False) -> list[str]:
        if not pointer or not pointer.startswith("/"):
            raise ReviewError("The issue JSON field path is not a valid JSON Pointer.")
        tokens = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
        if not allow_append and any(token == "-" for token in tokens):
            raise ReviewError("The '-' array append token is valid only for an add operation.")
        return tokens

    @classmethod
    def _get_pointer(cls, document: Any, pointer: str) -> Any:
        current = document
        for token in cls._pointer_tokens(pointer):
            current = current[int(token)] if isinstance(current, list) else current[token]
        return current

    @classmethod
    def _set_pointer(cls, document: Any, pointer: str, value: Any) -> None:
        tokens = cls._pointer_tokens(pointer)
        current = document
        for token in tokens[:-1]:
            current = current[int(token)] if isinstance(current, list) else current[token]
        last = tokens[-1]
        if isinstance(current, list):
            current[int(last)] = value
        else:
            current[last] = value

    @classmethod
    def _add_pointer(cls, document: Any, pointer: str, value: Any) -> None:
        tokens = cls._pointer_tokens(pointer, allow_append=True)
        if not tokens:
            raise ReviewError("Replacing the document root is not supported by an add operation.")
        current = document
        for token in tokens[:-1]:
            current = current[int(token)] if isinstance(current, list) else current[token]
        last = tokens[-1]
        if isinstance(current, list):
            if last == "-":
                current.append(value)
            else:
                index = int(last)
                if index < 0 or index > len(current):
                    raise IndexError(index)
                current.insert(index, value)
        elif isinstance(current, dict):
            if last in current:
                raise ReviewError(f"The add target already exists: {pointer}")
            current[last] = value
        else:
            raise TypeError("The add target parent is not an object or array.")

    @classmethod
    def _remove_pointer(cls, document: Any, pointer: str) -> Any:
        tokens = cls._pointer_tokens(pointer)
        if not tokens:
            raise ReviewError("Removing the document root is not supported.")
        current = document
        for token in tokens[:-1]:
            current = current[int(token)] if isinstance(current, list) else current[token]
        last = tokens[-1]
        if isinstance(current, list):
            return current.pop(int(last))
        if isinstance(current, dict):
            if last not in current:
                raise KeyError(last)
            return current.pop(last)
        raise TypeError("The remove target parent is not an object or array.")

    @classmethod
    def _apply_json_operation(cls, document: Any, operation: dict[str, Any]) -> dict[str, Any]:
        name = str(operation.get("operation", "")).lower()
        path = str(operation.get("path", ""))
        if name not in {"replace", "add", "remove"}:
            raise ReviewError("Correction operation must be replace, add, or remove.")
        before_missing = object()
        before: Any = before_missing
        try:
            before = copy.deepcopy(cls._get_pointer(document, path))
        except (KeyError, IndexError, TypeError, ValueError):
            if name != "add":
                raise ReviewError(f"The correction path does not exist: {path}")
        if "expected_current_value" in operation and before is not before_missing:
            if before != operation.get("expected_current_value"):
                raise ReviewError(f"The value at {path} changed after review; refresh the correction preview.")
        if name == "replace":
            if "value" not in operation:
                raise ReviewError("A replace operation requires a value.")
            cls._set_pointer(document, path, copy.deepcopy(operation["value"]))
        elif name == "add":
            if "value" not in operation:
                raise ReviewError("An add operation requires a value.")
            cls._add_pointer(document, path, copy.deepcopy(operation["value"]))
        else:
            cls._remove_pointer(document, path)
        if name == "remove":
            after = None
        else:
            try:
                after = copy.deepcopy(cls._get_pointer(document, path))
            except (KeyError, IndexError, TypeError, ValueError):
                after = None
        return {
            "operation_name": name,
            "path": path,
            "original_value": None if before is before_missing else before,
            "resulting_value": after,
        }

    def apply_requested_correction(self, issue_id: str, reviewer_values: dict[str, Any], confirmed: bool) -> dict[str, Any]:
        reviewer = normalize_reviewer(reviewer_values)
        if not confirmed:
            raise ReviewError("Explicitly confirm the correction before applying it.")
        request = self.latest_decisions().get(issue_id)
        if not request or request.get("decision") != "Request correction":
            raise ReviewError("The current dataset has no active human correction request for this issue.")
        dataset = self.dataset()
        pointer = request["json_field_path"]
        try:
            original = copy.deepcopy(self._get_pointer(dataset, pointer))
            self._set_pointer(dataset, pointer, copy.deepcopy(request["proposed_replacement"]))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ReviewError(f"The correction could not be applied at {pointer}: {exc}") from exc
        old_version, old_checksum = self.dataset_identity()
        new_version = old_version + 1
        new_path = self.versions / f"clinical_records_reference_v{new_version:04d}.json"
        atomic_json(new_path, dataset)
        state = {"dataset_version": new_version, "dataset_file": new_path.name, "created_at_utc": utc_now(), "supersedes_version": old_version}
        atomic_json(self.state_path, state)
        validation, _ = self.technical_validation(record_audit=True)
        new_checksum = sha256_value(dataset)
        event = self.audit("human_confirmed_correction_applied", {
            "correction_id": str(uuid.uuid4()),
            "request_decision_id": request["decision_id"],
            "issue_id": issue_id,
            "patient_id": request.get("patient_id"),
            "json_field_path": pointer,
            "original_value": original,
            "replacement_value": request["proposed_replacement"],
            "rationale": request["rationale"],
            "confirmed_by": reviewer,
            "old_dataset_version": old_version,
            "old_dataset_sha256": old_checksum,
            "new_dataset_version": new_version,
            "new_dataset_sha256": new_checksum,
            "technical_validation": validation.as_dict(),
            "review_effect": "All prior issue decisions and approvals are stale for the new dataset; renewed review and sign-off are required.",
        })
        return event

    def gate_report(self) -> dict[str, Any]:
        validation, _ = self.technical_validation()
        current = self.latest_decisions()
        missing = [issue["issue_id"] for issue in self.issues() if issue["issue_id"] not in current]
        correction_open = [k for k, v in current.items() if v["decision"] == "Request correction"]
        additional = [k for k, v in current.items() if v["decision"] == "Request additional information"]
        rejected = [k for k, v in current.items() if v["decision"] == "Reject clinical content"]
        accepted = {"Approve existing information", "Approve with documented limitation"}
        unresolved_critical = [
            issue["issue_id"] for issue in self.issues()
            if str(issue.get("severity", "")).lower() == "critical"
            and current.get(issue["issue_id"], {}).get("decision") not in accepted
        ]
        unresolved_major = [
            issue["issue_id"] for issue in self.issues()
            if str(issue.get("severity", "")).lower() == "major"
            and current.get(issue["issue_id"], {}).get("decision") not in accepted
        ]
        limitations = [
            {"issue_id": key, "rationale": value.get("rationale")}
            for key, value in current.items() if value["decision"] == "Approve with documented limitation"
        ]
        blockers = []
        if not validation.passed:
            blockers.append("Technical validation failed")
        if missing:
            blockers.append("Issues without a decision for this exact dataset: " + ", ".join(missing))
        if unresolved_critical:
            blockers.append("Unresolved critical issues: " + ", ".join(unresolved_critical))
        if unresolved_major:
            blockers.append("Unresolved major issues: " + ", ".join(unresolved_major))
        if correction_open:
            blockers.append("Requested corrections not completed and reaccepted: " + ", ".join(correction_open))
        if additional:
            blockers.append("Additional information requested: " + ", ".join(additional))
        if rejected:
            blockers.append("Rejected clinical content: " + ", ".join(rejected))
        version, checksum = self.dataset_identity()
        return {
            "dataset_version": version,
            "dataset_sha256": checksum,
            "technical_validation": validation.as_dict(),
            "current_issue_decisions": len(current),
            "total_issues": len(self.issues()),
            "limitations": limitations,
            "blockers": blockers,
            "eligible_for_clinical_approval": not blockers,
        }

    def record_clinical_approval(
        self,
        decision: str,
        justification: str,
        reviewer_values: dict[str, Any],
        signoff_confirmed: bool,
    ) -> dict[str, Any]:
        reviewer = normalize_reviewer(reviewer_values)
        if decision not in APPROVAL_DECISIONS:
            raise ReviewError("Select a clinical approval decision; no option is preselected.")
        if not signoff_confirmed:
            raise ReviewError("Actively acknowledge the mandatory human sign-off declaration.")
        justification = (justification or "").strip()
        if decision == "Clinically Approved with Limitations" and not justification:
            raise ReviewError("Written justification is mandatory for approval with limitations.")
        if decision in {"Further Clinical Review Required", "Clinically Rejected"} and not justification:
            raise ReviewError("Document the rationale for the pending or rejected clinical decision.")
        gate = self.gate_report()
        if decision in {"Clinically Approved", "Clinically Approved with Limitations"} and gate["blockers"]:
            raise ReviewError("Clinical approval is blocked: " + "; ".join(gate["blockers"]))
        if decision == "Clinically Approved" and gate["limitations"]:
            raise ReviewError("One or more findings were accepted with limitations; use 'Clinically Approved with Limitations'.")
        previous = read_jsonl(self.approvals_path)
        record = {
            "approval_id": str(uuid.uuid4()),
            "dataset_version": gate["dataset_version"],
            "dataset_sha256": gate["dataset_sha256"],
            "reviewer": reviewer,
            "approval_decision": decision,
            "justification": justification or None,
            "outstanding_limitations": gate["limitations"],
            "signoff_declaration": SIGNOFF_DECLARATION,
            "signoff_explicitly_confirmed": True,
            "review_timestamp_utc": utc_now(),
            "previous_approval_reference": previous[-1]["approval_id"] if previous else None,
            "additional_review_required": decision == "Further Clinical Review Required",
            "gate_snapshot": gate,
        }
        append_jsonl(self.approvals_path, record)
        self.audit("clinical_approval_decision_recorded", {"approval_id": record["approval_id"], "dataset_version": record["dataset_version"], "dataset_sha256": record["dataset_sha256"], "decision": decision})
        return record

    def current_approval(self) -> dict[str, Any] | None:
        version, checksum = self.dataset_identity()
        records = [r for r in read_jsonl(self.approvals_path) if r["dataset_version"] == version and r["dataset_sha256"] == checksum]
        return records[-1] if records else None

    def export_review_files(self, target: Path | None = None) -> list[Path]:
        target = target or self.exports
        target.mkdir(parents=True, exist_ok=True)
        decisions = read_jsonl(self.decisions_path)
        approvals = read_jsonl(self.approvals_path)
        audit = read_jsonl(self.audit_path)
        decision_file = target / "clinical_reference_human_review_decisions.json"
        approval_file = target / "clinical_reference_clinical_approval.json"
        signoff_file = target / "clinical_reference_clinical_signoff.txt"
        audit_file = target / "clinical_reference_human_review_audit_log.json"
        atomic_json(decision_file, decisions)
        atomic_json(approval_file, approvals)
        atomic_json(audit_file, audit)
        lines = ["CLINICAL REFERENCE HUMAN SIGN-OFF HISTORY", "", "Automated validation does not establish clinical accuracy.", ""]
        if not approvals:
            lines.append("No human clinical approval decision has been recorded.")
        for item in approvals:
            lines.extend([
                f"Approval ID: {item['approval_id']}",
                f"Dataset version: {item['dataset_version']}",
                f"Dataset SHA-256: {item['dataset_sha256']}",
                f"Decision: {item['approval_decision']}",
                f"Reviewer: {item['reviewer']['full_name']} ({item['reviewer']['professional_role']})",
                f"Qualifications: {item['reviewer']['declared_qualifications']} [{item['reviewer']['qualification_status']}]",
                f"Scope: {item['reviewer']['scope_of_review']}",
                f"Timestamp UTC: {item['review_timestamp_utc']}",
                f"Justification: {item.get('justification') or 'None documented'}",
                f"Declaration: {item['signoff_declaration']}",
                "",
            ])
        signoff_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return [decision_file, approval_file, signoff_file, audit_file]

    def freeze(self, freeze_type: str) -> tuple[Path, dict[str, Any]]:
        if freeze_type not in {"provisional", "approved"}:
            raise ReviewError("Freeze type must be provisional or approved.")
        gate = self.gate_report()
        approval = self.current_approval()
        approved_statuses = {"Clinically Approved", "Clinically Approved with Limitations"}
        if freeze_type == "approved":
            if gate["blockers"]:
                raise ReviewError("Approved freeze is blocked: " + "; ".join(gate["blockers"]))
            if not approval or approval.get("approval_decision") not in approved_statuses:
                raise ReviewError("Approved freeze requires an explicit current human clinical approval for the exact dataset checksum.")
        validation, schema = self.technical_validation(record_audit=True)
        version, checksum = self.dataset_identity()
        clinical_state = "Pending"
        if approval:
            clinical_state = {
                "Clinically Approved": "Approved",
                "Clinically Approved with Limitations": "Approved with limitations",
                "Further Clinical Review Required": "Pending",
                "Clinically Rejected": "Rejected",
            }[approval["approval_decision"]]
        manifest = {
            "manifest_version": "1.0",
            "created_at_utc": utc_now(),
            "dataset_version": version,
            "dataset_sha256": checksum,
            "technical_validation": "Passed" if validation.passed else "Failed",
            "clinical_review": clinical_state,
            "dataset_freeze": "Approved frozen" if freeze_type == "approved" else "Provisionally frozen",
            "human_clinical_review_occurred": bool(read_jsonl(self.decisions_path) or read_jsonl(self.approvals_path)),
            "declared_reviewer_qualifications": approval.get("reviewer", {}).get("declared_qualifications") if approval else None,
            "qualification_verification": approval.get("reviewer", {}).get("qualification_status") if approval else None,
            "reviewer_decision": approval.get("approval_decision") if approval else None,
            "approval_dataset_sha256": approval.get("dataset_sha256") if approval else None,
            "outstanding_limitations": approval.get("outstanding_limitations", []) if approval else gate["limitations"],
            "clinical_approval_pending": clinical_state == "Pending",
            "approved_for_downstream_narrative_generation": freeze_type == "approved" and clinical_state in {"Approved", "Approved with limitations"},
            "gate_snapshot": gate,
            "safeguard": "Clinical judgments and final clinical approval were not generated automatically by the application.",
        }
        freeze_id = f"v{version:04d}_{checksum[:12]}_{freeze_type}"
        folder = self.exports / freeze_id
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.dataset_path(), folder / "clinical_records_reference.json")
        atomic_json(folder / "clinical_records_schema_effective.json", schema)
        shutil.copy2(self.source_schema_path, folder / "clinical_records_schema_source.json")
        shutil.copy2(self.issues_path, folder / "clinical_reference_qa_issues.json")
        atomic_json(folder / "clinical_reference_technical_validation.json", validation.as_dict())
        atomic_json(folder / "clinical_reference_freeze_manifest.json", manifest)
        zip_path = self.exports / f"clinical_reference_{freeze_id}.zip"
        self.audit("dataset_freeze_created", {
            "freeze_type": freeze_type,
            "dataset_version": version,
            "dataset_sha256": checksum,
            "zip_file": zip_path.name,
        })
        self.export_review_files(folder)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.iterdir()):
                archive.write(path, path.name)
        return zip_path, manifest

    def status(self) -> dict[str, Any]:
        gate = self.gate_report()
        approval = self.current_approval()
        return {
            **gate,
            "current_clinical_approval": approval.get("approval_decision") if approval else "No current hash-bound approval",
            "dataset_file": str(self.dataset_path()),
            "active_upload_bundle": self.state().get("active_upload_bundle", "Bundled application inputs"),
            "stale_approval_records": len(read_jsonl(self.approvals_path)) - (1 if approval else 0),
        }


# Preserve the proven clinical-review behavior above while extending its storage
# and lifecycle model. Existing JSON and JSONL evidence remains authoritative.
_LegacyReviewStore = ReviewStore


class ReviewStore(_LegacyReviewStore):
    REGISTRY_VERSION = "2.0"

    def __init__(self, root: Path | str = APP_ROOT / "runtime") -> None:
        self.root = Path(root)
        self.versions = self.root / "dataset_versions"
        self.version_bundles = self.root / "version_bundles"
        self.uploads = self.root / "uploads"
        self.exports = self.root / "exports"
        self.metadata_backups = self.root / "metadata_backups"
        self.correction_previews = self.root / "correction_previews"
        self.safety_snapshots = self.root / "safety_snapshots"
        self.state_path = self.root / "state.json"
        self.decisions_path = self.root / "human_review_decisions.jsonl"
        self.approvals_path = self.root / "clinical_approvals.jsonl"
        self.audit_path = self.root / "audit_log.jsonl"
        self.issues_path = self.root / "issues.json"
        self.source_schema_path = self.root / "source_schema.json"
        self.version_registry_path = self.root / "version_registry.json"
        self.upload_registry_path = self.root / "upload_registry.json"
        self.freeze_registry_path = self.root / "freeze_registry.json"
        self.migration_report_path = self.root / "migration_report.json"
        self.lock_path = self.root / ".lifecycle.lock"
        self.initialize()

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        process_lock = _IN_PROCESS_LOCKS.setdefault(str(self.lock_path.resolve()), threading.RLock())
        with process_lock:
            with self.lock_path.open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def initialize(self) -> None:
        for directory in (
            self.versions, self.version_bundles, self.uploads, self.exports,
            self.metadata_backups, self.correction_previews, self.safety_snapshots,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.issues_path.exists():
            shutil.copy2(DEFAULT_ISSUES, self.issues_path)
        if not self.source_schema_path.exists():
            shutil.copy2(DEFAULT_SCHEMA, self.source_schema_path)
        created_workspace = False
        if not self.state_path.exists():
            dataset = load_json(DEFAULT_DATASET)
            version_path = self.versions / "clinical_records_reference_v0001.json"
            atomic_json(version_path, dataset)
            atomic_json(self.state_path, {
                "dataset_version": 1,
                "dataset_file": version_path.name,
                "created_at_utc": utc_now(),
                "creation_method": "initial_bundled_dataset",
            })
            created_workspace = True
        if created_workspace:
            self.audit("workspace_initialized", {
                "dataset_version": 1,
                "dataset_sha256": sha256_value(load_json(DEFAULT_DATASET)),
            })
        self.reconcile_runtime()

    def _empty_registry(self, key: str) -> dict[str, Any]:
        return {"registry_version": self.REGISTRY_VERSION, key: []}

    def _registry(self, path: Path, key: str) -> dict[str, Any]:
        if not path.exists():
            return self._empty_registry(key)
        value = load_json(path)
        if not isinstance(value, dict) or not isinstance(value.get(key), list):
            raise ReviewError(f"Invalid lifecycle registry: {path.name}")
        return value

    def _metadata_backup_once(self) -> Path | None:
        marker = self.metadata_backups / "registry_migration_completed.json"
        if marker.exists():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.metadata_backups / f"pre_registry_migration_{stamp}"
        target.mkdir(parents=True, exist_ok=False)
        for path in (
            self.state_path, self.issues_path, self.source_schema_path,
            self.decisions_path, self.approvals_path, self.audit_path,
            self.version_registry_path, self.upload_registry_path,
            self.freeze_registry_path,
        ):
            if path.exists():
                shutil.copy2(path, target / path.name)
        atomic_json(marker, {
            "created_at_utc": utc_now(),
            "backup_directory": str(target),
            "purpose": "Metadata backup before lifecycle-registry reconciliation",
        })
        return target

    def reconcile_runtime(self) -> dict[str, Any]:
        with self._locked():
            backup = self._metadata_backup_once()
            uploads = self._reconcile_upload_registry()
            versions, unavailable = self._reconcile_version_registry(uploads)
            freezes = self._reconcile_freeze_registry()
            report = {
                "migration_version": self.REGISTRY_VERSION,
                "reconciled_at_utc": utc_now(),
                "metadata_backup": str(backup) if backup else "Previously completed",
                "dataset_versions": len(versions["versions"]),
                "uploaded_bundles": len(uploads["uploads"]),
                "freeze_packages": len(freezes["freezes"]),
                "unrecoverable_historical_artifacts": unavailable,
                "idempotent": True,
            }
            atomic_json(self.migration_report_path, report)
            return report

    def _audit_events(self) -> list[dict[str, Any]]:
        return read_jsonl(self.audit_path)

    def audit(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._locked():
            prior = read_jsonl(self.audit_path)
            previous_hash = prior[-1]["event_hash"] if prior else None
            event = {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "timestamp_utc": utc_now(),
                "payload": payload,
                "previous_event_hash": previous_hash,
            }
            event["event_hash"] = sha256_value(event)
            append_jsonl(self.audit_path, event)
            return event

    def has_active_dataset(self) -> bool:
        state = self.state()
        return bool(state.get("dataset_version") and state.get("dataset_file"))

    def dataset_path(self) -> Path:
        if not self.has_active_dataset():
            raise ReviewError("No active dataset. Upload, validate, and activate a reference bundle first.")
        return self.versions / str(self.state()["dataset_file"])

    def dataset_identity(self) -> tuple[int, str]:
        if not self.has_active_dataset():
            raise ReviewError("No active dataset is available.")
        return int(self.state()["dataset_version"]), sha256_value(self.dataset())

    def _version_bundle(self, version: int) -> Path:
        return self.version_bundles / f"v{version:04d}"

    def version_entry(self, version: int) -> dict[str, Any]:
        for entry in self._registry(self.version_registry_path, "versions")["versions"]:
            if int(entry["version"]) == int(version):
                return entry
        raise ReviewError(f"Unknown dataset version: {version}")

    def _active_artifact(self, name: str, legacy: Path) -> Path:
        version, _ = self.dataset_identity()
        candidate = self._version_bundle(version) / name
        return candidate if candidate.exists() else legacy

    def issues(self) -> list[dict[str, Any]]:
        if not self.has_active_dataset():
            return []
        return load_json(self._active_artifact("qa_issues.json", self.issues_path))

    def _active_schema_path(self) -> Path:
        return self._active_artifact("source_schema.json", self.source_schema_path)

    def _reconcile_upload_registry(self) -> dict[str, Any]:
        existing = {
            item["bundle_id"]: item
            for item in self._registry(self.upload_registry_path, "uploads")["uploads"]
        }
        activation_events: dict[str, dict[str, Any]] = {}
        for event in self._audit_events():
            payload = event.get("payload", {})
            if payload.get("bundle_id"):
                activation_events[str(payload["bundle_id"])] = event
        entries: list[dict[str, Any]] = []
        for folder in sorted(path for path in self.uploads.iterdir() if path.is_dir()):
            bundle_id = folder.name
            report_path = folder / "validation_report.json"
            if not report_path.exists():
                report_path = folder / "upload_report.json"
            report = load_json(report_path) if report_path.exists() else {}
            event = activation_events.get(bundle_id)
            payload = event.get("payload", {}) if event else {}
            files = {
                "dataset": folder / "reference_dataset.json",
                "schema": folder / "reference_schema_source.json",
                "issues": folder / "qa_issues.json",
                "validation_report": report_path,
            }
            prior = existing.get(bundle_id, {})
            created = prior.get("uploaded_at_utc") or (event or {}).get("timestamp_utc")
            if not created:
                created = datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
            technical = report.get("technical_validation", {})
            validation_status = report.get("validation_status")
            if not validation_status:
                if technical:
                    validation_status = technical.get("technical_validation", "Unknown")
                else:
                    validation_status = "Not completed"
            entry = {
                "bundle_id": bundle_id,
                "uploaded_at_utc": created,
                "original_filenames": report.get("uploaded_filenames") or payload.get("uploaded_filenames") or prior.get("original_filenames", {}),
                "dataset_canonical_sha256": report.get("dataset_sha256") or prior.get("dataset_canonical_sha256"),
                "schema_canonical_sha256": report.get("source_schema_sha256") or prior.get("schema_canonical_sha256"),
                "qa_issues_canonical_sha256": report.get("qa_issues_sha256") or prior.get("qa_issues_canonical_sha256"),
                "physical_sha256": {
                    key: sha256_file(path) if path.exists() and path.is_file() else None
                    for key, path in files.items() if key != "validation_report"
                },
                "validation_status": validation_status,
                "validation_report": str(report_path.relative_to(self.root)) if report_path.exists() else None,
                "activation_status": "Activated" if event and payload.get("new_dataset_version") else prior.get("activation_status", "Not activated"),
                "activated_version": payload.get("new_dataset_version") if event else prior.get("activated_version"),
                "activation_reason": prior.get("activation_reason"),
                "files": {
                    key: str(path.relative_to(self.root)) if path.exists() else None
                    for key, path in files.items()
                },
            }
            manifest_path = folder / "upload_manifest.json"
            if not manifest_path.exists():
                atomic_json(manifest_path, entry)
            entry["files"]["manifest"] = str(manifest_path.relative_to(self.root))
            entries.append(entry)
        registry = {"registry_version": self.REGISTRY_VERSION, "uploads": entries}
        atomic_json(self.upload_registry_path, registry)
        return registry

    def _version_event_map(self) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for event in self._audit_events():
            payload = event.get("payload", {})
            version = payload.get("new_dataset_version")
            if event.get("event_type") == "workspace_initialized":
                version = payload.get("dataset_version")
            if version:
                result[int(version)] = event
        return result

    def _reconcile_version_registry(self, uploads: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        events = self._version_event_map()
        upload_map = {item["bundle_id"]: item for item in uploads["uploads"]}
        prior_entries = {
            int(item["version"]): item
            for item in self._registry(self.version_registry_path, "versions")["versions"]
        }
        state = self.state()
        active_version = state.get("dataset_version")
        entries: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        for dataset_file in sorted(self.versions.glob("clinical_records_reference_v*.json")):
            try:
                version = int(dataset_file.stem.rsplit("v", 1)[1])
            except ValueError:
                continue
            dataset = load_json(dataset_file)
            event = events.get(version, {})
            payload = event.get("payload", {})
            bundle_id = payload.get("bundle_id") or prior_entries.get(version, {}).get("source_upload_bundle")
            upload = upload_map.get(str(bundle_id)) if bundle_id else None
            if version == 1:
                creation_method = "initial_bundled_dataset"
                schema_source = DEFAULT_SCHEMA
                issues_source = DEFAULT_ISSUES
            elif event.get("event_type") in {"human_confirmed_correction_applied", "human_confirmed_correction_batch_applied"}:
                creation_method = "human_correction_batch" if event.get("event_type").endswith("batch_applied") else "human_correction"
                previous = self._version_bundle(int(payload.get("old_dataset_version", version - 1)))
                schema_source = previous / "source_schema.json"
                issues_source = previous / "qa_issues.json"
            elif event.get("event_type") == "historical_version_reactivated":
                creation_method = "historical_version_reactivation"
                source = self._version_bundle(int(payload.get("source_dataset_version", 0)))
                schema_source = source / "source_schema.json"
                issues_source = source / "qa_issues.json"
            else:
                creation_method = "uploaded_bundle_activation"
                schema_source = self.root / upload["files"]["schema"] if upload and upload.get("files", {}).get("schema") else Path("missing")
                issues_source = self.root / upload["files"]["issues"] if upload and upload.get("files", {}).get("issues") else Path("missing")
            if version == active_version:
                if not schema_source.exists() and self.source_schema_path.exists():
                    schema_source = self.source_schema_path
                if not issues_source.exists() and self.issues_path.exists():
                    issues_source = self.issues_path
            bundle = self._version_bundle(version)
            bundle.mkdir(parents=True, exist_ok=True)
            artifacts: dict[str, str | None] = {}
            dataset_copy = bundle / "dataset.json"
            if not dataset_copy.exists():
                shutil.copy2(dataset_file, dataset_copy)
            artifacts["dataset"] = str(dataset_copy.relative_to(self.root))
            schema_copy = bundle / "source_schema.json"
            issues_copy = bundle / "qa_issues.json"
            if schema_source.exists() and not schema_copy.exists():
                shutil.copy2(schema_source, schema_copy)
            if issues_source.exists() and not issues_copy.exists():
                shutil.copy2(issues_source, issues_copy)
            artifacts["source_schema"] = str(schema_copy.relative_to(self.root)) if schema_copy.exists() else None
            artifacts["qa_issues"] = str(issues_copy.relative_to(self.root)) if issues_copy.exists() else None
            validation_report: dict[str, Any] = {}
            effective_copy = bundle / "effective_schema.json"
            validation_path = bundle / "validation_report.json"
            if schema_copy.exists():
                try:
                    validation, effective = validate(dataset, load_json(schema_copy))
                    validation_report = validation.as_dict()
                    if not effective_copy.exists():
                        atomic_json(effective_copy, effective)
                    if not validation_path.exists():
                        atomic_json(validation_path, validation_report)
                except Exception as exc:
                    validation_report = {"technical_validation": "Unknown", "error": str(exc)}
            else:
                validation_report = {"technical_validation": "Unknown", "error": "Historical source schema not recoverable"}
                unavailable.append({"version": version, "artifact": "source_schema"})
            if not issues_copy.exists():
                unavailable.append({"version": version, "artifact": "qa_issues"})
            artifacts["effective_schema"] = str(effective_copy.relative_to(self.root)) if effective_copy.exists() else None
            artifacts["validation_report"] = str(validation_path.relative_to(self.root)) if validation_path.exists() else None
            created = (event or {}).get("timestamp_utc") or prior_entries.get(version, {}).get("created_at_utc")
            if version == active_version:
                created = state.get("created_at_utc") or created
            if not created:
                created = datetime.fromtimestamp(dataset_file.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
            entry = {
                "version": version,
                "dataset_file": dataset_file.name,
                "canonical_sha256": sha256_value(dataset),
                "physical_file_sha256": sha256_file(dataset_file),
                "created_at_utc": created,
                "creation_method": creation_method,
                "source_upload_bundle": bundle_id,
                "source_version": payload.get("source_dataset_version"),
                "supersedes_version": payload.get("old_dataset_version") or prior_entries.get(version, {}).get("supersedes_version"),
                "dataset_records": len(dataset),
                "validation": validation_report,
                "validation_timestamp_utc": prior_entries.get(version, {}).get("validation_timestamp_utc") or created,
                "artifacts": artifacts,
            }
            atomic_json(bundle / "version_manifest.json", entry)
            entry["artifacts"]["version_manifest"] = str((bundle / "version_manifest.json").relative_to(self.root))
            entries.append(entry)
        registry = {"registry_version": self.REGISTRY_VERSION, "versions": sorted(entries, key=lambda item: item["version"])}
        atomic_json(self.version_registry_path, registry)
        return registry, unavailable

    def _reconcile_freeze_registry(self) -> dict[str, Any]:
        prior = {
            item["freeze_id"]: item
            for item in self._registry(self.freeze_registry_path, "freezes")["freezes"]
        }
        audit_freezes = [event for event in self._audit_events() if event.get("event_type") == "dataset_freeze_created"]
        approvals = read_jsonl(self.approvals_path)
        versions = {
            int(item["version"]): item
            for item in self._registry(self.version_registry_path, "versions")["versions"]
        }
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in audit_freezes:
            payload = event.get("payload", {})
            zip_name = payload.get("zip_file")
            if not zip_name:
                continue
            zip_path = self.exports / str(zip_name)
            freeze_id = payload.get("freeze_id") or zip_path.stem.removeprefix("clinical_reference_")
            if freeze_id in seen:
                continue
            seen.add(str(freeze_id))
            manifest: dict[str, Any] = {}
            if zip_path.exists():
                try:
                    with zipfile.ZipFile(zip_path) as archive:
                        candidates = [name for name in archive.namelist() if name.endswith("freeze_manifest.json")]
                        if candidates:
                            manifest = json.loads(archive.read(candidates[0]))
                except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
                    manifest = {}
            verification = self.verify_freeze_package(zip_path) if zip_path.exists() else {"status": "Missing"}
            previous = prior.get(str(freeze_id), {})
            dataset_version = payload.get("dataset_version") or manifest.get("dataset_version")
            dataset_checksum = payload.get("dataset_sha256") or manifest.get("dataset_sha256")
            matching_approvals = [
                approval for approval in approvals
                if int(approval.get("dataset_version", -1)) == int(dataset_version or -1)
                and approval.get("dataset_sha256") == dataset_checksum
            ]
            controlling_approval = matching_approvals[-1] if matching_approvals else None
            version_entry = versions.get(int(dataset_version)) if dataset_version is not None else None
            entries.append({
                "freeze_id": str(freeze_id),
                "freeze_type": payload.get("freeze_type") or previous.get("freeze_type"),
                "created_at_utc": manifest.get("created_at_utc") or event.get("timestamp_utc"),
                "dataset_version": dataset_version,
                "dataset_canonical_sha256": dataset_checksum,
                "dataset_physical_sha256": manifest.get("dataset_file_sha256") or previous.get("dataset_physical_sha256") or (version_entry or {}).get("physical_file_sha256"),
                "clinical_decision": manifest.get("reviewer_decision") or (controlling_approval or {}).get("approval_decision"),
                "controlling_approval_id": manifest.get("controlling_approval_id") or previous.get("controlling_approval_id") or (controlling_approval or {}).get("approval_id"),
                "documented_limitations": manifest.get("outstanding_limitations", []),
                "active_at_creation": manifest.get("active_at_freeze_creation", True),
                "zip_file": str(zip_path.relative_to(self.root)) if zip_path.exists() else None,
                "zip_sha256": sha256_file(zip_path) if zip_path.exists() else None,
                "manifest_verification": verification,
            })
        registry = {"registry_version": self.REGISTRY_VERSION, "freezes": sorted(entries, key=lambda item: item.get("created_at_utc") or "")}
        atomic_json(self.freeze_registry_path, registry)
        return registry

    def technical_validation(self, record_audit: bool = False) -> tuple[ValidationResult, dict[str, Any]]:
        if not self.has_active_dataset():
            raise ReviewError("No active dataset is available for validation.")
        result, schema = validate(self.dataset(), load_json(self._active_schema_path()))
        version, checksum = self.dataset_identity()
        if record_audit:
            bundle = self._version_bundle(version)
            atomic_json(bundle / "effective_schema.json", schema)
            atomic_json(bundle / "validation_report.json", result.as_dict())
            with self._locked():
                registry = self._registry(self.version_registry_path, "versions")
                for entry in registry["versions"]:
                    if int(entry["version"]) == version:
                        entry["validation"] = result.as_dict()
                        entry["validation_timestamp_utc"] = utc_now()
                        entry["artifacts"]["effective_schema"] = str((bundle / "effective_schema.json").relative_to(self.root))
                        entry["artifacts"]["validation_report"] = str((bundle / "validation_report.json").relative_to(self.root))
                atomic_json(self.version_registry_path, registry)
            self.audit("technical_validation_run", {
                "dataset_version": version,
                "dataset_sha256": checksum,
                **result.as_dict(),
            })
        return result, schema

    def archive_and_validate_bundle(
        self,
        dataset_file: str | Path | None,
        schema_file: str | Path | None,
        issues_file: str | Path | None,
    ) -> dict[str, Any]:
        supplied = {
            "dataset": (dataset_file, "reference dataset"),
            "schema": (schema_file, "JSON Schema"),
            "issues": (issues_file, "QA issues"),
        }
        paths: dict[str, Path] = {}
        for key, (value, label) in supplied.items():
            if not value:
                raise ReviewError(f"Upload the {label} JSON file.")
            path = Path(value)
            if not path.is_file() or path.suffix.lower() != ".json":
                raise ReviewError(f"The {label} must be an available .json file.")
            if path.stat().st_size > MAX_UPLOAD_BYTES:
                raise ReviewError(f"The {label} exceeds the 25 MB upload limit.")
            paths[key] = path
        bundle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        bundle = self.uploads / bundle_id
        with self._locked():
            bundle.mkdir(parents=True, exist_ok=False)
            archived = {
                "dataset": bundle / "reference_dataset.json",
                "schema": bundle / "reference_schema_source.json",
                "issues": bundle / "qa_issues.json",
            }
            for key, target in archived.items():
                shutil.copy2(paths[key], target)
            initial = {
                "bundle_id": bundle_id,
                "uploaded_at_utc": utc_now(),
                "original_filenames": {key: path.name for key, path in paths.items()},
                "physical_sha256": {key: sha256_file(path) for key, path in archived.items()},
                "validation_status": "Not completed",
                "activation_status": "Not activated",
                "activated_version": None,
                "files": {key: str(path.relative_to(self.root)) for key, path in archived.items()},
            }
            atomic_json(bundle / "upload_manifest.json", initial)
            registry = self._registry(self.upload_registry_path, "uploads")
            registry["uploads"].append(initial)
            atomic_json(self.upload_registry_path, registry)
        self.audit("reference_bundle_uploaded", {
            "bundle_id": bundle_id,
            "uploaded_filenames": initial["original_filenames"],
            "physical_sha256": initial["physical_sha256"],
        })
        report: dict[str, Any]
        try:
            dataset, schema, issues, inspected = super().inspect_reference_bundle(
                archived["dataset"], archived["schema"], archived["issues"]
            )
            _, effective = validate(dataset, schema)
            report = {
                **inspected,
                "bundle_id": bundle_id,
                "validation_status": "Passed" if inspected["technical_validation_passed"] else "Failed",
                "validation_warnings": [],
                "ready_for_activation": bool(inspected["technical_validation_passed"]),
                "validated_at_utc": utc_now(),
                "physical_sha256": initial["physical_sha256"],
            }
            atomic_json(bundle / "effective_schema.json", effective)
        except Exception as exc:
            report = {
                "bundle_id": bundle_id,
                "validation_status": "Failed",
                "ready_for_activation": False,
                "technical_validation_passed": False,
                "technical_validation": {
                    "technical_validation": "Failed",
                    "structural_errors": [str(exc)],
                    "deterministic_errors": [],
                    "effective_schema_note": None,
                },
                "validation_warnings": [],
                "validated_at_utc": utc_now(),
                "physical_sha256": initial["physical_sha256"],
                "uploaded_filenames": initial["original_filenames"],
            }
        report_path = bundle / "validation_report.json"
        atomic_json(report_path, report)
        # Retain the original v5 filename for backward-compatible tooling.
        atomic_json(bundle / "upload_report.json", report)
        with self._locked():
            registry = self._registry(self.upload_registry_path, "uploads")
            for entry in registry["uploads"]:
                if entry["bundle_id"] == bundle_id:
                    entry.update({
                        "dataset_canonical_sha256": report.get("dataset_sha256"),
                        "schema_canonical_sha256": report.get("source_schema_sha256"),
                        "qa_issues_canonical_sha256": report.get("qa_issues_sha256"),
                        "validation_status": report["validation_status"],
                        "validation_report": str(report_path.relative_to(self.root)),
                    })
                    entry["files"]["validation_report"] = str(report_path.relative_to(self.root))
                    atomic_json(bundle / "upload_manifest.json", entry)
            atomic_json(self.upload_registry_path, registry)
        self.audit("reference_bundle_validation_completed", {
            "bundle_id": bundle_id,
            "validation_status": report["validation_status"],
            "ready_for_activation": report["ready_for_activation"],
        })
        return report

    def _next_version_locked(self) -> int:
        registered = [int(item["version"]) for item in self._registry(self.version_registry_path, "versions")["versions"]]
        files = []
        for path in self.versions.glob("clinical_records_reference_v*.json"):
            try:
                files.append(int(path.stem.rsplit("v", 1)[1]))
            except ValueError:
                continue
        return max(registered + files + [0]) + 1

    def _create_version_locked(
        self,
        dataset: dict[str, Any],
        schema: dict[str, Any],
        issues: list[dict[str, Any]],
        creation_method: str,
        source_upload_bundle: str | None = None,
        source_version: int | None = None,
        supersedes_version: int | None = None,
    ) -> dict[str, Any]:
        version = self._next_version_locked()
        validation, effective = validate(dataset, schema)
        created = utc_now()
        dataset_path = self.versions / f"clinical_records_reference_v{version:04d}.json"
        if dataset_path.exists():
            raise ReviewError(f"Dataset version target already exists: {dataset_path.name}")
        atomic_json(dataset_path, dataset)
        bundle = self._version_bundle(version)
        bundle.mkdir(parents=True, exist_ok=False)
        atomic_json(bundle / "dataset.json", dataset)
        atomic_json(bundle / "source_schema.json", schema)
        atomic_json(bundle / "effective_schema.json", effective)
        atomic_json(bundle / "qa_issues.json", issues)
        atomic_json(bundle / "validation_report.json", validation.as_dict())
        entry = {
            "version": version,
            "dataset_file": dataset_path.name,
            "canonical_sha256": sha256_value(dataset),
            "physical_file_sha256": sha256_file(dataset_path),
            "created_at_utc": created,
            "creation_method": creation_method,
            "source_upload_bundle": source_upload_bundle,
            "source_version": source_version,
            "supersedes_version": supersedes_version,
            "dataset_records": len(dataset),
            "validation": validation.as_dict(),
            "validation_timestamp_utc": created,
            "artifacts": {
                "dataset": str((bundle / "dataset.json").relative_to(self.root)),
                "source_schema": str((bundle / "source_schema.json").relative_to(self.root)),
                "effective_schema": str((bundle / "effective_schema.json").relative_to(self.root)),
                "qa_issues": str((bundle / "qa_issues.json").relative_to(self.root)),
                "validation_report": str((bundle / "validation_report.json").relative_to(self.root)),
                "version_manifest": str((bundle / "version_manifest.json").relative_to(self.root)),
            },
        }
        atomic_json(bundle / "version_manifest.json", entry)
        registry = self._registry(self.version_registry_path, "versions")
        registry["versions"].append(entry)
        registry["versions"].sort(key=lambda item: int(item["version"]))
        atomic_json(self.version_registry_path, registry)
        atomic_json(self.source_schema_path, schema)
        atomic_json(self.issues_path, issues)
        atomic_json(self.state_path, {
            "dataset_version": version,
            "dataset_file": dataset_path.name,
            "created_at_utc": created,
            "creation_method": creation_method,
            "supersedes_version": supersedes_version,
            "active_upload_bundle": source_upload_bundle,
            "source_version": source_version,
        })
        return entry

    def activate_uploaded_bundle(self, bundle_id: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ReviewError("Explicitly confirm activation of the validated uploaded bundle.")
        upload = self.upload_detail(bundle_id)
        if not upload.get("validation_report"):
            raise ReviewError("The uploaded bundle has not completed validation.")
        report = load_json(self.root / upload["validation_report"])
        if not report.get("ready_for_activation"):
            raise ReviewError("This bundle is not eligible for activation. Review its validation errors.")
        files = upload["files"]
        dataset = load_json(self.root / files["dataset"])
        schema = load_json(self.root / files["schema"])
        issues = self._validate_issue_register(load_json(self.root / files["issues"]))
        old_version = self.state().get("dataset_version") if self.has_active_dataset() else None
        old_checksum = self.dataset_identity()[1] if self.has_active_dataset() else None
        with self._locked():
            entry = self._create_version_locked(
                dataset, schema, issues,
                creation_method="uploaded_bundle_activation",
                source_upload_bundle=bundle_id,
                supersedes_version=int(old_version) if old_version else None,
            )
            registry = self._registry(self.upload_registry_path, "uploads")
            for item in registry["uploads"]:
                if item["bundle_id"] == bundle_id:
                    item["activation_status"] = "Activated"
                    item["activated_version"] = entry["version"]
                    item["activated_at_utc"] = utc_now()
                    atomic_json(self.uploads / bundle_id / "upload_manifest.json", item)
            atomic_json(self.upload_registry_path, registry)
        event = self.audit("reference_bundle_uploaded_and_activated", {
            "bundle_id": bundle_id,
            "old_dataset_version": old_version,
            "old_dataset_sha256": old_checksum,
            "new_dataset_version": entry["version"],
            "new_dataset_sha256": entry["canonical_sha256"],
            "technical_validation": entry["validation"],
            "review_effect": "Earlier decisions and approvals remain historical and do not authorize this version.",
        })
        return {
            **report,
            "bundle_id": bundle_id,
            "new_dataset_version": entry["version"],
            "audit_event_id": event["event_id"],
        }

    def import_reference_bundle(
        self,
        dataset_file: str | Path | None,
        schema_file: str | Path | None,
        issues_file: str | Path | None,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ReviewError("Explicitly confirm activation of the uploaded reference bundle.")
        report = self.archive_and_validate_bundle(dataset_file, schema_file, issues_file)
        return self.activate_uploaded_bundle(report["bundle_id"], True)

    def apply_requested_correction(self, issue_id: str, reviewer_values: dict[str, Any], confirmed: bool) -> dict[str, Any]:
        reviewer = normalize_reviewer(reviewer_values)
        if not confirmed:
            raise ReviewError("Explicitly confirm the correction before applying it.")
        request = self.latest_decisions().get(issue_id)
        if not request or request.get("decision") != "Request correction":
            raise ReviewError("The current dataset has no active human correction request for this issue.")
        dataset = self.dataset()
        operation = copy.deepcopy(request.get("correction_operation") or {
            "operation": "replace",
            "path": request["json_field_path"],
            "value": request.get("proposed_replacement"),
        })
        applied = self._apply_json_operation(dataset, operation)
        pointer = operation["path"]
        original = applied["original_value"]
        old_version, old_checksum = self.dataset_identity()
        schema = load_json(self._active_schema_path())
        issues = self.issues()
        with self._locked():
            entry = self._create_version_locked(
                dataset, schema, issues,
                creation_method="human_correction",
                supersedes_version=old_version,
                source_version=old_version,
            )
        event = self.audit("human_confirmed_correction_applied", {
            "correction_id": str(uuid.uuid4()),
            "request_decision_id": request["decision_id"],
            "issue_id": issue_id,
            "patient_id": request.get("patient_id"),
            "json_field_path": pointer,
            "original_value": original,
            "replacement_value": request["proposed_replacement"],
            "correction_operation": operation,
            "rationale": request["rationale"],
            "confirmed_by": reviewer,
            "old_dataset_version": old_version,
            "old_dataset_sha256": old_checksum,
            "new_dataset_version": entry["version"],
            "new_dataset_sha256": entry["canonical_sha256"],
            "technical_validation": entry["validation"],
            "review_effect": "Fresh review and approval are required for the new version.",
        })
        return event

    def current_correction_requests(self) -> list[dict[str, Any]]:
        """Return only current, exact-dataset correction requests."""
        records = []
        for issue_id, record in self.latest_decisions().items():
            if record.get("decision") != "Request correction":
                continue
            operation = copy.deepcopy(record.get("correction_operation") or {
                "operation": "replace",
                "path": record.get("json_field_path"),
                "value": record.get("proposed_replacement"),
            })
            records.append({
                "issue_id": issue_id,
                "patient_id": record.get("patient_id"),
                "decision_id": record.get("decision_id"),
                "rationale": record.get("rationale"),
                "operation": operation,
            })
        return sorted(records, key=lambda item: item["issue_id"])

    @staticmethod
    def _operation_conflicts(operations: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        seen: dict[str, str] = {}
        for item in operations:
            path = str(item["operation"].get("path", ""))
            issue_id = str(item["issue_id"])
            if path in seen:
                errors.append(f"{issue_id} duplicates the correction path used by {seen[path]}: {path}")
            seen[path] = issue_id
        paths = [(str(item["issue_id"]), str(item["operation"].get("path", "")).rstrip("/")) for item in operations]
        for index, (left_id, left) in enumerate(paths):
            for right_id, right in paths[index + 1:]:
                if left and right and (left.startswith(right + "/") or right.startswith(left + "/")):
                    errors.append(f"{left_id} and {right_id} use overlapping parent/child paths: {left} and {right}")
        return errors

    def preview_correction_batch(self, issue_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        selected = [str(value) for value in issue_ids or []]
        if not selected:
            raise ReviewError("Select at least one current correction request.")
        if len(selected) != len(set(selected)):
            raise ReviewError("A correction request may be selected only once.")
        available = {item["issue_id"]: item for item in self.current_correction_requests()}
        missing = [issue_id for issue_id in selected if issue_id not in available]
        if missing:
            raise ReviewError("These issues do not have a current correction request: " + ", ".join(missing) + ".")
        requests = [copy.deepcopy(available[issue_id]) for issue_id in selected]
        conflicts = self._operation_conflicts(requests)
        if conflicts:
            raise ReviewError("Correction conflicts must be resolved before preview: " + "; ".join(conflicts))
        version, checksum = self.dataset_identity()
        candidate = self.dataset()
        applied: list[dict[str, Any]] = []
        for request in requests:
            result = self._apply_json_operation(candidate, request["operation"])
            applied.append({**request, **result})
        validation, _ = validate(candidate, load_json(self._active_schema_path()))
        preview_id = str(uuid.uuid4())
        preview = {
            "preview_id": preview_id,
            "created_at_utc": utc_now(),
            "expected_dataset_version": version,
            "expected_dataset_sha256": checksum,
            "proposed_version": max([int(item["version"]) for item in self._registry(self.version_registry_path, "versions")["versions"]] + [version]) + 1,
            "proposed_canonical_sha256": sha256_value(candidate),
            "proposed_physical_sha256": physical_json_sha256(candidate),
            "operations": applied,
            "conflicts": [],
            "technical_validation": validation.as_dict(),
            "eligible_for_commit": validation.passed,
        }
        atomic_json(self.correction_previews / f"{preview_id}.json", preview)
        return preview

    def create_safety_snapshot(self) -> tuple[Path, dict[str, Any]]:
        if not self.has_active_dataset():
            raise ReviewError("No active dataset is available for a safety snapshot.")
        version, checksum = self.dataset_identity()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot_id = f"safety_snapshot_{stamp}_v{version:04d}_{checksum[:12]}"
        folder = self.safety_snapshots / snapshot_id
        zip_path = self.safety_snapshots / f"{snapshot_id}.zip"
        folder.mkdir(parents=True, exist_ok=False)
        shutil.copy2(self.dataset_path(), folder / "clinical_records_reference.json")
        shutil.copy2(self._active_schema_path(), folder / "clinical_records_schema_source.json")
        effective_path = self._active_artifact("effective_schema.json", self._active_schema_path())
        shutil.copy2(effective_path, folder / "clinical_records_schema_effective.json")
        shutil.copy2(self._active_artifact("qa_issues.json", self.issues_path), folder / "clinical_reference_qa_issues.json")
        validation, _ = self.technical_validation(record_audit=False)
        atomic_json(folder / "clinical_reference_technical_validation.json", validation.as_dict())
        self._write_version_review_exports(folder, version, checksum)
        freeze_manifests = []
        for freeze in self.freezes(version=version):
            freeze_zip = self.root / freeze.get("zip_file", "")
            if not freeze_zip.exists():
                continue
            try:
                with zipfile.ZipFile(freeze_zip) as archive:
                    manifest = json.loads(archive.read("clinical_reference_freeze_manifest.json"))
                name = f"freeze_manifest_{freeze['freeze_id']}.json"
                atomic_json(folder / name, manifest)
                freeze_manifests.append(name)
            except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                continue
        payload_checksums = {path.name: sha256_file(path) for path in sorted(folder.iterdir()) if path.is_file()}
        manifest = {
            "snapshot_id": snapshot_id,
            "created_at_utc": utc_now(),
            "dataset_version": version,
            "dataset_sha256": checksum,
            "freeze_manifests": freeze_manifests,
            "file_checksums": payload_checksums,
            "changes_active_state": False,
        }
        atomic_json(folder / "safety_snapshot_manifest.json", manifest)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.iterdir()):
                if path.is_file():
                    archive.write(path, path.name)
        return zip_path, manifest

    def apply_correction_batch(
        self,
        preview_id: str,
        safety_snapshot: str | Path,
        reviewer_values: dict[str, Any],
        confirmed: bool,
    ) -> dict[str, Any]:
        reviewer = normalize_reviewer(reviewer_values)
        if not confirmed:
            raise ReviewError("Explicitly confirm applying the complete correction batch.")
        preview_path = self.correction_previews / f"{preview_id}.json"
        if not preview_path.exists():
            raise ReviewError("The correction preview is unavailable; create a new preview.")
        snapshot_path = Path(safety_snapshot) if safety_snapshot else Path()
        if not snapshot_path.is_file():
            raise ReviewError("Create and select a valid safety snapshot before committing corrections.")
        preview = load_json(preview_path)
        old_version, old_checksum = self.dataset_identity()
        try:
            with zipfile.ZipFile(snapshot_path) as archive:
                snapshot_manifest = json.loads(archive.read("safety_snapshot_manifest.json"))
                for member_name, expected_hash in snapshot_manifest.get("file_checksums", {}).items():
                    if hashlib.sha256(archive.read(member_name)).hexdigest() != expected_hash:
                        raise ReviewError(f"The safety snapshot checksum failed for {member_name}.")
        except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise ReviewError("Create and select a valid safety snapshot before committing corrections.") from exc
        if preview.get("expected_dataset_version") != old_version or preview.get("expected_dataset_sha256") != old_checksum:
            raise ReviewError("The active dataset changed after preview; refresh the correction batch.")
        if (
            snapshot_manifest.get("dataset_version") != old_version
            or snapshot_manifest.get("dataset_sha256") != old_checksum
        ):
            raise ReviewError("The safety snapshot does not match the active dataset version and SHA-256.")
        if not preview.get("eligible_for_commit"):
            raise ReviewError("The correction batch did not pass technical validation.")
        candidate = self.dataset()
        applied: list[dict[str, Any]] = []
        for item in preview.get("operations", []):
            result = self._apply_json_operation(candidate, item["operation"])
            applied.append({**item, **result})
        validation, _ = validate(candidate, load_json(self._active_schema_path()))
        if not validation.passed:
            raise ReviewError("The correction batch failed validation and no new version was created.")
        with self._locked():
            current_version, current_checksum = self.dataset_identity()
            if (current_version, current_checksum) != (old_version, old_checksum):
                raise ReviewError("The active dataset changed during commit; no correction was applied.")
            entry = self._create_version_locked(
                candidate,
                load_json(self._active_schema_path()),
                self.issues(),
                creation_method="human_correction_batch",
                source_version=old_version,
                supersedes_version=old_version,
            )
        event = self.audit("human_confirmed_correction_batch_applied", {
            "batch_id": str(uuid.uuid4()),
            "preview_id": preview_id,
            "safety_snapshot": snapshot_manifest.get("snapshot_id", snapshot_path.name),
            "safety_snapshot_sha256": sha256_file(snapshot_path),
            "source_decision_ids": [item["decision_id"] for item in applied],
            "operations": applied,
            "confirmed_by": reviewer,
            "old_dataset_version": old_version,
            "old_dataset_sha256": old_checksum,
            "new_dataset_version": entry["version"],
            "new_dataset_sha256": entry["canonical_sha256"],
            "technical_validation": entry["validation"],
            "review_effect": "Fresh issue review and clinical approval are required; no approval was inherited.",
        })
        return {"event": event, "version": entry, "safety_snapshot": str(snapshot_path)}

    def export_correction_plan(self) -> Path:
        version, checksum = self.dataset_identity()
        current = self.latest_decisions()
        gate = self.gate_report()
        requests = []
        for issue_id, record in current.items():
            if record.get("decision") not in {"Request correction", "Request additional information"}:
                continue
            requests.append({
                "issue_id": issue_id,
                "patient_id": record.get("patient_id"),
                "decision": record.get("decision"),
                "decision_id": record.get("decision_id"),
                "rationale": record.get("rationale"),
                "operation": record.get("correction_operation"),
                "current_value": self.issue_view(issue_id).get("current_dataset_value"),
            })
        plan = {
            "dataset_version": version,
            "dataset_sha256": checksum,
            "created_at_utc": utc_now(),
            "requests": requests,
            "conflicts": self._operation_conflicts([{
                "issue_id": item["issue_id"], "operation": item["operation"]
            } for item in requests if item.get("operation")]),
            "technical_validation": gate["technical_validation"],
            "accepted_limitations": gate["limitations"],
            "audit_reference": self._audit_events()[-1]["event_id"] if self._audit_events() else None,
        }
        path = self.exports / f"correction_plan_v{version:04d}_{checksum[:12]}.json"
        atomic_json(path, plan)
        return path

    def reactivate_version(self, source_version: int, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ReviewError("Explicitly confirm creation of a new version from the historical version.")
        source = self.version_entry(int(source_version))
        required = ("dataset", "source_schema", "qa_issues")
        missing = [name for name in required if not source.get("artifacts", {}).get(name) or not (self.root / source["artifacts"][name]).exists()]
        if missing:
            raise ReviewError("Historical reactivation is blocked; unavailable artifacts: " + ", ".join(missing) + ".")
        dataset = load_json(self.root / source["artifacts"]["dataset"])
        schema = load_json(self.root / source["artifacts"]["source_schema"])
        issues = load_json(self.root / source["artifacts"]["qa_issues"])
        old_version = self.state().get("dataset_version") if self.has_active_dataset() else None
        with self._locked():
            entry = self._create_version_locked(
                dataset, schema, issues,
                creation_method="historical_version_reactivation",
                source_version=int(source_version),
                supersedes_version=int(old_version) if old_version else None,
            )
        event = self.audit("historical_version_reactivated", {
            "source_dataset_version": int(source_version),
            "source_dataset_sha256": source["canonical_sha256"],
            "old_dataset_version": old_version,
            "new_dataset_version": entry["version"],
            "new_dataset_sha256": entry["canonical_sha256"],
            "review_effect": "Historical decisions and approvals were not inherited.",
        })
        return {**entry, "new_dataset_version": entry["version"], "audit_event_id": event["event_id"]}

    def gate_report(self) -> dict[str, Any]:
        if not self.has_active_dataset():
            return {
                "dataset_version": None,
                "dataset_sha256": None,
                "technical_validation": {"technical_validation": "Not available"},
                "current_issue_decisions": 0,
                "total_issues": 0,
                "limitations": [],
                "blockers": ["No active dataset"],
                "eligible_for_clinical_approval": False,
            }
        return super().gate_report()

    def current_approval(self) -> dict[str, Any] | None:
        if not self.has_active_dataset():
            return None
        return super().current_approval()

    @staticmethod
    def approval_conflicts(decision: str, justification: str | None) -> list[str]:
        text = " ".join(str(justification or "").lower().split())
        approved = decision in {"Clinically Approved", "Clinically Approved with Limitations"}
        conflicts: list[str] = []
        approved_conflicts = {
            "approval is deferred": "approval is deferred",
            "approval is withheld": "approval is withheld",
            "final clinical approval cannot": "final clinical approval cannot be granted",
            "remains unresolved": "an issue remains unresolved",
            "not authorized for downstream": "downstream use is not authorized",
            "not approved for downstream": "downstream use is not approved",
        }
        pending_conflicts = {
            "clinically approved for downstream": "unrestricted downstream approval",
            "no blocking issue remains": "no blockers remain",
        }
        source = approved_conflicts if approved else pending_conflicts
        for phrase, label in source.items():
            if phrase in text:
                conflicts.append(label)
        return conflicts

    def generated_signoff_summary(self, decision: str) -> str:
        if decision not in APPROVAL_DECISIONS:
            raise ReviewError("Select a clinical approval decision before generating the summary.")
        gate = self.gate_report()
        version, checksum = self.dataset_identity()
        lines = [
            f"Dataset version {version}, SHA-256 {checksum}.",
            f"Technical validation: {gate['technical_validation'].get('technical_validation', 'Unknown')}.",
            f"Human issue decisions: {gate['current_issue_decisions']}/{gate['total_issues']}.",
            f"Clinical decision: {decision}.",
        ]
        if gate["blockers"]:
            lines.append("Blocking reasons: " + "; ".join(gate["blockers"]) + ".")
        else:
            lines.append("No blocking issue remains for this exact dataset version and checksum.")
        if gate["limitations"]:
            lines.append("Documented limitations:")
            for item in gate["limitations"]:
                lines.append(f"- {item['issue_id']}: {item.get('rationale') or 'No rationale documented'}")
        authorized = decision in {"Clinically Approved", "Clinically Approved with Limitations"} and not gate["blockers"]
        lines.append(
            "Downstream clinically approved narrative generation is authorized subject to documented limitations."
            if authorized else
            "Downstream use requiring clinical approval is not authorized."
        )
        return "\n".join(lines)

    def record_clinical_approval(
        self,
        decision: str,
        justification: str,
        reviewer_values: dict[str, Any],
        signoff_confirmed: bool,
    ) -> dict[str, Any]:
        conflicts = self.approval_conflicts(decision, justification)
        if conflicts:
            raise ReviewError(
                "The selected clinical decision conflicts with the justification: "
                + "; ".join(conflicts)
                + ". Revise the justification before recording the decision."
            )
        if os.environ.get("CLINICAL_REVIEW_REQUIRE_DECLARED_SCOPE", "").lower() in {"1", "true", "yes"}:
            required = {
                "clinical_specialty": "clinical specialty",
                "declared_qualifications": "declared qualifications",
                "scope_of_review": "scope of review",
                "registration_reference": "professional registration reference",
            }
            missing = [label for field, label in required.items() if not str(reviewer_values.get(field, "")).strip()]
            if missing or not reviewer_values.get("self_declaration_acknowledged"):
                detail = ", ".join(missing) if missing else "self-declaration acknowledgment"
                raise ReviewError("The configured governance policy requires: " + detail + ".")
        record = super().record_clinical_approval(decision, justification, reviewer_values, signoff_confirmed)
        return record

    def approval_history(self, version: int | None = None, checksum: str | None = None) -> list[dict[str, Any]]:
        if version is None or checksum is None:
            version, checksum = self.dataset_identity()
        if self.has_active_dataset():
            active_version, active_checksum = self.dataset_identity()
            is_active_dataset = int(version) == active_version and str(checksum) == active_checksum
        else:
            is_active_dataset = False
        records = self._records_for_version(self.approvals_path, int(version), str(checksum))
        raw_freezes = self._registry(self.freeze_registry_path, "freezes")["freezes"]
        results: list[dict[str, Any]] = []
        for index, source in enumerate(records):
            item = copy.deepcopy(source)
            superseded_by = records[index + 1]["approval_id"] if index + 1 < len(records) else None
            supersedes = records[index - 1]["approval_id"] if index else None
            conflicts = self.approval_conflicts(item.get("approval_decision", ""), item.get("justification"))
            is_controlling = is_active_dataset and index == len(records) - 1
            if is_controlling:
                classification = "Controlling approval"
            elif index == len(records) - 1:
                classification = "Historical — non-controlling"
            elif conflicts:
                classification = "Historical — superseded and internally inconsistent"
            else:
                classification = "Historical — superseded"
            item.update({
                "classification": classification,
                "is_controlling": is_controlling,
                "supersedes": supersedes,
                "superseded_by": superseded_by,
                "justification_conflicts": conflicts,
                "associated_freezes": [
                    freeze.get("freeze_id") for freeze in raw_freezes
                    if freeze.get("controlling_approval_id") == item.get("approval_id")
                ],
            })
            results.append(item)
        return results

    def _records_for_version(self, path: Path, version: int, checksum: str) -> list[dict[str, Any]]:
        return [
            record for record in read_jsonl(path)
            if int(record.get("dataset_version", -1)) == int(version)
            and record.get("dataset_sha256") == checksum
        ]

    def _write_version_review_exports(self, target: Path, version: int, checksum: str) -> list[Path]:
        decisions = self._records_for_version(self.decisions_path, version, checksum)
        approvals = self.approval_history(version, checksum)
        relevant_audit = [
            event for event in self._audit_events()
            if event.get("payload", {}).get("dataset_version") == version
            or event.get("payload", {}).get("new_dataset_version") == version
            or event.get("payload", {}).get("old_dataset_version") == version
        ]
        decision_file = target / "clinical_reference_human_review_decisions.json"
        approval_file = target / "clinical_reference_clinical_approval.json"
        audit_file = target / "clinical_reference_human_review_audit_log.json"
        signoff_file = target / "clinical_reference_clinical_signoff.txt"
        atomic_json(decision_file, decisions)
        atomic_json(approval_file, approvals)
        atomic_json(audit_file, relevant_audit)
        lines = ["CLINICAL REFERENCE HUMAN SIGN-OFF HISTORY", "", f"Dataset version: {version}", f"Dataset SHA-256: {checksum}", ""]
        if not approvals:
            lines.append("No human clinical approval decision has been recorded for this exact dataset version and checksum.")
        for item in approvals:
            lines.extend([
                f"Approval ID: {item['approval_id']}",
                f"Classification: {item['classification']}",
                f"Supersedes: {item.get('supersedes') or 'None'}",
                f"Superseded by: {item.get('superseded_by') or 'None'}",
                f"Decision: {item['approval_decision']}",
                f"Reviewer: {item['reviewer']['full_name']} ({item['reviewer']['professional_role']})",
                f"Timestamp UTC: {item['review_timestamp_utc']}",
                f"Justification: {item.get('justification') or 'None documented'}",
                "",
            ])
        signoff_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return [decision_file, approval_file, signoff_file, audit_file]

    def freeze(self, freeze_type: str) -> tuple[Path, dict[str, Any]]:
        if freeze_type not in {"provisional", "approved"}:
            raise ReviewError("Freeze type must be provisional or approved.")
        if not self.has_active_dataset():
            raise ReviewError("No active dataset is available to freeze.")
        gate = self.gate_report()
        approval = self.current_approval()
        approved_statuses = {"Clinically Approved", "Clinically Approved with Limitations"}
        if freeze_type == "approved":
            if gate["blockers"]:
                raise ReviewError("Approved freeze is blocked: " + "; ".join(gate["blockers"]))
            if not approval or approval.get("approval_decision") not in approved_statuses:
                raise ReviewError("Approved freeze requires current human clinical approval for this exact version and checksum.")
        validation, effective = self.technical_validation(record_audit=True)
        version, checksum = self.dataset_identity()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        freeze_id = f"freeze_{stamp}_v{version:04d}_{checksum[:12]}_{freeze_type}"
        folder = self.exports / freeze_id
        zip_path = self.exports / f"{freeze_id}.zip"
        with self._locked():
            folder.mkdir(parents=True, exist_ok=False)
        freeze_event = self.audit("dataset_freeze_created", {
            "freeze_id": freeze_id,
            "freeze_type": freeze_type,
            "dataset_version": version,
            "dataset_sha256": checksum,
            "zip_file": zip_path.name,
        })
        shutil.copy2(self.dataset_path(), folder / "clinical_records_reference.json")
        atomic_json(folder / "clinical_records_schema_effective.json", effective)
        shutil.copy2(self._active_schema_path(), folder / "clinical_records_schema_source.json")
        shutil.copy2(self._active_artifact("qa_issues.json", self.issues_path), folder / "clinical_reference_qa_issues.json")
        atomic_json(folder / "clinical_reference_technical_validation.json", validation.as_dict())
        self._write_version_review_exports(folder, version, checksum)
        payload_checksums = {
            path.name: sha256_file(path)
            for path in sorted(folder.iterdir()) if path.is_file()
        }
        clinical_state = "Pending"
        if approval:
            clinical_state = {
                "Clinically Approved": "Approved",
                "Clinically Approved with Limitations": "Approved with limitations",
                "Further Clinical Review Required": "Pending",
                "Clinically Rejected": "Rejected",
            }[approval["approval_decision"]]
        manifest = {
            "manifest_version": "2.0",
            "freeze_id": freeze_id,
            "created_at_utc": freeze_event["timestamp_utc"],
            "freeze_type": freeze_type,
            "dataset_version": version,
            "dataset_sha256": checksum,
            "dataset_canonical_sha256": checksum,
            "dataset_file_sha256": sha256_file(self.dataset_path()),
            "technical_validation": "Passed" if validation.passed else "Failed",
            "clinical_review": clinical_state,
            "dataset_freeze": "Approved frozen" if freeze_type == "approved" else "Provisionally frozen",
            "reviewer_decision": approval.get("approval_decision") if approval else None,
            "controlling_approval_id": approval.get("approval_id") if approval else None,
            "approval_dataset_sha256": approval.get("dataset_sha256") if approval else None,
            "outstanding_limitations": approval.get("outstanding_limitations", []) if approval else gate["limitations"],
            "provisional_reason": gate["blockers"] if freeze_type == "provisional" else [],
            "active_at_freeze_creation": True,
            "approved_for_downstream_narrative_generation": freeze_type == "approved" and clinical_state in {"Approved", "Approved with limitations"},
            "file_checksums": payload_checksums,
            "checksum_note": "The manifest lists every payload member. The manifest cannot self-hash; the registry stores the ZIP checksum.",
            "safeguard": "Clinical judgments and final clinical approval were not generated automatically by the application.",
        }
        manifest_path = folder / "clinical_reference_freeze_manifest.json"
        atomic_json(manifest_path, manifest)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.iterdir()):
                if path.is_file():
                    archive.write(path, path.name)
        verification = self.verify_freeze_package(zip_path)
        freeze_entry = {
            "freeze_id": freeze_id,
            "freeze_type": freeze_type,
            "created_at_utc": manifest["created_at_utc"],
            "dataset_version": version,
            "dataset_canonical_sha256": checksum,
            "dataset_physical_sha256": manifest["dataset_file_sha256"],
            "clinical_decision": approval.get("approval_decision") if approval else None,
            "controlling_approval_id": approval.get("approval_id") if approval else None,
            "documented_limitations": manifest["outstanding_limitations"],
            "active_at_creation": True,
            "zip_file": str(zip_path.relative_to(self.root)),
            "zip_sha256": sha256_file(zip_path),
            "manifest_verification": verification,
        }
        with self._locked():
            registry = self._registry(self.freeze_registry_path, "freezes")
            registry["freezes"].append(freeze_entry)
            registry["freezes"].sort(key=lambda item: item.get("created_at_utc") or "")
            atomic_json(self.freeze_registry_path, registry)
        return zip_path, manifest

    def verify_freeze_package(self, zip_path: Path) -> dict[str, Any]:
        if not zip_path.exists():
            return {"status": "Missing", "errors": ["Freeze ZIP is unavailable"]}
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
                manifests = [name for name in names if name.endswith("clinical_reference_freeze_manifest.json")]
                if not manifests:
                    return {"status": "Failed", "errors": ["Freeze manifest is missing"]}
                manifest = json.loads(archive.read(manifests[0]))
                expected = manifest.get("file_checksums")
                if not expected:
                    return {"status": "Legacy package — payload checksums unavailable", "errors": []}
                errors: list[str] = []
                warnings: list[str] = []
                for name, checksum in expected.items():
                    if name not in names:
                        errors.append(f"Missing package member: {name}")
                        continue
                    actual = hashlib.sha256(archive.read(name)).hexdigest()
                    if actual != checksum:
                        errors.append(f"Checksum mismatch: {name}")
                dataset_name = "clinical_records_reference.json"
                if dataset_name in names:
                    dataset_bytes = archive.read(dataset_name)
                    try:
                        dataset = json.loads(dataset_bytes)
                        if sha256_value(dataset) != manifest.get("dataset_canonical_sha256", manifest.get("dataset_sha256")):
                            errors.append("Dataset canonical SHA-256 does not match the manifest")
                    except json.JSONDecodeError:
                        errors.append("Frozen dataset is not valid JSON")
                    if hashlib.sha256(dataset_bytes).hexdigest() != manifest.get("dataset_file_sha256"):
                        errors.append("Dataset physical SHA-256 does not match the manifest")
                approval_name = "clinical_reference_clinical_approval.json"
                if manifest.get("freeze_type") == "approved":
                    if approval_name not in names:
                        errors.append("Approved freeze is missing the clinical approval history")
                    else:
                        approvals = json.loads(archive.read(approval_name))
                        matching = [
                            item for item in approvals
                            if item.get("approval_id") == manifest.get("controlling_approval_id")
                            and int(item.get("dataset_version", -1)) == int(manifest.get("dataset_version", -2))
                            and item.get("dataset_sha256") == manifest.get("dataset_sha256")
                        ]
                        if len(matching) != 1:
                            errors.append("Approved freeze does not identify exactly one matching controlling approval")
                        elif matching[0].get("approval_decision") not in {"Clinically Approved", "Clinically Approved with Limitations"}:
                            errors.append("The controlling approval is not an approved clinical decision")
                        exact = [
                            item for item in approvals
                            if int(item.get("dataset_version", -1)) == int(manifest.get("dataset_version", -2))
                            and item.get("dataset_sha256") == manifest.get("dataset_sha256")
                        ]
                        if exact and exact[-1].get("approval_id") != manifest.get("controlling_approval_id"):
                            warnings.append("The package contains a newer superseding approval than the manifest controller")
                        current = self._records_for_version(
                            self.approvals_path,
                            int(manifest.get("dataset_version", -1)),
                            str(manifest.get("dataset_sha256", "")),
                        )
                        if current and current[-1].get("approval_id") != manifest.get("controlling_approval_id"):
                            warnings.append("A newer approval now supersedes this freeze's controlling approval")
                    if manifest.get("technical_validation") != "Passed":
                        errors.append("Approved freeze technical validation is not Passed")
                status = "Failed" if errors else ("Passed with warnings" if warnings else "Passed")
                return {"status": status, "errors": errors, "warnings": warnings}
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            return {"status": "Failed", "errors": [str(exc)], "warnings": []}

    def freezes(self, version: int | None = None, freeze_type: str | None = None) -> list[dict[str, Any]]:
        records = copy.deepcopy(self._registry(self.freeze_registry_path, "freezes")["freezes"])
        if version is not None:
            records = [item for item in records if int(item.get("dataset_version", -1)) == int(version)]
        if freeze_type:
            records = [item for item in records if item.get("freeze_type") == freeze_type]
        for item in records:
            if item.get("freeze_type") == "provisional":
                item["classification"] = "Provisional freeze"
                continue
            approvals = self._records_for_version(
                self.approvals_path,
                int(item.get("dataset_version", -1)),
                str(item.get("dataset_canonical_sha256", "")),
            )
            controlling = approvals[-1].get("approval_id") if approvals else None
            if controlling and item.get("controlling_approval_id") == controlling:
                item["classification"] = "Current controlling approved freeze"
            elif item.get("controlling_approval_id"):
                item["classification"] = "Historical approved freeze — controlling approval superseded"
            else:
                item["classification"] = "Historical approved freeze"
        return sorted(records, key=lambda item: item.get("created_at_utc") or "")

    def latest_freeze(self, freeze_type: str) -> dict[str, Any] | None:
        records = self.freezes(freeze_type=freeze_type)
        return records[-1] if records else None

    def _version_issues(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        relative = entry.get("artifacts", {}).get("qa_issues")
        return load_json(self.root / relative) if relative and (self.root / relative).exists() else []

    @classmethod
    def _json_diff(cls, before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
        if type(before) is not type(after):
            return [{"path": path or "/", "source_value": before, "current_value": after}]
        if isinstance(before, dict):
            changes: list[dict[str, Any]] = []
            for key in sorted(set(before) | set(after)):
                token = str(key).replace("~", "~0").replace("/", "~1")
                child = f"{path}/{token}"
                if key not in before:
                    changes.append({"path": child, "source_value": None, "current_value": after[key]})
                elif key not in after:
                    changes.append({"path": child, "source_value": before[key], "current_value": None})
                else:
                    changes.extend(cls._json_diff(before[key], after[key], child))
            return changes
        if isinstance(before, list):
            changes = []
            for index in range(max(len(before), len(after))):
                child = f"{path}/{index}"
                if index >= len(before):
                    changes.append({"path": child, "source_value": None, "current_value": after[index]})
                elif index >= len(after):
                    changes.append({"path": child, "source_value": before[index], "current_value": None})
                else:
                    changes.extend(cls._json_diff(before[index], after[index], child))
            return changes
        return [] if before == after else [{"path": path or "/", "source_value": before, "current_value": after}]

    def version_detail(self, version: int) -> dict[str, Any]:
        entry = copy.deepcopy(self.version_entry(int(version)))
        checksum = entry["canonical_sha256"]
        decisions = self._records_for_version(self.decisions_path, int(version), checksum)
        latest_decisions = {record["issue_id"]: record for record in decisions}
        approvals = self.approval_history(int(version), checksum)
        approval = approvals[-1] if approvals else None
        version_freezes = self.freezes(version=int(version))
        active = self.has_active_dataset() and int(self.state()["dataset_version"]) == int(version)
        approval_label = approval.get("approval_decision") if approval else "Not approved"
        if approval and not active and approval.get("approval_decision") in {"Clinically Approved", "Clinically Approved with Limitations"}:
            approval_label = "Historically approved — not the active dataset"
        entry.update({
            "active": active,
            "review_progress": f"{len(latest_decisions)}/{len(self._version_issues(entry))}",
            "clinical_decision": approval_label,
            "approval_id": approval.get("approval_id") if approval else None,
            "approval_history": approvals,
            "limitations": approval.get("outstanding_limitations", []) if approval else [],
            "provisional_freezes": [item["freeze_id"] for item in version_freezes if item.get("freeze_type") == "provisional"],
            "approved_freezes": [item["freeze_id"] for item in version_freezes if item.get("freeze_type") == "approved"],
            "latest_freeze_date": version_freezes[-1]["created_at_utc"] if version_freezes else None,
            "downloadable_files": [
                str(self.root / relative) for relative in entry.get("artifacts", {}).values()
                if relative and (self.root / relative).exists()
            ] + [str(self.root / item["zip_file"]) for item in version_freezes if item.get("zip_file")],
        })
        source_version = entry.get("source_version") or entry.get("supersedes_version")
        if source_version and int(source_version) != int(version):
            try:
                source = self.version_entry(int(source_version))
                source_dataset = load_json(self.root / source["artifacts"]["dataset"])
                current_dataset = load_json(self.root / entry["artifacts"]["dataset"])
                source_decisions = self._records_for_version(
                    self.decisions_path, int(source_version), source["canonical_sha256"]
                )
                source_approvals = self.approval_history(int(source_version), source["canonical_sha256"])
                issue_paths = {item.get("json_field_path"): item.get("issue_id") for item in self._version_issues(entry)}
                entry["re_review_comparison"] = {
                    "label": "Historical decision — review again for the current version",
                    "source_version": int(source_version),
                    "changed_paths": [
                        {**change, "related_issue_id": issue_paths.get(change["path"])}
                        for change in self._json_diff(source_dataset, current_dataset)
                    ],
                    "historical_decisions": source_decisions,
                    "historical_approvals": source_approvals,
                    "current_decisions": decisions,
                }
            except (ReviewError, KeyError, OSError, json.JSONDecodeError):
                entry["re_review_comparison"] = {"status": "Historical comparison unavailable"}
        return entry

    def version_rows(self) -> list[list[Any]]:
        rows = []
        for entry in self._registry(self.version_registry_path, "versions")["versions"]:
            detail = self.version_detail(int(entry["version"]))
            rows.append([
                f"v{int(entry['version']):04d}",
                "Yes" if detail["active"] else "No",
                entry.get("created_at_utc"),
                entry.get("creation_method"),
                entry.get("validation", {}).get("technical_validation", "Unknown"),
                detail["review_progress"],
                detail["clinical_decision"],
                len(detail["provisional_freezes"]),
                len(detail["approved_freezes"]),
                detail["latest_freeze_date"],
            ])
        return sorted(rows, key=lambda row: row[0], reverse=True)

    def upload_detail(self, bundle_id: str) -> dict[str, Any]:
        for entry in self._registry(self.upload_registry_path, "uploads")["uploads"]:
            if entry["bundle_id"] == bundle_id:
                detail = copy.deepcopy(entry)
                detail["downloadable_files"] = [
                    str(self.root / relative) for relative in detail.get("files", {}).values()
                    if relative and (self.root / relative).exists()
                ]
                return detail
        raise ReviewError(f"Unknown uploaded bundle: {bundle_id}")

    def upload_rows(self) -> list[list[Any]]:
        rows = []
        for entry in self._registry(self.upload_registry_path, "uploads")["uploads"]:
            rows.append([
                entry["bundle_id"],
                entry.get("uploaded_at_utc"),
                entry.get("validation_status"),
                entry.get("activation_status"),
                entry.get("activated_version"),
            ])
        return sorted(rows, key=lambda row: row[1] or "", reverse=True)

    def lifecycle_label(self) -> str:
        if not self.has_active_dataset():
            return "No active dataset"
        gate = self.gate_report()
        validation_status = gate["technical_validation"].get("technical_validation")
        if validation_status not in {"Passed"}:
            return "Active — validation pending" if validation_status in {None, "Not available", "Unknown"} else "Active — validation failed"
        approval = self.current_approval()
        decisions = gate["current_issue_decisions"]
        freezes = self.freezes(version=gate["dataset_version"])
        has_provisional = any(item.get("freeze_type") == "provisional" for item in freezes)
        has_approved = any(item.get("freeze_type") == "approved" for item in freezes)
        if has_provisional and has_approved:
            return "Active — provisional and approved freezes available"
        if has_approved:
            return "Active — approved freeze available"
        if has_provisional:
            return "Active — provisional freeze available"
        if approval:
            decision = approval.get("approval_decision")
            if decision == "Clinically Approved":
                return "Active — approved, not yet frozen"
            if decision == "Clinically Approved with Limitations":
                return "Active — approved with limitations, not yet frozen"
            if decision == "Further Clinical Review Required":
                return "Active — further clinical review required"
            if decision == "Clinically Rejected":
                return "Active — clinically rejected"
        if decisions == 0:
            return "Active — clinical review not started"
        return "Active — clinical review in progress"

    def dashboard(self) -> dict[str, Any]:
        latest_approved = self.latest_freeze("approved")
        latest_provisional = self.latest_freeze("provisional")
        if not self.has_active_dataset():
            return {
                "lifecycle_label": "No active dataset",
                "active_dataset": None,
                "latest_approved_freeze": latest_approved or "No approved freeze has been created.",
                "latest_provisional_freeze": latest_provisional or "No provisional freeze has been created.",
                "historical_versions": self.version_rows(),
            }
        version, checksum = self.dataset_identity()
        detail = self.version_detail(version)
        gate = self.gate_report()
        approval = self.current_approval()
        active_freezes = self.freezes(version=version)
        active = {
            "version": version,
            "filename": self.dataset_path().name,
            "canonical_sha256": checksum,
            "physical_file_sha256": sha256_file(self.dataset_path()),
            "created_at_utc": self.state().get("created_at_utc"),
            "creation_source": detail.get("creation_method"),
            "source_upload_bundle": detail.get("source_upload_bundle"),
            "technical_validation": gate["technical_validation"],
            "validation_timestamp_utc": detail.get("validation_timestamp_utc"),
            "review_progress": detail["review_progress"],
            "clinical_approval": approval.get("approval_decision") if approval else "No current hash-bound approval",
            "controlling_approval_id": approval.get("approval_id") if approval else None,
            "documented_limitations": approval.get("outstanding_limitations", []) if approval else gate["limitations"],
            "provisional_freezes": [item["freeze_id"] for item in active_freezes if item.get("freeze_type") == "provisional"],
            "approved_freezes": [item["freeze_id"] for item in active_freezes if item.get("freeze_type") == "approved"],
            "changed_since_validation": detail.get("validation", {}).get("technical_validation") is None,
            "changed_since_approval": bool(approval and approval.get("dataset_sha256") != checksum),
            "blockers": gate["blockers"],
        }
        return {
            "lifecycle_label": self.lifecycle_label(),
            "active_dataset": active,
            "latest_approved_freeze": self._freeze_summary(latest_approved, version, "No approved freeze has been created."),
            "latest_provisional_freeze": self._freeze_summary(latest_provisional, version, "No provisional freeze has been created."),
            "historical_versions": self.version_rows(),
        }

    @staticmethod
    def _freeze_summary(record: dict[str, Any] | None, active_version: int, empty: str) -> dict[str, Any] | str:
        if not record:
            return empty
        return {
            **record,
            "is_active_version": int(record.get("dataset_version", -1)) == int(active_version),
        }

    def freeze_status(self) -> dict[str, Any]:
        if not self.has_active_dataset():
            return {
                "active_dataset": None,
                "freeze_eligibility": "Blocked",
                "blocking_reasons": ["No active dataset"],
                "existing_freezes": [],
            }
        gate = self.gate_report()
        approval = self.current_approval()
        version, checksum = self.dataset_identity()
        return {
            "active_version": version,
            "active_sha256": checksum,
            "validation_status": gate["technical_validation"].get("technical_validation"),
            "clinical_approval": approval.get("approval_decision") if approval else "No current hash-bound approval",
            "approved_freeze_eligible": not gate["blockers"] and bool(approval and approval.get("approval_decision") in {"Clinically Approved", "Clinically Approved with Limitations"}),
            "blocking_reasons": gate["blockers"],
            "existing_freezes": self.freezes(version=version),
        }

    def status(self) -> dict[str, Any]:
        if not self.has_active_dataset():
            return {
                "lifecycle_label": "No active dataset",
                "dataset_version": None,
                "dataset_sha256": None,
                "current_clinical_approval": "No active dataset",
                "blockers": ["No active dataset"],
            }
        gate = self.gate_report()
        approval = self.current_approval()
        return {
            **gate,
            "lifecycle_label": self.lifecycle_label(),
            "current_clinical_approval": approval.get("approval_decision") if approval else "No current hash-bound approval",
            "dataset_file": str(self.dataset_path()),
            "active_upload_bundle": self.state().get("active_upload_bundle") or "Bundled application inputs",
            "historical_approval_records": len(read_jsonl(self.approvals_path)) - (1 if approval else 0),
            "active_freezes": self.freezes(version=int(gate["dataset_version"])),
        }
