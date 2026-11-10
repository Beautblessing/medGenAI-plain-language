"""Model-based metrics, loaded lazily and cached per process.

Every function returns a dict with a "status" key:
  "ok"              – scores are present
  "not_applicable"  – the metric is not valid for this language or task (e.g. AlignScore for Spanish)
  "unavailable"     – a package or model is missing; "reason" says which. The app keeps working.
"""
from __future__ import annotations

import os
import re
import threading
from functools import lru_cache
from typing import Any

from narrative_eval import textcount as tm

_LOCK = threading.Lock()
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODEL_CONFIG: dict[str, Any] = {
    "bertscore": {
        "model_type": {"en": "roberta-large", "es": "xlm-roberta-large"},
        "rescale_with_baseline": {"en": True, "es": False},
    },
    "alignscore": {
        "base_model": "roberta-base",
        "ckpt_path": os.environ.get("ALIGNSCORE_CKPT", os.path.join(APP_DIR, "AlignScore", "checkpoints", "alignscore-base.ckpt")),
        "evaluation_mode": "nli_sp",
        "languages": ["en"],
    },
    # Embeddings and NLI use the exact pinned revisions of the Prompt 4.1 package
    # (narrative_eval.config.MODELS), loaded by narrative_eval.models from the local HF cache.
    "nli": {"window_sentences": 3, "facts_per_sentence": 3},
}


def _unavailable(e: BaseException) -> dict[str, Any]:
    return {"status": "unavailable", "reason": f"{type(e).__name__}: {e}"[:400]}


DEVICE_NAMES = {"cuda": "NVIDIA GPU (CUDA)", "mps": "Apple GPU (MPS)", "cpu": "CPU"}


def device() -> str:
    """cuda > mps (Apple Silicon GPU) > cpu. CLINICAL_EVAL_DEVICE=cpu|mps|cuda forces one (auto by default)."""
    forced = os.environ.get("CLINICAL_EVAL_DEVICE", "auto").strip().lower()
    if forced in ("cpu", "mps", "cuda"):
        return forced
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


# ------------------------------------------------------------------ BERTScore
def bertscore(candidate: str, reference: str, language: str) -> dict[str, Any]:
    cfg = MODEL_CONFIG["bertscore"]
    try:
        from bert_score import score
        model_type = cfg["model_type"][language]
        rescale = cfg["rescale_with_baseline"][language]
        with _LOCK:
            p, r, f = score([candidate], [reference], model_type=model_type, lang=language,
                            rescale_with_baseline=rescale, device=device(), verbose=False)
        return {"status": "ok", "precision": round(float(p[0]), 4), "recall": round(float(r[0]), 4),
                "f1": round(float(f[0]), 4), "model": model_type, "rescaled_with_baseline": rescale,
                "note": "Reference-based semantic overlap. It does not establish factual correctness."}
    except Exception as e:  # noqa: BLE001
        return _unavailable(e)


# ------------------------------------------------------------------ AlignScore (English only)
@lru_cache(maxsize=1)
def _alignscore_model():
    import sys
    cfg = MODEL_CONFIG["alignscore"]
    here = os.environ.get("ALIGNSCORE_SRC", os.path.join(APP_DIR, "AlignScore", "src"))
    if here not in sys.path and os.path.isdir(here):
        sys.path.insert(0, here)
    # The local AlignScore checkpoint is a full Lightning checkpoint; torch >= 2.6 refuses it under
    # weights_only loading. It is a trusted local file from the AlignScore authors.
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
    import torch
    import transformers
    if not hasattr(transformers, "AdamW"):
        # AlignScore imports transformers.AdamW, removed in transformers 4.46; it is only used for training.
        transformers.AdamW = torch.optim.AdamW
    from alignscore import AlignScore
    if not os.path.isfile(cfg["ckpt_path"]):
        raise FileNotFoundError(f"AlignScore checkpoint not found: {cfg['ckpt_path']}")
    dev = device() if device() != "mps" else "cpu"
    # AlignScore calls load_from_checkpoint on an instance, which pytorch-lightning >= 2 rejects.
    # Build the network without a checkpoint, then load the trained weights explicitly (strict=False
    # as in the original code) and report how many keys matched.
    import logging
    logging.getLogger().setLevel(logging.ERROR)
    scorer = AlignScore(model=cfg["base_model"], batch_size=8, device=dev, ckpt_path=None,
                        evaluation_mode=cfg["evaluation_mode"], verbose=False)
    state = torch.load(cfg["ckpt_path"], map_location="cpu", weights_only=False)["state_dict"]
    missing, unexpected = scorer.model.model.load_state_dict(state, strict=False)
    if len(missing) > 10:
        raise RuntimeError(f"AlignScore checkpoint did not match the network ({len(missing)} missing keys)")
    scorer.model.model.to(dev).eval()
    scorer._load_report = {"missing_keys": len(missing), "unexpected_keys": len(unexpected)}
    return scorer


