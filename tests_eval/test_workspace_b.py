"""Prompt 5.0 Workspace B tests: releases, transformations, generation, review queue, exports, isolation.

Run with isolated runtimes:
  CLINICAL_REVIEW_RUNTIME="$(mktemp -d)" CLINICAL_EVAL_RUNTIME="$(mktemp -d)" python -m pytest -q tests_eval
The generation-model test is skipped unless Qwen2.5-1.5B-Instruct is in the local HF cache.
"""
import csv
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from evaluation import evaluate, generators, transforms
from evaluation.batch import RunStore, aggregate, flat_row, generate_and_evaluate
from evaluation.datasets import EvaluationDatasetRegistry
from evaluation.export import build_bundle
from evaluation.references import load_approved_freeze
from evaluation.review_queue import ReviewQueue
from evaluation.transforms import transform

APP = Path(__file__).resolve().parents[1]
RELEASES = APP / "reference_narratives" / "releases"
FREEZE = APP / "runtime" / "exports" / "freeze_20261106T152049729544Z_v0009_08567e1be79e_approved.zip"
P41 = RELEASES / "clinical_narratives_track_a_prompt4_1_v9_claude_candidate.zip"

pytestmark = pytest.mark.skipif(not (FREEZE.exists() and P41.exists()),
                                reason="approved freeze or bundled releases not available")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): _sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture()
def ref():
    return load_approved_freeze(FREEZE)


@pytest.fixture()
def reg(tmp_path):
    r = EvaluationDatasetRegistry(tmp_path)
    ids = {"p41": r.import_zip(P41)["evaluation_dataset_id"]}
    return r, ids


def _release_store(registry, dataset_id):
    s = load_approved_freeze(FREEZE)
    s.narratives = registry.records(dataset_id)
    return s


# ---------------------------------------------------------------- releases
def test_only_the_claude_41_release_is_known(tmp_path):
    from evaluation.datasets import KNOWN_RELEASES, PRIMARY_RELEASE_SHA256
    assert list(KNOWN_RELEASES) == [PRIMARY_RELEASE_SHA256] == [_sha(P41)]
    assert sorted(p.name for p in RELEASES.glob("*.zip")) == [P41.name]


def test_releases_keep_all_texts_nonempty(reg):
    r, ids = reg
    a = r.records(ids["p41"])
    assert sorted(a) == [f"HC{i:04d}" for i in range(1, 11)]
    assert all(v.strip() for rec in a.values() for v in rec.values())


def _repack(src: Path, dst: Path, mutate):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, mutate(info.filename, zin.read(info.filename)))


def test_csv_mismatch_is_reported(tmp_path):
    def mutate(name, data):
        if name.endswith("clinical_records_english.csv"):
            rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
            rows[1][-1] = rows[1][-1] + " EDITED"
            buf = io.StringIO()
            csv.writer(buf).writerows(rows)
            return buf.getvalue().encode()
        return data
    bad = tmp_path / "bad.zip"
    _repack(P41, bad, mutate)
    entry = EvaluationDatasetRegistry(tmp_path / "rt").import_zip(bad)
    assert entry["import_validation_result"] == "Failed"
    assert entry["display_name"].startswith("External candidate")
    assert any("does not exactly match" in e or "SHA256SUMS" in e or "checksum" in e.lower()
               for e in entry["validation_errors"])


def test_wrong_reference_binding_fails(tmp_path):
    def mutate(name, data):
        return data.replace(b"08567e1be79e321263fb3d9b3a306d9b2c9a645e14898706d4accef2952b447e", b"0" * 64)
    bad = tmp_path / "unbound.zip"
    _repack(P41, bad, mutate)
    entry = EvaluationDatasetRegistry(tmp_path / "rt").import_zip(bad)
    assert entry["import_validation_result"] == "Failed"
    assert entry["provenance_verification_status"] == "Unverified"


def test_absolute_path_member_is_rejected(tmp_path):
    bad = tmp_path / "abs.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("/etc/evil.json", "{}")
    with pytest.raises(ValueError):
        EvaluationDatasetRegistry(tmp_path / "rt").import_zip(bad)


def test_symlink_member_is_rejected(tmp_path):
    bad = tmp_path / "link.zip"
    with zipfile.ZipFile(bad, "w") as z:
        info = zipfile.ZipInfo("link.json")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError):
        EvaluationDatasetRegistry(tmp_path / "rt").import_zip(bad)


def test_external_upload_does_not_overwrite_the_release(reg, tmp_path):
    r, ids = reg
    first = r.get(ids["p41"])
    before = _sha(tmp_path / first["archive_file"])
    other = tmp_path / "other.zip"
    _repack(P41, other, lambda n, d: d + (b" " if n.endswith("readme.txt") else b""))
    e = r.import_zip(other)
    assert e["evaluation_dataset_id"] != ids["p41"] and e["display_name"].startswith("External candidate")
    assert _sha(tmp_path / first["archive_file"]) == before
    assert r.get(ids["p41"]) == first


