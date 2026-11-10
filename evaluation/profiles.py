"""Task profiles: which metrics run for each transformation, against what, and what raises a flag.

Thresholds are provisional study settings, not validated clinical cut-offs. Every result
records the profile hash so that a run can be tied to the settings that produced it.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .checks.clinical_facts import TASK_RULES

TASKS = ("anonymization", "summarization", "plain_language")

PROFILES: dict[str, dict[str, Any]] = {
    "anonymization": {
        "label": "Anonymization",
        "source_kind": "professional",      # V9 professional narrative is the default input
        "reference_kind": None,             # the source itself is what must be preserved
        "roles": {
            "pii": "primary", "facts": "primary", "bertscore": "preservation_vs_source",
            "alignscore": "supporting", "embeddings": "optional", "nli": "changed_facts_and_negations",
            "readability": "reported_only",
        },
        "fact_rules": TASK_RULES["anonymization"],
        "nli_directions": ["output_vs_source", "source_vs_output"],
        "flags": {"any_direct_identifier_leak": True, "min_fact_agreement": 1.0,
                  "min_alignscore": 0.5, "max_nli_contradictions": 0, "min_bertscore_f1_vs_source": None},
    },
    "summarization": {
        "label": "Summarization",
        "source_kind": "professional",
        "reference_kind": None,             # no reference summaries exist yet; a supplied one is used if given
        "roles": {
            "pii": "optional", "facts": "primary", "bertscore": "similarity",
            "alignscore": "factual_consistency", "embeddings": "similarity", "nli": "contradictions",
            "readability": "useful",
        },
        "fact_rules": TASK_RULES["summarization"],
        "nli_directions": ["output_vs_source"],
        "flags": {"any_direct_identifier_leak": False, "min_fact_agreement": 1.0,
                  "min_alignscore": 0.5, "max_nli_contradictions": 0, "max_length_ratio": 0.8},
    },
    "plain_language": {
        "label": "Plain language",
        "source_kind": "professional",
        "reference_kind": "patient",        # V9 patient explanation (candidate reference)
        "roles": {
            "pii": "optional", "facts": "primary", "bertscore": "semantic_preservation",
            "alignscore": "factual_consistency", "embeddings": "semantic_and_bilingual_equivalence",
            "nli": "contradictions", "readability": "primary",
        },
        "fact_rules": TASK_RULES["plain_language"],
        "nli_directions": ["output_vs_source"],
        "flags": {"any_direct_identifier_leak": False, "min_fact_agreement": 1.0,
                  "min_alignscore": 0.5, "max_nli_contradictions": 0,
                  # Higher = easier for both scales. 60 is the conventional "plain English" band for
                  # Flesch and the "normal" band for Fernandez-Huerta; both are provisional targets.
                  "readability_target": {"en": 60.0, "es": 60.0},
                  # NLI sentences below this entailment probability are listed as possibly added content.
                  "max_unsupported_entailment": 0.02},
    },
}


def get_profile(task: str) -> dict[str, Any]:
    if task not in PROFILES:
        raise KeyError(f"Unknown task {task!r}; expected one of {TASKS}")
    return copy.deepcopy(PROFILES[task])


def profile_hash(task: str) -> str:
    raw = json.dumps(PROFILES[task], sort_keys=True, ensure_ascii=False, default=sorted).encode()
    return hashlib.sha256(raw).hexdigest()[:16]
