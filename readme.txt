MEDGENAI 4.1 — REPRODUCIBILITY GUIDE
====================================

Submission snapshot: 10 November 2026
Project: Development and Comparative Evaluation of Bilingual Plain-Language
Clinical Narrative Generation with Human Oversight

This repository contains a local Gradio research prototype with three separate
workspaces:

1. Clinical Reference Sign-off and Freeze
2. Narrative Release Review, Sign-off, Freeze and Download
3. Clinical NLP Evaluation

The software does not make clinical judgments, verify reviewer credentials or
turn automated scores into clinical approval. All 10 cases are fictional and
synthetic. Do not upload real patient data.


1. PUBLICATION BOUNDARY
-----------------------

Included:

- application, evaluation and test source;
- clinical_records_reference.json: the shareable reviewed 10-case fixture;
- inputs/clinical_records_reference_reviewed_v1.2.1.json: identical copy used
  by the application;
- matching JSON Schema, QA register, QA resolution log and report;
- distributable Dataset 2i / Prompt 4.1-derived narrative candidate and its
  full generation/provenance ZIP;
- prompts, model configuration, calibration fixtures and dependency files;
- vendored AlignScore source and upstream license, but not its checkpoint;
- the two frozen references used by every reported run, published with the
  project owner's approval (they carry reviewer initials only):
    runtime/exports/freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip
      approved clinical reference, version 9 (Workspace 1)
    reference_narratives/releases/nfreeze_20261110T111810196437Z_4fc96ff2.zip
      signed-off narrative release, 40/40 texts, incl. 2 HC0005 corrections
      (Workspace 2), SHA-256
      07e30bfc1545ea71ed90e21303f3a5f4e8e96837ec3e3ad5453b97f211ad1eaf

Excluded by .gitignore:

- everything else in runtime/, and all of runtime_eval/ and
  runtime_narrative_review/;
- local audit logs, review decision ledgers, approvals, corrections, registries,
  upload history, provisional freezes and exports;
- virtual environments, credentials, caches and downloaded model weights;
- local backups and scratch analyses.

Identities of the approved clinical-reference v9 freeze:

- dataset file SHA-256:
  710fdb5e4373f5d16ccbf64ddc6b3207b16ebefc0cdaf5ac9b1820e1a9c7806a
- canonical JSON SHA-256:
  08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e
- approved freeze ZIP SHA-256:
  11643597900d00351770f44f358264dab6c181ba3c67cc7811b7cbc26e4d8f0f

The public fixture is byte-identical to the approved v9 dataset. The approval
evidence is the freeze ZIP above, checked by its SHA-256.


2. CLONE AND PYTHON SETUP
-------------------------

Public repository: https://github.com/Beautblessing/medGenAI-plain-language

macOS/Linux:

  git clone https://github.com/Beautblessing/medGenAI-plain-language.git
  cd medGenAI-plain-language
  python3.12 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements-lock.txt

Windows PowerShell:

  git clone https://github.com/Beautblessing/medGenAI-plain-language.git
  cd medGenAI-plain-language
  py -3.12 -m venv .venv
  .venv\Scripts\Activate.ps1
  python -m pip install --upgrade pip
  python -m pip install -r requirements-lock.txt

Tested publication environment: macOS, Python 3.12.7. The exact package set is
in requirements-lock.txt. requirements.txt plus requirements-eval.txt gives
compatible ranges but is not the exact recorded environment.


3. VERIFY THE INCLUDED DATA
---------------------------

From the repository root:

  shasum -a 256 -c PUBLIC_DATA_SHA256SUMS

On Linux, use:

  sha256sum -c PUBLIC_DATA_SHA256SUMS

Then validate the schema and deterministic semantic checks:

  python inputs/validate_schema_v1.2.1.py clinical_records_reference.json

Expected final lines:

  Uploaded reference structural errors: 0
  Additional deterministic issues: 0

The file inputs/clinical_records_reference.json is retained only as the
pre-review source used by the QA history. It follows an earlier flat structure
and must not be substituted for the reviewed v1.2.1 fixture.

Public dataset fields include fictional names and addresses, SYN-ID/SYN-PHONE
values and example.invalid e-mail addresses. They are deliberate test fields,
not personal data. The QA register documents the remaining source uncertainties
(including HC0005 and HC0010) without inventing missing facts.


4. MODEL ACQUISITION, REVISIONS AND LICENSES
--------------------------------------------

No API key is needed. With network access, download the pinned resources once:

  python setup_eval_models.py

The helper obtains exact pinned revisions for the semantic and NLI models from
narrative_eval/config.py, the BERTScore backbones, the default local Qwen model,
NLTK resources and en_core_web_sm. Model IDs/revisions and recorded library
versions are in narrative_eval/hf_model_config_v9.json and
evaluation/generators.py.

Default generation checkpoint:

- Qwen/Qwen2.5-1.5B-Instruct
- revision 989aa7980e4cf806f80c7fef2b1adb7bc71aa306
- license: Apache-2.0 as declared by the model publisher

Optional generation models are downloaded only when requested:

  CLINICAL_EVAL_GEN_MODELS=all python setup_eval_models.py