# ---------------------------------------------------------------- transformations
@pytest.mark.parametrize("lang", ["en", "es"])
def test_anonymization_replacement_table_and_number_preservation(ref, lang):
    case = "HC0001"
    rec = ref.record(case)
    name = rec["patient_information"]["full_name"]
    src = f"{name}. " + ref.narrative(case, "professional", lang) if ref.narrative(case, "professional", lang) else \
        f"{name} has amoxicillin 500 mg every 8 hours."
    t = transform("anonymization", "ds", case, lang, src, reference_record=rec)
    assert t["execution_status"] == "Completed"
    assert name not in t["generated_output"]
    cats = {r["identifier_category"] for r in t["details"]["replacement_table"]}
    assert "full_name" in cats
    if rec["patient_information"].get("id") and rec["patient_information"]["id"] in src:
        assert "id" in cats and rec["patient_information"]["id"] not in t["generated_output"]
    assert t["details"]["clinical_number_preservation"]["passed"]
    assert t["output_sha256"] == hashlib.sha256(t["generated_output"].encode()).hexdigest()


@pytest.mark.parametrize("task", ["summarization", "plain_language"])
def test_rule_baselines_record_provenance(reg, task):
    r, ids = reg
    s = _release_store(r, ids["p41"])
    src = s.narrative("HC0005", "professional", "es")
    t = transform(task, ids["p41"], "HC0005", "es", src, method="rule_baseline")
    assert t["execution_status"] == "Completed" and t["generated_output"]
    assert t["source_sha256"] == hashlib.sha256(src.encode()).hexdigest()
    assert t["method"].startswith("deterministic_")


def test_unavailable_generation_model_fails_cleanly(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setitem(generators.GENERATION_MODELS, "missing", {
        "label": "missing", "repo_id": "example-org/does-not-exist-model", "revision": None, "license": ""})
    t = transform("summarization", "ds", "HC0001", "en", "Patient has a sore throat.", method="hf_local:missing")
    assert t["execution_status"] == "Failed"
    assert t["generated_output"] is None and t["output_sha256"] is None
    assert t["error_details"]


def test_empty_source_fails_cleanly():
    t = transform("summarization", "ds", "HC0001", "en", "   ", method="rule_baseline")
    assert t["execution_status"] == "Failed"


def test_prompts_carry_case_safeguards():
    m = generators.build_messages("plain_language", "en", "text", "HC0005")
    assert "ibuprofen" in m[0]["content"].lower() and "discontinu" in m[0]["content"].lower()
    m = generators.build_messages("summarization", "es", "texto", "HC0010")
    assert "heparina" in m[0]["content"].lower() and "fecha" in m[0]["content"].lower()
    with pytest.raises(ValueError):
        generators.build_messages("anonymization", "en", "text")


def _qwen_cached() -> bool:
    try:
        generators._snapshot("qwen2.5-1.5b-instruct")
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _qwen_cached(), reason="Qwen2.5-1.5B-Instruct not in the local HF cache")
def test_local_model_generates_with_provenance(ref):
    src = ref.narrative("HC0001", "professional", "en") or "Streptococcal tonsillopharyngitis. Amoxicillin 500 mg every 8 hours."
    t = transform("summarization", "ds", "HC0001", "en", src, method="hf_local:qwen2.5-1.5b-instruct",
                  params={"target_words": 80, "max_new_tokens": 160})
    assert t["execution_status"] == "Completed", t["error_details"]
    d = t["details"]
    assert d["model"] == "Qwen/Qwen2.5-1.5B-Instruct" and d["model_revision"]
    assert t["parameters"]["do_sample"] is False
    again = transform("summarization", "ds", "HC0001", "en", src, method="hf_local:qwen2.5-1.5b-instruct",
                      params={"target_words": 80, "max_new_tokens": 160})
    assert again["output_sha256"] == t["output_sha256"]  # greedy decoding is reproducible on one device


def test_anonymized_release_text_has_no_direct_identifier_leak(reg):
    """Regression: the case id (e.g. 'Synthetic case HC0001') must not survive anonymization."""
    from evaluation.checks.pii import check_pii, summarize_pii
    r, ids = reg
    s = _release_store(r, ids["p41"])
    for case in s.case_ids:
        for lang in ("en", "es"):
            src = s.narrative(case, "professional", lang)
            t = transform("anonymization", ids["p41"], case, lang, src, reference_record=s.record(case))
            summary = summarize_pii(check_pii(s.record(case), t["generated_output"]), s.record(case), t["generated_output"])
            assert summary["direct_leaks"] == 0, (case, lang, summary)


