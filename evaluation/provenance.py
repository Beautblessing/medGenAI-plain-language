"""Metric and environment provenance attached to every stored evaluation run."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

PACKAGES = ("gradio", "jsonschema", "torch", "transformers", "sentence-transformers", "huggingface-hub",
            "bert-score", "textstat", "pyphen", "nltk", "pytorch-lightning", "spacy")


@lru_cache(maxsize=1)
def environment() -> dict[str, Any]:
    versions = {}
    for p in PACKAGES:
        try:
            versions[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            versions[p] = None
    return {"python": sys.version.split()[0], "platform": platform.platform(), "packages": versions}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def alignscore_checkpoint(cache_dir: str | os.PathLike | None = None) -> dict[str, Any]:
    """SHA-256 of the AlignScore checkpoint, cached by size and mtime (the file is ~2 GB)."""
    from .models import MODEL_CONFIG
    path = Path(MODEL_CONFIG["alignscore"]["ckpt_path"])
    if not path.is_file():
        return {"path": str(path), "status": "missing"}
    st = path.stat()
    key = f"{st.st_size}:{int(st.st_mtime)}"
    cache = Path(cache_dir) / "alignscore_checkpoint_sha256.json" if cache_dir else None
    if cache and cache.exists():
        try:
            data = json.loads(cache.read_text())
            if data.get("key") == key:
                return {"path": path.name, "sha256": data["sha256"], "bytes": st.st_size, "status": "ok"}
        except (OSError, json.JSONDecodeError):
            pass
    digest = _file_sha256(path)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "sha256": digest}))
    return {"path": path.name, "sha256": digest, "bytes": st.st_size, "status": "ok"}


def metric_configuration(cache_dir: str | os.PathLike | None = None, *, hash_checkpoint: bool = True) -> dict[str, Any]:
    from narrative_eval.config import MODELS
    from .models import MODEL_CONFIG
    cfg = json.loads(json.dumps(MODEL_CONFIG, default=str))
    cfg["alignscore"]["ckpt_path"] = os.path.basename(cfg["alignscore"]["ckpt_path"])
    if hash_checkpoint:
        cfg["alignscore"]["checkpoint"] = alignscore_checkpoint(cache_dir)
    cfg["pinned_hf_models"] = {k: {"repo_id": v["repo_id"], "revision": v["revision"]} for k, v in MODELS.items()}
    return cfg


def stamp(run: dict[str, Any], cache_dir: str | os.PathLike | None = None, *, hash_checkpoint: bool = True) -> dict[str, Any]:
    """Add environment and metric configuration to a run record (in place) and return it."""
    run["metric_provenance"] = {"environment": environment(),
                                "metric_configuration": metric_configuration(cache_dir, hash_checkpoint=hash_checkpoint)}
    return run
