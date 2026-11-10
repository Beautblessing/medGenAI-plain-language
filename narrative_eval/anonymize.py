"""Deterministic synthetic-identifier replacement (anonymization test harness).

Replaces the identifiers recorded in patient_information with fixed placeholder
tokens and verifies that none of the targeted strings remain. This tests removal of
KNOWN synthetic identifiers only. It is not evidence of de-identification of real
patient records, which also contain free-text identifiers this method cannot find.
"""
import re

TOKENS = {"full_name": "[PATIENT_NAME]", "fictional_identification_number": "[PATIENT_ID]", "date_of_birth": "[DOB]",
          "phone_number": "[PHONE]", "email": "[EMAIL]", "address": "[ADDRESS]"}


def targets(rec):
    pi = rec["patient_information"]
    t = [(k, pi[k]) for k in TOKENS if pi.get(k)]
    # name parts (first name(s), surnames) as separate targets, longest first
    parts = [p for p in re.split(r"\s+", pi["full_name"]) if len(p) > 2]
    t += [("name_part", p) for p in parts]
    return sorted(t, key=lambda kv: -len(kv[1]))


def anonymize(text, rec):
    out, log = text, []
    for k, v in targets(rec):
        tok = TOKENS.get(k, "[PATIENT_NAME]")
        n = len(re.findall(re.escape(v), out, flags=re.I))
        if n:
            out = re.sub(re.escape(v), tok, out, flags=re.I)
            log.append({"identifier_type": k, "replacements": n, "token": tok})
    return out, log


def verify(anon_text, rec):
    remaining = [{"identifier_type": k, "value_present": True} for k, v in targets(rec) if re.search(re.escape(v), anon_text, re.I)]
    return {"targets_checked": len(targets(rec)), "targets_remaining": len(remaining), "remaining": remaining, "passed": not remaining,
            "scope_note": "Synthetic identifier removal test only; not proof of de-identification of real records."}


def compare(original, anonymized):
    import difflib
    return list(difflib.unified_diff(original.splitlines(), anonymized.splitlines(), "original", "anonymized", lineterm="", n=0))
