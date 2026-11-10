"""Gradio UI for the Narrative Release Review and Sign-off workspace (Gate C).

Reads the imported narrative release and the approved freeze read-only (through evaluation_ui) and writes only
the append-only decision log in narrative_review.py. It never changes clinical-reference data, approvals or hashes.
"""
from __future__ import annotations

import html
import zipfile
from typing import Any

import gradio as gr

import evaluation_ui as ev
import difflib

from narrative_review import ACTION_HELP, DECISIONS, ROLES, TEXT_TYPES, NarrativeReviewLog, locate_issues, sha256
from narrative_freeze import NarrativeApprovalFreeze

_LOG: NarrativeReviewLog | None = None
_LIFECYCLE: NarrativeApprovalFreeze | None = None
_CHECKS: dict[tuple, list[list[str]]] = {}
TEXT_CHOICES = [("Both texts", "both"), ("Professional narrative only", "professional"),
                ("Patient explanation only", "patient")]


def lifecycle() -> NarrativeApprovalFreeze:
    global _LIFECYCLE
    if _LIFECYCLE is None or _LIFECYCLE.root != log().root:
        _LIFECYCLE = NarrativeApprovalFreeze(log().root)
    return _LIFECYCLE
SHORT_TYPES = {"professional": "Professional", "patient": "Patient"}
STATUS_STYLE = {"Approved": "approved", "Approved with comments": "approved", "Needs revision": "rejected"}


def log() -> NarrativeReviewLog:
    global _LOG
    if _LOG is None:
        _LOG = NarrativeReviewLog()
    return _LOG


def _release_sha(dataset_id: str) -> str:
    entry = ev.registry().get(dataset_id)
    return entry.get("narrative_archive_sha256") or entry.get("narrative_json_sha256") or ""


def _originals(dataset_id: str, case_id: str, language: str) -> dict[str, str]:
    store = ev.release_store(dataset_id)
    return {t: store.narrative(case_id, t, language, original=True) or "" for t in TEXT_TYPES}


def _current(dataset_id: str, case_id: str, language: str) -> dict[str, tuple[str, int]]:
    """Text under review for each type: the newest correction, or the release text (version 0)."""
    orig = _originals(dataset_id, case_id, language)
    return {t: log().current_text(dataset_id, case_id, language, t, orig[t]) for t in TEXT_TYPES}


def _texts(dataset_id: str, case_id: str, language: str) -> dict[str, str]:
    return {t: tv[0] for t, tv in _current(dataset_id, case_id, language).items()}


def _badge(dataset_id: str, case_id: str, language: str, text_type: str, text: str, version: int = 0) -> str:
    status = log().text_status(dataset_id, case_id, language, text_type, text)
    d = log().latest(dataset_id, case_id, language, text_type, text)
    who = f" by {html.escape(d['reviewer'])}" if d else ""
    when = (f'<span style="font-weight:400;white-space:nowrap">Decided {d["decided_utc"][:10]}</span>' if d else "")
    kind = STATUS_STYLE.get(status, "pending")
    ver = f" · corrected version {version}" if version else ""
    return (f'<div class="status-banner status-{kind}" role="status"><div style="width:100%">'
            f'<strong>{TEXT_TYPES[text_type]}{ver}</strong>'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:0 12px">'
            f'<span>{html.escape(status)}{who}</span>{when}</div></div></div>')


def _checks(dataset_id: str, case_id: str, language: str, texts: dict[str, str]) -> list[list[str]]:
    """Automatic checks of both reference texts against the approved structured record (not stored as runs)."""
    key = (dataset_id, case_id, language, sha256(texts["professional"]), sha256(texts["patient"]))
    if key in _CHECKS:
        return _CHECKS[key]
    ref = ev.release_store(dataset_id)
    cols = {}
    for t in TEXT_TYPES:
        run = ev.evaluate(ref, task="plain_language", case_id=case_id, language=language, output_text=texts[t],
                          source_text=texts["professional"], use_models=False)
        fs = run["factual_agreement_structured"]["summary"]
        rd = run["readability"]["output"]
        cols[t] = [f"{fs['required_supported']}/{fs['required_items']}", str(fs["critical_problems"]),
                   f"{fs['contradictions']} / {fs['unsupported_additions']}", ev._fmt(rd.get("primary_score")),
                   str(rd.get("words", "—"))]
    labels = ["Required structured facts supported", "Critical problems", "Contradictions / unsupported additions",
              "Readability (higher = easier)", "Words"]
    rows = [[label, cols["professional"][i], cols["patient"][i]] for i, label in enumerate(labels)]
    _CHECKS[key] = rows
    return rows


