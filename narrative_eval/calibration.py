"""Evaluation calibration: controlled premise/hypothesis pairs.

The pairs are constructed test material. They are stored only in the calibration
outputs and never written into the reference dataset or the narratives.

design_expected_category / design_expected_nli are the labels intended by the person
constructing the pair (the pipeline author, an AI model). They are NOT human expert
reference labels. human_reference_label is left null until a human reviewer records one.
"""
import re
from . import models as M
from . import catalog as C

LUMBAR_SRC_ES = ("Por otro lado, conocemos que la paciente ha sufrido infecciones de tracto urinario de repetición por los que solicitamos "
                 "uroanálisis y urocultivo por descartar nuevo cuadro de cólico renal que esté solapándose a la lumbalgia ya conocida de la "
                 "paciente, resultando compatible con infección de orina para la cual también instauramos tratamiento antibiótico.")
LUMBAR_PROG_ES = ("Tras la mala evolución de la paciente y deterioro tan rápido de su patología de base, comentamos el caso de esta paciente en "
                  "sesión clínica en nuestro centro de salud donde enseñamos la primera radiografía anteroposterior y decidimos continuar estudio "
                  "pues sospechamos cambios estructurales radicales que en servicio de urgencias pasaron por alto en dicha radiografía.")
ADAPT_A_ABX = "The test is positive, and she is treated with antibiotics."
ADAPT_B_ABX = "The test is positive, and she is treated with antibiotics (nitrofurantoin 100 mg twice daily for 7 days)."
ADAPT_A_PROG = "Progress: No information is available."
ADAPT_B_PROG = ("As her condition worsened quickly, the clinical team discussed her case and reviewed the first X-ray. They suspected that "
                "important structural changes might have been missed in the emergency department.")
SRC_A = "Case10-2020-023_Expert_Adapted_PlainLanguage.txt"
SRC_B = "Case10-002-2020-023_Expert_Adapted_PlainLanguage.txt"
SRC_DOCX = "CasoClinico2020-10-23.docx"

