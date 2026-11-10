"""Layer B - multilingual semantic similarity (sentence-transformers/paraphrase-multilingual-mpnet-base-v2).

Cosine similarity of aligned clinical units. High similarity does not show that
doses, negation or certainty are right; low similarity is a review signal only.
No acceptance threshold is used.
"""
import numpy as np
from . import models as M
from .texts import sections, split_sentences

CLIN_TO_PAT = [  # (unit, clinical section keys, patient section keys)
    ("presentation", ["reason", "history"], ["why"]),
    ("findings", ["examination"], ["found"]),
    ("assessment", ["assessment"], ["means"]),
    ("treatment", ["treatment"], ["treatment", "treatment_extra"]),
    ("warnings_and_follow_up", ["followup_warnings"], ["warnings", "followup"]),
]
CLIN_KEYS = ["header", "reason", "history", "examination", "assessment", "treatment", "followup_warnings"]
PAT_KEYS = ["why", "found", "means", "treatment", "treatment_extra", "warnings", "followup"]
ANCHOR = [("reason_for_visit", "why"), ("main_findings", "found"), ("diagnosis_explanation", "means"),
          ("treatment_explanation", "treatment"), ("warning_explanation", "warnings"), ("follow_up_explanation", "followup")]
COMPARISONS = [("en_professional_vs_en_patient", "clinical_narrative_en", "patient_explanation_en", "register"),
               ("es_professional_vs_es_patient", "clinical_narrative_es", "patient_explanation_es", "register"),
               ("en_professional_vs_es_professional", "clinical_narrative_en", "clinical_narrative_es", "bilingual"),
               ("en_patient_vs_es_patient", "patient_explanation_en", "patient_explanation_es", "bilingual")]


def unit_vector(text):
    sents = split_sentences(text) or [text]
    e = M.embed(sents)
    v = e.mean(axis=0)
    lens = M.token_lengths_embedder(sents)
    return v / np.linalg.norm(v), sum(1 for n in lens if n > 128), len(sents)


def cos(a, b):
    return float(np.clip(a @ b, -1, 1))


def _units(sec, keys):
    return " ".join(sec[k] for k in keys if k in sec).strip()


def compare_pair(pid, texts, name, fa, fb, kind):
    sa, sb = sections(texts[fa], fa), sections(texts[fb], fb)
    if kind == "register":
        pairs = [(u, _units(sa, ka), _units(sb, kb)) for u, ka, kb in CLIN_TO_PAT]
    else:
        keys = CLIN_KEYS if fa.startswith("clinical") else PAT_KEYS
        pairs = [(k, sa.get(k, ""), sb.get(k, "")) for k in keys if k in sa or k in sb]
    rows = []
    for unit, ta, tb in pairs:
        if not ta or not tb:
            rows.append({"patient_id": pid, "comparison": name, "unit": unit, "text_a_field": fa, "text_b_field": fb, "cosine": None,
                         "status": "not_calculated", "null_reason": "unit missing in one text", "text_a": ta, "text_b": tb,
                         "sentences_truncated_a": None, "sentences_truncated_b": None})
            continue
        va, tra, _ = unit_vector(ta)
        vb, trb, _ = unit_vector(tb)
        rows.append({"patient_id": pid, "comparison": name, "unit": unit, "text_a_field": fa, "text_b_field": fb, "cosine": round(cos(va, vb), 6),
                     "status": "calculated", "null_reason": None, "text_a": ta, "text_b": tb, "sentences_truncated_a": tra, "sentences_truncated_b": trb})
    vals = [r["cosine"] for r in rows if r["cosine"] is not None]
    agg = {"patient_id": pid, "comparison": name, "unit": "AGGREGATE_unweighted_mean_of_aligned_units", "text_a_field": fa, "text_b_field": fb,
           "cosine": round(float(np.mean(vals)), 6) if vals else None, "status": "calculated" if vals else "not_calculated",
           "null_reason": None if vals else "no aligned units", "text_a": f"{len(vals)} units", "text_b": "", "sentences_truncated_a": None, "sentences_truncated_b": None}
    return rows + [agg]


def source_anchor(pid, rec, texts):
    """Patient explanation sections vs the source plain_language_patient_version (same language)."""
    plv = rec["plain_language_patient_version"]
    rows = []
    for lang in ("en", "es"):
        f = f"patient_explanation_{lang}"
        sec = sections(texts[f], f)
        vals = []
        for src_key, pkey in ANCHOR:
            src = plv[src_key][lang]
            gen = sec.get(pkey, "") + (" " + sec["treatment_extra"] if pkey == "treatment" and "treatment_extra" in sec else "")
            va, tra, _ = unit_vector(src)
            vb, trb, _ = unit_vector(gen)
            c = round(cos(va, vb), 6); vals.append(c)
            rows.append({"patient_id": pid, "comparison": f"source_plain_language_{lang}_vs_{lang}_patient", "unit": pkey, "text_a_field": f"/{pid}/plain_language_patient_version/{src_key}/{lang}",
                         "text_b_field": f, "cosine": c, "status": "calculated", "null_reason": None, "text_a": src, "text_b": gen.strip(),
                         "sentences_truncated_a": tra, "sentences_truncated_b": trb})
        rows.append({"patient_id": pid, "comparison": f"source_plain_language_{lang}_vs_{lang}_patient", "unit": "AGGREGATE_unweighted_mean_of_aligned_units",
                     "text_a_field": f"/{pid}/plain_language_patient_version", "text_b_field": f, "cosine": round(float(np.mean(vals)), 6), "status": "calculated",
                     "null_reason": None, "text_a": f"{len(vals)} units", "text_b": "", "sentences_truncated_a": None, "sentences_truncated_b": None})
    return rows


def run_layer_b(pid, rec, texts):
    rows = []
    for name, fa, fb, kind in COMPARISONS:
        rows += compare_pair(pid, texts, name, fa, fb, kind)
    rows += source_anchor(pid, rec, texts)
    return rows


def match_propositions(facts, texts, fields, content_sents):
    """For every source fact (EN statement) find the most similar sentence in each
    generated text. Returns rows with cosine; used to align NLI pairs."""
    fe = M.embed([f["statement_en"] for f in facts])
    rows = []
    for f in fields:
        sents = content_sents[f]
        if not sents:
            continue
        se = M.embed([s for _, s in sents])
        sim = fe @ se.T
        for i, fact in enumerate(facts):
            j = int(np.argmax(sim[i]))
            rows.append({"fact_id": fact["fact_id"], "patient_id": fact["patient_id"], "narrative_field": f, "source_json_path": fact["source_path"],
                         "epistemic_tag": fact["epistemic_tag"], "source_statement": fact["statement_en"], "matched_section": sents[j][0],
                         "matched_sentence": sents[j][1], "cosine": round(float(sim[i, j]), 6)})
    return rows