def _history_rows(dataset_id: str, case_id: str | None = None) -> list[list[str]]:
    rows = [[d["patient_id"], d["language"].upper(), SHORT_TYPES[d["text_type"]], d["decision"], d["reviewer"],
             d["reviewer_role"], d["comment"], d["decided_utc"][:19].replace("T", " ")]
            for d in log().history(dataset_id, case_id)]
    rows += [[c["patient_id"], c["language"].upper(), SHORT_TYPES[c["text_type"]], f"Correction v{c['version']} saved",
              c["editor"], c["editor_role"], c["reason"], c["created_utc"][:19].replace("T", " ")]
             for c in log().corrections(dataset_id) if case_id is None or c["patient_id"] == case_id]
    return sorted(rows, key=lambda r: r[7], reverse=True)


def _needs_revision(dataset_id: str, case_id: str, language: str) -> list[str]:
    cur = _current(dataset_id, case_id, language)
    out = []
    for t, (text, _) in cur.items():
        d = log().latest(dataset_id, case_id, language, t, text)
        if d and d["decision"] == "Needs revision":
            out.append(t)
    return out


_ISSUE_COLOURS = ["#fde68a", "#bfdbfe", "#fbcfe8", "#bbf7d0", "#fed7aa", "#ddd6fe"]


def _where_html(text: str, instruction: str, language: str) -> str:
    """The text under correction with the passages each instruction issue points to highlighted."""
    loc = locate_issues(text, instruction, language)
    if not loc["issues"]:
        return ""
    col = lambda n: _ISSUE_COLOURS[(n - 1) % len(_ISSUE_COLOURS)]
    tag = lambda n: (f'<span aria-label="Issue {n}" style="display:inline-block;min-width:1.5em;padding:0 5px;'
                     f'text-align:center;border-radius:9px;background:{col(n)};border:1px solid rgba(0,0,0,.25);'
                     f'font-weight:700;font-size:0.75em;line-height:1.5;margin-right:6px;user-select:none">'
                     f'{n}</span>')
    chip_col = {"Replace": ("#fef3c7", "#92400e"), "Remove": ("#fee2e2", "#991b1b"), "Add": ("#dcfce7", "#166534"),
                "Merge": ("#e0e7ff", "#3730a3")}
    chip = lambda a: (f'<span title="{ACTION_HELP[a]}" style="display:inline-block;padding:0 7px;border-radius:4px;'
                      f'background:{chip_col[a][0]};color:{chip_col[a][1]};font-weight:700;font-size:0.75em;'
                      f'letter-spacing:.03em;margin-right:6px;user-select:none">{a.upper()}</span>')
    items = "".join(
        f'<li style="margin:4px 0">{tag(i["number"])}{chip(i["action"])}{html.escape(i["issue"])} '
        f'<span style="color:#6b7280;font-size:0.85em">— '
        + (f'{i["matches"]} passage{"s" if i["matches"] != 1 else ""} highlighted: {ACTION_HELP[i["action"]]}' if i["matches"]
           else "no passage found: it may describe something missing")
        + '</span></li>' for i in loc["issues"])
    body, pos = [], 0
    for a, b, ns in loc["spans"]:
        if a < pos:
            continue
        body.append(html.escape(text[pos:a]))
        style = (f"background:{col(ns[0])};border-radius:3px;padding:0 2px;"
                 + (f"box-shadow:inset 0 -3px 0 {col(ns[1])};" if len(ns) > 1 else ""))
        body.append(f'<mark title="Issue {", ".join(map(str, ns))}" style="{style}">'
                    f'{"".join(tag(n) for n in ns)}{html.escape(text[a:b])}</mark>')
        pos = b
    body.append(html.escape(text[pos:]))
    return ('<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;background:#fff">'
            '<strong>Suggested passages to check</strong><div style="color:#4b5563;font-size:0.88em;margin-top:2px">'
            'Matched from the words of the instruction. A highlighted passage may already be correct: check it '
            'against the instruction. Something missing from the text cannot be highlighted, so read the whole '
            'text. The action (Replace, Remove, Add, Merge) is suggested from the wording; a reviewer can set it '
            'by starting an issue with "Remove:", "Add:", "Replace:" or "Merge:".</div>'
            f'<ol style="list-style:none;padding-left:0;margin:6px 0 10px">{items}</ol>'
            '<div style="white-space:pre-wrap;max-height:380px;overflow-y:auto;line-height:1.6;'
            f'border-top:1px solid #e5e7eb;padding-top:8px">{"".join(body)}</div></div>')


