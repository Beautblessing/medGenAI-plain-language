"""Layer D - natural-language inference on aligned propositions.

Forward check: premise = source fact (rendered from the record), hypothesis = generated
sentence -> does the source support the claim?
Reverse check: premise = generated sentence, hypothesis = source fact -> does the output
carry the source fact? (omission signal)
Sentence screen: every generated content sentence against its 3 most similar source
facts (by Layer B embeddings); best entailment is recorded.

Neutral is not automatically a hallucination (a sentence often combines several facts
while the premise holds only one). Contradiction is a review flag, not a clinical
judgement. Decision rule: argmax; no thresholds.
"""
import numpy as np
from . import models as M
from .config import MODELS


def relevant(fact, field):
    if field.startswith("clinical"):
        return True
    if fact["section"] == "identity":
        return fact["source_path"].endswith("encounter_date")
    if "/vital_signs/" in fact["source_path"]:
        return False
    if fact["epistemic_tag"] == "unknown" and fact["section"] == "history":
        return False
    return True


def run_pairs(key, premises, hypotheses):
    return M.nli(key, premises, hypotheses)


def layer_d(matches, facts_by_id, fields_lang):
    """matches: rows from semantic.match_propositions."""
    rows = []
    prem = [m["source_statement"] for m in matches]
    hyp = [m["matched_sentence"] for m in matches]
    fwd = run_pairs("nli_multilingual", prem, hyp)
    rev = run_pairs("nli_multilingual", hyp, prem)
    en_idx = [i for i, m in enumerate(matches) if m["narrative_field"].endswith("en")]
    fwd_en = dict(zip(en_idx, run_pairs("nli_english_supplementary", [prem[i] for i in en_idx], [hyp[i] for i in en_idx]))) if en_idx else {}
    rev_en = dict(zip(en_idx, run_pairs("nli_english_supplementary", [hyp[i] for i in en_idx], [prem[i] for i in en_idx]))) if en_idx else {}
    for i, m in enumerate(matches):
        fact = facts_by_id[m["fact_id"]]
        base = {**m, "relevant_to_output_type": relevant(fact, m["narrative_field"]), "premise_language": "en",
                "hypothesis_language": m["narrative_field"][-2:]}
        for direction, res, prem_t, hyp_t in (("forward_source_premise", fwd[i], prem[i], hyp[i]), ("reverse_output_premise", rev[i], hyp[i], prem[i])):
            rows.append({**base, "model": MODELS["nli_multilingual"]["repo_id"], "model_revision": MODELS["nli_multilingual"]["revision"],
                         "direction": direction, "premise": prem_t, "hypothesis": hyp_t, **res})
        if i in fwd_en:
            for direction, res, prem_t, hyp_t in (("forward_source_premise", fwd_en[i], prem[i], hyp[i]), ("reverse_output_premise", rev_en[i], hyp[i], prem[i])):
                rows.append({**base, "model": MODELS["nli_english_supplementary"]["repo_id"], "model_revision": MODELS["nli_english_supplementary"]["revision"],
                             "direction": direction, "premise": prem_t, "hypothesis": hyp_t, **res})
    return rows


def sentence_screen(pid, facts, content_sents, k=3):
    """Every generated sentence vs its k most similar source facts (forward NLI)."""
    fe = M.embed([f["statement_en"] for f in facts])
    out = []
    for field, sents in content_sents.items():
        if not sents:
            continue
        se = M.embed([s for _, s in sents])
        sim = se @ fe.T
        P, H, meta = [], [], []
        for j, (sec, s) in enumerate(sents):
            top = np.argsort(-sim[j])[:k]
            for t in top:
                P.append(facts[t]["statement_en"]); H.append(s); meta.append((j, sec, s, facts[t], float(sim[j, t])))
        res = run_pairs("nli_multilingual", P, H)
        best = {}
        for (j, sec, s, fact, c), r in zip(meta, res):
            cur = best.get(j)
            if cur is None or r["entailment"] > cur["entailment"]:
                best[j] = {"patient_id": pid, "narrative_field": field, "section": sec, "sentence": s, "best_premise_fact_id": fact["fact_id"],
                           "best_premise": fact["statement_en"], "best_premise_source_path": fact["source_path"], "retrieval_cosine": round(c, 6),
                           "entailment": r["entailment"], "neutral": r["neutral"], "contradiction": r["contradiction"],
                           "predicted_label": r["predicted_label"], "truncated": r["truncated"], "candidates_considered": k,
                           "max_contradiction_over_candidates": None}
        for (j, sec, s, fact, c), r in zip(meta, res):
            b = best[j]
            b["max_contradiction_over_candidates"] = max(b["max_contradiction_over_candidates"] or 0.0, r["contradiction"])
        out += [best[j] for j in sorted(best)]
    return out
