"""Narrative file parsing and section segmentation."""
import re
from .config import FIELDS

CLIN_HEADINGS = {
    "en": ["REASON FOR CONSULTATION", "CLINICAL HISTORY", "EXAMINATION AND INVESTIGATIONS", "ASSESSMENT AND DIAGNOSIS",
           "TREATMENT AND CLINICAL COURSE", "FOLLOW-UP AND WARNING SIGNS"],
    "es": ["MOTIVO DE CONSULTA", "HISTORIA CLÍNICA", "EXPLORACIÓN Y PRUEBAS COMPLEMENTARIAS", "VALORACIÓN CLÍNICA Y DIAGNÓSTICO",
           "TRATAMIENTO Y EVOLUCIÓN", "SEGUIMIENTO Y SIGNOS DE ALARMA"],
}
CLIN_KEYS = ["reason", "history", "examination", "assessment", "treatment", "followup_warnings"]
PAT_HEADINGS = {
    "en": {"Why you came": "why", "What we found": "found", "What this means": "means", "Your treatment": "treatment",
           "About your headache medicine": "treatment_extra", "Warning signs": "warnings", "Follow-up": "followup"},
    "es": {"Por qué vino": "why", "Qué encontramos": "found", "Qué significa": "means", "Su tratamiento": "treatment",
           "Sobre su medicamento para el dolor de cabeza": "treatment_extra", "Signos de alarma": "warnings", "Seguimiento": "followup"},
}


def parse_narrative_file(path):
    out, cur = {}, None
    for line in open(path, encoding="utf-8").read().split("\n"):
        if line.startswith("@@"):
            cur = line[2:].strip(); out[cur] = []
        elif cur:
            out[cur].append(line)
    out = {k: "\n".join(v).strip("\n") for k, v in out.items()}
    assert list(out) == FIELDS, list(out)
    return out


def sections(text, field):
    """Return ordered {section_key: body}. Clinical: header line -> 'header'."""
    lang = field[-2:]
    lines = text.split("\n")
    res, cur = {}, None
    if field.startswith("clinical"):
        heads = dict(zip(CLIN_HEADINGS[lang], CLIN_KEYS))
        cur = "header"
    else:
        heads = PAT_HEADINGS[lang]
    for ln in lines:
        s = ln.strip()
        if s in heads:
            cur = heads[s]; res.setdefault(cur, [])
            continue
        if cur is None or not s:
            continue
        res.setdefault(cur, []).append(s)
    return {k: " ".join(v) for k, v in res.items()}


def split_sentences(text):
    """Sentence units: split on [.!?;] + whitespace? No: on [.!?] followed by whitespace and at line breaks
    (same rule as the readability splitter); headings are excluded by the caller."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            if re.search(r"\w", part):
                out.append(part)
    return out


def content_sentences(text, field):
    """Sentences excluding headings, with the section key of each."""
    res = []
    for key, body in sections(text, field).items():
        for s in split_sentences(body):
            res.append((key, s))
    return res