def alignscore(context: str, claim: str, language: str) -> dict[str, Any]:
    if language not in MODEL_CONFIG["alignscore"]["languages"]:
        return {"status": "not_applicable",
                "reason": "AlignScore checkpoints are English-oriented and have not been validated for Spanish. "
                          "Use multilingual NLI and structured fact checks for Spanish."}
    try:
        with _LOCK:
            s = _alignscore_model().score(contexts=[context], claims=[claim])
        return {"status": "ok", "score": round(float(s[0]), 4), "checkpoint": os.path.basename(MODEL_CONFIG["alignscore"]["ckpt_path"]),
                "load_report": getattr(_alignscore_model(), "_load_report", None),
                "note": "Source-to-output factual consistency (0-1)."}
    except Exception as e:  # noqa: BLE001
        return _unavailable(e)


# ------------------------------------------------------------------ embeddings and NLI (Prompt 4.1 pinned models)
def _pinned(key: str) -> str:
    from narrative_eval.config import MODELS
    return f"{MODELS[key]['repo_id']}@{MODELS[key]['revision'][:12]}"


def embedding_similarity(a: str, b: str) -> dict[str, Any]:
    """Cosine of mean sentence embeddings (same unit-embedding rule as the Prompt 4.1 Layer B)."""
    try:
        import numpy as np
        from narrative_eval import models as M

        def unit(t):
            s = tm.sentences(t) or [t]
            v = M.embed(s).mean(axis=0)
            return v / (np.linalg.norm(v) or 1.0)
        with _LOCK:
            c = float(unit(a) @ unit(b))
        return {"status": "ok", "cosine": round(c, 4), "model": _pinned("semantic"),
                "note": "Semantic similarity only; does not establish factual correctness."}
    except Exception as e:  # noqa: BLE001
        return _unavailable(e)


def _windows(text: str, n: int) -> list[str]:
    s = tm.sentences(text)
    if not s:
        return [text]
    return [" ".join(s[i:i + n]) for i in range(0, len(s), max(1, n - 1))]


def _is_heading(sent: str) -> bool:
    return not re.search(r"[.!?:]\s*$", sent.strip()) and len(tm.words(sent)) <= 6


def _retrieved_nli(premises: list[str], sentences: list[str], k: int) -> list[dict[str, Any]]:
    """Each sentence vs its k most similar premises (embedding retrieval, as in Prompt 4.1 sentence_screen).
    The label is the argmax of the closest premise; the strongest contradiction among the k is also kept."""
    import numpy as np
    from narrative_eval import models as M
    pe, se = M.embed(premises), M.embed(sentences)
    sim = se @ pe.T
    P, H, meta = [], [], []
    for j, s in enumerate(sentences):
        for t in np.argsort(-sim[j])[:k]:
            P.append(premises[t]); H.append(s); meta.append((j, int(t), float(sim[j, t])))
    res = M.nli("nli_multilingual", P, H)
    rows: dict[int, dict[str, Any]] = {}
    for (j, t, c), r in zip(meta, res):
        cur = rows.get(j)
        if cur is None:   # first = most similar premise: its argmax is the label
            label = {"entailment": "entailed", "contradiction": "contradiction"}.get(r["predicted_label"], "not_entailed")
            rows[j] = {"sentence": sentences[j], "label": label, "closest_premise": premises[t], "retrieval_cosine": round(c, 4),
                       "entailment": round(r["entailment"], 4), "contradiction": round(r["contradiction"], 4),
                       "max_contradiction_over_candidates": round(r["contradiction"], 4), "truncated": r["truncated"]}
        else:
            cur["max_contradiction_over_candidates"] = round(max(cur["max_contradiction_over_candidates"], r["contradiction"]), 4)
    return [rows[j] for j in sorted(rows)]