PAIRS = [
 # id, test_type, premise, p_lang, hypothesis, h_lang, provenance, expected_category, expected_forward_nli
 ("CAL-01", "supported_paraphrase", "Ramipril 2.5 mg orally once daily in the morning, long-term.", "en",
  "Take ramipril 2.5 mg by mouth once a day in the morning. It is a long-term blood pressure medicine.", "en", "constructed from HC0002 facts", "supported", "entailment"),
 ("CAL-02", "supported_paraphrase", "She denies fever, flank pain and vaginal discharge.", "en",
  "You have no fever, no pain in your side and no vaginal discharge.", "en", "constructed from HC0007 facts", "supported", "entailment"),
 ("CAL-03", "omission_of_important_information", "Ibuprofen 400 mg orally every 8 hours with food for up to 7 days.", "en",
  "Take ibuprofen 400 mg by mouth.", "en", "constructed from HC0006 facts (frequency, food and duration removed)", "omission", "entailment (forward); non-entailment expected in reverse"),
 ("CAL-04", "changed_medication_dose", "Ibuprofen 400 mg orally every 8 hours with food for up to 7 days.", "en",
  "Take ibuprofen 600 mg by mouth every 8 hours with food for up to 7 days.", "en", "constructed from HC0006 facts", "contradiction", "contradiction"),
 ("CAL-05", "changed_medication_frequency", "Nitrofurantoin modified release 100 mg orally twice daily with food for 5 days.", "en",
  "Take nitrofurantoin modified release 100 mg three times a day with food for 5 days.", "en", "constructed from HC0007 facts", "contradiction", "contradiction"),
 ("CAL-06", "negated_vs_positive_symptom", "He denies difficulty swallowing, pain on swallowing and coughing up blood.", "en",
  "He has been coughing up blood.", "en", "constructed from HC0009 facts", "contradiction", "contradiction"),
 ("CAL-07", "negated_vs_positive_symptom", "Niega fiebre, dolor en el flanco y flujo vaginal.", "es",
  "La paciente tiene fiebre.", "es", "constructed from HC0007 facts (Spanish)", "contradiction", "contradiction"),
 ("CAL-08", "suspected_vs_confirmed_diagnosis", "Suspected gastroesophageal reflux disease; the diagnosis is clinical and remains provisional pending response to an empirical proton pump inhibitor trial.", "en",
  "Gastroesophageal reflux disease has been confirmed.", "en", "constructed from HC0005 facts", "contradiction", "contradiction or neutral"),
 ("CAL-09", "suspected_vs_confirmed_diagnosis", "Probable antiphospholipid syndrome; requires repeat antibody testing at least 12 weeks later for confirmation.", "en",
  "Tiene un síndrome antifosfolípido confirmado.", "es", "constructed from HC0010 facts (cross-language)", "contradiction", "contradiction or neutral"),
 ("CAL-10", "planned_vs_completed_procedure", "Pelvic ultrasound was ordered on 29/10/2026 and has not been performed.", "en",
  "The pelvic ultrasound was performed and was normal.", "en", "constructed from HC0008 facts", "contradiction", "contradiction"),
 ("CAL-11", "planned_vs_completed_procedure", "Multidisciplinary tumour board to decide between radiotherapy and transoral laser microsurgery; definitive cancer treatment has not yet started.", "en",
  "He has already completed radiotherapy.", "en", "constructed from HC0009 facts", "contradiction", "contradiction"),
 ("CAL-12", "invented_clinical_chronology", "Symptoms began 2 days before the visit.", "en",
  "Her symptoms began 2 weeks ago, after a long journey.", "en", "constructed from HC0007 facts", "unsupported_addition", "contradiction or neutral"),
 ("CAL-13", "invented_clinical_chronology", "During the first 72 hours, through approximately 03/11/2026, he remained haemodynamically stable; a discharge date is not documented.", "en",
  "He was discharged home on 03/11/2026.", "en", "constructed from HC0010 facts (QA-010)", "unsupported_addition", "neutral or contradiction"),
 ("CAL-14", "supported_cross_language_paraphrase", "Omeprazole 20 mg orally once daily, 30 minutes before breakfast, for 8 weeks.", "en",
  "Tome omeprazol 20 mg por vía oral una vez al día, 30 minutos antes del desayuno, durante 8 semanas.", "es", "constructed from HC0005 facts", "supported", "entailment"),
 ("CAL-15", "supported_cross_language_paraphrase", "Hoarseness unchanged since the biopsy; definitive cancer treatment has not yet started.", "en",
  "La ronquera no ha cambiado desde la biopsia. El tratamiento del cáncer todavía no ha empezado.", "es", "constructed from HC0009 facts", "supported", "entailment"),
 ("CAL-16", "unsupported_medication_addition", "Oral antibiotic therapy started.", "en",
  "Nitrofurantoin 100 mg twice daily for 7 days was started.", "en", "constructed (generic antibiotic source)", "unsupported_addition", "neutral"),
 ("CAL-17", "unsupported_medication_addition", ADAPT_A_ABX, "en", ADAPT_B_ABX, "en", f"{SRC_A} (premise) vs {SRC_B} (hypothesis)", "unsupported_addition", "neutral"),
 ("CAL-18", "unsupported_medication_addition", LUMBAR_SRC_ES, "es", ADAPT_B_ABX, "en", f"{SRC_DOCX} controlling source (premise) vs {SRC_B} (hypothesis)", "unsupported_addition", "neutral"),
 ("CAL-19", "missing_documented_progression", LUMBAR_PROG_ES, "es", ADAPT_A_PROG, "en", f"{SRC_DOCX} documented progression (premise) vs {SRC_A} (hypothesis)", "contradiction", "contradiction or neutral"),
 ("CAL-20", "missing_documented_progression", ADAPT_B_PROG, "en", ADAPT_A_PROG, "en", f"{SRC_B} progression text (premise) vs {SRC_A} (hypothesis)", "contradiction", "contradiction or neutral"),
 ("CAL-21", "supported_cross_language_paraphrase", LUMBAR_PROG_ES, "es", ADAPT_B_PROG, "en", f"{SRC_DOCX} (premise) vs {SRC_B} progression (hypothesis)", "supported", "entailment"),
]

NEG_ANY = r"\b(no|not|denies|denied|without|none|never|niega|sin|ni|nunca)\b"
SUSP = r"(suspect|probabl|possibl|provisional|requires[^.]{0,40}confirm|sospech|probable|posible|provisional)"
CONF = r"\b(confirmed|confirmad[oa])\b"
PLANNED = r"(ordered|planned|not (yet )?(been )?(performed|started)|pending|to decide|solicitad|pendiente|prevista|aún no|todavía no)"
DONE = r"(was performed|was done|completed|has already|se realizó|ya ha|ha completado|showed|mostró)"
NOINFO = r"no (progress )?information|no information is available|no hay información|sin información"
PROGRESS_CUES = r"(evolución|worsen|deterior|progress|course|72 hours|unchanged|remained|stable)"
NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])")
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")


def _nums(t):
    return {float(n.replace(",", ".")) for n in NUM.findall(DATE.sub(" ", t))}


