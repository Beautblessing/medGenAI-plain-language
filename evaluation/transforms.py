"""Standalone, deterministic transformation baselines with provenance.

These are research baselines, not clinical decisions or validated de-identification.
They make no network calls and never write to the approved reference runtime.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from narrative_eval.anonymize import anonymize as replace_identifiers
from narrative_eval.anonymize import targets as identifier_targets
from narrative_eval.anonymize import verify as verify_identifiers

from .checks.common import num_set


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])", text.strip()) if part.strip()]


def _record(task: str, dataset_id: str, case_id: str, language: str, source: str,
            output: str, method: str, parameters: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    return {
        "transformation_id": str(uuid.uuid4()),
        "evaluation_dataset_id": dataset_id,
        "patient_id": case_id,
        "language": language,
        "task": task,
        "source_text": source,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "generated_output": output,
        "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        "method": method,
        "parameters": parameters,
        "execution_timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "execution_status": "Completed",
        "error_details": None,
        "details": details,
        "governance_note": "Research transformation only; not clinical approval or validated real-patient de-identification.",
    }


def anonymize(dataset_id: str, case_id: str, language: str, source: str,
              reference_record: dict[str, Any]) -> dict[str, Any]:
    output, replacements = replace_identifiers(source, reference_record)
    # The record/case id (patient_information.id, e.g. HC0001) is a direct identifier for the identifier checks,
    # but the vendored Prompt 4.1 replacer does not target it; replace it here with its own placeholder.
    case_ref = str((reference_record.get("patient_information") or {}).get("id") or "")
    if case_ref:
        n = len(re.findall(rf"\b{re.escape(case_ref)}\b", output))
        if n:
            output = re.sub(rf"\b{re.escape(case_ref)}\b", "[CASE_ID]", output)
            replacements = [*replacements, {"identifier_type": "id", "replacements": n, "token": "[CASE_ID]"}]
    verification = verify_identifiers(output, reference_record)
    values = dict(identifier_targets(reference_record)) if reference_record.get("patient_information") else {}
    if case_ref:
        values["id"] = case_ref
    table = [{"identifier_category": r["identifier_type"], "original_synthetic_value": values.get(r["identifier_type"]),
              "replacement": r["token"], "occurrences": r["replacements"]} for r in replacements]
    # numbers that belong to identifiers (phone, ID, date of birth) may legitimately disappear; others must not
    id_numbers = set()
    for _, v in identifier_targets(reference_record) if reference_record.get("patient_information") else []:
        id_numbers |= num_set(str(v), language)
    lost = sorted((num_set(source, language) - num_set(output, language)) - id_numbers)
    return _record("anonymization", dataset_id, case_id, language, source, output,
                   "deterministic_known_synthetic_identifier_replacement_v1", {},
                   {"replacements": replacements, "replacement_table": table, "verification": verification,
                    "clinical_number_preservation": {"lost_non_identifier_numbers": lost, "passed": not lost},
                    "scope_note": "Removal of known synthetic identifiers only; not validated de-identification "
                                  "of real patient data. Clinical dates are kept (policy: identifiers only)."})


def summarize(dataset_id: str, case_id: str, language: str, source: str,
              max_sentences: int = 6) -> dict[str, Any]:
    sentences = _sentences(source)
    clinical = re.compile(
        r"diagnos|assessment|medicat|prescri|treat|warning|emergen|follow|review|"
        r"diagn[oó]st|tratamiento|medic|alarma|urgenc|seguimiento|revisi[oó]n",
        re.I,
    )
    selected: list[str] = []
    if sentences:
        selected.append(sentences[0])
    for sentence in sentences[1:]:
        if clinical.search(sentence) and sentence not in selected:
            selected.append(sentence)
        if len(selected) >= max_sentences:
            break
    for sentence in sentences:
        if len(selected) >= max_sentences:
            break
        if sentence not in selected:
            selected.append(sentence)
    output = " ".join(selected)
    return _record("summarization", dataset_id, case_id, language, source, output,
                   "deterministic_clinically_weighted_extractive_baseline_v1",
                   {"max_sentences": int(max_sentences)},
                   {"source_sentences": len(sentences), "selected_sentences": len(selected),
                    "limitation": "Extractive research baseline; does not paraphrase or guarantee clinical completeness."})


PLAIN = {
    "en": {
        "administered": "given", "commenced": "started", "approximately": "about",
        "subsequently": "later", "haemodynamically": "with stable blood pressure and circulation",
        "analgesia": "pain relief", "orally": "by mouth",
        "odynophagia": "pain when swallowing", "erythematous": "red", "bilateral": "on both sides",
        "bilaterally": "on both sides", "lymphadenopathy": "swollen glands", "dyspnoea": "shortness of breath",
        "dyspnea": "shortness of breath", "hypertension": "high blood pressure", "polyuria": "passing a lot of urine",
        "polydipsia": "being very thirsty", "nocturia": "passing urine at night", "pyrexia": "fever",
        "febrile": "with fever", "afebrile": "without fever", "tachycardia": "a fast heartbeat",
        "dyslipidaemia": "high blood fats", "dyslipidemia": "high blood fats", "as needed": "only when needed",
    },
    "es": {
        "administrado": "dado", "posteriormente": "después", "aproximadamente": "cerca de",
        "analgesia": "tratamiento para el dolor", "por vía oral": "por boca", "odinofagia": "dolor al tragar",
        "eritematosas": "enrojecidas", "eritematosos": "enrojecidos", "bilateralmente": "a ambos lados",
        "bilateral": "a ambos lados", "adenopatías": "ganglios inflamados", "disnea": "falta de aire",
        "hipertensión": "tensión alta", "poliuria": "orinar mucho", "polidipsia": "mucha sed",
        "nicturia": "orinar por la noche", "dislipidemia": "colesterol alto", "taquicardia": "pulso rápido",
        "si precisa": "solo si lo necesita",
    },
}

# Sections of the professional narrative, by heading (EN/ES), with how much a patient explanation needs them.
_SECTION_WEIGHT = [
    (re.compile(r"follow|warning|seguimiento|alarma", re.I), 5),
    (re.compile(r"treatment|tratamiento", re.I), 4),
    (re.compile(r"assessment|diagnos|valoraci|diagn", re.I), 4),
    (re.compile(r"reason|motivo", re.I), 3),
    (re.compile(r"examination|exploraci", re.I), 1),
    (re.compile(r"history|historia", re.I), 0),
]
_DOSE = re.compile(r"\d+(?:[.,]\d+)?\s?(?:mg|mcg|µg|g|mL|ml|IU|UI)\b")
_URGENT = re.compile(r"emergenc|urgent|immediately|call |urgencia|inmediat|llame|llamar", re.I)
_HEADING = re.compile(r"^[^a-záéíóúñü]*[A-ZÁÉÍÓÚÑ][^a-záéíóúñü]*$")


def strip_identifier_header(source: str, reference_record: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Remove the synthetic identifiers (name, ID, DOB, phone, email, address, case id) from a professional
    narrative before it is adapted for the patient. Age, sex and the encounter date are kept.
    Returns (cleaned text, identifier categories removed)."""
    pi = (reference_record or {}).get("patient_information") or {}
    if not pi:
        return source, []
    text, removed = source, []
    case_ref = str(pi.get("id") or "")
    if case_ref:
        new = re.sub(rf"(?:Synthetic case|Caso sint[eé]tico)\s+{re.escape(case_ref)}\.\s*", "", text, flags=re.I)
        new = re.sub(rf"\b{re.escape(case_ref)}\b", "", new)
        if new != text:
            removed.append("id")
        text = new
    labelled = {"date_of_birth": r"(?:DOB|FN|fecha de nacimiento)\s*", "fictional_identification_number": r"ID\s*",
                "phone_number": r"(?:tel\.?|tfno\.?|phone)\s*"}
    for key in ("full_name", "fictional_identification_number", "date_of_birth", "phone_number", "email", "address"):
        value = pi.get(key)
        if not value:
            continue
        pattern = f"(?:{labelled[key]})?{re.escape(str(value))}" if key in labelled else re.escape(str(value))
        new = re.sub(pattern, "", text, flags=re.I)
        if new != text:
            removed.append(key)
        text = new
    # tidy the punctuation the removals leave behind, on the header line only
    head, sep, rest = text.partition("\n")
    head = re.sub(r"\(\s*\)", "", head)
    head = re.sub(r"\s*,\s*(?:,\s*)+", ", ", head)
    head = re.sub(r"\s+([,.])", r"\1", head)
    head = re.sub(r"(^|\.\s*)[,\s]+", r"\1", head).strip()
    head = re.sub(r",\s*\.", ".", head)
    head = re.sub(r"\.(?=[A-ZÁÉÍÓÚÑ])", ". ", head)
    head = head[:1].upper() + head[1:]
    return head + sep + rest, removed


