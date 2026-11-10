# Dataset and reference narratives

All data in this repository is synthetic and fictional, created for this project. No real patient
data is used, and no external dataset has to be downloaded. Every file below is already in place
after `git clone`; do not move or rename it.

## 1. Structured clinical reference dataset

| Property | Value |
| --- | --- |
| Type | Fully synthetic structured clinical records (JSON) |
| Patients | 10, `HC0001`–`HC0010` |
| Languages | English and Spanish |
| Sections per patient | 13 (listed below) |
| Schema | `Synthetic Bilingual Clinical Records Reference Schema V1.2.1`, JSON Schema draft 2020-12 |
| Approved version | 9, "Clinically Approved with Limitations" (see `01_clinical_reference_signoff_and_freeze.md`) |

The 13 sections of every record:

1. `patient_information`
2. `reason_for_consultation`
3. `relevant_medical_history`
4. `current_symptoms`
5. `clinical_examination_and_investigations`
6. `clinical_assessment_and_diagnosis`
7. `care_and_treatment`
8. `medication_management`
9. `clinical_progress`
10. `warnings_and_red_flags`
11. `follow_up_and_monitoring_plan`
12. `medical_glossary`
13. `plain_language_patient_version`

Identifiers in `patient_information` are fictional (`SYN-ID-*`, `SYN-PHONE-*`, `example.invalid`
addresses).

## 2. Where each file is

| File | Purpose |
| --- | --- |
| `clinical_records_reference.json` | Reviewed dataset (byte-identical to approved version 9). Upload this in Workspace 1. |
| `inputs/clinical_records_reference_reviewed_v1.2.1.json` | Same file, the copy the application reads |
| `inputs/clinical_records_reference.json` | Pre-review source dataset, kept for provenance |
| `inputs/clinical_records_schema_v1.2.1.json` | Authoritative JSON Schema used by approved version 9 |
| `inputs/clinical_reference_qa_issues.json` | QA register: 12 issues reviewed before approval |
| `inputs/clinical_reference_qa_resolution_log_v1.2.1.json`, `inputs/clinical_reference_qa_resolution_report_v1.2.1.txt` | How each QA issue was resolved |
| `inputs/validate_schema_v1.2.1.py` | Standalone schema validator |
| `reference_narratives/releases/clinical_narratives_track_a_prompt4_1_v9_claude_candidate.zip` | Reference narrative release (Dataset 2i), full generation and provenance package |
| `reference_narratives/clinical_narratives_bilingual_v9_prompt4_1.json` | The 40 texts of that release, as read by the app |
| `reference_narratives/releases/nfreeze_20261110T111810196437Z_4fc96ff2.zip` | Signed-off narrative freeze (40/40 approved, HC0005 corrections): the reference for the reported results |
| `runtime/exports/freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip` | Approved v9 clinical reference freeze, loaded by Workspace 3 |
| `reference_narratives/SOURCE.md` | Where the release came from |
| `Dataset v1/` | Prompt 4.1 comparison report and triage of fact-check findings |

## 3. Reference narratives (Dataset 2i)

The release holds 40 texts: 10 patients × 2 languages × 2 text types.

- **Professional clinical narrative**: the source text that is adapted.
- **Reference patient explanation**: the plain-language baseline that generated outputs are compared with.

The texts were generated from approved version 9 with narrative prompt 4.1 (generation-only package)
and passed automated source-fidelity checks. They become human-approved references only through the
sign-off in Workspace 2 (`02_narrative_release_review_and_signoff.md`). Clinical facts are always
checked against the structured version 9 dataset, never against the narratives.

## 4. Checksums

`PUBLIC_DATA_SHA256SUMS` lists the SHA-256 of every data file. Check them after cloning:

```
shasum -a 256 -c PUBLIC_DATA_SHA256SUMS      # Linux: sha256sum -c PUBLIC_DATA_SHA256SUMS
```

| Identity | SHA-256 |
| --- | --- |
| Reviewed dataset file | `710fdb5e4373f5d16ccbf64ddc6b3207b16ebefc0cdaf5ac9b1820e1a9c7806a` |
| Reviewed dataset, canonical JSON | `08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e` |
| Schema v1.2.1 | `9967d8d26957a63cd1805dcac4d8b9a3f7468604cb75019b0657cc7f24cf9dbc` |
| Narrative release ZIP (Dataset 2i) | `374910b82d732c4fa6101a421d4d4caf66b8c62eeca327a7b6210184ed8ce120` |
| Approved v9 freeze ZIP | `11643597900d00351770f44f358264dab6c181ba3c67cc7811b7cbc26e4d8f0f` |
| Signed-off narrative freeze ZIP | `07e30bfc1545ea71ed90e21303f3a5f4e8e96837ec3e3ad5453b97f211ad1eaf` |
| Narrative texts JSON | `dd25e3181e7453a23bcb7c479d4fb21997265acaaa8542374809a2f6933b7de9` |
