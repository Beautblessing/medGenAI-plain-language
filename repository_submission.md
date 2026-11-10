# Repository Submission

## Public repository

**Status:** Published.
**URL:** https://github.com/Beautblessing/medGenAI-plain-language

## Project

**Development and Comparative Evaluation of Bilingual Plain-Language Clinical Narrative Generation with Human Oversight**

This project is a local Gradio research prototype that evaluates whether local language models and a deterministic baseline can transform professional English and Spanish clinical narratives into patient-facing plain language without changing clinical facts. It separates clinical-reference governance, exact-text narrative review and freeze, and NLP experimentation.

## Reproduction summary

1. Clone the public repository.
2. Create a Python 3.12 virtual environment.
3. Install `requirements-lock.txt`.
4. Verify the included fictional data with `PUBLIC_DATA_SHA256SUMS`.
5. Run `setup_eval_models.py` once if model-based generation and metrics are required.
6. The approved v9 freeze (`runtime/exports/…_approved.zip`, SHA-256 `11643597…8f0f`) and the signed-off narrative freeze (`reference_narratives/releases/nfreeze_20261110T111810196437Z_4fc96ff2.zip`, SHA-256 `07e30bfc…1eaf`) are included and checked in step 4.
7. Start locally with `HF_HUB_OFFLINE=1 python app.py` and open `http://127.0.0.1:7860`.
8. Import the signed-off narrative freeze ZIP in Upload Evaluation Dataset, run the selected comparison, inspect metrics and export the results.
9. Run the isolated test commands in `readme.txt`.

## Dataset availability

The repository includes the complete project-owned 10-case fictional bilingual dataset, reviewed dataset, matching schema, QA register, QA resolution evidence and distributable Dataset 2i narrative candidate. No external patient dataset is required.

Important identities:

- reviewed dataset file SHA-256: `710fdb5e4373f5d16ccbf64ddc6b3207b16ebefc0cdaf5ac9b1820e1a9c7806a`
- reviewed dataset canonical SHA-256: `08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e`
- Dataset 2i narrative archive SHA-256: `374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120`

The two freeze ZIPs used by the reported results are included with the owner's approval (reviewer initials only). The local audit logs, decision ledgers and other runtime records are excluded. Placement and checksums are in `readme.txt`.

## Limitations

- Ten synthetic cases do not establish clinical generalizability.
- Automated fact checks and similarity metrics do not establish factual correctness or clinical approval.
- Multilingual NLI can miss negation and frequency changes.
- Anonymization and summarization are outside the submitted experiment scope.
- No clinical or bilingual reviewer credentials are independently verified by the software.