def nli_check(premise_text: str, hypothesis_text: str, *, direction: str = "output_vs_source") -> dict[str, Any]:
    """Each hypothesis sentence against its most similar windows of the premise text (multilingual mDeBERTa).

    Calibration in the Prompt 4.1 package: this model missed negation flips and a frequency change,
    so a clean NLI result does not clear a text; structured fact checks remain the authority.
    """
    cfg = MODEL_CONFIG["nli"]
    try:
        wins = _windows(premise_text, cfg["window_sentences"])
        sents = [s for s in tm.sentences(hypothesis_text) if len(tm.words(s)) >= 3 and not _is_heading(s)]
        with _LOCK:
            rows = _retrieved_nli(wins, sents, k=2) if sents else []
        n = len(rows) or 1
        return {"status": "ok", "direction": direction, "model": _pinned("nli_multilingual"), "sentences": rows,
                "contradiction_count": sum(r["label"] == "contradiction" for r in rows),
                "entailed_rate": round(sum(r["label"] == "entailed" for r in rows) / n, 4),
                "negation_flips": negation_flips(rows),
                "calibration_note": "Prompt 4.1 calibration: missed negation flips and a frequency change."}
    except Exception as e:  # noqa: BLE001
        return _unavailable(e)


def fact_nli(case_id: str, record: dict[str, Any], output: str) -> dict[str, Any]:
    """NLI against the structured record itself: every output sentence vs its most similar facts
    from the Prompt 4.1 fact inventory (narrative_eval.inventory statements as premises)."""
    try:
        from narrative_eval.inventory import build_inventory
        facts = build_inventory(case_id, record)
        sents = [s for s in tm.sentences(output) if len(tm.words(s)) >= 3 and not _is_heading(s)]
        with _LOCK:
            rows = _retrieved_nli([f["statement_en"] for f in facts], sents,
                                  k=MODEL_CONFIG["nli"]["facts_per_sentence"]) if sents else []
        by_stmt = {f["statement_en"]: f for f in facts}
        for r in rows:
            r["closest_fact_source_path"] = by_stmt[r["closest_premise"]]["source_path"]
        n = len(rows) or 1
        return {"status": "ok", "model": _pinned("nli_multilingual"), "embedder": _pinned("semantic"),
                "facts_in_inventory": len(facts), "sentences": rows,
                "contradiction_count": sum(r["label"] == "contradiction" for r in rows),
                "entailed_rate": round(sum(r["label"] == "entailed" for r in rows) / n, 4),
                "note": "'not_entailed' is not a hallucination verdict; a sentence often combines several facts."}
    except Exception as e:  # noqa: BLE001
        return _unavailable(e)


_NEG = re.compile(r"\b(no|not|never|without|denies|negative|sin|nunca|niega|negativ[oa])\b", re.I)


def negation_flips(rows: list[dict[str, Any]]) -> list[str]:
    """Contradicting sentences whose negation polarity differs from the closest premise (heuristic)."""
    out = []
    for r in rows:
        if r["label"] == "contradiction" and r.get("closest_premise"):
            if bool(_NEG.search(r["sentence"])) != bool(_NEG.search(r["closest_premise"])):
                out.append(r["sentence"])
    return out
