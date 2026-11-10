"""Identifier removal and residual-PII checks.

Two layers:
1. Record-specific: every value in patient_information (name and its parts, synthetic id,
   phone, email, address, dates) is searched for verbatim in the output. This is exact,
   because the dataset tells us what the identifiers are.
2. Generic patterns: emails, phone-like numbers, synthetic-id formats, record ids and
   DD/MM/YYYY dates, to catch identifiers the model invented or copied from elsewhere.

Direct identifiers are 'critical'; quasi-identifiers (dates, age, sex) are 'minor' and are
reported for human judgement, because some studies keep them on purpose.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from narrative_eval import anonymize as AN

from .common import LEAK, SUPPORTED, Finding

DIRECT_FIELDS = ["full_name", "fictional_identification_number", "phone_number", "email", "address", "id"]
QUASI_FIELDS = ["date_of_birth", "encounter_date", "age"]

GENERIC_PATTERNS = {
    "email_address": (r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "critical"),
    "synthetic_identifier": (r"\bSYN-(ID|PHONE)-\d+\b", "critical"),
    "record_identifier": (r"\bHC\d{4}\b", "critical"),
    "phone_like_number": (r"(?<!\d)(\+?\d[\d \-]{7,}\d)(?!\d)", "major"),
    "date_ddmmyyyy": (r"\b\d{1,2}/\d{1,2}/\d{4}\b", "minor"),
}

# Name parts shorter than this, or common words, are not searched alone.
_MIN_NAME_PART = 4


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def check_pii(record: dict[str, Any], output: str, *, include_quasi: bool = True) -> list[Finding]:
    info = record["patient_information"]
    out: list[Finding] = []
    folded = _fold(output)
    already: set[str] = set()

    def look(value: str, field: str, severity: str, label: str):
        if not value:
            return
        v = _fold(str(value))
        i = folded.find(v)
        if i >= 0:
            already.add(output[i:i + len(v)])
            a, b = max(0, i - 40), min(len(output), i + len(v) + 40)
            out.append(Finding(f"pii::{field}", LEAK, severity, f"{label} still present", f"/patient_information/{field}",
                               str(value), output[a:b].replace("\n", " ")))
        else:
            out.append(Finding(f"pii::{field}", SUPPORTED, "info", f"{label} not present", f"/patient_information/{field}",
                               str(value)))

    for fld in DIRECT_FIELDS:
        look(info.get(fld), fld, "critical", fld.replace("_", " ").capitalize())
    # parts of the name (first name, each surname) on their own
    parts = [p for p in re.split(r"[\s\-]+", info.get("full_name", "")) if len(p) >= _MIN_NAME_PART]
    for k, p in enumerate(parts):
        if _fold(info.get("full_name", "")) in folded:
            break  # already reported as the full name
        if re.search(rf"\b{re.escape(_fold(p))}\b", folded):
            m = re.search(rf"\b{re.escape(_fold(p))}\b", folded)
            out.append(Finding(f"pii::name_part_{k}", LEAK, "critical", f"Part of the patient's name ('{p}') present",
                               "/patient_information/full_name", p,
                               output[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")))
    if include_quasi:
        for fld in QUASI_FIELDS:
            v = info.get(fld)
            if fld == "age":
                m = v is not None and re.search(rf"\b{v}[- ](year|años|año)", output, re.I)
                out.append(Finding("pii::age", LEAK if m else SUPPORTED, "minor" if m else "info",
                                   "Age present (quasi-identifier)" if m else "Age not present", "/patient_information/age",
                                   str(v), m.group(0) if m else None, required=False))
            else:
                look(v, fld, "minor", f"{fld.replace('_', ' ').capitalize()} (quasi-identifier)")
                out[-1].required = False

    # generic patterns: only report hits not already explained above
    for name, (rx, sev) in GENERIC_PATTERNS.items():
        if name == "date_ddmmyyyy" and not include_quasi:
            continue
        for m in re.finditer(rx, output):
            if any(m.group(0) in a or a in m.group(0) for a in already):
                continue
            out.append(Finding(f"pii_pattern::{name}", LEAK, sev, f"Identifier-like text: {name.replace('_', ' ')}",
                               None, None, output[max(0, m.start() - 40):m.end() + 40].replace("\n", " "),
                               required=sev != "minor"))
    return out


def summarize_pii(findings: list[Finding], record: dict[str, Any] | None = None, output: str | None = None) -> dict[str, Any]:
    leaks = [f for f in findings if f.classification == LEAK]
    direct = [f for f in leaks if f.severity in ("critical", "major")]
    checked = [f for f in findings if f.check.startswith("pii::") and f.required]
    removed = [f for f in checked if f.classification == SUPPORTED]
    return {
        "direct_identifiers_checked": len(checked),
        "direct_identifiers_removed": len(removed),
        "identifier_removal_rate": round(len(removed) / len(checked), 4) if checked else None,
        "direct_leaks": len(direct),
        "quasi_identifier_hits": len(leaks) - len(direct),
        "passed": not direct,
        # the Prompt 4.1 synthetic-identifier verification, reported alongside for comparability
        "narrative_eval_verify": AN.verify(output, record) if record is not None and output is not None else None,
    }