def _preview_html(before: str, after: str) -> str:
    """Before/after preview of an unsaved correction: removed words struck through, added words underlined."""
    before, after = before or "", after or ""
    if not after.strip() or after.strip() == before.strip():
        return ('<div style="color:#6b7280;font-size:0.9em;padding:6px 2px">Preview of your changes: no changes '
                'yet. Edit the text above; removed words appear struck through in red, added words in green.</div>')
    import re

    def render(tokens):
        out = ""
        for t in tokens:
            if t.startswith("\n"):
                out += t
            else:
                out += ("" if not out or out.endswith("\n") else " ") + html.escape(t)
        return out

    def words(x, y):
        """Word-level diff that keeps line breaks as their own tokens, so lines never merge."""
        xa, ya = re.findall(r"\n+|[^\s]+", x), re.findall(r"\n+|[^\s]+", y)
        out, d, n = "", 0, 0
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=xa, b=ya, autojunk=False).get_opcodes():
            if op == "equal":
                seg = render(xa[i1:i2])
                out += ("" if not out or out.endswith("\n") or seg.startswith("\n") else " ") + seg
                continue
            for kind, toks in (("del", xa[i1:i2]), ("ins", ya[j1:j2])):
                if (kind == "del" and op == "insert") or (kind == "ins" and op == "delete"):
                    continue
                words_only = [t for t in toks if not t.startswith("\n")]
                if kind == "del":
                    d += len(words_only)
                else:
                    n += len(words_only)
                style = ("background:#fee2e2;color:#991b1b" if kind == "del"
                         else "background:#dcfce7;color:#166534")
                # a removed line break is shown as ↵ inside the deletion (the lines are joined after the edit);
                # an added line break is shown as ↵ and then really breaks the line
                parts = re.split(r"(\n+)", render(toks))
                if kind == "del":
                    seg = f'<del style="{style}">' + "".join(" ↵ " * len(p) if p.startswith("\n") else p
                                                             for p in parts) + "</del>"
                else:
                    seg = "".join(f'<ins style="{style}">{"↵" * len(p)}</ins>{p}' if p.startswith("\n")
                                  else (f'<ins style="{style}">{p}</ins>' if p.strip() else p) for p in parts)
                out += ("" if not out or out.endswith("\n") or seg.startswith("\n") else " ") + seg
        return out, d, n

    pa = [x for x in before.strip().split("\n\n")]
    pb = [x for x in after.strip().split("\n\n")]
    blocks, n_del, n_add, hidden = [], 0, 0, 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=pa, b=pb, autojunk=False).get_opcodes():
        if op == "equal":
            hidden += i2 - i1
            continue
        old, new = "\n\n".join(pa[i1:i2]), "\n\n".join(pb[j1:j2])
        body, d, n = words(old, new)
        n_del, n_add = n_del + d, n_add + n
        blocks.append(f'<div style="white-space:pre-wrap;line-height:1.6;border-left:3px solid #f59e0b;'
                      f'padding:4px 10px;margin:8px 0">{body}</div>')
    note = (f'<div style="color:#6b7280;font-size:0.85em">{hidden} unchanged paragraph{"s" if hidden != 1 else ""} '
            'not shown.</div>' if hidden else "")
    return ('<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;background:#fff">'
            f'<strong>Preview of your changes (not saved yet)</strong> <span style="color:#6b7280;font-size:0.85em">'
            f'{n_del} word{"s" if n_del != 1 else ""} removed · {n_add} added · removed in red, added in green'
            f'</span>{"".join(blocks)}{note}</div>')


