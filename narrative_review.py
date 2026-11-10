"""Narrative Release Review and Sign-off (Gate C): human review of generated reference narratives.

Sits between the clinical-reference sign-off (structured records, runtime/) and the NLP evaluation
(runtime_eval/). It reads the imported narrative release read-only and writes only its own append-only
decision log (CLINICAL_NARRATIVE_REVIEW_RUNTIME, default runtime_narrative_review/). A decision is bound to the
SHA-256 of the exact text that was reviewed: if the text differs, the decision does not count for it.

The app records who said what; it does not verify credentials or make clinical judgments.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
DECISIONS = ("Approved", "Approved with comments", "Needs revision")
APPROVING = {"Approved", "Approved with comments"}
TEXT_TYPES = {"professional": "Professional clinical narrative", "patient": "Patient explanation"}
ROLES = ("Clinician", "Bilingual clinical reviewer", "Other")
HEADERS = ["Patient", "Language", "Text", "Decision", "Reviewer", "Role", "Comment", "Decided UTC", "Text SHA-256"]
_LOCK = threading.Lock()


def runtime_dir() -> Path:
    return Path(os.environ.get("CLINICAL_NARRATIVE_REVIEW_RUNTIME", str(APP_DIR / "runtime_narrative_review")))


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class NarrativeReviewLog:
    """Append-only log of Gate C decisions (one JSON object per line)."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else runtime_dir()
        self.path = self.root / "narrative_review_decisions.jsonl"

    def all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, *, dataset_id: str, release_sha256: str, case_id: str, language: str, text_type: str,
               text: str, decision: str, reviewer: str, role: str, comment: str = "") -> dict[str, Any]:
        if decision not in DECISIONS:
            raise ValueError(f"Decision must be one of {DECISIONS}")
        if text_type not in TEXT_TYPES:
            raise ValueError(f"Text type must be one of {tuple(TEXT_TYPES)}")
        if not (reviewer or "").strip():
            raise ValueError("Enter the reviewer's name.")
        if decision != "Approved" and not (comment or "").strip():
            raise ValueError("Add a comment for 'Approved with comments' or 'Needs revision'.")
        if not (text or "").strip():
            raise ValueError("There is no text to review for this patient and language.")
        entry = {
            "decision_id": str(uuid.uuid4()),
            "evaluation_dataset_id": dataset_id,
            "narrative_release_sha256": release_sha256,
            "patient_id": case_id,
            "language": language,
            "text_type": text_type,
            "text_sha256": sha256(text),
            "decision": decision,
            "reviewer": reviewer.strip(),
            "reviewer_role": role,
            "comment": (comment or "").strip(),
            "decided_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "note": "Recorded human decision; the app does not verify credentials or make clinical judgments.",
        }
        with _LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def latest(self, dataset_id: str, case_id: str, language: str, text_type: str,
               text: str | None = None) -> dict[str, Any] | None:
        """Most recent decision for this text; with `text`, only a decision on exactly this text counts."""
        digest = sha256(text) if text is not None else None
        hits = [d for d in self.all() if d["evaluation_dataset_id"] == dataset_id and d["patient_id"] == case_id
                and d["language"] == language and d["text_type"] == text_type
                and (digest is None or d["text_sha256"] == digest)]
        return hits[-1] if hits else None

    def history(self, dataset_id: str, case_id: str | None = None) -> list[dict[str, Any]]:
        return [d for d in self.all() if d["evaluation_dataset_id"] == dataset_id
                and (case_id is None or d["patient_id"] == case_id)]

    # ------------------------------------------------------------------ corrections
    @property
    def corrections_path(self) -> Path:
        return self.root / "narrative_review_corrections.jsonl"

    def corrections(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        if not self.corrections_path.exists():
            return []
        items = [json.loads(l) for l in self.corrections_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [c for c in items if dataset_id is None or c["evaluation_dataset_id"] == dataset_id]

    def chain(self, dataset_id: str, case_id: str, language: str, text_type: str, original: str) -> list[dict]:
        """Corrections of this text in order, each starting from the text the previous one produced."""
        current, out = sha256(original), []
        for c in self.corrections(dataset_id):
            if (c["patient_id"], c["language"], c["text_type"]) == (case_id, language, text_type) \
                    and c["original_sha256"] == current:
                out.append(c)
                current = c["corrected_sha256"]
        return out

    def current_text(self, dataset_id: str, case_id: str, language: str, text_type: str,
                     original: str) -> tuple[str, int]:
        """(text under review, version): version 0 is the release text, n the n-th correction."""
        ch = self.chain(dataset_id, case_id, language, text_type, original)
        return (ch[-1]["corrected_text"], len(ch)) if ch else (original, 0)

    def approved_text(self, dataset_id: str, case_id: str, language: str, text_type: str,
                      original: str) -> tuple[str, int]:
        """The newest version with an approving decision on its exact text; otherwise the release text."""
        versions = [(original, 0)] + [(c["corrected_text"], i + 1) for i, c in
                                      enumerate(self.chain(dataset_id, case_id, language, text_type, original))]
        for text, version in reversed(versions[1:]):
            d = self.latest(dataset_id, case_id, language, text_type, text)
            if d and d["decision"] in APPROVING:
                return text, version
        return original, 0

    def record_correction(self, *, dataset_id: str, case_id: str, language: str, text_type: str, original: str,
                          corrected: str, editor: str, role: str, reason: str) -> dict[str, Any]:
        current, version = self.current_text(dataset_id, case_id, language, text_type, original)
        last = self.latest(dataset_id, case_id, language, text_type, current)
        if not last or last["decision"] != "Needs revision":
            raise ValueError("A correction can only be saved for a text whose latest decision is 'Needs revision'.")
        if not (editor or "").strip():
            raise ValueError("Enter the name of the person making the correction.")
        if not (reason or "").strip():
            raise ValueError("Describe what was corrected.")
        if not (corrected or "").strip() or corrected.strip() == current.strip():
            raise ValueError("The corrected text is empty or unchanged.")
        corrected = corrected.strip()
        entry = {
            "correction_id": str(uuid.uuid4()),
            "evaluation_dataset_id": dataset_id,
            "patient_id": case_id, "language": language, "text_type": text_type,
            "version": version + 1,
            "original_sha256": sha256(current),
            "corrected_sha256": sha256(corrected),
            "corrected_text": corrected,
            "word_diff": word_diff(current, corrected),
            "addresses_decision_id": last["decision_id"],
            "revision_instruction": last["comment"],
            "editor": editor.strip(), "editor_role": role, "reason": reason.strip(),
            "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "note": "Manual correction; the imported release file is unchanged. Needs a fresh review decision.",
        }
        with _LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.corrections_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    # ------------------------------------------------------------------ status
    def text_status(self, dataset_id: str, case_id: str, language: str, text_type: str, text: str) -> str:
        d = self.latest(dataset_id, case_id, language, text_type, text)
        if d:
            return d["decision"]
        return "Not reviewed (text changed)" if self.latest(dataset_id, case_id, language, text_type) else "Not reviewed"

    def gate_c(self, dataset_id: str, case_id: str, language: str, texts: dict[str, str]) -> dict[str, Any]:
        """Gate C for one patient and language: both texts approved, each on its exact current text."""
        decisions = {t: self.latest(dataset_id, case_id, language, t, texts.get(t, "")) for t in TEXT_TYPES}
        approved = [t for t, d in decisions.items() if d and d["decision"] in APPROVING]
        return {"passed": len(approved) == len(TEXT_TYPES), "approved": len(approved), "required": len(TEXT_TYPES),
                "reviewers": sorted({d["reviewer"] for d in decisions.values() if d}),
                "decisions": {t: (d["decision"] if d else None) for t, d in decisions.items()}}

    def release_status(self, dataset_id: str, store) -> dict[str, Any]:
        rows, approved, revision, total = [], 0, 0, 0
        for case in store.case_ids:
            for lang in ("en", "es"):
                for t in TEXT_TYPES:
                    text, version = self.current_text(dataset_id, case, lang, t,
                                                      store.narrative(case, t, lang, original=True) or "")
                    status = self.text_status(dataset_id, case, lang, t, text)
                    if version:
                        status = f"{status} (corrected v{version})"
                    d = self.latest(dataset_id, case, lang, t, text)
                    total += 1
                    approved += status.split(" (corrected")[0] in APPROVING
                    revision += status.startswith("Needs revision")
                    rows.append([case, lang.upper(), TEXT_TYPES[t], status, d["reviewer"] if d else "",
                                 d["decided_utc"][:19].replace("T", " ") if d else ""])
        signed = approved == total and total > 0
        return {"rows": rows, "approved": approved, "needs_revision": revision, "total": total, "signed_off": signed}

    def export_csv(self, dataset_id: str) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(HEADERS)
        for d in self.history(dataset_id):
            w.writerow([d["patient_id"], d["language"].upper(), TEXT_TYPES[d["text_type"]], d["decision"],
                        d["reviewer"], d["reviewer_role"], d["comment"], d["decided_utc"], d["text_sha256"]])
        return buf.getvalue()


def word_diff(old: str, new: str) -> str:
    """Compact word-level diff: [-removed-] {+added+}."""
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            seg = a[i1:i2]
            out.append(" ".join(seg) if len(seg) <= 12 else " ".join(seg[:5] + ["…"] + seg[-5:]))
        if op in ("delete", "replace"):
            out.append("[-" + " ".join(a[i1:i2]) + "-]")
        if op in ("insert", "replace"):
            out.append("{+" + " ".join(b[j1:j2]) + "+}")
    return " ".join(out)


def install_override(store, dataset_id: str, log: "NarrativeReviewLog | None" = None) -> None:
    """Make a reference store return approved corrections (and only those) for this release."""
    lg = log or NarrativeReviewLog()
    store.text_override = lambda case, kind, lang, text: lg.approved_text(dataset_id, case, lang, kind, text)[0]


def gate_c_flag(log: NarrativeReviewLog, run: dict[str, Any], texts: dict[str, str]) -> str:
    """Wording of the Gate C info flag for an evaluation run."""
    g = log.gate_c(run.get("evaluation_dataset_id", ""), run["case_id"], run["language"], texts)
    if g["passed"]:
        return f"Reference narratives signed off by {', '.join(g['reviewers'])}"
    return (f"Reference narratives are candidates and have not been signed off yet "
            f"({g['approved']} of {g['required']} texts approved for this patient and language)")


# ---------------------------------------------------------------------- where to correct
# Helps the editor find the passages a revision instruction refers to. It is a lexical pointer only:
# the reviewer's instruction stays authoritative and the editor reads the whole text.
_STOP = set("""about above after again against also among because been before being below between both cannot could
does doing down during each from further have having here into itself just more most only other ought over same should
some such than that their theirs them then there these they this those through under until very were what when where
which while with would your yours text explanation narrative record records patient professional statement sentence
section heading headings missing advises advice instruction action says said needs need line lines only extra
explanation clinical para como esta este estos estas pero porque sobre tiene texto registro paciente explicación
narrativa sección frase falta debe""".split())
_STANDARD_HEADINGS = {
    "en": ["why you came", "what we found", "what this means", "treatment", "warning signs", "follow-up"],
    "es": ["por qué vino", "qué encontramos", "qué significa", "tratamiento", "signos de alarma", "seguimiento"],
}


def _stems(text: str) -> set[str]:
    import re
    return {w[:5] for w in re.findall(r"[a-záéíóúñü]+", (text or "").lower()) if len(w) >= 4 and w not in _STOP}


_NEGATORS = {"no", "not", "never", "without", "none", "denies", "deny", "nor", "sin", "niega", "ni", "nunca"}


def _affirmed_stems(text: str) -> set[str]:
    """Stems of words that are not negated: a word within three words after "no", "without", "denies",
    "sin", "niega"... does not count, so "no blood in vomit and no black stools" does not match an
    instruction about vomiting blood or black stools."""
    import re
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    out = set()
    for i, w in enumerate(words):
        if len(w) >= 4 and w not in _STOP and not (_NEGATORS & set(words[max(0, i - 3):i])):
            out.add(w[:5])
    return out


def instruction_issues(instruction: str) -> list[str]:
    """Split a revision instruction into separate issues (sentences, semicolons, numbered items)."""
    import re
    parts = re.split(r"(?<=[.;!?])\s+|\n+|\s*(?:^|\s)\d+[.)]\s+", (instruction or "").strip())
    return [p.strip(" ;.") for p in parts if p and len(p.strip(" ;.")) > 3]


def _segments(text: str) -> list[tuple[int, int, bool]]:
    """(start, end, is_heading) of each heading line and sentence in the text."""
    import re
    out, pos = [], 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        start = pos
        pos += len(line) + 1
        s = line.strip()
        if not s:
            continue
        nxt = next((l for l in lines[i + 1:] if l.strip()), "")
        if len(s.split()) <= 6 and not re.search(r"[.:;!?]$", s) and nxt:
            off = line.index(s)
            out.append((start + off, start + off + len(s), True))
            continue
        for m in re.finditer(r"[^.!?]+[.!?]*", line):
            if m.group().strip():
                lead = len(m.group()) - len(m.group().lstrip())
                out.append((start + m.start() + lead, start + m.end(), False))
    return out


_ACTION_PREFIX = {
    "remove": "Remove", "delete": "Remove", "eliminar": "Remove", "quitar": "Remove", "borrar": "Remove",
    "add": "Add", "añadir": "Add", "anadir": "Add", "agregar": "Add", "incluir": "Add",
    "replace": "Replace", "change": "Replace", "reword": "Replace", "rewrite": "Replace", "correct": "Replace",
    "sustituir": "Replace", "reemplazar": "Replace", "cambiar": "Replace", "corregir": "Replace",
    "merge": "Merge", "fusionar": "Merge", "unir": "Merge",
}
_ACTION_WORDS = [  # checked in this order; first match wins
    ("Replace", r"only advises|only says|downgrad|too weak|understat|overstat|wrong|incorrect|inaccurate|instead of|"
                r"should (?:say|be|read)|unclear|misleading|contradict|solo indica|solo dice|incorrect[oa]|err[óo]ne[oa]|"
                r"debería decir|en lugar de|poco clar[oa]|contradice"),
    ("Remove", r"not in the record|not supported|unsupported|invented|fabricat|\bextra\b|superfluous|\bremove|\bdelete|"
               r"no está en el registro|no consta|no respaldad|inventad|sobra|eliminar|quitar"),
    ("Add", r"\bmissing|\bomit|\blacks?\b|\bleaves out|\bnot mentioned|\bno mention|\badd\b|\bfalta|\bomite|"
            r"\bno menciona|\bañadir|\bagregar"),
]
ACTION_HELP = {
    "Replace": "rewrite it",
    "Remove": "delete it",
    "Add": "add the missing information",
    "Merge": "remove the heading, keep its text in the section above",
}


def issue_action(issue: str, matches: int = 0, heading: bool = False) -> str:
    """Suggested action for one issue: an explicit prefix ("Remove: ...") wins, then the instruction's wording;
    an issue without action words is "Replace" when a passage was found and "Add" when none was."""
    import re
    m = re.match(r"\s*([A-Za-záéíóúñ]+)\s*:", issue or "")
    if m and m.group(1).lower() in _ACTION_PREFIX:
        return _ACTION_PREFIX[m.group(1).lower()]
    low = (issue or "").lower()
    for action, pattern in _ACTION_WORDS:
        if re.search(pattern, low):
            return "Merge" if action == "Remove" and heading else action
    return "Replace" if matches else "Add"


def locate_issues(text: str, instruction: str, language: str = "en") -> dict[str, Any]:
    """Map each issue of a revision instruction to the sentences or headings it most likely refers to."""
    import re
    issues = instruction_issues(instruction)
    segs = _segments(text or "")
    standard = _STANDARD_HEADINGS.get(language, _STANDARD_HEADINGS["en"])
    hits: dict[tuple[int, int], set[int]] = {}
    found = []
    for n, issue in enumerate(issues, 1):
        quotes = [q.lower() for q in re.findall(r"[\"“'‘]([^\"”'’]{4,})[\"”'’]", issue)]
        istems = _stems(issue)
        matched = []
        for a, b, is_head in segs:
            seg = text[a:b]
            low = seg.lower()
            if quotes and any(q in low for q in quotes):
                matched.append((a, b))
                continue
            if is_head:
                if re.search(r"\b(heading|headings|encabezado|título)\b", issue, re.I) and not any(
                        h in low or low in h or (set(h.split()) & set(low.split()) - {"your", "you", "su"})
                        for h in standard):
                    matched.append((a, b))
                continue
            if len(istems & _affirmed_stems(seg)) >= (1 if len(istems) <= 2 else 2):
                matched.append((a, b))
        for span in matched:
            hits.setdefault(span, set()).add(n)
        heading = any(is_head for a, b, is_head in segs if (a, b) in matched)
        found.append({"number": n, "issue": issue, "matches": len(matched),
                      "action": issue_action(issue, len(matched), heading)})
    return {"issues": found, "spans": sorted((a, b, sorted(ns)) for (a, b), ns in hits.items())}
