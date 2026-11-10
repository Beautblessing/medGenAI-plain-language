# Workspace 3: Clinical NLP Evaluation

Code: `evaluation_ui.py`, `evaluation/` (engine, generators, checks, metric models, batch, export),
`narrative_eval/` (Prompt 4.1 evaluation code, vendored unchanged). Local state: `runtime_eval/`.

## 1. Purpose and research question

Can a small local language model rewrite a professional clinical narrative as a plain-language
explanation for the patient, in English and Spanish, without losing or changing clinical facts?

Each experiment is one three-way comparison per patient (HC0001–HC0010) and language:

| Column | Text |
| --- | --- |
| Professional narrative (source) | From the signed-off narrative release |
| Generated plain-language output | From a generation method below |
| Reference patient explanation | From the signed-off release, scored the same way as the baseline |

Clinical facts are always checked against the approved version 9 structured record, not against any text.
Anonymization and summarization are out of scope: their code and tests remain, but their tabs are hidden
(shown again only with `CLINICAL_EVAL_ENABLE_ANONYMIZATION=1` / `CLINICAL_EVAL_ENABLE_SUMMARIZATION=1`).

## 2. Inputs

| Input | Where it comes from |
| --- | --- |
| Approved v9 freeze ZIP | `runtime/exports/freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip` or `CLINICAL_EVAL_FREEZE`; SHA-256 checked |
| Narrative package | Uploaded in Upload Evaluation Dataset: the signed-off narrative freeze `reference_narratives/releases/nfreeze_20261110T111810196437Z_4fc96ff2.zip` (includes the HC0005 corrections; use this to reproduce the reported results) or the original release ZIP in `reference_narratives/releases/` |

## 3. Tabs

| Tab | What happens |
| --- | --- |
| Upload Evaluation Dataset | Import a narrative package. Each import is registered as an immutable dataset with its SHA-256 and provenance; a signed-off freeze is recognised as such |
| Explore Clinical Narratives | Choose patient and language: professional narrative and patient explanation side by side, the other language below, the authoritative facts and limitations |
| Plain-Language Adaptation | Choose a method, load the model, run and evaluate one case; the three-way table is shown first |
| Evaluation and Metrics | Full evidence for every run: each metric, flagged sentences, provenance |
| Batch Comparison and Export | Generate and evaluate all 10 patients × EN/ES for a method, compare methods and languages, build the export bundle (CSV/JSON with `SHA256SUMS`) |
| Evaluation Review Queue | Automated findings queued for human review (separate from clinical sign-off) |

## 4. Generation methods

| Method | Details |
| --- | --- |
| Rule baseline | `deterministic_lexical_plain_language_baseline_v3`: clinically weighted sentence selection, fixed headings, abbreviations written out. Byte-identical on every run |
| Qwen2.5-1.5B-Instruct (default) | `Qwen/Qwen2.5-1.5B-Instruct` at revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, Apache-2.0. Prompt `plain_v8_sectionwise_layout`: identifiers removed, the narrative split by section, each section rewritten separately under strict rules, joined under fixed headings. Greedy decoding (`do_sample=False`, `repetition_penalty=1.05`, `seed=0`, up to 400 new tokens) |
| Optional models | Qwen2.5-3B-Instruct (Qwen Research License, non-commercial), Qwen2.5-7B-Instruct (Apache-2.0), Phi-3.5-mini-instruct (MIT), Salamandra-7B-instruct (Apache-2.0). Same prompt; download with `CLINICAL_EVAL_GEN_MODELS=all` |

Prompt options: `CLINICAL_EVAL_PL_STRATEGY=whole` sends the narrative in one prompt;
`CLINICAL_EVAL_ONE_SHOT=1` adds another patient's reference explanation as an example (off by default
because the 1.5B model copied its facts). Device: `CLINICAL_EVAL_DEVICE=auto|cpu|mps|cuda`.

## 5. Measures

| Dimension | Measure | Role |
| --- | --- | --- |
| Readability | Length, sentence length; Flesch Reading Ease and Flesch-Kincaid grade (EN), Fernández-Huerta (ES) | Primary; target 60 for both scales (provisional) |
| Factual consistency | Required structured facts from the v9 record (Prompt 4.1 catalog checks plus dose and frequency rules): supported, omitted, changed | Decides the verdict |
| Factual consistency | NLI of each output sentence against the source (`MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`; `cross-encoder/nli-deberta-base` for English): contradictions and unsupported additions | Flags for review |
| Factual consistency | AlignScore-base, English only (Spanish reported as not applicable) | Flags for review |
| Semantic fidelity | Embedding cosine to the source (`paraphrase-multilingual-mpnet-base-v2`) | Never decides |
| Similarity to reference | BERTScore F1 (`roberta-large` EN, `xlm-roberta-large` ES) and embedding cosine to the reference explanation | Never decides |

Verdicts: **fail** (a critical problem such as a changed dose, a suspected diagnosis stated as confirmed,
or a drug not in the record), **needs human review**, or **passed automated checks**. None of them is a
clinical approval. Thresholds are in `evaluation/profiles.py`. A metric whose model is missing is shown
as unavailable, never as a score.

## 6. Provenance and outputs (`runtime_eval/`)

| File | Content |
| --- | --- |
| `dataset_registry.json`, `datasets/<id>/` | Imported packages with original ZIP and SHA-256 |
| `transformations/transformation_<id>.json` | Every generation, including failures, with source and output SHA-256, method, model revision, prompt version, device |
| `evaluation_runs.jsonl` | Append-only runs; `metric_provenance` (model versions), `narrative_freeze`, `reference_text_versions` |
| `review_queue_items.jsonl` | Evaluation review queue |
| `exports/` | CSV/JSON results and export bundles with `SHA256SUMS` |

The rule baseline, fact checks and readability are byte-identical across runs. Qwen output is
reproducible on the same hardware and library versions; model-based metric values can differ slightly on
other hardware.
