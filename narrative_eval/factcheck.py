"""Layer C - deterministic factual-consistency checks.

Lexical and rule-based. A 'supported' finding means a pattern matched, not that the
statement is medically correct; an 'omission' means a pattern did not match and must
be confirmed by a human. Regex matching alone cannot establish medical accuracy.
"""
import re
from . import catalog as C
from .config import FIELDS, FIELD_TYPE
from .source import ptr, all_strings
from .texts import content_sentences, split_sentences

DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
NUM_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])")
DOTSAFE = r"(?:[^.]|(?<=\d)\.(?=\d))"  # D41-001: '[^.]' gaps must not stop at decimal points (e.g. '2.5 mg')


def _rx(p):
    return p.replace("[^.]", DOTSAFE)


HIGH_IMPACT = {"medication", "diagnosis", "certainty", "warning", "followup", "qa_limitation", "symptom", "progress", "unsupported_medication"}
LIMIT_REGEX = "Lexical pattern match on the whole narrative; cannot judge meaning, paraphrases outside the pattern, or clinical correctness."


class Findings:
    def __init__(self):
        self.items = []

    def add(self, pid, field, family, path, evidence, claim, category, method, limitation=LIMIT_REGEX, check_id=None):
        self.items.append({
            "finding_id": f"FC-{len(self.items)+1:04d}", "patient_id": pid, "narrative_field": field, "check_family": family,
            "check_id": check_id, "source_json_path": path, "source_fact_or_evidence": evidence, "generated_claim_or_sentence": claim,
            "category": category, "evaluation_method": method, "model_score": None, "confidence_limitations": limitation,
            "impact": "high" if (family in HIGH_IMPACT and category != "supported") else ("standard" if category != "supported" else "none"),
            "human_review_status": "pending_human_review" if category != "supported" else "not_required_unless_sampled",
        })


def _sent_with(text, rx):
    rx = _rx(rx)
    for s in split_sentences(text):
        if re.search(rx, s, re.I):
            return s
    return None


def _num_norm(n):
    v = float(n.replace(",", "."))
    return repr(int(v)) if v == int(v) else repr(v)


def numbers_in(text):
    t = DATE_RE.sub(" ", text)
    t = re.sub(r"SYN-(ID|PHONE)-\d+|patient\d+@example\.invalid|HC\d{4}|\b\d+ Fictional Avenue", " ", t)
    return {_num_norm(n) for n in NUM_RE.findall(t)}


def source_numbers(rec, pid, freeze_ref=None):
    vals = set()
    for _, v in all_strings(rec):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            vals.add(_num_norm(str(v)))
        elif isinstance(v, str):
            vals |= numbers_in(v)
    return vals


def source_dates(rec):
    out = set()
    for _, v in all_strings(rec):
        if isinstance(v, str):
            out |= set(DATE_RE.findall(v))
    return out