def _correction_panel(dataset_id, case_id, language, which=None):
    """(accordion, text choice, instruction, editable text, where-to-correct, preview) for 'Needs revision' texts."""
    flagged = _needs_revision(dataset_id, case_id, language) if case_id else []
    if not flagged:
        return gr.update(visible=False), gr.update(choices=[], value=None), "", "", "", ""
    which = which if which in flagged else flagged[0]
    text, version = _current(dataset_id, case_id, language)[which]
    d = log().latest(dataset_id, case_id, language, which, text)
    instruction = (f"**Revision instruction** from {html.escape(d['reviewer'])} ({d['decided_utc'][:10]}), "
                   f"{TEXT_TYPES[which].lower()}{f' v{version}' if version else ''}:\n\n> "
                   + html.escape(d["comment"]).replace("\n", "\n> "))
    return (gr.update(visible=True, open=True), gr.update(choices=[(TEXT_TYPES[t], t) for t in flagged], value=which),
            instruction, text, _where_html(text, d["comment"], language), _preview_html(text, text))


def _view(dataset_id, case_id, lang_label):
    empty = ("", "", "", "", [], {}, {}, [], gr.update(visible=False), gr.update(choices=[], value=None), "", "", "", "")
    if not case_id or not ev._known_release(dataset_id):
        return empty
    language = ev.LANGS[lang_label]
    cur = _current(dataset_id, case_id, language)
    texts = {t: tv[0] for t, tv in cur.items()}
    return (texts["professional"], texts["patient"],
            _badge(dataset_id, case_id, language, "professional", *cur["professional"]),
            _badge(dataset_id, case_id, language, "patient", *cur["patient"]),
            _checks(dataset_id, case_id, language, texts),
            ev.release_store(dataset_id).record(case_id), ev._case_limitations(case_id),
            _history_rows(dataset_id, case_id), *_correction_panel(dataset_id, case_id, language))


def _record(dataset_id, case_id, lang_label, which, decision, reviewer, role, comment):
    if not ev._known_release(dataset_id):
        raise gr.Error("Import and select the narrative release first (Clinical NLP Evaluation → Upload Evaluation Dataset).")
    if not case_id:
        raise gr.Error("Select a patient first.")
    if not decision:
        raise gr.Error("Choose a decision.")
    language = ev.LANGS[lang_label]
    texts = _texts(dataset_id, case_id, language)
    targets = list(TEXT_TYPES) if which == "both" else [which]
    try:
        for t in targets:
            log().record(dataset_id=dataset_id, release_sha256=_release_sha(dataset_id), case_id=case_id,
                         language=language, text_type=t, text=texts[t], decision=decision, reviewer=reviewer or "",
                         role=role or "Other", comment=comment or "")
    except ValueError as e:
        raise gr.Error(str(e)) from e
    names = " and ".join(TEXT_TYPES[t].lower() for t in targets)
    msg = (f'<div class="status-banner status-{STATUS_STYLE.get(decision, "neutral")}" role="status"><span>'
           f'<strong>Recorded:</strong> {html.escape(decision)} for the {names} of {case_id} '
           f'({language.upper()}) by {html.escape(reviewer.strip())}.</span></div>')
    return (msg, *_view(dataset_id, case_id, lang_label), "")


def _save_correction(dataset_id, case_id, lang_label, which, corrected, editor, role, reason):
    if not ev._known_release(dataset_id) or not case_id:
        raise gr.Error("Select the release and patient first.")
    if not which:
        raise gr.Error("No text of this patient and language is marked 'Needs revision'.")
    language = ev.LANGS[lang_label]
    original = _originals(dataset_id, case_id, language)[which]
    try:
        c = log().record_correction(dataset_id=dataset_id, case_id=case_id, language=language, text_type=which,
                                    original=original, corrected=corrected or "", editor=editor or "",
                                    role=role or "Other", reason=reason or "")
    except ValueError as e:
        raise gr.Error(str(e)) from e
    msg = (f'<div class="status-banner status-pending" role="status"><span><strong>Correction v{c["version"]} saved</strong> '
           f'for the {TEXT_TYPES[which].lower()} of {case_id} ({language.upper()}). It now needs a fresh decision '
           f'below.</span><span class="status-badge">Not reviewed</span></div>'
           f'<details style="margin-top:6px"><summary>Changes (word level)</summary><p style="font-size:0.9em">'
           f'{html.escape(c["word_diff"])}</p></details>')
    return (msg, *_view(dataset_id, case_id, lang_label), "")