Check each publisher's model card before redistribution. The application lists
the configured Qwen 3B, Qwen 7B, Phi-3.5 and Salamandra alternatives; no claim
is made that every optional model was used for the submitted results.

AlignScore requires a separate upstream checkpoint (not in Git):

  mkdir -p AlignScore/checkpoints
  curl -L -o AlignScore/checkpoints/alignscore-base.ckpt \
    https://huggingface.co/yzha/AlignScore/resolve/main/AlignScore-base.ckpt
  shasum -a 256 AlignScore/checkpoints/alignscore-base.ckpt

Expected SHA-256:

  6aedb637f0596ab29baef91e94466a57f032e02feea654978518919fe0981607

AlignScore source is vendored under its MIT license. BERTScore uses
roberta-large for English and xlm-roberta-large for Spanish. Model scores may
vary slightly across hardware/library builds and do not establish correctness.

After downloads, force offline operation:

  export HF_HUB_OFFLINE=1
  export TRANSFORMERS_OFFLINE=1


5. RUN THE APPLICATION
----------------------

  HF_HUB_OFFLINE=1 python app.py

Open http://127.0.0.1:7860. The code does not enable a public Gradio share link.

A public clone starts with empty, local, gitignored runtime folders. To recreate
the reference lifecycle in Workspace 1, upload these three files:

1. clinical_records_reference.json
2. inputs/clinical_records_schema_v1.2.1.json
3. inputs/clinical_reference_qa_issues.json

Technical validation and activation do not constitute clinical approval. Human
issue decisions, clinical approval and freeze creation must be performed by an
authorized reviewer; never fabricate them to make a test pass.

The historical approved v9 freeze that every reported run uses is already in
place after cloning:

  runtime/exports/freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip

Workspace 3 loads it from that path (or from CLINICAL_EVAL_FREEZE, if set) and
refuses to run unless its SHA-256 matches the value in section 1. No other
runtime file is needed. The public clone never substitutes a fixture for it.


6. NARRATIVE APPROVAL AND FREEZE
--------------------------------

The implemented Gradio system has an actual release-level Narrative Approval
and Freeze action; it is not limited to storing individual review decisions.

- Narrative Review records an exact-text decision for each of 40 current texts
  (10 cases x 2 languages x professional/patient text).
- Needs revision blocks coverage until a corrected text receives a fresh
  exact-hash decision.
- Sign-off Status is complete only when all current texts satisfy the selected
  acceptance policy.
- Create freeze consumes that completed sign-off, binds the decision IDs,
  reviewer evidence, source release hashes and current text hashes, and writes
  a new immutable ZIP plus registry entry.
- Existing freezes are never overwritten. An unchanged signed-off release
  reuses the verified freeze instead of creating a duplicate.
- The evaluation importer verifies a narrative freeze and records which freeze
  each run used.

Implementation: narrative_freeze.py and narrative_review_ui.py.
Tests: tests_eval/test_narrative_review.py and
tests_eval/test_dataset_registry.py.

The signed-off narrative freeze used for the reported results is included:

  reference_narratives/releases/nfreeze_20261110T111810196437Z_4fc96ff2.zip

All 40 texts were decided on 9-10 November 2026 (23 Approved, 17 Approved with
comments); the HC0005 patient explanations (EN and ES) were corrected and
re-approved. A public clone has an empty runtime_narrative_review/, so it can
inspect the workspace and make its own decisions, but the historical decision
ledger itself is not published; the freeze ZIP carries the evidence.


7. IMPORT AND VIEW THE DISTRIBUTABLE NARRATIVES
-----------------------------------------------

Included candidate:

  reference_narratives/releases/
    clinical_narratives_track_a_prompt4_1_v9_claude_candidate.zip

Archive SHA-256:

  374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120

This is Dataset 2i, release ID evalds_374910b82d732c4f. It contains 40 bilingual
texts, generation manifest, QA, prompts, source-fidelity evidence, CSVs and
verification scripts. It is a candidate/provenance artifact, not public human
sign-off evidence. The separately named canonical Prompt 4.1 and Prompt 4.2
archives were not found in this project and are not claimed as included.

To reproduce the reported results, import the SIGNED-OFF FREEZE instead:
in Workspace 3, choose Upload Evaluation Dataset and upload

  reference_narratives/releases/nfreeze_20261110T111810196437Z_4fc96ff2.zip

It is recognised as a signed-off freeze (dataset id
evalds_freeze_07e30bfc1545ea71, validation Passed) and its approved texts,
including both HC0005 corrections, become the reference. Importing the original
candidate ZIP above instead uses the uncorrected HC0005 texts.

Then use Explore Clinical Narratives to view professional and patient-facing
EN/ES text side by side with authoritative facts and limitations.


8. EXPERIMENTS
--------------

Submitted scope:

- professional narrative (source);
- generated plain-language adaptation;
- included patient explanation (reference baseline);
- 10 fictional cases in English and Spanish.

Plain-language methods:

- deterministic rule baseline v3;
- local Qwen2.5-1.5B-Instruct (revision 989aa798...) with prompt
  plain_v8_sectionwise_layout (evaluation/generators.py: the narrative is split
  by section, each section is rewritten separately and joined under fixed
  headings; earlier wording in prompts/plain_language_prompt_v7.txt) and greedy
  decoding (do_sample=False, repetition_penalty=1.05, seed=0).
- optional: Qwen2.5-3B/7B-Instruct, Phi-3.5-mini-instruct, Salamandra-7B-instruct
  (same prompt; see section 4).

Workspace 3 tabs, in order: Upload Evaluation Dataset, Explore Clinical
Narratives, Plain-Language Adaptation, Evaluation and Metrics, Batch Comparison
and Export, Evaluation Review Queue. In Plain-Language Adaptation choose the
method, click Load selected model, then run and evaluate; the three-way table
(professional narrative | generated output | reference patient explanation) is
shown first. Tick "Include locally available model-based metrics" for NLI,
AlignScore, BERTScore and embeddings.

The application removes known synthetic identifiers before adaptation. This is
test-fixture preprocessing, not validated de-identification for real data.

Anonymization and summarization are explicitly outside the submitted experiment
scope because no approved reference summaries or real-PHI validation set exists.
Their retained code can be exposed for inspection only:

  CLINICAL_EVAL_ENABLE_ANONYMIZATION=1 \
  CLINICAL_EVAL_ENABLE_SUMMARIZATION=1 \
  HF_HUB_OFFLINE=1 python app.py

Deterministic baseline outputs, structured checks and readability calculations
are reproducible from fixed inputs. Qwen uses greedy decoding and a pinned
revision, but exact floating-point results can still depend on device and
library versions. Every run records model and metric provenance.


9. METRICS AND INTERPRETATION
-----------------------------

The evaluation engine reports structured lexical fact checks, readability,
embedding similarity, BERTScore, multilingual NLI and AlignScore when their
local resources are available.

- English AlignScore is source-to-output consistency only.
- Spanish AlignScore is deliberately not applicable.
- BERTScore is similarity, not clinical correctness.
- Multilingual NLI is supplementary and can miss negation/frequency changes.
- Structured fact checks are lexical and require human review.
- Missing models are reported unavailable; no score is invented.

Real offline smoke check executed 10 November 2026 on one English paraphrase:

- English BERTScore: status ok, P 0.9353, R 0.9332, F1 0.9344
  (roberta-large, baseline-rescaled).
- English AlignScore: status ok, 0.9934; checkpoint load report 0 missing keys,
  1 unexpected key.

These values prove only that the local offline metric paths executed. They are
not experiment results and must not be cited as clinical performance. The full
test suite exercised the locally cached multilingual NLI paths; none were
reported as human approval.


10. BATCH EXPORTS
-----------------

Workspace 3 -> Batch Comparison and Export generates the selected method for
all 10 cases and both languages. Build export bundle creates CSV/JSON results,
provenance and SHA256SUMS. Use the public option to pseudonymize reviewer fields.
Review evaluation findings in the separate Evaluation Review Queue. Evaluation
review never changes clinical-reference or narrative approval.


11. TESTS REPRODUCED ON 10 NOVEMBER 2026
----------------------------------------

Always isolate test state from local governance state:

  review_tmp="$(mktemp -d)"
  CLINICAL_REVIEW_RUNTIME="$review_tmp" \
    python -m pytest -q test_*.py

The whole suite can also be run in one command:

  CLINICAL_REVIEW_RUNTIME="$(mktemp -d)" \
  CLINICAL_EVAL_RUNTIME="$(mktemp -d)" \
  CLINICAL_NARRATIVE_REVIEW_RUNTIME="$(mktemp -d)" \
  HF_HUB_OFFLINE=1 python -m pytest -q test_*.py tests_eval -k "not local_model"

Latest recorded result (10 November 2026, after the final code changes):
122 passed, 2 skipped, 1 deselected. The 2 skips are NLI tests that need the
pinned NLI models; the deselected test (local_model) checks Qwen output
reproducibility and needs the downloaded Qwen model; run it separately with
  python -m pytest -q tests_eval -k local_model

The publication audit also performs a clean-clone test after the Git commit.
Its exact result is recorded in PUBLICATION_AUDIT.md; do not infer a result if
that section says pending.


12. LIMITATIONS
---------------

- Ten synthetic cases cannot establish general clinical performance.
- Human reviewer credentials are self-declared and not verified by the app.
- Public data inclusion is not public approval evidence.
- Similarity/NLI metrics cannot establish factual or clinical correctness.
- Source uncertainties remain documented; missing facts were not invented.
- The code is a research prototype, not a medical device or clinical service.
- No top-level project/data license was selected during this audit. Public
  visibility alone does not grant reuse rights; third-party licenses still
  apply. Add an owner-approved license before inviting reuse.

Detailed documentation of each workspace, the dataset and the results is in
docs/ (01_clinical_reference_signoff_and_freeze.md,
02_narrative_release_review_and_signoff.md, 03_clinical_nlp_evaluation.md,
04_dataset_and_reference_narratives.md, 05_results_and_limitations.md).

See PUBLICATION_AUDIT.md for the audited inventory and
repository_submission.md for the submission URL/status.