def run_layer_c(pid, rec, texts, fs=None):
    """texts: {field: text}. Returns Findings (appends to fs if given)."""
    fs = fs or Findings()
    P = f"/{pid}"
    # 1 curated catalog
    for fid, path, scope, fam, en, es, desc in C.CATALOG[pid]:
        for f in FIELDS:
            if scope != "all" and FIELD_TYPE[f] != scope:
                continue
            rx = en if f.endswith("en") else es
            s = _sent_with(texts[f], rx)
            ev = f"{desc} | source value: {str(ptr(rec, path))[:200]}"
            fs.add(pid, f, fam, P + path, ev, s, "supported" if s else "omission", f"catalog regex ({'EN' if f.endswith('en') else 'ES'}): {rx}", check_id=fid)
    # 2 identity and encounter (professional notes carry full synthetic identity; patient texts the encounter date)
    pi = rec["patient_information"]
    for f in FIELDS:
        keys = ["full_name", "fictional_identification_number", "date_of_birth", "encounter_date", "phone_number", "email", "address"] if FIELD_TYPE[f] == "clinical" else ["encounter_date"]
        for k in keys:
            ok = pi[k] in texts[f]
            fs.add(pid, f, "identity", f"{P}/patient_information/{k}", pi[k], pi[k] if ok else None, "supported" if ok else "omission", "exact string match", "Exact match only.", check_id=f"id_{k}")
        if FIELD_TYPE[f] == "clinical":
            age_ok = re.search(rf"\b{pi['age']} (years|años)\b", texts[f]) is not None
            fs.add(pid, f, "identity", f"{P}/patient_information/age", str(pi["age"]), str(pi["age"]) if age_ok else None, "supported" if age_ok else "omission", "regex '<age> years|años'", check_id="id_age")
    # 3 documented negatives (symptom polarity)
    for path, en, es, desc in C.NEGATIVES[pid]:
        for f in FIELDS:
            rx, neg = (en, C.NEG) if f.endswith("en") else (es, C.NEG_ES)
            sents = [s for s in split_sentences(texts[f]) if re.search(rx, s, re.I)]
            # only sentences outside warning/follow-up instructions express the patient's current state
            state = [s for k, s in content_sentences(texts[f], f) if k not in ("warnings", "followup", "followup_warnings") and re.search(rx, s, re.I)]
            if not state:
                cat = "omission" if FIELD_TYPE[f] == "clinical" else "requires_human_review"
                fs.add(pid, f, "symptom", P + path, desc, None, cat, f"negated-term search: {rx}",
                       "Absent in descriptive sections; for patient texts a documented negative may be legitimately omitted, so this is a review item.", check_id="neg")
                continue
            negated = [s for s in state if re.search(neg, s[:re.search(rx, s, re.I).end()], re.I)]
            if negated:
                fs.add(pid, f, "symptom", P + path, desc, negated[0], "supported", f"negation cue before term ({neg})", "Cue-based; scope of negation not parsed.", check_id="neg")
            else:
                fs.add(pid, f, "symptom", P + path, desc, state[0], "contradiction", f"term present without negation cue ({neg})", "Cue-based; may be a false alarm if negation is expressed differently.", check_id="neg")
    # 4 suspected-not-confirmed
    for path, en, es, desc in C.SUSPECTED.get(pid, []):
        for f in FIELDS:
            rx, conf, hedge = (en, C.CONFIRM_EN, C.HEDGE_EN) if f.endswith("en") else (es, C.CONFIRM_ES, C.HEDGE_ES)
            bad = [s for s in split_sentences(texts[f]) if re.search(rx, s, re.I) and re.search(conf, s, re.I) and not re.search(hedge, s, re.I)]
            if bad:
                fs.add(pid, f, "certainty", P + path, desc, bad[0], "contradiction", "sentence names suspected condition with confirmation cue and no hedge", check_id="susp")
            else:
                fs.add(pid, f, "certainty", P + path, desc, _sent_with(texts[f], rx), "supported", "no unhedged confirmation of suspected condition", check_id="susp")
    # 5 progress
    prog = C.PROGRESS.get(pid, {"documented": False, "path": "/clinical_progress/clinical_course"})
    for f in FIELDS:
        en = f.endswith("en")
        noinfo = re.search(C.NO_PROGRESS_INFO_EN if en else C.NO_PROGRESS_INFO_ES, texts[f], re.I)
        if prog["documented"]:
            if noinfo:
                fs.add(pid, f, "progress", P + prog["path"], rec["clinical_progress"]["clinical_course"], noinfo.group(0), "contradiction", "'no progress information' wording despite documented progress", check_id="prog")
        else:
            s = _sent_with(texts[f], C.NOT_ASSESSED_EN if en else C.NOT_ASSESSED_ES)
            if FIELD_TYPE[f] == "clinical":
                fs.add(pid, f, "progress", P + prog["path"], rec["clinical_progress"]["clinical_course"], s, "supported" if s else "omission", "regex: progress not yet assessed", check_id="prog")
            irx = r"\b(has improved|have improved|improved after|resolved|is better now)\b" if en else r"\b(mejoró|ha mejorado|se resolvió)\b"
            improved = None
            for s in split_sentences(texts[f]):
                for m in re.finditer(irx, s, re.I):
                    if not re.search(r"\b(if|once|until|unless|when|si|cuando|hasta)\b[^,;]{0,30}$", s[:m.start()], re.I):
                        improved = improved or s
            if improved:
                fs.add(pid, f, "progress", P + prog["path"], rec["clinical_progress"]["clinical_course"], improved, "unsupported_addition", "improvement claim although progress not yet assessed", check_id="prog_improved")
    # 6 QA limitations
    for qa, L in C.LIMITATIONS.items():
        if L["patient"] != pid:
            continue
        for f in FIELDS:
            lang = f[-2:]
            req = [bool(re.search(_rx(rx), texts[f], re.I)) for rx in L["required"][lang]]
            forb = [m.group(0) for rx in L["forbidden"][lang] for m in [re.search(_rx(rx), texts[f], re.I)] if m]
            if forb:
                fs.add(pid, f, "qa_limitation", L["source_path"], f"{qa}: {L['statement']}", "; ".join(forb), "contradiction", "forbidden pattern found", check_id=qa)
            fs.add(pid, f, "qa_limitation", L["source_path"], f"{qa}: {L['statement']}", None if not all(req) else "required patterns present",
                   "supported" if all(req) else "omission", f"required patterns: {L['required'][lang]}", check_id=qa)
    # 7 dates
    sd = source_dates(rec)
    for f in FIELDS:
        for d in sorted(set(DATE_RE.findall(texts[f]))):
            if d not in sd:
                fs.add(pid, f, "chronology", P, "dates present in source record", _sent_with(texts[f], re.escape(d)), "unsupported_addition", "date not found anywhere in source record", "Exact date match.", check_id="date")
    # 8 numbers
    sn = source_numbers(rec, pid)
    for f in FIELDS:
        extra = sorted(numbers_in(texts[f]) - sn, key=lambda x: float(x))
        for n in extra:
            nrx = re.escape(n) if f.endswith("en") else re.escape(n.replace(".", ","))
            fs.add(pid, f, "numeric", P, "numbers present in source record", _sent_with(texts[f], rf"(?<![\d.,]){nrx}(?![\d])"), "requires_human_review",
                   "number not found anywhere in source record", "May be a legitimate derived value (e.g. a day count) or an unsupported number.", check_id="number")
        fs.add(pid, f, "numeric", P, f"{len(numbers_in(texts[f]) & sn)} of {len(numbers_in(texts[f]))} numbers found in source", None,
               "supported" if not extra else "requires_human_review", "set difference of numbers (text minus source)", check_id="number_summary")
    # 9 medications: unsupported names
    src_txt = " ".join(str(v) for _, v in all_strings(rec) if isinstance(v, str)).lower()
    for f in FIELDS:
        low = texts[f].lower()
        for d in C.FOREIGN_DRUGS:
            if re.search(rf"\b{re.escape(d)}\b", low):
                fs.add(pid, f, "unsupported_medication", P + "/medication_management/medications", "medications in source", _sent_with(texts[f], re.escape(d)), "unsupported_addition", "drug name not in this record (foreign-drug lexicon)", check_id="drug_foreign")
        for en, ess in C.DRUG_LEXICON.items():
            names = [en] if f.endswith("en") else ess
            if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names) and en not in src_txt:
                fs.add(pid, f, "unsupported_medication", P + "/medication_management/medications", "medications in source", _sent_with(texts[f], "|".join(map(re.escape, names))), "unsupported_addition", "drug from study lexicon not present in this record", check_id="drug_other_record")
    # 10 follow-up scheduling status
    fu = rec["follow_up_and_monitoring_plan"]["follow_up_items"]
    unsched = [i for i, x in enumerate(fu) if not x["scheduled_date"]]
    for f in FIELDS:
        en = f.endswith("en")
        if unsched:
            rx = r"(\b(no|not|neither|none)\b[^.]{0,60}\bbooked\b|\bbooked\b[^.]{0,20}\b(not|no)\b)" if en else r"(\b(sin|no|ninguna|todavía no|aún no)\b[^.]{0,60}\b(fecha|cita|reservad[ao]s?)\b|\b(fecha|cita)s?\b[^.]{0,30}\b(aún|todavía)\b)"
            s = _sent_with(texts[f], rx)
            fs.add(pid, f, "followup", f"{P}/follow_up_and_monitoring_plan/follow_up_items/{unsched[0]}/scheduled_date", "planned follow-up without scheduled date", s,
                   "supported" if s else "omission", "regex: 'not booked' status statement", check_id="fu_status")
        bad = _sent_with(texts[f], r"(appointment|visit|review) (is |has been )?booked (for|on) \d" if en else r"(cita|revisión) (reservada|programada) (para el|el) \d")
        if bad and not any(x["scheduled_date"] and x["scheduled_date"] in bad for x in fu):
            fs.add(pid, f, "followup", f"{P}/follow_up_and_monitoring_plan/follow_up_items", "follow-up scheduling", bad, "contradiction", "booked date stated for unscheduled item", check_id="fu_booked")
    return fs


def bilingual_parity(pid, texts, fs):
    """EN vs ES number-set parity within each narrative type (translation-difference screen)."""
    for typ in ("clinical_narrative", "patient_explanation"):
        en, es = numbers_in(texts[typ + "_en"]), numbers_in(texts[typ + "_es"])
        if en != es:
            fs.add(pid, f"{typ}_en|{typ}_es", "bilingual", f"/{pid}", "English and Spanish versions should carry the same numbers",
                   f"only EN: {sorted(en - es)}; only ES: {sorted(es - en)}", "requires_human_review", "number-set comparison EN vs ES", "Word-form numbers (e.g. 'twice', 'dos') are not compared.", check_id="bilingual_numbers")
        else:
            fs.add(pid, f"{typ}_en|{typ}_es", "bilingual", f"/{pid}", "English and Spanish versions should carry the same numbers", f"{len(en)} numbers identical", "supported", "number-set comparison EN vs ES", check_id="bilingual_numbers")
    return fs
