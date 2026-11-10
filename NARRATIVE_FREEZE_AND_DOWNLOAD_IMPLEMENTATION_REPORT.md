# Narrative Freeze and Download Implementation Report

Date: 10 November 2026
Project inspected: `~/Documents/medGenAI_4_1`

## Outcome

The existing Gradio app already completed narrative approval and sign-off through exact-text review decisions and the full-coverage **Sign-off Status**. It did not contain the immutable Narrative Freeze action that consumes that completed status.

The app now has three separate governance concepts:

1. **Clinical Reference Sign-off & Freeze** — the existing protected subsystem in `runtime/`; unchanged.
2. **Narrative review decisions** — append-only decisions for individual exact text hashes.
3. **Freeze and Download** — consumes the completed Sign-off Status and creates the immutable ZIP freeze without another approval.

No approval was fabricated or repeated. Existing narrative approval and sign-off are complete at 40/40 current texts. Clicking Freeze binds those exact decisions, existing reviewer names/roles, hashes and completion time into an append-only consumed-sign-off record and the immutable freeze manifest.

## Verified release identity and naming

The existing naming convention is preserved. The registered release is shown as:

**Reference clinical narratives (EN/ES, from approved v9)**

Its immutable source archive SHA-256 is:

`374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120`

Other uploaded archives remain separately registered external candidates and never replace this release.

## Supplied review CSV and QA-005

The supplied file `narrative_review_decisions_evalds_374910b82d732c4f.csv` has SHA-256:

`3e331d1ddd0ce7904e2454f6031a914d740a178ce8583cac8eb49d1cc04dbfc2`

It contains 42 decision rows: 23 Approved, 17 Approved with comments, and 2 Needs revision. The two Needs revision rows are the HC0005 English and Spanish patient explanations and describe QA-005: bleeding urgency, unsupported endoscopy content, and an extra heading.

The production narrative runtime is newer than the initial pending state represented by those two rows. It preserves both Needs revision decisions, two version-1 corrections, and fresh exact-hash Approved decisions for the corrected English and Spanish texts. Current default-policy coverage is therefore 40/40. The new lifecycle still treats QA-005 as a hard blocker whenever either current text is Needs revision or lacks a fresh acceptable decision.

Current narrative snapshot/run ID:

`narrun_374910b82d732c4f_6a4838e9df221e62`

Current narrative freezes: 0.

## What was implemented

- Direct consumption of completed Sign-off Status; no second approval form, signer or policy selection.
- Append-only `narrative_signoff_consumptions.jsonl` records created when a completed status is first frozen.
- Sign-off binding to evaluation dataset ID, source release SHA-256, narrative release version, narrative run/version ID, canonical current-records SHA-256, every current decision ID, existing reviewer name/role, and all per-text SHA-256 values.
- Exact-release validation for individual decision evidence, in addition to dataset ID and text hash.
- Freeze-time revalidation so stale sign-off evidence cannot authorize changed text.
- Immutable ZIP export with a unique freeze ID; existing exports are never overwritten.
- Exact source narrative archive preservation when the upload was a ZIP; exact source narrative JSON preservation for every freeze.
- Signed-off current narrative snapshot, current decision evidence, consumed Sign-off Status, freeze manifest, per-file checksums, per-text hashes, and `SHA256SUMS` in every narrative freeze.
- Independent freeze registry containing ZIP and manifest checksums.
- ZIP integrity and payload-checksum verification before download is exposed.
- Safe registry-path resolution and clear validation, permission, missing-source, stale-sign-off, and coverage errors.
- Gradio **Freeze and Download** tab with live Sign-off Status consumption, freeze history, verification status, and download.
- Evaluation upload support for verified Freeze and Download ZIPs: the importer selects
  `snapshot/approved_narratives.json`, verifies the manifest, `SHA256SUMS`, 40 per-text hashes and complete Sign-off
  Status, and retains the preserved source narrative JSON/archive as provenance.
- Preservation of the existing release display name and archive-hash identity.

## How to use it

1. Start the app with `HF_HUB_OFFLINE=1 python app.py`.
2. Open **Clinical NLP Evaluation → Upload Evaluation Dataset**.
3. Upload the exact narrative package ZIP. An individual JSON is supported but is labelled unverified provenance.
4. Confirm the displayed dataset ID, archive SHA-256, source reference binding, and release label.
5. Open **Narrative Release Review and Sign-off → Review Narratives**.
6. Review all 40 current texts. Record decisions using the actual reviewer name and role.
7. For Needs revision, save a correction, then record a fresh decision on the corrected text hash.
8. Open **Sign-off Status** to inspect decision coverage and download the decision CSV.
9. Open **Freeze and Download**. It reads the completed Sign-off Status directly.
10. Click **Freeze and Download Signed-off Release**. The app rechecks the exact snapshot and all current decisions, binds their existing reviewers, and verifies the source artifact and checksums.
11. Download the latest verified ZIP from the same tab. Keep its displayed ZIP SHA-256 with the uploaded artifact.
12. The downloaded freeze ZIP can be uploaded again through **Clinical NLP Evaluation → Upload Evaluation Dataset**;
    it is registered as a signed-off frozen release rather than rejected for containing both source and snapshot JSON.

The Clinical Reference approval/freeze remains unchanged throughout this workflow.

## Backup and protected runtime verification

Pre-change backup:

`.backup_2026-11-10_pre_narrative_approval_freeze/`

The backup includes the changed source/docs/tests and complete copies plus SHA-256 inventories of `runtime/`, `runtime_eval/`, and `runtime_narrative_review/`.

The approved Clinical Reference dataset, decisions, approval ledger, audit history, and reference freeze artifacts were not changed. The narrative runtime now intentionally contains the completed Sign-off Status consumption record and the user-created freeze `nfreeze_20261110T111810196437Z_4fc96ff2.zip`; these are preserved in the upload-ready package.

## Verification results

- Focused sign-off-consumption, freeze-import, and dataset tests: 22 passed.
- Complete isolated regression suite: **118 passed**.
- Fresh-package Gradio construction smoke test: `Blocks`, 353 components.
- Warnings: one upstream `websockets.legacy` deprecation warning and one upstream PyTorch JIT future warning; neither is a test failure.

The complete regression suite used isolated temporary Clinical Reference, evaluation, and narrative-review runtimes.

## Main changed files

- `narrative_freeze.py` — Sign-off Status consumption, coverage, freeze, registry, and verification logic.
- `narrative_review_ui.py` — Freeze and Download UI and callbacks.
- `evaluation/datasets.py` — preserved release display name and archive-hash identity.
- `tests_eval/test_narrative_review.py` — sign-off consumption, QA-005 blocking, immutable ZIP, and checksum tests.
- `tests_eval/test_dataset_registry.py` and `tests_eval/test_workspace_b.py` — identity-separation coverage.
- `readme.txt` — operator workflow and storage documentation.

## Upload readiness

The source release package excludes local virtual environments, cached bytecode, test caches, the 1.8 GB AlignScore checkpoint, and the pre-change backup. Model weights are installed separately using the documented setup steps. Production runtime evidence required by the app is included unchanged.