def _sections(source: str) -> list[tuple[int, str]]:
    """(section weight, sentence) in reading order; heading lines are dropped."""
    out, weight, block = [], 2, []

    def flush():
        for sentence in _sentences(" ".join(block)):
            out.append((weight, sentence))
        block.clear()

    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue
        if _HEADING.match(line) and len(line) < 60:
            flush()
            weight = next((w for rx, w in _SECTION_WEIGHT if rx.search(line)), 2)
            continue
        block.append(line)
    flush()
    return out


# Output headings, matching the reference patient explanations
_PL_HEADINGS = {
    "en": {"reason": "Why you came", "found": "What we found", "means": "What this means",
           "treatment": "Your treatment", "warning": "Warning signs", "follow": "Follow-up"},
    "es": {"reason": "Por qué vino", "found": "Qué encontramos", "means": "Qué significa",
           "treatment": "Su tratamiento", "warning": "Signos de alarma", "follow": "Seguimiento"},
}
_PL_SECTION = [  # professional heading → output section
    (re.compile(r"follow|warning|seguimiento|alarma", re.I), "follow"),
    (re.compile(r"treatment|tratamiento", re.I), "treatment"),
    (re.compile(r"assessment|diagnos|valoraci|diagn", re.I), "means"),
    (re.compile(r"reason|motivo", re.I), "reason"),
    (re.compile(r"examination|exploraci", re.I), "found"),
    (re.compile(r"history|historia", re.I), "reason"),
]
# Abbreviations written out (applied before the plain-word list)
ABBREVIATIONS = {
    "en": {"BP": "blood pressure", "HR": "heart rate", "RR": "breathing rate", "SpO2": "oxygen level",
           "bpm": "beats per minute", "breaths/min": "breaths per minute", "BMI": "body mass index",
           "ECG": "heart tracing (ECG)", "HbA1c": "average blood sugar (HbA1c)", "IV": "into a vein",
           "CT": "CT scan", "FEV1": "lung test result (FEV1)", "eGFR": "kidney function (eGFR)",
           "LDL": "LDL (\"bad\") cholesterol", "PPI": "acid-reducing medicine", "NSAID": "anti-inflammatory painkiller",
           "NSAIDs": "anti-inflammatory painkillers", "ENT": "ear, nose and throat"},
    "es": {"PA": "presión arterial", "FC": "frecuencia cardiaca", "FR": "frecuencia respiratoria",
           "SatO2": "oxígeno en sangre", "lpm": "latidos por minuto", "rpm": "respiraciones por minuto",
           "IMC": "índice de masa corporal", "ECG": "electrocardiograma (ECG)", "HbA1c": "azúcar media en sangre (HbA1c)",
           "FGe": "función del riñón (FGe)", "TC": "escáner (TC)", "FEV1": "prueba de pulmón (FEV1)",
           "LDL": "colesterol LDL (\"malo\")", "IBP": "medicamento para reducir el ácido",
           "AINE": "antiinflamatorio", "AINE.": "antiinflamatorio.", "ORL": "otorrinolaringología"},
}
# A few phrases turned towards the patient ("you" / "usted")
VOICE = {
    "en": [(r"\b(?:She|He) denies\b", "You do not have"), (r"\b(?:She|He) reports\b", "You report"),
           (r"\b(?:She|He) has\b", "You have"), (r"\b(?:She|He) is\b", "You are"), (r"\b(?:She|He) was\b", "You were"),
           (r"\bAdvised to\b", "You were advised to"), (r"\bStarted on\b", "You started"),
           (r"\bher\b", "your"), (r"\bhis\b", "your")],
    "es": [(r"\bNiega\b", "No tiene"), (r"\bRefiere\b", "Usted refiere"), (r"\bSe indica\b", "Le indicamos"),
           (r"\bSe pauta\b", "Le recetamos"), (r"\bSe inicia\b", "Empieza"), (r"\bSe inician\b", "Empiezan")],
}