def _status(dataset_id):
    if not ev._known_release(dataset_id):
        return "", [], gr.update(value=None)
    st = log().release_status(dataset_id, ev.release_store(dataset_id))
    if st["signed_off"]:
        banner = ('<div class="status-banner status-approved" role="status"><span><strong>Narrative release signed off:</strong> '
                  f'all {st["total"]} reference texts are approved.</span><span class="status-badge">Signed off</span></div>')
    else:
        banner = ('<div class="status-banner status-pending" role="status"><span><strong>Narrative release not signed off yet:</strong> '
                  f'{st["approved"]} of {st["total"]} reference texts approved'
                  + (f', {st["needs_revision"]} need revision' if st["needs_revision"] else "")
                  + '.</span><span class="status-badge">Not signed off</span></div>')
    path = log().root / "exports"
    path.mkdir(parents=True, exist_ok=True)
    out = path / f"narrative_review_decisions_{dataset_id}.csv"
    out.write_text(log().export_csv(dataset_id), encoding="utf-8")
    return banner, st["rows"], str(out)


def _freeze_candidates() -> list[dict[str, Any]]:
    """Every imported release with its Sign-off Status coverage (read-only)."""
    out = []
    for item in ev.registry().all():
        if item.get("import_validation_result") != "Passed":
            continue
        did = item["evaluation_dataset_id"]
        try:
            snap = lifecycle().snapshot(did, ev.release_store(did), item, log())
            cov = lifecycle().coverage(snap, "approved_or_comments")
            accepted, required, passed = cov["accepted"], cov["required"], cov["passed"]
        except (KeyError, OSError, ValueError):
            accepted, required, passed = 0, 0, False
        out.append({"id": did, "name": item.get("display_name") or did, "accepted": accepted,
                    "required": required, "passed": passed})
    return out


def _freeze_release_choice(selected=None):
    """The release to freeze: the signed-off one. A choice is shown only when several releases are signed off."""
    cands = _freeze_candidates()
    done = [c for c in cands if c["passed"]]
    pool = done or cands[:1]
    choices = [(c["name"], c["id"]) for c in pool]
    value = selected if selected in {c["id"] for c in pool} else (pool[0]["id"] if pool else None)
    return gr.update(choices=choices, value=value, visible=len(done) > 1)


def _release_line(dataset_id, accepted, required) -> str:
    entry = ev.registry().get(dataset_id)
    done = required and accepted == required
    status = (f"Sign-off complete: {accepted}/{required}" if done
              else f"Sign-off incomplete: {accepted}/{required}")
    return (f'<div style="padding:8px 12px;border:1px solid var(--border-color-primary);border-radius:8px">'
            f'<span style="color:var(--body-text-color-subdued);font-size:0.85em">Release</span><br>'
            f'<strong>{html.escape(entry.get("display_name") or dataset_id)}</strong> · '
            f'{"✅" if done else "⏳"} {status}</div>')


