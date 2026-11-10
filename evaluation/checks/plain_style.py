"""Plain-language screening checks for patient explanations (lexical, deterministic).

These are review prompts, not verdicts: they point a reviewer at medical terms left unexplained, warning
signs repeated in the follow-up section, and sentences that blame the patient for missing data. The
structured fact checks remain the authority on clinical content.
"""
from __future__ import annotations

import re
from typing import Any

from ..transforms import layout_sections

# Medical terms and abbreviations a patient explanation should explain or avoid (stems, case-insensitive
# unless written in capitals). Extend per case set; matches are reported with the term found.
JARGON = {
    "en": ["erythemat", "exudat", "lymphadenopath", "palatine", "tonsillopharyngitis", "pharyngitis", "peritonsillar",
           "cervical", "bilateral", "unilateral", "dyspn", "tachycard", "bradycard", "febrile", "oedema", "edema",
           "auscultat", "hypertensi", "hypotensi", "analgesi", "antipyretic", "prophyla", "etiolog", "aetiolog",
           "Centor", "SpO2", "BMI", "PRN", "NSAID", "HbA1c", "eGFR", "ECG"],
    "es": ["eritemat", "exudad", "adenopat", "palatin", "faringoamigdalitis", "faringitis", "periamigdal",
           "cervical", "bilateral", "unilateral", "disnea", "taquicard", "bradicard", "febril", "edema",
           "auscultac", "hipertensi", "hipotensi", "analgési", "antitérmic", "profilax", "etiolog",
           "Centor", "SatO2", "SpO2", "IMC", "AINE", "HbA1c", "FGe", "ECG"],
}
_AFTER = re.compile(r"\w*\s*(?:\(|,?\s*(?:which means|meaning|that is|que significa|es decir)\b)", re.I)
_BEFORE = re.compile(r"(?:called|known as|named|llamad[oa]s?|conocid[oa]s? como|denominad[oa]s?)\s+(?:[\w-]+\s+){0,2}$",
                     re.I)
_APPOSITION = re.compile(r"\w*\s*(?:,|—|–|-)\s*(?:a|an|the|un|una|unos|unas|el|la|los|las)\s", re.I)
_URGENT = re.compile(r"emergenc|urgent|immediately|same[- ]day|call 112|urgencia|inmediat|llame|mismo día", re.I)
_BLAME = re.compile(r"\byou did(?:n't| not) (?:provide|give|tell|report)\b|\busted no (?:nos )?(?:dio|proporcion|indic)", re.I)
_FOLLOW = re.compile(r"^(follow[- ]?up|seguimiento)$", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+")


def _term_re(term: str) -> re.Pattern[str]:
    if term.isupper() or any(c.isdigit() for c in term) or term[0].isupper():
        return re.compile(rf"(?<![\w]){re.escape(term)}(?![a-z])")
    return re.compile(rf"\b{re.escape(term)}\w*", re.I)


def _sections(text: str) -> list[tuple[str, str]]:
    laid, _ = layout_sections(text)
    out: list[tuple[str, list[str]]] = [("", [])]
    for block in laid.split("\n\n"):
        lines = block.strip().splitlines()
        if not lines:
            continue
        if len(lines[0].split()) <= 6 and not re.search(r"[.!?:]$", lines[0].strip()):
            out.append((lines[0].strip(), lines[1:]))
        else:
            out[-1][1].extend(lines)
    return [(h, " ".join(b)) for h, b in out if b]


def _explained(text: str, start: int, end: int) -> bool:
    """A term counts as explained when an explanation sits right next to it: 'X (…)', 'X, which means …',
    'X, a …' or 'called X'."""
    return bool(_AFTER.match(text, end) or _APPOSITION.match(text, end) or _BEFORE.search(text[max(0, start - 40):start]))


def check_plain_style(output_text: str, language: str) -> dict[str, Any]:
    text = output_text or ""
    unexplained: list[dict[str, str]] = []
    for term in JARGON.get(language, []):
        for m in _term_re(term).finditer(text):
            if not _explained(text, m.start(), m.end()):
                unexplained.append({"term": m.group(0), "evidence": text[max(0, m.start() - 40): m.end() + 40].strip()})
                break
    repeated = [s.strip() for h, body in _sections(text) if _FOLLOW.match(h)
                for s in _SENT.split(body) if _URGENT.search(s)]
    blame = [s.strip() for s in _SENT.split(text.replace("\n", " ")) if _BLAME.search(s)]
    return {
        "status": "ok",
        "unexplained_terms": unexplained,
        "warning_signs_repeated_in_follow_up": repeated,
        "patient_blaming_sentences": blame,
        "method": "lexical screening (term list, urgency words in the Follow-up section, fixed phrases)",
        "note": "Screening only: a reviewer decides whether each item needs correcting.",
    }
