IDS = [f"HC{n:04d}" for n in range(1, 11)]
FIELDS = ["clinical_narrative_en", "clinical_narrative_es", "patient_explanation_en", "patient_explanation_es"]
FIELD_LANG = {f: f[-2:] for f in FIELDS}
FIELD_TYPE = {f: ("clinical" if f.startswith("clinical") else "patient") for f in FIELDS}
EXPECTED_CANONICAL_SHA256 = "08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e"
MODELS = {
    "semantic": {"repo_id": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
                  "revision": "4328cf26390c98c5e3c738b4460a05b95f4911f5"},
    "nli_multilingual": {"repo_id": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
                          "revision": "8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c"},
    "nli_english_supplementary": {"repo_id": "cross-encoder/nli-deberta-base",
                                   "revision": "f375a3f8bdba5a9ea9489179fe2f7b7d69c00b76"},
}
CSV_MAP = {"clinical_records_english.csv": "clinical_narrative_en", "clinical_records_spanish.csv": "clinical_narrative_es",
           "patient_explanations_english.csv": "patient_explanation_en", "patient_explanations_spanish.csv": "patient_explanation_es"}
