# Clinical NLP evaluation tabs

Five tabs are added after the governance tabs: **Anonymization**, **Summarization**, **Plain language**,
**Evaluation** and **Batch Results**. Every tab calls one engine (`evaluation/`), which wraps the
Prompt 4.1 evaluation code (`narrative_eval/`, vendored unchanged). No metric is implemented in a UI callback.

## What is measured, and what decides the verdict

| Section | Method | Decides the verdict? |
| --- | --- | --- |
| Agreement with the structured record | Prompt 4.1 Layer C catalog checks + engine dose/frequency-change rules; NLI of each sentence against the record's fact inventory | Yes |
| Identifier checks | Every `patient_information` value, name parts, generic PII patterns; Prompt 4.1 `anonymize.verify` reported alongside | Yes (anonymization: any direct identifier fails) |
| Faithfulness to the input text | AlignScore (English only), multilingual NLI | Flags for review |
| Similarity | BERTScore, multilingual embeddings, EN↔ES equivalence | Never |
| Readability | Flesch/FKGL (EN), Fernández-Huerta (ES), Prompt 4.1 counting rules | Plain language only (target 60) |

Verdicts: **fail** (a critical problem such as a changed dose, a suspected diagnosis called confirmed, a drug
not in the record, or an identifier left in an anonymized text), **needs human review**, or **passed automated
checks**. None of these is a clinical approval. Thresholds live in `evaluation/profiles.py` and are provisional.

Known limits: Layer C is lexical; the Prompt 4.1 calibration found that the multilingual NLI model misses negation
flips and frequency changes; AlignScore is not validated for Spanish and is not run for it; the reference
narratives are candidates pending human review.

## Setup (once)

```bash
source .venv/bin/activate
python -m pip install -r requirements-eval.txt
python setup_eval_models.py          # downloads pinned models (~4 GB) and language resources
```

Then run as before, offline: `HF_HUB_OFFLINE=1 python app.py`.

## Files

- `evaluation/` engine: `engine.py` (single entry point), `profiles.py` (task rules and thresholds),
  `checks/` (facts, PII, readability), `models/` (BERTScore, AlignScore, embeddings, NLI), `batch.py`.
- `narrative_eval/` Prompt 4.1 code, unchanged (`VENDORED.md`, `VENDORED_SHA256SUMS`).
- `evaluation_ui.py` the five Gradio tabs.
- `reference_narratives/` Prompt 4.1 generation-only narratives (candidate references).
- `runtime_eval/` append-only evaluation runs, human-review flags and exports. The governance `runtime/`
  is only read, never written.

## Tests

```bash
CLINICAL_REVIEW_RUNTIME="$(mktemp -d)" python -m pytest -q tests_eval
```
The two NLI tests are skipped until the models are downloaded.