def _drugs(t):
    t = t.lower(); out = set()
    for en, ess in C.DRUG_LEXICON.items():
        if any(re.search(rf"\b{re.escape(n)}\b", t) for n in [en] + ess):
            out.add(en)
    for d in C.FOREIGN_DRUGS:
        if re.search(rf"\b{re.escape(d)}\b", t):
            out.add(d)
    return out


def deterministic_pair_check(premise, hypothesis):
    """Language-agnostic rule screen used for calibration. Returns (category, reasons)."""
    reasons = []
    extra_drugs = _drugs(hypothesis) - _drugs(premise)
    if extra_drugs:
        reasons.append(f"medication(s) not in premise: {sorted(extra_drugs)}")
        return "unsupported_addition", reasons
    if re.search(NOINFO, hypothesis, re.I) and re.search(PROGRESS_CUES, premise, re.I):
        return "contradiction", ["hypothesis says no progress information while premise documents progress"]
    if re.search(SUSP, premise, re.I) and re.search(CONF, hypothesis, re.I) and not re.search(SUSP + "|" + NEG_ANY, hypothesis, re.I):
        return "contradiction", ["suspected in premise, stated as confirmed in hypothesis"]
    if re.search(PLANNED, premise, re.I) and re.search(DONE, hypothesis, re.I):
        return "contradiction", ["planned/not performed in premise, completed in hypothesis"]
    if set(DATE.findall(hypothesis)) and re.search(r"discharg|alta", hypothesis, re.I) and re.search(r"not documented|no consta", premise, re.I):
        return "unsupported_addition", ["dated discharge asserted where premise says the date is not documented"]
    pn, hn = _nums(premise), _nums(hypothesis)
    if hn - pn:
        return "contradiction" if (pn - hn) else "unsupported_addition", [f"numbers not in premise: {sorted(hn - pn)}" + (f"; premise numbers missing: {sorted(pn - hn)}" if pn - hn else "")]
    if re.search(NEG_ANY, premise, re.I) and not re.search(NEG_ANY, hypothesis, re.I):
        return "contradiction", ["negation cue in premise but not in hypothesis (polarity flip candidate)"]
    if pn - hn:
        return "omission", [f"premise numbers missing from hypothesis: {sorted(pn - hn)}"]
    return "supported", ["no rule fired"]


def run_calibration():
    prem = [p[2] for p in PAIRS]; hyp = [p[4] for p in PAIRS]
    fwd = M.nli("nli_multilingual", prem, hyp)
    rev = M.nli("nli_multilingual", hyp, prem)
    en_pairs = [i for i, p in enumerate(PAIRS) if p[3] == "en" and p[5] == "en"]
    fwd_en = dict(zip(en_pairs, M.nli("nli_english_supplementary", [prem[i] for i in en_pairs], [hyp[i] for i in en_pairs])))
    rows = []
    for i, (pid, typ, p, pl, h, hl, prov, exp_cat, exp_nli) in enumerate(PAIRS):
        det_cat, det_why = deterministic_pair_check(p, h)
        e = fwd_en.get(i)
        rows.append({"pair_id": pid, "test_type": typ, "premise": p, "premise_language": pl, "hypothesis": h, "hypothesis_language": hl,
                     "provenance": prov, "design_expected_category": exp_cat, "design_expected_forward_nli": exp_nli,
                     "design_label_author": "pipeline author (AI model) at construction time; not a human expert label",
                     "deterministic_prediction": det_cat, "deterministic_reasons": det_why,
                     "deterministic_matches_design_label": det_cat == exp_cat,
                     "mdeberta_forward": fwd[i], "mdeberta_reverse": rev[i],
                     "mdeberta_forward_matches_design_label": fwd[i]["predicted_label"] in exp_nli.split(" (")[0].replace(" or ", "|").split("|"),
                     "nli_deberta_base_en_forward": e,
                     "nli_deberta_base_en_matches_design_label": (e["predicted_label"] in exp_nli.split(" (")[0].replace(" or ", "|").split("|")) if e else None,
                     "human_reference_label": None, "human_review_status": "pending_human_review"})
    summary = {"pairs": len(rows),
               "deterministic_agreement_with_design_labels": sum(r["deterministic_matches_design_label"] for r in rows),
               "mdeberta_forward_agreement_with_design_labels": sum(r["mdeberta_forward_matches_design_label"] for r in rows),
               "nli_deberta_base_en_agreement_with_design_labels": sum(1 for r in rows if r["nli_deberta_base_en_matches_design_label"]),
               "nli_deberta_base_en_pairs": len(en_pairs),
               "note": "Agreement is with labels intended by construction, not with human reference labels (none recorded). No thresholds were derived from these pairs."}
    return rows, summary
