"""Source integrity and record access (Gate A). The reference JSON is read-only."""
import hashlib, json, os, zipfile
from .config import IDS, EXPECTED_CANONICAL_SHA256


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def canonical_sha256(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def load_reference(freeze_dir):
    with open(os.path.join(freeze_dir, "clinical_records_reference.json"), encoding="utf-8") as f:
        return json.load(f)


def verify_freeze(freeze_zip, freeze_dir):
    """Return a dict of integrity checks; never modifies anything."""
    man = json.load(open(os.path.join(freeze_dir, "clinical_reference_freeze_manifest.json"), encoding="utf-8"))
    ref = load_reference(freeze_dir)
    file_checks = {f: sha256_file(os.path.join(freeze_dir, f)) == h for f, h in man["file_checksums"].items()}
    with zipfile.ZipFile(freeze_zip) as z:
        zip_names = sorted(n for n in z.namelist() if not n.endswith("/"))
        zip_ok = z.testzip() is None
    canon = canonical_sha256(ref)
    sections = {pid: len(ref[pid]) for pid in ref}
    return {
        "freeze_zip_sha256": sha256_file(freeze_zip),
        "freeze_zip_crc_ok": zip_ok,
        "freeze_zip_members": zip_names,
        "dataset_file_sha256": sha256_file(os.path.join(freeze_dir, "clinical_records_reference.json")),
        "dataset_file_sha256_matches_manifest": sha256_file(os.path.join(freeze_dir, "clinical_records_reference.json")) == man["dataset_file_sha256"],
        "canonical_sha256": canon,
        "canonical_matches_expected": canon == EXPECTED_CANONICAL_SHA256,
        "canonical_matches_manifest": canon == man["dataset_canonical_sha256"],
        "manifest_file_checksums_ok": file_checks,
        "patient_ids": list(ref),
        "exact_patient_set": list(ref) == IDS,
        "sections_per_patient": sections,
        "thirteen_sections_each": all(v == 13 for v in sections.values()),
        "passed": all(file_checks.values()) and zip_ok and canon == EXPECTED_CANONICAL_SHA256 and list(ref) == IDS and all(v == 13 for v in sections.values()),
    }


def ptr(obj, path):
    """Resolve a JSON Pointer-like path ('/a/0/b') inside obj."""
    for p in [x for x in path.strip("/").split("/") if x != ""]:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    return obj


def all_strings(obj, prefix=""):
    """Yield (path, value) for every scalar leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from all_strings(v, f"{prefix}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from all_strings(v, f"{prefix}/{i}")
    else:
        yield prefix, obj
