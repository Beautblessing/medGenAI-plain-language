"""Hugging Face model loading with pinned revisions and deterministic CPU settings.

Models are loaded from the local HF cache at the exact commit SHA in config.MODELS
(local_files_only=True, so a missing download raises instead of silently fetching
another revision). Weights are never copied into the package.
"""
import importlib.metadata as md
import os
import time
from .config import MODELS

_CACHE = {}


def _det():
    import torch
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(int(os.environ.get("NARRATIVE_EVAL_THREADS", "4")))
    return torch


def lib_versions():
    out = {}
    for p in ["torch", "transformers", "sentence-transformers", "huggingface-hub", "tokenizers", "safetensors", "numpy"]:
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            out[p] = None
    return out


REQUIRED = {"semantic": ["config.json", "model.safetensors", "tokenizer.json", "modules.json", "1_Pooling"],
            "nli_multilingual": ["config.json", "model.safetensors", "tokenizer_config.json", "spm.model"],
            "nli_english_supplementary": ["config.json", "model.safetensors", "tokenizer_config.json", "vocab.json"]}


def snapshot_path(key):
    """Path of the pinned snapshot in the local HF cache (HF_HOME or ~/.cache/huggingface).
    Only the files needed for inference were downloaded (allow_patterns), so the snapshot is
    resolved directly by revision and checked for the required files."""
    from huggingface_hub.constants import HF_HUB_CACHE
    m = MODELS[key]
    p = os.path.join(HF_HUB_CACHE, "models--" + m["repo_id"].replace("/", "--"), "snapshots", m["revision"])
    missing = [f for f in REQUIRED[key] if not os.path.exists(os.path.join(p, f))]
    if missing:
        raise FileNotFoundError(f"{m['repo_id']}@{m['revision']}: missing {missing} in {p}; run download_models.py")
    return p


def load_embedder():
    if "emb" in _CACHE:
        return _CACHE["emb"]
    _det()
    from sentence_transformers import SentenceTransformer
    t = time.time()
    m = SentenceTransformer(snapshot_path("semantic"), device="cpu")
    m.eval()
    _CACHE["emb"] = m
    _CACHE["emb_load_s"] = round(time.time() - t, 2)
    return m


def load_nli(key):
    if key in _CACHE:
        return _CACHE[key]
    torch = _det()
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    p = snapshot_path(key)
    tok = AutoTokenizer.from_pretrained(p)
    mod = AutoModelForSequenceClassification.from_pretrained(p, dtype=torch.float32)
    mod.eval()
    labels = {int(k): v.lower() for k, v in mod.config.id2label.items()}
    _CACHE[key] = (tok, mod, labels)
    return _CACHE[key]


def embed(texts, batch_size=32):
    import torch
    m = load_embedder()
    with torch.inference_mode():
        return m.encode(list(texts), batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)


def token_lengths_embedder(texts):
    m = load_embedder()
    tok = m.tokenizer
    return [len(tok(t, add_special_tokens=True)["input_ids"]) for t in texts]


def nli(key, premises, hypotheses, max_length=512, batch_size=16):
    """Return list of dicts with entailment/neutral/contradiction probabilities,
    predicted label (argmax, no threshold) and whether truncation occurred."""
    import torch
    tok, mod, labels = load_nli(key)
    out = []
    for i in range(0, len(premises), batch_size):
        P, Hh = premises[i:i + batch_size], hypotheses[i:i + batch_size]
        full = tok(P, Hh, add_special_tokens=True, truncation=False)["input_ids"]
        enc = tok(P, Hh, return_tensors="pt", padding=True, truncation="only_first", max_length=max_length)
        with torch.inference_mode():
            probs = torch.softmax(mod(**enc).logits.float(), dim=-1).cpu().numpy()
        for j, pr in enumerate(probs):
            d = {labels[k]: round(float(pr[k]), 6) for k in range(len(pr))}
            out.append({"entailment": d["entailment"], "neutral": d["neutral"], "contradiction": d["contradiction"],
                        "predicted_label": max(d, key=d.get), "input_tokens": len(full[j]), "truncated": len(full[j]) > max_length})
    return out


def model_config_record():
    """Configuration actually used, for the package (no weights)."""
    import json
    rec = {"library_versions": lib_versions(), "device": "cpu", "precision": "float32",
           "determinism": "torch.manual_seed(0); torch.use_deterministic_algorithms(True); fixed thread count; eval mode; inference_mode; no sampling",
           "models": {}}
    for key, m in MODELS.items():
        r = dict(m)
        try:
            p = snapshot_path(key)
            cfg = json.load(open(os.path.join(p, "config.json"), encoding="utf-8"))
            r.update({"resolved_commit_sha": os.path.basename(p), "tokenizer_revision": os.path.basename(p) + " (tokenizer files loaded from the same pinned snapshot)",
                      "files": sorted(os.listdir(p)), "id2label": cfg.get("id2label"), "loading_error": None})
        except Exception as e:  # report, never hide
            r.update({"resolved_commit_sha": None, "loading_error": repr(e)})
        rec["models"][key] = r
    rec["models"]["semantic"].update({"max_seq_length": 128, "pooling": "mean pooling (model's 1_Pooling config), L2-normalised embeddings",
                                       "unit_embedding": "mean of L2-normalised sentence embeddings, re-normalised; sentences > 128 tokens are truncated by the model and counted",
                                       "batch_size": 32, "similarity": "cosine"})
    for k in ("nli_multilingual", "nli_english_supplementary"):
        rec["models"][k].update({"max_sequence_length": 512, "truncation": "only_first (premise) if pair exceeds 512 tokens; recorded per pair",
                                 "batch_size": 16, "padding": "dynamic per batch", "decision_rule": "argmax over softmax probabilities; no tuned thresholds"})
    rec["models"]["nli_english_supplementary"]["scope"] = "English texts only; results kept separate from the multilingual model"
    rec["thresholds"] = "None. No acceptance threshold was established; no reviewed examples exist to calibrate one. Scores are reported raw."
    return rec