def _plain_words(text: str, language: str) -> tuple[str, list[dict[str, Any]]]:
    replacements = []
    for table in (ABBREVIATIONS[language], PLAIN[language]):
        for technical, simple in table.items():
            flags = 0 if table is ABBREVIATIONS[language] else re.I
            updated, count = re.subn(rf"(?<![\w/]){re.escape(technical)}(?![\w])", simple, text, flags=flags)
            if count:
                replacements.append({"technical_term": technical, "replacement": simple, "count": count})
                text = updated
    for pattern, repl in VOICE[language]:
        text = re.sub(pattern, repl, text)
    return text, replacements


def _short_sentences(sentence: str) -> list[str]:
    """Split a long sentence at semicolons into separate short sentences."""
    parts = [p.strip() for p in re.split(r";\s+", sentence) if p.strip()]
    out = []
    for k, part in enumerate(parts):
        part = part[:1].upper() + part[1:]
        if not re.search(r"[.!?:]$", part):
            part += "."
        out.append(part)
    return out


def plain_language(dataset_id: str, case_id: str, language: str, source: str,
                   max_sentences: int = 8) -> dict[str, Any]:
    # tag each sentence with its professional section
    tagged, weight, section, block = [], 2, "reason", []

    def flush():
        for sentence in _sentences(" ".join(block)):
            tagged.append((weight, section, sentence))
        block.clear()

    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue
        if _HEADING.match(line) and len(line) < 60:
            flush()
            weight = next((w for rx, w in _SECTION_WEIGHT if rx.search(line)), 2)
            section = next((sec for rx, sec in _PL_SECTION if rx.search(line)), "found")
            continue
        block.append(line)
    flush()
    # choose the clinically most useful sentences, keep reading order
    scored = []
    for i, (w, sec, sentence) in enumerate(tagged):
        score = w + (2 if _DOSE.search(sentence) else 0) + (2 if _URGENT.search(sentence) else 0)
        scored.append((score, -i, sec, sentence))
    keep = sorted(sorted(scored, reverse=True)[:max_sentences], key=lambda t: -t[1])
    # group under the reference headings; urgent follow-up sentences become warning signs
    groups: dict[str, list[str]] = {k: [] for k in _PL_HEADINGS[language]}
    for _, _, sec, sentence in keep:
        for short in _short_sentences(sentence):
            target = "warning" if sec == "follow" and _URGENT.search(short) else sec
            groups[target].append(short)
    blocks = [f"{_PL_HEADINGS[language][k]}\n" + " ".join(v) for k, v in groups.items() if v]
    output, replacements = _plain_words("\n\n".join(blocks), language)
    return _record("plain_language", dataset_id, case_id, language, source, output,
                   "deterministic_lexical_plain_language_baseline_v3",
                   {"max_sentences": int(max_sentences)},
                   {"lexical_replacements": replacements, "source_sentences": len(tagged),
                    "selection": "Sentences ranked by section (follow-up and warning signs > treatment = diagnosis > "
                                 "reason > examination > history) plus doses and urgent-care wording; kept in order, "
                                 "split at semicolons and grouped under the reference explanation headings.",
                    "limitation": "Rule-based research baseline; accessibility and clinical adequacy require human review."})


