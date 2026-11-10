"""Readability using narrative_eval.textcount (Prompt 4.1 package; same counting rules as the references).

English: Flesch Reading Ease and Flesch-Kincaid Grade Level.
Spanish: Fernandez-Huerta (the real formula, with Spanish syllabification).
Scales are language-specific and are not comparable across languages. They estimate text
complexity, not patient comprehension.
"""
from __future__ import annotations

from typing import Any

from narrative_eval import textcount as tm


def readability(text: str, language: str) -> dict[str, Any]:
    ws, ss = tm.words(text), tm.sentences(text)
    res: dict[str, Any] = {
        "language": language,
        "words": len(ws),
        "sentences": len(ss),
        "words_per_sentence": round(len(ws) / len(ss), 2) if ss else None,
    }
    if language == "en":
        try:
            fre, fkgl = tm.flesch(text)
        except LookupError as e:  # textstat 0.7.13 needs the NLTK cmudict corpus (setup_eval_models.py)
            fre = fkgl = None
            res["unavailable_reason"] = f"English syllable dictionary missing: run setup_eval_models.py ({type(e).__name__})"
        res["flesch_reading_ease"] = None if fre is None else round(fre, 2)
        res["flesch_kincaid_grade"] = None if fkgl is None else round(fkgl, 2)
        res["primary_score_name"] = "flesch_reading_ease"
    else:
        fh = tm.fernandez_huerta(text)
        res["fernandez_huerta"] = None if fh is None else round(fh, 2)
        res["primary_score_name"] = "fernandez_huerta"
    res["primary_score"] = res.get(res["primary_score_name"])
    return res


def readability_delta(source: str | None, output: str, language: str) -> dict[str, Any]:
    out = {"output": readability(output, language)}
    if source:
        src = readability(source, language)
        out["source"] = src
        a, b = src["primary_score"], out["output"]["primary_score"]
        # For both Flesch and Fernandez-Huerta, higher means easier to read.
        out["easier_than_source_by"] = None if a is None or b is None else round(b - a, 2)
        out["length_ratio"] = round(out["output"]["words"] / src["words"], 3) if src["words"] else None
    return out
