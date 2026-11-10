"""Structured clinical fact checks: agreement of one output text with the approved JSON record.

This is the authority check. It never looks at a reference narrative, so a text that is very
similar to a reference but changes a dose or a diagnosis still fails here.

Core logic is the Prompt 4.1 Layer C detector set (narrative_eval.factcheck.run_layer_c and
narrative_eval.catalog), vendored unchanged. This module only:
  1. adapts a single model output to the four-field interface that Layer C expects,
  2. applies task rules (what a summary may omit, what an anonymized note must keep),
  3. re-tests omissions after spelling out number words ("every eight hours"), and
  4. adds two supplementary rules Layer C does not have: a changed dose or a changed
     frequency next to a drug name. The Prompt 4.1 calibration showed the multilingual NLI
     model misses frequency changes, so these must be caught deterministically.
All checks are lexical screens: a missing match is a potential omission for human review.
"""
from __future__ import annotations

import re
from typing import Any

from narrative_eval import catalog as C
from narrative_eval.config import FIELDS
from narrative_eval.factcheck import run_layer_c
from narrative_eval.source import all_strings

from .common import (CONTRADICTION, OMISSION, REVIEW, SUPPORTED, UNSUPPORTED, Finding, nums,
                     spell_out_numbers)

HIGH_IMPACT = {"medication", "diagnosis", "certainty", "warning", "followup", "qa_limitation", "symptom", "progress",
               "unsupported_medication"}

# Which Layer C families/scopes are required per task. Anything else that is missing is
# reported as 'requires_human_review' with severity info, so it is visible but not a failure.
TASK_RULES: dict[str, dict[str, Any]] = {
    "anonymization": {   # must preserve everything the input carried
        "field_type": "clinical", "relative_to_source": True,
        "required_scopes": {"all", "clinical"}, "optional_families": set(),
    },
    "summarization": {   # may drop detail, never critical facts
        "field_type": "clinical", "relative_to_source": False,
        "required_scopes": {"all"}, "optional_families": {"symptom", "progress", "investigation", "history", "examination"},
    },
    "plain_language": {
        "field_type": "patient", "relative_to_source": False,
        "required_scopes": {"all", "patient"}, "optional_families": {"symptom"},
    },
}

_DROP_FAMILIES = {"identity", "bilingual"}   # identifiers are judged by the PII module


def _field(task: str, language: str) -> str:
    kind = TASK_RULES[task]["field_type"]
    return f"clinical_narrative_{language}" if kind == "clinical" else f"patient_explanation_{language}"


def _layer_c_single(case_id: str, record: dict[str, Any], text: str, field: str) -> list[dict[str, Any]]:
    texts = {f: (text if f == field else "") for f in FIELDS}
    return [i for i in run_layer_c(case_id, record, texts).items
            if i["narrative_field"] == field and i["check_family"] not in _DROP_FAMILIES]


def _key(i: dict[str, Any]) -> tuple:
    return (i["check_family"], i["check_id"], i["source_json_path"])


# Layer C checks that exist only because of what a particular output says (extra numbers, dates or drugs,
# negation cues): they are reported, but never count towards the required-fact denominator.
OUTPUT_DERIVED_CHECKS = {"number", "number_summary", "date", "drug_foreign", "drug_other_record", "prog_improved",
                         "fu_booked", "bilingual_numbers", "neg"}


def _scope_of(case_id: str) -> dict[str, str]:
    return {entry[0]: entry[2] for entry in C.CATALOG.get(case_id, [])}


def _severity(cat: str, family: str, required: bool) -> str:
    if cat == SUPPORTED:
        return "info"
    if cat in (CONTRADICTION, UNSUPPORTED):
        return "critical" if family in HIGH_IMPACT else "major"
    if cat == OMISSION:
        return "major" if required else "info"
    return "minor"


