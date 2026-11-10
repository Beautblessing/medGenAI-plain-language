"""One-time download of every model and language resource the evaluation engine uses.

Run once with network access:   python setup_eval_models.py
Afterwards the app can run offline:   HF_HUB_OFFLINE=1 python app.py
Pinned NLI/embedding revisions come from narrative_eval.config.MODELS (Prompt 4.1).
The AlignScore checkpoint is already in AlignScore/checkpoints/ and is not downloaded.
"""
import json
import subprocess
import sys

import nltk
from huggingface_hub import model_info, snapshot_download

from narrative_eval.config import MODELS
from evaluation.models import MODEL_CONFIG

PATTERNS = {"semantic": ["*.json", "*.txt", "model.safetensors", "sentencepiece.bpe.model", "1_Pooling/*"],
            "nli_multilingual": ["*.json", "model.safetensors", "spm.model"],
            "nli_english_supplementary": ["*.json", "*.txt", "model.safetensors"]}

out = {}
for key, m in MODELS.items():                      # exact Prompt 4.1 revisions
    info = model_info(m["repo_id"], revision=m["revision"])
    assert info.sha == m["revision"], (info.sha, m["revision"])
    out[key] = snapshot_download(m["repo_id"], revision=m["revision"], allow_patterns=PATTERNS[key])
for name in sorted(set(MODEL_CONFIG["bertscore"]["model_type"].values()) | {MODEL_CONFIG["alignscore"]["base_model"]}):
    out[name] = snapshot_download(name, allow_patterns=["*.json", "*.txt", "*.model", "model.safetensors", "merges.txt", "vocab.*"])
# Generation models for plain-language adaptation (evaluation/generators.py). By default only the default model
# (Qwen2.5-1.5B, about 3 GB). The comparison models are 6-15 GB each:
#   CLINICAL_EVAL_GEN_MODELS=all python setup_eval_models.py
#   CLINICAL_EVAL_GEN_MODELS=qwen2.5-3b-instruct,qwen2.5-7b-instruct python setup_eval_models.py
import os
from evaluation.generators import DEFAULT_GENERATION_MODEL, GENERATION_MODELS
wanted = os.environ.get("CLINICAL_EVAL_GEN_MODELS", DEFAULT_GENERATION_MODEL).strip()
keys = list(GENERATION_MODELS) if wanted == "all" else [k.strip() for k in wanted.split(",") if k.strip()]
for key in keys:
    g = GENERATION_MODELS[key]
    out[f"generation:{key}"] = snapshot_download(g["repo_id"], revision=g["revision"],
                                                 allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model",
                                                                 "merges.txt", "vocab.*"])
for pkg in ("cmudict", "punkt", "punkt_tab"):
    nltk.download(pkg, quiet=True)
subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"], check=False)
print(json.dumps(out, indent=1))
print("Done. Run the app with HF_HUB_OFFLINE=1 from now on.")
