# Results and limitations

## Results

The batch results (10 patients × English/Spanish, rule baseline and Qwen2.5-1.5B-Instruct, compared
with the reference patient explanations) are being run and will be added here. Every number reported
will come from an export bundle built in Workspace 3 → Batch Comparison and Export, with its
`SHA256SUMS`, so it can be checked against a replication run.

## Limitations

- Ten synthetic cases cannot establish general clinical performance; batch counts are outputs, not patients.
- Structured fact checks are lexical; the multilingual NLI model can miss negation and frequency changes.
- BERTScore and embedding similarity do not establish factual or clinical correctness.
- AlignScore is used for English only.
- Reviewer credentials are self-declared and not verified by the software.
- Source uncertainties stay documented (HC0005 ibuprofen status; HC0010 discharge date and heparin dose/time);
  missing facts are never invented.
- Identifier removal before adaptation handles the known synthetic identifiers only; it is not validated
  de-identification of real data.
- This is a research prototype, not a medical device or clinical service.