def check_structured_facts(record: dict[str, Any], case_id: str, output: str, language: str, task: str,
                           source_text: str | None = None, all_case_ids: list[str] | None = None) -> list[Finding]:
    rules = TASK_RULES[task]
    field = _field(task, language)
    items = _layer_c_single(case_id, record, output, field)

    # number words: an omission that matches once numbers are spelled as digits is supported
    spelled = spell_out_numbers(output, language)
    if spelled != output:
        alt = {_key(i): i for i in _layer_c_single(case_id, record, spelled, field) if i["category"] == SUPPORTED}
        for i in items:
            if i["category"] == OMISSION and _key(i) in alt:
                i.update(category=SUPPORTED, generated_claim_or_sentence=alt[_key(i)]["generated_claim_or_sentence"],
                         evaluation_method=i["evaluation_method"] + " (matched after number-word normalisation)")

    # anonymization: only what the input itself carried must survive
    in_source: set | None = None
    if rules["relative_to_source"] and source_text:
        in_source = {_key(i) for i in _layer_c_single(case_id, record, source_text, field) if i["category"] == SUPPORTED}

    scopes = _scope_of(case_id)
    out: list[Finding] = []
    for i in items:
        fam, cat = i["check_family"], i["category"]
        scope = scopes.get(i["check_id"])
        # Whether a fact is required depends only on the case and the task, never on how the output did, so
        # the denominator is the same for every output of a case. A required fact the lexical check cannot
        # decide stays in the denominator and is listed as "not assessed".
        required = i["check_id"] not in OUTPUT_DERIVED_CHECKS and not (fam in rules["optional_families"]
                        or (scope is not None and scope not in rules["required_scopes"])
                        or (in_source is not None and _key(i) not in in_source))
        shown = cat if (cat != OMISSION or required) else REVIEW
        out.append(Finding(
            check=f"{fam}::{i['check_id']}", classification=shown, severity=_severity(shown, fam, required),
            explanation=f"{i['source_fact_or_evidence']} [{i['evaluation_method']}]"[:500],
            json_pointer=i["source_json_path"].removeprefix(f"/{case_id}") or "/",
            source_evidence=str(i["source_fact_or_evidence"])[:300],
            output_evidence=(i["generated_claim_or_sentence"] or None), required=required,
            origin="narrative_eval Layer C (Prompt 4.1)"))

    out += _wrong_patient(case_id, output, all_case_ids)
    out += _dose_and_frequency(record, output, language)
    return out


def _wrong_patient(case_id: str, output: str, all_case_ids: list[str] | None) -> list[Finding]:
    others = [o for o in (all_case_ids or []) if o != case_id and re.search(rf"\b{o}\b", output)]
    if not others:
        return []
    return [Finding("engine::wrong_patient_reference", CONTRADICTION, "critical",
                    f"Text mentions another patient's record id: {others}", "/patient_information/id", case_id,
                    str(others), origin="engine supplementary rule")]


# ------------------------------------------------------------------ supplementary rules
_UNIT = r"(mg|g|mcg|µg|micrograms?|microgramos|mL|ml|units|unidades|IU|UI|puffs?|inhalations?|inhalaciones|sprays?|pulverizaciones)"
_FREQ = {
    "en": [(r"once (a day|daily)|every 24 hours", "1/day"), (r"twice (a day|daily)|two times (a day|daily)", "2/day"),
           (r"three times (a day|daily)", "3/day"), (r"four times (a day|daily)", "4/day"),
           (r"every (\d+) hours", "q{}h"), (r"once (a )?week(ly)?|every week", "1/week")],
    "es": [(r"una vez al día|cada 24 horas", "1/day"), (r"dos veces al día", "2/day"), (r"tres veces al día", "3/day"),
           (r"cuatro veces al día", "4/day"), (r"cada (\d+) horas", "q{}h"), (r"una vez (a la|por) semana|semanal", "1/week")],
}
_EQUIV = {"q24h": "1/day", "q12h": "2/day", "q8h": "3/day", "q6h": "4/day"}


def _freqs(text: str, language: str) -> set[str]:
    found = set()
    for rx, code in _FREQ[language]:
        for m in re.finditer(rx, text, re.I):
            c = code.format(m.group(1)) if "{}" in code else code
            found.add(_EQUIV.get(c, c))
    return found


_PRN = {"en": re.compile(r"as needed|when needed|if needed|(?:if|when) you need|only when|as required|if necessary|\bprn\b", re.I),
        "es": re.compile(r"a demanda|si lo necesita|si precisa|cuando lo necesite|solo si|si es necesario", re.I)}


def _clause(sentence: str, name: str, other_names: list[str]) -> str:
    """The part of a sentence about one medicine: from its name up to the next other medicine's name."""
    m = re.search(rf"\b{re.escape(name)}\b", sentence, re.I)
    if not m:
        return sentence
    rest = sentence[m.start():]
    ends = [x.start() for o in other_names if o != name
            for x in [re.search(rf"\b{re.escape(o)}\b", rest[len(name):], re.I)] if x]
    return rest[: len(name) + min(ends)] if ends else rest


def _names(med_name: str, language: str) -> list[str]:
    base = (med_name.split(" (")[0].split("/")[0].lower().replace(" nasal spray", "").replace(" furoate", "")
            .replace("unfractionated ", ""))
    return [base] if language == "en" else C.DRUG_LEXICON.get(base, [base])