def _approval_freeze_status(dataset_id):
    """Live freeze readiness, consuming the existing Sign-off Status."""
    empty = ("", "", gr.update(value=[], visible=False), [], [], gr.update(value=None), "",
             gr.update(interactive=False))
    if not ev._known_release(dataset_id):
        return empty
    try:
        entry = ev.registry().get(dataset_id)
        snapshot = lifecycle().snapshot(dataset_id, ev.release_store(dataset_id), entry, log())
        coverage = lifecycle().coverage(snapshot, "approved_or_comments")
    except (KeyError, OSError, ValueError) as exc:
        banner = (f'<div class="status-banner status-fail" role="alert"><span><strong>Freeze unavailable:</strong> '
                  f'{html.escape(str(exc))}</span><span class="status-badge">Blocked</span></div>')
        return ("", banner, gr.update(value=[], visible=False), [], [], gr.update(value=None), "",
                gr.update(interactive=False))
    pending = [[p["patient_id"], p["language"].upper(), TEXT_TYPES[p["text_type"]],
                p.get("decision") or "Not reviewed", p.get("pending_finding_id") or "", p["text_sha256"]]
               for p in coverage["pending"]]
    freezes = lifecycle().freezes(dataset_id)
    current = lifecycle().matching_freeze(dataset_id, snapshot["current_records_sha256"])
    if current and not coverage["passed"]:
        state = (f'<div class="status-banner status-pending" role="status"><span><strong>Sign-off changed after '
                 f'freezing:</strong> freeze {html.escape(current["freeze_id"])} holds these texts, but only '
                 f'{coverage["accepted"]} of {coverage["required"]} are still signed off (see below).</span>'
                 f'<span class="status-badge">Check sign-off</span></div>')
    elif coverage["passed"] and current:
        state = (f'<div class="status-banner status-approved" role="status"><span><strong>'
                 f'{html.escape(current["freeze_id"])}</strong> (verified). Download it below.</span>'
                 '<span class="status-badge">Frozen</span></div>')
    elif coverage["passed"]:
        changed = " The texts changed after the last freeze, so a new freeze is needed." if freezes else ""
        state = (f'<div class="status-banner status-approved" role="status"><span><strong>Ready to freeze:</strong> '
                 f'all {coverage["required"]} current texts are signed off.{changed} Press “Create freeze”.</span>'
                 f'<span class="status-badge">Ready</span></div>')
    else:
        qa = sorted({p.get("pending_finding_id") for p in coverage["pending"] if p.get("pending_finding_id")})
        qa_text = f' Outstanding finding: {", ".join(qa)}.' if qa else ""
        state = (f'<div class="status-banner status-pending" role="status"><span><strong>Not ready:</strong> '
                 f'{coverage["accepted"]} of {coverage["required"]} current texts are signed off.'
                 f'{html.escape(qa_text)} Complete the sign-off first (texts listed below).</span>'
                 f'<span class="status-badge">Freeze blocked</span></div>')
    records = [[a["signoff_record_id"], a["narrative_run_version_id"], len(a.get("decision_ids", [])),
                ", ".join(r["name"] for r in a.get("reviewers", [])), a["approved_utc"], a["consumed_utc"]]
               for a in reversed(lifecycle().signoff_records(dataset_id))]
    freeze_rows = []
    for item in reversed(freezes):
        try:
            valid = lifecycle().verify_freeze(item)["valid"]
        except (OSError, ValueError, zipfile.BadZipFile):
            valid = False
        is_current = bool(current) and item["freeze_id"] == current["freeze_id"]
        freeze_rows.append([item["freeze_id"], item["created_utc"][:19].replace("T", " "),
                            "Verified" if valid else "Verification failed",
                            "Yes" if is_current else "No (texts changed since)", item["zip_sha256"]])
    latest_path = None
    if freezes:
        checked = lifecycle().verify_freeze(current or freezes[-1])
        latest_path = checked["path"] if checked["valid"] else None
    tech = (f"**Narrative run/version ID:** `{snapshot['narrative_run_version_id']}`  \n"
            f"**Current records SHA-256:** `{snapshot['current_records_sha256']}`  \n"
            f"**Source release SHA-256:** `{snapshot['source_release_sha256']}`")
    return (_release_line(dataset_id, coverage["accepted"], coverage["required"]), state,
            gr.update(value=pending, visible=bool(pending)), records, freeze_rows, gr.update(value=latest_path), tech,
            gr.update(interactive=bool(coverage["passed"] and not current)))