# ---------------------------------------------------------------- batch, queue, export, isolation
def test_generate_and_evaluate_batch_and_aggregate(reg, tmp_path):
    r, ids = reg
    s = _release_store(r, ids["p41"])
    res, transformations, failures = generate_and_evaluate(
        s, dataset_id=ids["p41"], task="summarization", languages=["en", "es"], method="rule_baseline",
        case_ids=["HC0001", "HC0005", "HC0010"], use_models=False)
    assert len(transformations) == 6 and len(res) == 6 and not failures
    assert all(x["transformation_id"] and x["evaluation_dataset_id"] == ids["p41"] for x in res)
    agg = aggregate([flat_row(x) for x in res])
    assert sum(a["n"] for a in agg) == 6
    assert all(a["distinct_patients"] == 3 for a in agg)
    assert "median_fact_agreement" in agg[0]


def test_review_queue_is_append_only_and_separate(ref, tmp_path):
    run = evaluate(ref, task="summarization", case_id="HC0001", language="en",
                   output_text="Amoxicillin 250 mg orally every 8 hours for 10 days.", use_models=False)
    q = ReviewQueue(tmp_path)
    n = q.add_run(run)
    assert n >= 1 and q.add_run(run) == 0
    item = q.items("Pending")[0]
    with pytest.raises(ValueError):
        q.decide(item["item_id"], "Confirmed problem", "  ")
    q.decide(item["item_id"], "Confirmed problem", "Q")
    q.decide(item["item_id"], "False positive", "Q")
    assert q.items("Reviewed")[0]["decision"] == "False positive"
    assert len(q._read(q.decisions_path)) == 2
    assert {p.name for p in tmp_path.iterdir()} == {"review_queue_items.jsonl", "review_queue_decisions.jsonl"}


def test_export_bundle_checksums_and_public_pseudonyms(ref, tmp_path):
    store = RunStore(tmp_path)
    run = evaluate(ref, task="plain_language", case_id="HC0001", language="en",
                   output_text="Take amoxicillin 250 mg by mouth every 8 hours for 10 days.",
                   source_text="Amoxicillin 500 mg orally every 8 hours for 10 days.", use_models=False)
    store.append(run)
    q = ReviewQueue(tmp_path)
    q.add_run(run)
    q.decide(q.items()[0]["item_id"], "Confirmed problem", "Dr Real Name", "private note")
    path = build_bundle(tmp_path, public=True, hash_checkpoint=False)
    with zipfile.ZipFile(path) as z:
        sums = dict(line.split("  ", 1)[::-1] for line in z.read("SHA256SUMS").decode().splitlines())
        for name, digest in sums.items():
            assert hashlib.sha256(z.read(name)).hexdigest() == digest
        blob = b"".join(z.read(n) for n in z.namelist())
        assert b"Dr Real Name" not in blob and b"private note" not in blob
        assert b"reviewer-1" in z.read("review/human_review_queue.csv")
        assert "metrics/patient_level_metrics.csv" in z.namelist()


def test_export_keeps_only_plain_language_by_default(ref, tmp_path):
    """Master Prompt 5.2: anonymization and summarization are out of scope and left out of the default export."""
    store, q = RunStore(tmp_path), ReviewQueue(tmp_path)
    src = "Synthetic case [CASE_ID]. Amoxicillin 500 mg orally every 8 hours for 10 days."
    for task in ("summarization", "anonymization", "plain_language"):
        run = evaluate(ref, task=task, case_id="HC0001", language="en", output_text=src, source_text=src,
                       use_models=False)
        store.append(run)
        q.add_run(run)
    with zipfile.ZipFile(build_bundle(tmp_path, hash_checkpoint=False)) as z:
        assert {r["task"] for r in json.loads(z.read("metrics/evaluation_runs.json"))} == {"plain_language"}
        assert "outputs/plain_language.csv" in z.namelist()
        assert "outputs/anonymized_texts.csv" not in z.namelist() and "outputs/summaries.csv" not in z.namelist()
        queue_csv = z.read("review/human_review_queue.csv")
        assert b"anonymization" not in queue_csv and b"summarization" not in queue_csv
        manifest = json.loads(z.read("provenance/provenance_manifest.json"))
        assert manifest["excluded_tasks"] == ["anonymization", "summarization"]
    with zipfile.ZipFile(build_bundle(tmp_path, hash_checkpoint=False, exclude_tasks=())) as z:
        assert {r["task"] for r in json.loads(z.read("metrics/evaluation_runs.json"))} == \
            {"summarization", "anonymization", "plain_language"}
        assert "outputs/anonymized_texts.csv" in z.namelist() and "outputs/summaries.csv" in z.namelist()


