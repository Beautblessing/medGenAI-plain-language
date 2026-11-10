"""Layer A - readability. Formulas estimate textual complexity only; they do not
measure patient comprehension. Headings are excluded before counting so that
heading lines do not count as sentences."""
import importlib.metadata as md
from .textcount import words, sentences, es_syllables, en_syllables, fernandez_huerta, flesch, self_test
from .texts import sections

METHOD = {
    "tokenizer": "regex [A-Za-zÀ-ÖØ-öø-ÿ0-9]+ with internal ’'-/., joiners (decimals and 'budesonide/formoterol' count as one word)",
    "sentence_splitter": "split at [.!?] followed by whitespace and at every line break; headings removed before counting",
    "en_syllables": "textstat.syllable_count (pyphen en_US hyphenation dictionary) per word; numeric tokens = 1",
    "es_syllables": "rule-based orthographic nucleus counter (hiatus/diphthong rules, silent u in que/qui/gue/gui); numeric tokens = 1; 30-word self-test",
    "FRE": "206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)  (Flesch 1948)",
    "FKGL": "0.39*(words/sentences) + 11.8*(syllables/words) - 15.59  (Kincaid et al. 1975)",
    "FH": "206.84 - 0.60*P - 1.02*F, P = syllables per 100 words, F = words per sentence (Fernández-Huerta 1959 as implemented in textstat and legible.es)",
    "FH_literal_notebook_variant": "206.84 - 60*(syllables/words) - 102*(sentences/words) (form used in the supplied Metrics.ipynb); reported for comparability only",
    "limitations": [
        "Readability formulas measure surface features (word and sentence length), not comprehension or medical accuracy.",
        "Numbers, units, drug names and abbreviations distort syllable counts.",
        "FH and FRE are on different scales; do not compare English and Spanish scores directly.",
        "The FH variants differ (0.60*P vs 60*syll/word are equal; 1.02*F vs 102*sent/word are not) so the literal notebook variant gives different values.",
    ],
}


def versions():
    out = {}
    for p in ["textstat", "pyphen"]:
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            out[p] = None
    return out


def body_text(text, field):
    return "\n".join(v for k, v in sections(text, field).items())


def fh_literal(text):
    ws, ss = words(text), sentences(text)
    if not ws or not ss:
        return None
    return 206.84 - 60.0 * es_syllables(text) / len(ws) - 102.0 * len(ss) / len(ws)


def readability(text, field):
    """Return a dict of readability metrics for one narrative (headings excluded)."""
    lang = field[-2:]
    t = body_text(text, field)
    ws, ss = words(t), sentences(t)
    r = {"language": lang, "word_count": len(ws), "sentence_count": len(ss),
         "avg_sentence_length": round(len(ws) / len(ss), 4) if ss else None}
    if lang == "en":
        fre, fkgl = flesch(t)
        r.update({"syllable_count": en_syllables(t), "flesch_reading_ease": round(fre, 4), "flesch_kincaid_grade": round(fkgl, 4),
                  "fernandez_huerta": None, "fernandez_huerta_literal_variant": None,
                  "null_reason": "Fernández-Huerta is a Spanish formula; not calculated for English"})
    else:
        r.update({"syllable_count": es_syllables(t), "flesch_reading_ease": None, "flesch_kincaid_grade": None,
                  "fernandez_huerta": round(fernandez_huerta(t), 4), "fernandez_huerta_literal_variant": round(fh_literal(t), 4),
                  "null_reason": "Flesch/FKGL are English formulas; not calculated for Spanish"})
    return r


def syllable_self_test():
    return self_test()