def _freeze_release(dataset_id):
    if not ev._known_release(dataset_id):
        raise gr.Error("Import and select a narrative release first.")
    try:
        frozen = lifecycle().create_freeze(
            dataset_id=dataset_id, store=ev.release_store(dataset_id), entry=ev.registry().get(dataset_id),
            eval_root=ev.EVAL_RUNTIME, log=log(),
        )
        verified = lifecycle().verify_freeze(frozen)
        if not verified["valid"]:
            raise ValueError("The new freeze did not pass verification: " + "; ".join(verified["errors"]))
    except (KeyError, OSError, PermissionError, ValueError, zipfile.BadZipFile) as exc:
        raise gr.Error(str(exc)) from exc
    # One banner only: the status banner above already reports the verified freeze and its ZIP SHA-256.
    message = ""
    return (message, *_approval_freeze_status(dataset_id))


def add_narrative_review_tabs():
    try:
        ev.registry()
    except Exception as e:  # noqa: BLE001
        with gr.Tab("Narrative review"):
            gr.Markdown(f"**Narrative review unavailable:** {html.escape(str(e))}")
        return
    with gr.Tab("Review Narratives", id="nr_review") as review_tab:
        gr.Markdown("Narrative sign-off: a qualified reviewer reads each generated reference text against the approved "
                    "structured record and records a decision. A decision applies only to the exact text shown "
                    "(SHA-256). This workspace never changes the clinical reference or its approvals.")
        with gr.Row():
            release = ev._release_dropdown("Narrative release", scale=3)
            case = gr.Dropdown(ev.PATIENT_CHOICES, value="", label="Patient", scale=1)
            language = gr.Radio(list(ev.LANGS), value="English", label="Language", scale=1)
        with gr.Row():
            with gr.Column():
                prof_badge = gr.HTML()
                professional = gr.Textbox(lines=16, label="Professional clinical narrative", interactive=False,
                                          autoscroll=False)
            with gr.Column():
                pat_badge = gr.HTML()
                patient = gr.Textbox(lines=16, label="Patient explanation", interactive=False, autoscroll=False)
        checks = gr.Dataframe(headers=["Automatic check against the approved record", "Professional narrative",
                                       "Patient explanation"], interactive=False, wrap=True,
                              column_widths=["46%", "27%", "27%"],
                              label="Automatic checks (support the review; they do not replace it)")
        with gr.Accordion("Approved structured record and documented limitations", open=False):
            with gr.Row():
                facts = gr.JSON(label="Approved structured record (authoritative)")
                limitations = gr.JSON(label="Documented approved-reference limitations")
        with gr.Accordion("Correct a text marked 'Needs revision'", open=True, visible=False) as corr_box:
            gr.Markdown("Edit the text by hand to follow the revision instruction. The release file is not changed: "
                        "the correction is saved as a new version with its own SHA-256, linked to the decision, and "
                        "needs a fresh decision below. Only an approved correction is used by the evaluation.")
            corr_which = gr.Radio([], label="Text to correct")
            instruction = gr.Markdown()
            where = gr.HTML()
            corrected = gr.Textbox(lines=16, label="Corrected text (editable)", autoscroll=False)
            preview = gr.HTML()
            with gr.Row():
                editor = gr.Textbox(label="Corrected by", scale=1)
                editor_role = gr.Dropdown(list(ROLES), value=ROLES[0], label="Role", scale=1, min_width=280)
            reason = gr.Textbox(lines=2, label="What was corrected",
                                placeholder="For example: 1. Warning signs: any vomited blood or black stools is an "
                                            "emergency. 3. Removed the endoscopy sentence.")
            save_corr = gr.Button("Save corrected version", variant="primary")
        gr.Markdown("#### Record a decision")
        with gr.Row():
            which = gr.Radio(TEXT_CHOICES, value="both", label="Text reviewed", scale=1, min_width=420)
            decision = gr.Radio(list(DECISIONS), value=None, label="Decision", scale=1, min_width=420)
        with gr.Row():
            reviewer = gr.Textbox(label="Reviewer name", scale=1)
            role = gr.Dropdown(list(ROLES), value=ROLES[0], label="Role", scale=1, min_width=280)
        comment = gr.Textbox(lines=2, label="Comment (required unless Approved)",
                             placeholder="For Needs revision: one issue per sentence or line. You may start an issue with "
                                         "Replace:, Remove:, Add: or Merge:, e.g. \"Remove: endoscopy sentence, not in the record.\"")
        record_btn = gr.Button("Record decision", variant="primary")
        result = gr.HTML()
        history = gr.Dataframe(headers=["Patient", "Language", "Text", "Decision", "Reviewer", "Role", "Comment",
                                        "Date time"], interactive=False, wrap=True,
                               column_widths=["80px", "85px", "12ch", "105px", "90px", "12ch", "240px", "120px"], max_height=520,
                               elem_classes=["nr-history"],
                               label="Decisions for this patient (append-only, newest first)")
        outs = [professional, patient, prof_badge, pat_badge, checks, facts, limitations, history,
                corr_box, corr_which, instruction, corrected, where, preview]
        for component in (release, case, language):
            component.change(_view, [release, case, language], outs)
            component.change(lambda: "", None, result)
        corr_which.input(lambda ds, c, l, w: _correction_panel(ds, c, ev.LANGS[l], w)[2:],
                         [release, case, language, corr_which], [instruction, corrected, where, preview])
        corrected.input(lambda ds, c, l, w, t: _preview_html(_current(ds, c, ev.LANGS[l])[w][0], t) if c and w else "",
                        [release, case, language, corr_which, corrected], preview, show_progress="hidden")
        record_btn.click(_record, [release, case, language, which, decision, reviewer, role, comment],
                         [result, *outs, comment])
        save_corr.click(_save_correction, [release, case, language, corr_which, corrected, editor, editor_role, reason],
                        [result, *outs, reason])
        review_tab.select(ev._release_choices, outputs=release).then(_view, [release, case, language], outs)

    with gr.Tab("Sign-off Status", id="nr_status") as status_tab:
        gr.Markdown("Sign-off status of the whole narrative release: 10 patients × English/Spanish × professional narrative and "
                    "patient explanation. The evaluation workspace reads this status and reports it on every run.")
        release_s = ev._release_dropdown("Narrative release")
        banner = gr.HTML()
        table = gr.Dataframe(headers=["Patient", "Language", "Text", "Status", "Reviewer", "Date time"],
                             interactive=False, wrap=True)
        download = gr.File(label="Download all decisions (CSV)")
        release_s.change(_status, release_s, [banner, table, download])
        status_tab.select(ev._release_choices, outputs=release_s).then(_status, release_s, [banner, table, download])

    with gr.Tab("Freeze and Download", id="nr_freeze_download") as approval_tab:
        gr.Markdown(
            "Uses the completed **Sign-off Status** to create and download a verified, immutable ZIP. It does not "
            "require another approval or change the Clinical Reference freeze."
        )
        release_a = gr.Dropdown(choices=[], value=None, label="Signed-off release to freeze", visible=False)
        release_line = gr.HTML()
        lifecycle_banner = gr.HTML()
        pending = gr.Dataframe(
            headers=["Patient", "Language", "Text", "Current decision", "Finding", "Text SHA-256"],
            interactive=False, wrap=True, label="Texts still to sign off", visible=False,
        )
        freeze_btn = gr.Button("Create freeze", variant="primary")
        action_result = gr.HTML()
        freeze_download = gr.File(label="Download the verified freeze ZIP")
        freezes_table = gr.Dataframe(
            headers=["Freeze ID", "Created (UTC)", "Verification", "Matches current texts", "ZIP SHA-256"],
            interactive=False, wrap=True, label="Freezes of this release (immutable)",
        )
        with gr.Accordion("Technical details", open=False):
            tech = gr.Markdown()
            approvals_table = gr.Dataframe(
                headers=["Sign-off record ID", "Narrative run/version ID", "Decisions", "Reviewers",
                         "Sign-off completed UTC", "Consumed UTC"],
                interactive=False, wrap=True, label="Sign-off records bound to freezes (append-only)",
            )
        status_outputs = [release_line, lifecycle_banner, pending, approvals_table, freezes_table, freeze_download,
                          tech, freeze_btn]
        release_a.change(_approval_freeze_status, release_a, status_outputs)
        release_a.change(lambda: "", None, action_result)
        freeze_btn.click(_freeze_release, release_a, [action_result, *status_outputs])
        approval_tab.select(_freeze_release_choice, release_a, release_a).then(
            _approval_freeze_status, release_a, status_outputs
        )