def test_evaluation_workflow_never_touches_reference_runtime(reg, tmp_path):
    """Isolation: importing, transforming, evaluating, reviewing and exporting leave runtime/ untouched."""
    runtime = APP / "runtime"
    before = _tree_hashes(runtime)
    freeze_before = _sha(FREEZE)
    r, ids = reg
    s = _release_store(r, ids["p41"])
    res, transformations, _ = generate_and_evaluate(
        s, dataset_id=ids["p41"], task="anonymization", languages=["en"], method="rule_baseline",
        case_ids=["HC0002"], use_models=False)
    store, q = RunStore(tmp_path), ReviewQueue(tmp_path)
    for x in res:
        store.append(x)
        q.add_run(x)
    build_bundle(tmp_path, public=False, hash_checkpoint=False)
    assert _tree_hashes(runtime) == before
    assert _sha(FREEZE) == freeze_before == "11643597900d00351770f44f358264dab6c181ba3c67cc7811b7cbc26e4d8f0f"
    assert not any("runtime/" in str(p) and "runtime_eval" not in str(p) for p in tmp_path.rglob("*")
                   if str(p).startswith(str(runtime)))


def test_plain_language_input_has_no_identifiers_and_baseline_keeps_key_facts(reg):
    """Identifiers are removed before adaptation; the rule baseline keeps diagnosis, medicines and warning signs."""
    r, ids = reg
    s = _release_store(r, ids["p41"])
    for lang in ("en", "es"):
        src = s.narrative("HC0001", "professional", lang)
        t = transform("plain_language", ids["p41"], "HC0001", lang, src, reference_record=s.record("HC0001"))
        pi = s.record("HC0001")["patient_information"]
        for key in ("full_name", "fictional_identification_number", "date_of_birth", "phone_number", "email", "address"):
            assert pi[key] not in t["source_text"] and pi[key] not in t["generated_output"]
        assert "HC0001" not in t["source_text"] and "20/10/2026" in t["source_text"]
        assert t["details"]["input_preprocessing"]["original_source_sha256"] == hashlib.sha256(src.encode()).hexdigest()
        assert "amoxicil" in t["generated_output"].lower() and "500 mg" in t["generated_output"]


def test_plain_language_prompt_example_is_another_patient(monkeypatch):
    monkeypatch.setenv("CLINICAL_EVAL_ONE_SHOT", "1")   # off by default: a 1.5B model copied facts from the example
    for case in ("HC0001", "HC0002", "HC0003"):
        for lang in ("en", "es"):
            msgs = generators.build_messages("plain_language", lang, "source", case, 300)
            ex = generators._example("plain_language", lang, case)
            assert ex and ex[0] != case and ex[1].strip()[:40] in msgs[1]["content"]
    assert generators._example("summarization", "en", "HC0001") is None

    monkeypatch.setenv("CLINICAL_EVAL_ONE_SHOT", "0")
    assert generators._example("plain_language", "en", "HC0001") is None


def test_sectionwise_split_keeps_every_part_and_moves_urgent_signs(reg):
    r, ids = reg
    s = _release_store(r, ids["p41"])
    src, _ = transforms.strip_identifier_header(s.narrative("HC0001", "professional", "en"), s.record("HC0001"))
    parts = dict(generators._source_sections(src))
    assert list(parts) == ["reason", "found", "means", "treatment", "warning", "follow"]
    assert "amoxicillin 500 mg" in parts["treatment"] and "emergency services" in parts["warning"]
    assert "emergency" not in parts["follow"].lower()


def test_negation_contractions_count_as_negation():
    import re
    from narrative_eval import catalog
    assert re.search(catalog.NEG, "You don't have a cough", re.I)
    assert re.search(catalog.NEG, "You haven’t had ear pain", re.I)


def test_generation_models_are_pinned_and_listed():
    import re
    from evaluation.generators import DEFAULT_GENERATION_MODEL, GENERATION_MODELS, methods, model_status
    assert DEFAULT_GENERATION_MODEL == "qwen2.5-1.5b-instruct"
    assert {"qwen2.5-3b-instruct", "qwen2.5-7b-instruct", "phi-3.5-mini-instruct",
            "salamandra-7b-instruct"} <= set(GENERATION_MODELS)
    for key, cfg in GENERATION_MODELS.items():
        assert re.fullmatch(r"[0-9a-f]{40}", cfg["revision"]), key       # exact commit, recorded in every output
        assert cfg["license"] and cfg["size_gb"] > 0
        assert ("hf_local:" + key) in [v for _, v in methods("plain_language")]
        assert model_status(key)["status"] in ("not_loaded", "loaded", "error")
    assert methods("plain_language")[1][1] == "hf_local:" + DEFAULT_GENERATION_MODEL   # default stays first
