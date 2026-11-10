# Public Repository Audit

Audit date: 10 November 2026
Project path: `~/Documents/medGenAI_4_1`

## Scope and outcome

The project is suitable for a public source repository after excluding local runtime state and large downloaded model files. The included clinical records are explicitly synthetic and fictional. No API key or paid service is required.

## Included

- Gradio source code and evaluation modules.
- Clinical-reference and evaluation tests.
- Exact dependency files and model-download helper.
- Fictional 10-case English/Spanish source dataset.
- Reviewed version 9 dataset.
- Authoritative JSON Schema and QA register.
- QA resolution log and report.
- Dataset 2i narrative candidate and its generation package.
- Prompt, model, metric and calibration configuration.
- AlignScore source and its upstream MIT license, without the checkpoint.
- Reproduction and implementation documentation.

## Excluded by `.gitignore`

- `.venv/`, caches, bytecode and test caches.
- `AlignScore/checkpoints/alignscore-base.ckpt` and all downloaded model weights.
- `.env`, keys, certificates and credential files.
- `.backup_*/` and local backups.
- `runtime/`, `runtime_eval/`, and `runtime_narrative_review/`.
- Local audit trails, reviewer identities, review decisions, approvals, freeze registries and generated exports.
- Gradio temporary and flagging output.

## Data and identity findings

- The dataset contains fictional names, synthetic identifiers (`SYN-ID-*`), invalid/example contact information and imaginary addresses. It contains no real patient data.
- Reviewed dataset physical SHA-256: `710fdb5e4373f5d16ccbf64ddc6b3207b16ebefc0cdaf5ac9b1820e1a9c7806a`.
- Reviewed dataset canonical JSON SHA-256: `08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e`.
- Dataset 2i release ID: `evalds_374910b82d732c4f`.
- Dataset 2i archive SHA-256: `374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120`.
- Dataset 2i is not the canonical Prompt 4.1 archive `a77753…` and is not Prompt 4.2 `66d46d…`; neither of those archives was found in this project.
- Update 10 Nov 2026 (owner decision): the approved clinical-reference freeze (SHA-256 `11643597…8f0f`, reviewer initials "BJ") and the signed-off narrative freeze `nfreeze_20261110T111810196437Z_4fc96ff2.zip` (SHA-256 `07e30bfc…1eaf`, initials "T. J.", "B. J.") are now published so a clone can reproduce the evaluation. All other runtime records remain excluded.

## Secret scan

No embedded private key or token pattern was found outside excluded runtime, environment and cache folders. References to `api_key` in vendored AlignScore code are function parameters, not credentials.

## Reproducibility boundary

A public clone can initialize the Clinical Reference workspace from the included synthetic files and can inspect or test the source and deterministic evaluation code. Exact governed evaluation through the Gradio Evaluation workspace uses the published approved freeze at the path and checksum documented in `readme.txt`. This requirement is explicit; the project does not fabricate or silently substitute approval evidence.

## Narrative approval/freeze verification

The current implementation is release-level, not decision-storage-only. `NarrativeApprovalFreeze.consume_signoff()` requires complete exact-text coverage, binds the current text hashes and decision IDs, and `create_freeze()` writes an immutable verified package without overwriting prior freezes. The Gradio `Create freeze` action is connected to this lifecycle, and the evaluation importer records the narrative freeze identity used by a run. Local reviewer-bearing freeze artifacts were hash-checked but remain excluded from Git.

## Reproducibility corrections made during this audit

- Restored the actual v1.2.1 schema used by approved version 9 (`9967d8d…dbc`) to `inputs/clinical_records_schema_v1.2.1.json`; the prior public copy still named the older `HC001` identifiers.
- Corrected `inputs/validate_schema_v1.2.1.py` to open the real schema filename and build `HC0001`–`HC0010` fixtures.
- Added root `clinical_records_reference.json`, byte-identical to the reviewed v1.2.1/approved-v9 dataset, while retaining the pre-review flat source under `inputs/` for provenance.
- Replaced machine-specific instructions and stale test counts with clone-safe, actually executed commands in `readme.txt`.

Schema/semantic validation now reports 0 structural errors and 0 additional deterministic issues for the root reference fixture.

## Tests executed on 10 November 2026

- Clinical lifecycle: **53 passed** in an isolated temporary runtime.
- Narrative lifecycle and dataset-registry targets: **22 passed** in isolated temporary runtimes.
- Offline evaluation regression with two unavailable local-NLI assertions explicitly deselected: **70 passed, 2 deselected**. The unfiltered run had two model-dependent failures because cached NLI resources were detected but could not be loaded by the installed local stack in offline mode.
- A separately recorded English metric path smoke check reported BERTScore `status=ok`, F1 `0.9344`, and AlignScore `status=ok`, score `0.9934`, with 0 missing and 1 unexpected checkpoint key. These are smoke-path observations, not experiment or clinical-performance results.

Clean-clone verification is recorded below after the initial commit.

**Clean-clone result:** passed. A local clone of commit `9f42682` contained no runtime trees, verified every entry in `PUBLIC_DATA_SHA256SUMS`, reported zero structural and zero deterministic issues for the public reference, passed all 53 clinical-reference tests in a temporary runtime, imported the bundled Dataset 2i candidate successfully, and built the Gradio `Blocks` application without the protected production runtime.

## Licensing note

The repository contains third-party AlignScore code with its own MIT license. Model licenses are shown in the application configuration and `readme.txt`. No new top-level project license was selected during this audit; repository publication does not imply permissions beyond applicable copyright and third-party licenses.