def failed_record(task: str, dataset_id: str, case_id: str, language: str, source: str, method: str,
                  parameters: dict[str, Any], error: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = _record(task, dataset_id, case_id, language, source or "", "", method, parameters, details or {})
    rec.update({"generated_output": None, "output_sha256": None, "execution_status": "Failed", "error_details": error})
    return rec


def transform(task: str, dataset_id: str, case_id: str, language: str, source: str, *,
              reference_record: dict[str, Any] | None = None, method: str = "rule_baseline",
              params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single entry point for every transformation; never raises for a generation failure."""
    params = dict(params or {})
    if not source or not source.strip():
        return failed_record(task, dataset_id, case_id, language, source, method, params, "Source text is empty.")
    if task == "anonymization":
        return anonymize(dataset_id, case_id, language, source, reference_record or {})
    preprocessing = None
    if task == "plain_language" and reference_record:
        original = source
        source, removed = strip_identifier_header(source, reference_record)
        preprocessing = {"identifier_header_removed": bool(removed), "identifiers_removed": removed,
                         "original_source_sha256": hashlib.sha256(original.encode()).hexdigest(),
                         "note": "Synthetic identifiers are removed before adaptation; age, sex and encounter date "
                                 "are kept. source_text/source_sha256 are the text the method received."}
    rec = _transform_core(task, dataset_id, case_id, language, source, method, params)
    if preprocessing:
        rec["details"]["input_preprocessing"] = preprocessing
    return rec


def plain_text(text: str) -> tuple[str, bool]:
    """Remove Markdown formatting (#, **, *, __) that a model may add; words and numbers are unchanged."""
    # a Markdown heading gets a blank line before it and none after it
    out = re.sub(r"\n*^\s{0,3}#{1,6}\s*(.+?)\s*\n+", r"\n\n\1\n", text, flags=re.M)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"__(.+?)__", r"\1", out)
    out = re.sub(r"(?m)^(\s*)[*]\s+", r"\1- ", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, out != text.strip()


# Section labels a patient explanation may use (current and earlier prompts, and the reference texts).
_LAYOUT_HEADINGS = [
    "why you came to the clinic", "why you came", "what we found", "what this means", "your treatment", "treatment",
    "warning signs", "follow-up", "follow up", "por qué vino a la consulta", "por qué vino", "qué encontramos",
    "qué significa", "su tratamiento", "tratamiento", "signos de alarma", "seguimiento",
]
_HEAD_ALT = "|".join(re.escape(h).replace(r"\ ", r"\s+").replace(r"\-", r"[-\s]") for h in _LAYOUT_HEADINGS)
_HEAD_LINE = re.compile(rf"^\s*[#*_]*\s*({_HEAD_ALT})\s*[*_]*\s*:?\s*[*_]*\s*$", re.I)
_HEAD_GLUED = re.compile(rf"([.!?)])[ \t]+((?:{_HEAD_ALT}))[ \t]*:?[ \t]*(?=\n|$)", re.I)


def layout_sections(text: str) -> tuple[str, bool]:
    """Put every section label of a patient explanation on its own line, with a blank line before it and the
    section text starting on the next line. Only line breaks change; words and numbers are untouched."""
    if not text:
        return text, False
    work = _HEAD_GLUED.sub(r"\1\n\2", text)          # "...occur. Follow-Up" at the end of a line
    out: list[str] = []
    after_heading = False
    for line in work.splitlines():
        m = _HEAD_LINE.match(line)
        if m:
            while out and not out[-1].strip():
                out.pop()
            if out:
                out.append("")
            out.append(m.group(1).strip())
            after_heading = True
            continue
        if after_heading and not line.strip():
            continue
        after_heading = False
        out.append(line.rstrip())
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return result, result != text.strip()


def _transform_core(task, dataset_id, case_id, language, source, method, params):
    if method == "rule_baseline":
        n = int(params.get("max_sentences") or (6 if task == "summarization" else 8))
        return (summarize if task == "summarization" else plain_language)(dataset_id, case_id, language, source, n)
    from . import generators
    res = generators.generate(task, language, source, method=method, case_id=case_id, params=params)
    details = {k: v for k, v in res.items() if k not in ("output", "status", "error", "parameters")}
    if res.get("status") != "ok":
        return failed_record(task, dataset_id, case_id, language, source, method, res.get("parameters", params),
                             res.get("error", "generation failed"), details)
    output, markdown_removed = plain_text(res["output"])
    if markdown_removed:
        details["markdown_removed"] = True
    if task == "plain_language":
        output, relaid = layout_sections(output)
        if relaid:
            details["section_layout_fixed"] = True
        details["raw_model_output_sha256"] = hashlib.sha256(res["output"].encode()).hexdigest()
    rec = _record(task, dataset_id, case_id, language, source, output, method, res["parameters"], details)
    rec["details"]["limitation"] = ("Model-generated research output; it may omit or alter clinical facts and "
                                   "requires the automated checks and human review.")
    return rec


def export_record(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2)
