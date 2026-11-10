# Prompt 4.1 (Track A, v9): evaluation of three candidate packages and recommendation

Run date: 2026-11-09 (UTC). No generation package was modified. All texts remain candidates pending human clinical review (Gate E not started for any package).

## Recommendation

**Use `374910b8…ce120` as the 4.1 package for the Gradio app.** It is the generation-only package, its 40 texts are the ones the full evaluation was run on (`93c2dc23…`), and the Gradio evaluation engine already uses its JSON as the reference narratives (`evaluation_engine/reference_narratives/SOURCE.md`, file SHA-256 `dd25e318…`). Keep `93c2dc23…` alongside it as its evaluation record.

Do not switch to the fresh run (`a4c66db1…`) now. Its automated results are about the same (slightly more readable patient texts, fewer NLI contradiction flags), but none of the differences decide the choice. Its section headings do not match what the 4.1 evaluator (and therefore the Gradio engine) parses, and it has some real omissions to fix first (listed below). It is still useful as an independently written second set.

**Open point:** you said Prompt 5.0 expects the 4.1 ZIP `a77753f0…`. None of the three packages evaluated here has that SHA-256, and no file in the project folder carries it. Please check which package `a77753f0` is before Prompt 5.0 runs. If it is a fourth version, it has not been evaluated.

## The three candidates (keyed by ZIP SHA-256)

| Key | ZIP SHA-256 | What it is | Narrative texts |
|---|---|---|---|
| 93c2dc23 | `93c2dc239d8d66afc62d6a42a8f8c28090bb721b1d9627a0d9189581d49ba21d` | Combined generation + evaluation package (`clinical_narratives_v9_prompt4_1/`) | Set 1 |
| 374910b8 | `374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120` | Uploaded ZIP 2; same as `clinical_narratives_v9_prompt4_1_generation/` (generation-only repackage) | Set 1, byte-identical `records` |
| a4c66db1 | `a4c66db194bd1c8aef7980bbfbdc6f2eb671863e118c8cd7ecb37f25525944c6` | Uploaded ZIP 1; the fresh independent run (`clinical_narratives_v9_fresh_run/`) | Set 2 |

All three share the same file name, `clinical_narratives_track_a_prompt4_1_v9_candidate.zip`.

## Method

- The same evaluation code was used for every package: the 14 `narrative_eval` modules from the 93c2dc23 package, each checked against that package's SHA256SUMS. The orchestrator was adapted only to read an existing narrative JSON instead of assembling one from drafts (`tools/run_eval_candidate.py`; the original is kept as `tools/run_pipeline_orig.py`). The QA item specific to the earlier run's self-check was dropped.
- Pinned models (same revisions as `hf_model_config_v9.json`): paraphrase-multilingual-mpnet-base-v2 `4328cf26`, mDeBERTa-v3-base-mnli-xnli `8adb042d`, nli-deberta-base `f375a3f8`. CPU, float32, deterministic settings.
- Layers: A readability (Flesch, FKGL, Fernández-Huerta), B semantic similarity, C deterministic fact catalog, D NLI (proposition-level and sentence screen), 21-pair calibration, scorecard, anonymization check.
- **Reproducibility check:** re-running 93c2dc23 reproduced its stored readability, Layer C, semantic, NLI and scorecard CSVs byte for byte. 374910b8 gave byte-identical results to 93c2dc23, as expected from identical texts. numpy (2.5.4 vs 2.5.3) and pandas (3.1.1 vs 3.0.5) differ slightly from the pins without changing any output.

### Heading problem (fresh run)

The 4.1 evaluator recognises sections only by exact heading strings (`narrative_eval/texts.py`, for example `REASON FOR CONSULTATION` and `Why you came`). The fresh run uses title-case clinical headings and different patient headings, so on the unmodified texts every clinical note parsed as one undivided header block. The patient sections "why", "warnings" and "follow-up" were not found, and the run stopped at the semantic summary.

To evaluate it fairly, an evaluation-only copy was made with **only whole heading lines relabelled** (182 lines; `results/a4c66db1_fresh_run_headings_relabelled/heading_relabel_log.json`; body text untouched). The one schema error reported for that copy comes from the evaluation note added to the copy. The delivered fresh JSON validates against its own schema.

## Results

### Structural (Gate C)

All three pass: valid against their own output schema, 10 IDs, 40 non-empty texts, all four CSVs match the JSON exactly, UTF-8 without BOM.

### Readability (means over 10 patients)

| Text | Set 1 (93c2dc23 = 374910b8) | Set 2 (a4c66db1) |
|---|---|---|
| Clinical EN: Flesch / FKGL / words | 35.95 / 11.63 / 376 | 40.45 / 11.12 / 458 |
| Clinical ES: Fernández-Huerta / words | 56.08 / 431 | 53.88 / 509 |
| Patient EN: Flesch / FKGL / words | 71.43 / 7.30 / 359 | **73.28 / 6.64** / 450 |
| Patient ES: Fernández-Huerta / words | 70.25 / 379 | **72.72** / 469 |

