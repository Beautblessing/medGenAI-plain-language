"""Text counting utilities (carried over unchanged from the Prompt 4.0 package, textmetrics.py).

Word, sentence and syllable counting rules are documented here so that results
are reproducible. Readability formulas estimate textual complexity only; they do
not measure patient comprehension.
"""
import re
import unicodedata

WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+(?:[’'\-/.,][A-Za-zÀ-ÖØ-öø-ÿ0-9]+)*")


def words(text):
    """Word tokens: runs of letters/digits, keeping internal hyphens, slashes,
    apostrophes and decimal separators (e.g. 'budesonide/formoterol', '38.6')."""
    return WORD_RE.findall(unicodedata.normalize("NFC", text))


def sentences(text):
    """Sentences: split at '.', '!' or '?' followed by whitespace, and at
    every line break (headings and bullet lines count as their own unit).
    Decimal points are not followed by whitespace, so '38.6' is not split."""
    units = []
    for line in text.split("\n"):
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            if words(part):
                units.append(part)
    return units


# ---------------- Spanish syllables (rule-based, orthographic) ----------------
_STRONG = set("aeoáéíóú")  # accented i/u behave as strong (hiatus)
_VOWELS = set("aeiouáéíóúü")


def es_syllables_word(w):
    """Count Spanish orthographic syllables by vowel nuclei.

    Rules: a nucleus is a maximal vowel group, split where two strong vowels
    (a, e, o, or accented í/ú/á/é/ó) are adjacent (hiatus). Unaccented i/u next
    to another vowel form a diphthong/triphthong with it. 'u' in 'que/qui/gue/gui'
    is silent. Final 'y' after a vowel is part of the nucleus (e.g. 'muy', 'hay').
    Words with no letters (numbers) count as 1 syllable per digit group (a
    documented approximation)."""
    w = w.lower()
    if not re.search(r"[a-záéíóúüñ]", w):
        return 1
    w = re.sub(r"(?<=[qg])u(?=[eiéí])", "", w)
    count = 0
    prev = None  # previous char class: None, 'S' strong, 'W' weak
    for ch in w:
        if ch in _VOWELS:
            cls = "S" if ch in _STRONG else "W"
            if prev is None:
                count += 1
            elif prev == "S" and cls == "S":
                count += 1  # hiatus
            prev = cls
        else:
            prev = None
    return max(count, 1)


def es_syllables(text):
    return sum(es_syllables_word(w) for w in words(text))


def fernandez_huerta(text):
    """Fernández-Huerta (1959) as commonly implemented:
    L = 206.84 - 0.60*P - 1.02*F, with P = syllables per 100 words and
    F = mean words per sentence (the variant used by textstat and legible.es).
    """
    ws = words(text)
    ss = sentences(text)
    if not ws or not ss:
        return None
    p = 100.0 * es_syllables(text) / len(ws)
    f = len(ws) / len(ss)
    return 206.84 - 0.60 * p - 1.02 * f


# ---------------- English (Flesch / Flesch-Kincaid) ----------------
def en_syllables(text):
    import textstat  # pyphen-based syllable counter
    textstat.set_lang("en_US")
    total = 0
    for w in words(text):
        if re.search(r"[A-Za-z]", w):
            total += max(1, textstat.syllable_count(w))
        else:
            total += 1
    return total


def flesch(text):
    ws, ss = words(text), sentences(text)
    if not ws or not ss:
        return None, None
    wps = len(ws) / len(ss)
    spw = en_syllables(text) / len(ws)
    fre = 206.835 - 1.015 * wps - 84.6 * spw
    fkgl = 0.39 * wps + 11.8 * spw - 15.59
    return fre, fkgl


ES_SYLLABLE_TESTS = {
    # word: expected syllables (standard Spanish syllabification)
    "casa": 2, "aéreo": 4, "país": 2, "ciudad": 2, "huevo": 2, "queso": 2,
    "guitarra": 3, "paraguas": 3, "teatro": 3, "oído": 3, "muy": 1, "hay": 1,
    "corazón": 3, "medicamento": 5, "respiración": 4, "amígdalas": 4,
    "infección": 3, "diabetes": 3, "ecografía": 5, "hemoglobina": 5,
    "fiebre": 2, "náuseas": 3, "aire": 2, "estreptococo": 5, "día": 2,
    "cuello": 2, "agua": 2, "leer": 2, "creer": 2, "lingüística": 4,
}


def self_test():
    failures = {w: (es_syllables_word(w), n) for w, n in ES_SYLLABLE_TESTS.items()
                if es_syllables_word(w) != n}
    assert sentences("Fever 38.6 °C. Next line\n- bullet: text") == ["Fever 38.6 °C.", "Next line", "bullet: text"], sentences("Fever 38.6 °C. Next line\n- bullet: text")
    return {"spanish_syllable_tests": len(ES_SYLLABLE_TESTS), "failures": failures}
