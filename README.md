# Bilingual Plain-Language Clinical Narrative Generation with Human Oversight

A local Gradio research prototype that tests whether small local language models and a deterministic
baseline can rewrite professional English and Spanish clinical narratives as plain-language
explanations for patients without changing clinical facts. All data is synthetic and fictional.

**To replicate the experiments, follow [`readme.txt`](readme.txt) step by step.**

## Three workspaces

| Workspace | Input | Output | Docs |
| --- | --- | --- | --- |
| 1. Clinical Reference Sign-off and Freeze | Reference dataset + JSON Schema + QA register | Approved, frozen reference (version 9) | [docs/01](docs/01_clinical_reference_signoff_and_freeze.md) |
| 2. Narrative Release Review and Sign-off | 40 reference narratives (10 patients × EN/ES × professional / patient) | Signed-off, frozen narrative release | [docs/02](docs/02_narrative_release_review_and_signoff.md) |
| 3. Clinical NLP Evaluation | Professional narrative, generated output, reference explanation | Readability, factual consistency and semantic fidelity results | [docs/03](docs/03_clinical_nlp_evaluation.md) |

Workspace 1 is the only source of clinical facts. Workspaces 2 and 3 read it and never change it.

## Data

The dataset is our own and is included: see [docs/04](docs/04_dataset_and_reference_narratives.md)
and `PUBLIC_DATA_SHA256SUMS`. Results and limitations: [docs/05](docs/05_results_and_limitations.md).

## Quick start

```
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python setup_eval_models.py            # once, downloads the pinned models
HF_HUB_OFFLINE=1 python app.py         # http://127.0.0.1:7860
```

Model weights and the AlignScore checkpoint are not stored in the repository; `readme.txt` gives the
download and checksum steps. This is a research prototype, not a medical device.