Both sets meet the dashboard's plain-language target of 60. Set 2 is a little easier to read but about 25% longer.

### Semantic similarity (mpnet, mean of patient aggregates)

| Comparison | Set 1 | Set 2 |
|---|---|---|
| EN professional vs EN patient | 0.747 | 0.775 |
| ES professional vs ES patient | 0.767 | 0.810 |
| EN vs ES professional | 0.920 | 0.922 |
| EN vs ES patient | 0.901 | 0.907 |
| Source plain-language EN vs patient EN | 0.700 | 0.719 |
| Source plain-language ES vs patient ES | 0.722 | 0.733 |

The two sets are essentially equal on bilingual agreement. Similarity is not a measure of correctness.

### NLI (mDeBERTa unless stated)

| Measure | Set 1 | Set 2 |
|---|---|---|
| Forward (source premise) contradiction flags, 1,770 pairs | 139 | 101 |
| Reverse contradiction flags | 153 | 178 |
| EN nli-deberta forward contradictions, 885 pairs | 71 | 50 |
| Sentence screen: contradiction / sentences | 38 / 996 (3.8%) | 59 / 1,267 (4.7%) |

Contradiction flags are review signals. In Set 2 most sentence-screen flags fall on the identifier header lines (the header is split into short sentences) or on correct statements, and none showed a factual error on inspection. Calibration was identical for both: deterministic 19/21, mDeBERTa 17/21, nli-deberta (EN) 12/14.

### Deterministic fact checks (Layer C)

| | Set 1 | Set 2 (headings relabelled) |
|---|---|---|
| Supported | 986 | 888 |
| Omission | 2 | 104 |
| Requires human review | 10 | 6 |
| Unsupported additions | 0 | 0 |
| Contradictions | 0 | 0 |
| Numbers not in source | 0 | 0 |

**These counts cannot be compared directly.** The catalog regexes were written and then corrected against the Set 1 texts (see `provenance/detector_revision_log_v9.json` in 93c2dc23), so they reward Set 1's wording. Even so, the two Set 1 "omissions" are false positives: metformin 500 mg is present but outside the regex window.

The 104 Set 2 omissions were triaged by hand (`results/fresh_layer_c_manual_triage.json`; this triage was done by the generating model, not a human reviewer):

- **56 are detector wording mismatches.** The fact is present but phrased differently. For example, QA-005 "does not say whether you should stop it", QA-011 "No consta la cantidad", "aún no se ha valorado", and "Days 1 to 7".
- **48 are genuine omissions,** none of them clinical errors:
  - 18: the visit date is missing from patient explanations (deliberate, because patient texts were written without identifiers).
  - 28: no explicit "no appointment has been booked yet" statement, although no booking is claimed.
  - 2: HC0009 patient texts don't say the hoarseness is unchanged since the biopsy.
- 6 review items where a documented negative is worded differently.

Neither set has an unsupported drug, a changed dose, an out-of-source date or number, a suspected diagnosis presented as confirmed, or an invented heparin dose or discharge date.

## Fix before use

**If 374910b8 is used (recommended):**
1. Confirm which package `a77753f0…` is, because Prompt 5.0 expects it (see above).
2. Gate E is still open: the 157 QA items recorded in 93c2dc23 (156 automated, which this rerun reproduces, plus one self-check item from that run) and the clinical/bilingual human review are pending.

**If the fresh run (a4c66db1) is ever used:**
1. Change its section headings to the evaluator vocabulary (the mapping is in `tools/headnorm.py`) and regenerate the JSON/CSVs as a documented correction, keeping the original.
2. Add the visit date and an explicit "not yet booked" status to the patient explanations. Add "hoarseness unchanged since biopsy" to the HC0009 patient texts.
3. Consider writing the identifier header with commas, not full stops, so NLI does not screen identifier fragments as clinical sentences.
4. Its own 15 QA items (for example the HC0004 reliever-urgency conflict between the source's plain-language section and its warning signs) still need review.

**For the evaluator itself (affects any new texts):** section parsing depends on exact heading strings. Set 1's HC0001 patient heading "Why you came to the clinic" is also missed, so that section is dropped from evaluation. The Layer C catalog is tuned to Set 1's wording.

## Files

- `results/<key>/`: full evaluation outputs per package (readability, semantic, NLI, Layer C findings, scorecard, QA register, calibration, anonymization, validation report).
- `results/fresh_layer_c_manual_triage.json`: triage of the fresh run's 104 omissions.
- `tools/`: adapted runner, original orchestrator, heading relabeller, environment versions.
