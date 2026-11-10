"""Read-only access to the approved reference dataset and the V9 reference narratives.

Two kinds of reference are kept apart on purpose:

* the structured record (approved dataset JSON) is the authority for clinical facts;
* the V9 narratives are reference *texts* used only for similarity metrics. They are
  candidates until Gate C human review is recorded, and every result says so.

Nothing in this module writes to disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Identity of the approved dataset (version 9, "Clinically Approved with Limitations").
EXPECTED_DATASET_CANONICAL_SHA256 = "08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e"
EXPECTED_DATASET_FILE_SHA256 = "710fdb5e4373f5d16ccbf64ddc6b3207b16ebefc0cdaf5ac9b1820e1a9c7806a"
EXPECTED_FREEZE_SHA256 = "11643597900d00351770f44f358264dab6c181ba3c67cc7811b7cbc26e4d8f0f"

NARRATIVE_FIELDS = {
    ("professional", "en"): "clinical_narrative_en",
    ("professional", "es"): "clinical_narrative_es",
    ("patient", "en"): "patient_explanation_en",
    ("patient", "es"): "patient_explanation_es",
}


def canonical_sha256(value: Any) -> str:
    """Same canonicalisation as review_core.sha256_value."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ReferenceStore:
    dataset: dict[str, Any]
    dataset_path: str
    dataset_file_sha256: str
    dataset_canonical_sha256: str
    narratives: dict[str, dict[str, str]] = field(default_factory=dict)
    narratives_path: str | None = None
    narratives_sha256: str | None = None
    narratives_status: str = "not loaded"
    warnings: list[str] = field(default_factory=list)
    dataset_version: int = 9
    approved_freeze_sha256: str | None = None
    approved_freeze_path: str | None = None
    # Optional hook (Gate C corrections): (case_id, kind, language, original_text) -> text to use. Only a reviewed
    # and approved correction is ever returned; the release file itself is never changed.
    text_override: Any = None

    # ---------------------------------------------------------------- lookups
    @property
    def case_ids(self) -> list[str]:
        return sorted(self.dataset)

    def record(self, case_id: str) -> dict[str, Any]:
        if case_id not in self.dataset:
            raise KeyError(f"Unknown case id {case_id!r}")
        return self.dataset[case_id]

    def narrative(self, case_id: str, kind: str, language: str, original: bool = False) -> str | None:
        """kind = 'professional' | 'patient'; language = 'en' | 'es'. With original=True, the release text as
        imported; otherwise an approved Gate C correction replaces it when one exists."""
        fld = NARRATIVE_FIELDS[(kind, language)]
        text = (self.narratives.get(case_id) or {}).get(fld)
        if not original and self.text_override is not None and text is not None:
            return self.text_override(case_id, kind, language, text)
        return text

    def provenance(self) -> dict[str, Any]:
        return {
            "dataset_path": self.dataset_path,
            "dataset_file_sha256": self.dataset_file_sha256,
            "dataset_canonical_sha256": self.dataset_canonical_sha256,
            "dataset_identity_verified": self.dataset_canonical_sha256 == EXPECTED_DATASET_CANONICAL_SHA256,
            "dataset_version": self.dataset_version,
            "approved_freeze_sha256": self.approved_freeze_sha256,
            "approved_freeze_path": self.approved_freeze_path,
            "reference_narratives_path": self.narratives_path,
            "reference_narratives_sha256": self.narratives_sha256,
            "reference_narratives_status": self.narratives_status,
            "warnings": list(self.warnings),
        }


def load_references(dataset_path: str | os.PathLike, narratives_path: str | os.PathLike | None = None,
                    *, require_expected_dataset: bool = True) -> ReferenceStore:
    dpath = Path(dataset_path)
    data = json.loads(dpath.read_text(encoding="utf-8"))
    canon = canonical_sha256(data)
    warnings: list[str] = []
    if canon != EXPECTED_DATASET_CANONICAL_SHA256:
        msg = (f"Dataset canonical SHA-256 {canon[:12]}… does not match the approved version 9 "
               f"({EXPECTED_DATASET_CANONICAL_SHA256[:12]}…).")
        if require_expected_dataset:
            raise ValueError(msg)
        warnings.append(msg)
    store = ReferenceStore(dataset=data, dataset_path=str(dpath), dataset_file_sha256=file_sha256(dpath),
                           dataset_canonical_sha256=canon, warnings=warnings)
    if narratives_path:
        npath = Path(narratives_path)
        doc = json.loads(npath.read_text(encoding="utf-8"))
        meta = doc.get("generation_metadata", {})
        if meta.get("source_canonical_sha256") not in (None, canon):
            warnings.append("Reference narratives were generated from a different dataset version.")
        store.narratives = doc.get("records", {})
        store.narratives_path = str(npath)
        store.narratives_sha256 = file_sha256(npath)
        status = meta.get("output_status", "unknown")
        # Gate C (authorised human review of generated text) is not recorded in V9.
        store.narratives_status = ("candidate, not clinically reviewed" if "candidate" in str(status)
                                   else str(status))
    return store


def load_approved_freeze(freeze_path: str | os.PathLike) -> ReferenceStore:
    """Load the authoritative dataset directly from the verified immutable freeze."""
    path = Path(freeze_path)
    freeze_hash = file_sha256(path)
    if freeze_hash != EXPECTED_FREEZE_SHA256:
        raise ValueError(
            f"Approved freeze SHA-256 {freeze_hash[:12]}… does not match the Track A baseline "
            f"({EXPECTED_FREEZE_SHA256[:12]}…)."
        )
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("The approved reference freeze failed ZIP integrity verification.")
        names = archive.namelist()
        candidates = [name for name in names if Path(name).name == "clinical_records_reference.json"]
        if len(candidates) != 1:
            raise ValueError("The approved freeze does not contain exactly one clinical_records_reference.json.")
        raw = archive.read(candidates[0])
    if hashlib.sha256(raw).hexdigest() != EXPECTED_DATASET_FILE_SHA256:
        raise ValueError("The reference JSON physical SHA-256 in the approved freeze is incorrect.")
    dataset = json.loads(raw.decode("utf-8"))
    canonical = canonical_sha256(dataset)
    if canonical != EXPECTED_DATASET_CANONICAL_SHA256:
        raise ValueError("The reference JSON canonical SHA-256 in the approved freeze is incorrect.")
    return ReferenceStore(
        dataset=dataset,
        dataset_path=f"{path}::{candidates[0]}",
        dataset_file_sha256=EXPECTED_DATASET_FILE_SHA256,
        dataset_canonical_sha256=canonical,
        dataset_version=9,
        approved_freeze_sha256=freeze_hash,
        approved_freeze_path=str(path),
    )


def default_store() -> ReferenceStore:
    """Load from environment variables CLINICAL_EVAL_DATASET and CLINICAL_EVAL_NARRATIVES."""
    ds = os.environ.get("CLINICAL_EVAL_DATASET")
    if not ds:
        raise RuntimeError("Set CLINICAL_EVAL_DATASET to the approved dataset JSON")
    return load_references(ds, os.environ.get("CLINICAL_EVAL_NARRATIVES"))
