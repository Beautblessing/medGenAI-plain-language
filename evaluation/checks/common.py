"""Shared result types and text helpers for the deterministic checks."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# classification values
SUPPORTED = "supported"
OMISSION = "omission"
CONTRADICTION = "contradiction"
UNSUPPORTED = "unsupported_addition"
REVIEW = "requires_human_review"
LEAK = "identifier_leak"

SEVERITY_ORDER = {"critical": 3, "major": 2, "minor": 1, "info": 0}


@dataclass
class Finding:
    check: str
    classification: str
    severity: str
    explanation: str
    json_pointer: str | None = None
    source_evidence: str | None = None
    output_evidence: str | None = None
    required: bool = True
    origin: str = "engine"

    @property
    def is_problem(self) -> bool:
        return self.classification != SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_problem"] = self.is_problem
        return d


def snippet(text: str, m: re.Match | None, pad: int = 60) -> str | None:
    if m is None:
        return None
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def ptr(obj: Any, path: str) -> Any:
    cur = obj
    for p in path.strip("/").split("/"):
        if p == "":
            continue
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    return cur


def strings_of(o: Any):
    if isinstance(o, dict):
        for v in o.values():
            yield from strings_of(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings_of(v)
    elif o is not None:
        yield str(o)


DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
NUM_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])")


def norm_num(s: str) -> str:
    s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return s
    return repr(int(v)) if v == int(v) else repr(v)


def nums(text: str) -> list[str]:
    t = DATE_RE.sub(" ", text)
    return [norm_num(n) for n in NUM_RE.findall(t)]


# Model outputs often spell small numbers out ("every eight hours", "diez días").
# Matching is done against the original text and against this normalised form, and a
# fact counts as present if either matches, so wording like "one-sided" is unaffected.
_EN_NUMS = {
    "seventy-two": 72, "seventy two": 72, "forty-eight": 48, "forty eight": 48, "twenty-four": 24,
    "twenty four": 24, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "thirty": 30,
}
_ES_NUMS = {
    "setenta y dos": 72, "cuarenta y ocho": 48, "veinticuatro": 24, "uno": 1, "una": 1, "dos": 2,
    "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "catorce": 14, "quince": 15, "veinte": 20, "treinta": 30,
}


def spell_out_numbers(text: str, language: str) -> str:
    table = _EN_NUMS if language == "en" else _ES_NUMS
    out = text
    for word in sorted(table, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(word)}\b", str(table[word]), out, flags=re.I)
    return out


def search_either(rx: str, text: str, language: str) -> re.Match | None:
    m = re.search(rx, text, flags=re.I)
    if m:
        return m
    return re.search(rx, spell_out_numbers(text, language), flags=re.I)


def num_set(text: str, language: str) -> set[str]:
    return set(nums(text)) | set(nums(spell_out_numbers(text, language)))