def _dose_and_frequency(record: dict[str, Any], output: str, language: str) -> list[Finding]:
    meds = record["medication_management"]["medications"]
    text = spell_out_numbers(output, language)
    sentences = [s for s in re.split(r"(?<=[.!?;])\s+|\n", text) if s.strip()]
    out: list[Finding] = []
    for i, med in enumerate(meds):
        names = _names(med["name"], language)
        same = [m for m in meds if set(_names(m["name"], language)) == set(names)]
        allowed_nums = {n for m in same for n in nums(" ".join(str(v) for _, v in all_strings(m) if v is not None))}
        allowed_freq: set[str] = set()
        for m in same:
            allowed_freq |= _freqs(" ".join(str(v) for _, v in all_strings(m) if isinstance(v, str)), "en")
        all_names = [n for m in meds for n in _names(m["name"], language)]
        for nm in names:
            for j, sent in enumerate(sentences):
                if not re.search(rf"\b{re.escape(nm)}\b", sent, re.I):
                    continue
                about = _clause(sent, nm, all_names)
                # a short follow-on sentence with no medicine name ("Only take it when you need it.") is about
                # the same medicine
                nxt = sentences[j + 1] if j + 1 < len(sentences) else ""
                if nxt and len(nxt.split()) <= 12 and not any(re.search(rf"\b{re.escape(o)}\b", nxt, re.I)
                                                               for o in all_names) and not nxt.lstrip().startswith("-"):
                    about += " " + nxt
                for m in re.finditer(rf"\b{re.escape(nm)}\b[^.\n;]{{0,40}}?(\d+(?:[.,]\d+)?)\s*{_UNIT}\b", sent, re.I):
                    n = nums(m.group(1))
                    if n and n[0] not in allowed_nums:
                        out.append(Finding("engine::medication_dose_changed", CONTRADICTION, "critical",
                                           f"{med['name']}: '{m.group(1)} {m.group(2)}' does not appear in this medication's record",
                                           f"/medication_management/medications/{i}", med.get("dose"), sent.strip(),
                                           origin="engine supplementary rule"))
                # a frequency stated in the same sentence as the drug must match the record
                said = _freqs(about, language)
                if said and allowed_freq and (said - allowed_freq):
                    out.append(Finding("engine::medication_frequency_changed", CONTRADICTION, "critical",
                                       f"{med['name']}: frequency {sorted(said)} differs from the record {sorted(allowed_freq)}",
                                       f"/medication_management/medications/{i}/frequency", med.get("frequency"),
                                       sent.strip(), origin="engine supplementary rule"))
                # "only when needed" for a medicine the record gives on a schedule
                record_prn = any(_PRN[language].search(" ".join(str(v) for _, v in all_strings(m) if isinstance(v, str)))
                                 or _PRN["en"].search(" ".join(str(v) for _, v in all_strings(m) if isinstance(v, str)))
                                 for m in same)
                if not record_prn and _PRN[language].search(about):
                    out.append(Finding("engine::medication_made_as_needed", CONTRADICTION, "critical",
                                       f"{med['name']}: described as 'only when needed', but the record gives it on a "
                                       "regular schedule", f"/medication_management/medications/{i}/frequency",
                                       med.get("frequency"), sent.strip(), origin="engine supplementary rule"))
    seen, uniq = set(), []
    for f in out:
        k = (f.check, f.output_evidence, f.explanation)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def summarize_facts(findings: list[Finding]) -> dict[str, Any]:
    required = [f for f in findings if f.required and f.origin.startswith("narrative_eval")]
    supported = [f for f in required if f.classification == SUPPORTED]
    not_assessed = [f for f in required if f.classification == REVIEW]
    contradictions = [f for f in findings if f.classification == CONTRADICTION]
    unsupported = [f for f in findings if f.classification == UNSUPPORTED]
    omissions = [f for f in findings if f.classification == OMISSION]
    critical = [f for f in findings if f.severity == "critical" and f.is_problem]
    return {
        "required_items": len(required),
        "required_supported": len(supported),
        "required_not_assessed": len(not_assessed),
        "fact_agreement": round(len(supported) / len(required), 4) if required else None,
        "omissions": len(omissions),
        "contradictions": len(contradictions),
        "unsupported_additions": len(unsupported),
        "critical_problems": len(critical),
        "passed": not critical and not omissions and not unsupported,
        "method": "narrative_eval Layer C (Prompt 4.1) + engine supplementary dose/frequency rules; lexical screening",
    }
