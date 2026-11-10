# Workspace 1: Clinical Reference Sign-off and Freeze

Code: `app.py`, `review_core.py`. Local state: `runtime/` (not published; see readme.txt).

## 1. Purpose

This workspace turns the synthetic clinical records into a validated, human-reviewed and frozen
reference. That reference is the only source of clinical facts for the other two workspaces: generated
texts are checked against it, and nothing downstream can change it.

Lifecycle: **Upload → Validate → Activate → Review QA issues → Clinical approval → Freeze → Download**.

The application records human decisions. It does not make clinical judgments or verify reviewer
credentials, and a technical pass is never a clinical approval.

## 2. Inputs

Three files are uploaded together in **Upload and Validate Bundle** (all in this repository):

| Upload field | File | Role |
| --- | --- | --- |
| Reference dataset JSON | `clinical_records_reference.json` | The 10 structured records |
| JSON Schema | `inputs/clinical_records_schema_v1.2.1.json` | Structural contract (draft 2020-12) |
| QA issues JSON | `inputs/clinical_reference_qa_issues.json` | The 12 findings to review |

Schema validation checks structure only, not medical accuracy.

## 3. Tabs and stages

| Tab | What happens |
| --- | --- |
| Dataset Status and Validation | Dashboard: active version, validation result, review progress, decision, latest approved and provisional freezes |
| Upload and Validate Bundle | Archives the three files, runs schema and deterministic checks. A passed bundle is activated only after explicit confirmation, as a new immutable version |
| Clinical Review and Human Decisions | One decision per QA issue, with a named reviewer, role and written rationale: Approve existing information, Approve with documented limitation, Request correction, Request additional information, Reject clinical content. Corrections are JSON-Pointer operations applied as one validated batch, which creates a new version that must be reviewed again. Final approval: Clinically Approved, Clinically Approved with Limitations, Further Clinical Review Required, Clinically Rejected |
| Freeze and Downloads | Creates a provisional or approved freeze bound to one exact version and its SHA-256, and downloads it |
| Version and Upload History | Read-only history of every version and upload |

No decision is preselected, approvals never carry over to a new version, and an approved freeze needs
passed validation, a current decision on every issue, no blocker, and an approval bound to the exact
version hash.

## 4. Approved reference used in this project

| Item | Value |
| --- | --- |
| Dataset version | 9 |
| Decision | Clinically Approved with Limitations |
| Freeze | `freeze_20261106T152049729544Z_v0009_08567e1be79e_approved`, created 6 Nov 2026 |
| Freeze ZIP SHA-256 | `11643597900d00351770f44f358264dab6c181ba3c67cc7811b7cbc26e4d8f0f` |
| Dataset file SHA-256 | `710fdb5e4373f5d16ccbf64ddc6b3207b16ebefc0cdaf5ac9b1820e1a9c7806a` |
| Canonical dataset SHA-256 | `08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e` |

The freeze ZIP holds the dataset, the source and effective schema, the QA register, the technical
validation result, the review decisions and audit log, the approval and sign-off, and a manifest.

### Documented limitations accepted at approval

| Issue | Patient | Limitation |
| --- | --- | --- |
| QA-005 | HC0005 | Ibuprofen stays listed as active; paracetamol is preferred for headaches, but no discontinuation is recorded |
| QA-010 | HC0010 | Retrospective chronology: no exact discharge date |
| QA-011 | HC0010 | IV unfractionated heparin given, but the dose and time are not documented |

Generated texts must keep these limitations rather than invent the missing facts.

## 5. Reproducing this workspace

From a fresh clone you can upload the three files above, validate and activate them, and inspect every
check. The human decisions and the approval cannot be reproduced automatically and must never be
fabricated; the historical approved freeze is published at
`runtime/exports/freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip`, and that is what the
evaluation uses.
