from __future__ import annotations

import html
import json
import os
from pathlib import Path

try:
    import gradio as gr
except ImportError as exc:  # Clearer error than a later attribute failure.
    raise SystemExit("Gradio is not installed. Run: python -m pip install -r requirements.txt") from exc

from review_core import (
    APPROVAL_DECISIONS,
    ISSUE_DECISIONS,
    SIGNOFF_DECLARATION,
    ReviewError,
    ReviewStore,
)


_RUNTIME_OVERRIDE = os.environ.get("CLINICAL_REVIEW_RUNTIME")
STORE = ReviewStore(Path(_RUNTIME_OVERRIDE)) if _RUNTIME_OVERRIDE else ReviewStore()


CLINICAL_THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.teal,
    secondary_hue=gr.themes.colors.blue,
    neutral_hue=gr.themes.colors.slate,
).set(
    button_primary_background_fill="#0F766E",
    button_primary_background_fill_hover="#115E59",
    button_primary_border_color="#0F766E",
    button_primary_text_color="#FFFFFF",
    button_primary_background_fill_dark="#0D9488",
    button_primary_background_fill_hover_dark="#14B8A6",
    button_primary_border_color_dark="#2DD4BF",
    button_primary_text_color_dark="#FFFFFF",
    body_background_fill="#F8FAFC",
    body_background_fill_dark="#0F172A",
    block_background_fill="#FFFFFF",
    block_background_fill_dark="#111827",
    block_border_color="#CBD5E1",
    block_border_color_dark="#475569",
    block_label_background_fill="#F8FAFC",
    block_label_background_fill_dark="#1E293B",
    block_label_border_color="#CBD5E1",
    block_label_border_color_dark="#475569",
    block_label_text_color="#0F172A",
    block_label_text_color_dark="#F8FAFC",
    block_title_background_fill="#F8FAFC",
    block_title_text_color="#0F172A",
    block_title_text_color_dark="#F8FAFC",
    body_text_color="#0F172A",
    body_text_color_dark="#F8FAFC",
    body_text_color_subdued="#475569",
    body_text_color_subdued_dark="#CBD5E1",
)


CLINICAL_CSS = """
.app-footer { margin-top: 24px; padding-top: 10px; border-top: 1px solid #e5e7eb; text-align: center; }
.app-footer p { font-size: 0.82em; color: #6b7280; }
.nr-history .table-wrap { overflow-x: auto; }
.nr-history table { min-width: 910px; }
.nr-history td, .nr-history th, .nr-history td * { word-break: normal !important; overflow-wrap: normal !important; hyphens: none; }
.ev-wide .table-wrap { overflow-x: auto; }
.ev-wide table { min-width: 1100px; }
.ev-wide td, .ev-wide th, .ev-wide td * { word-break: normal !important; overflow-wrap: anywhere; hyphens: none; }
/* every table: column headings wrap onto more lines instead of being cut off with "…" */
.table-wrap th, .table-wrap th * { white-space: normal !important; overflow: visible !important;
  text-overflow: clip !important; word-break: normal !important; overflow-wrap: break-word; }
/* Evaluation tab tables are plain HTML (evaluation_ui.html_table): as tall as their rows, no scroll box */
.ev-title { font-size: var(--block-label-text-size, 0.85em); color: var(--block-label-text-color, #6b7280);
  margin: 4px 0 6px; }
.ev-table { width: 100%; }
.ev-table table { table-layout: fixed; width: 100%; border-collapse: collapse; margin: 0; font-size: 0.85em;
  border: 1px solid var(--border-color-primary, #d1d5db) !important; }
.ev-table th, .ev-table td { text-align: left; vertical-align: top;
  padding: 7px 9px; border: 1px solid var(--border-color-primary, #d1d5db) !important; white-space: normal;
  word-break: normal; overflow-wrap: break-word; hyphens: none; }
.ev-table th { font-weight: 600; background: var(--background-fill-secondary, #f9fafb); overflow-wrap: normal;
  hyphens: manual; }
.ev-table tbody tr:nth-child(even) td { background: var(--table-even-background-fill, rgba(0,0,0,0.02)); }
.ev-table td.ev-empty { color: var(--body-text-color-subdued, #6b7280); }
.ev-find table { table-layout: fixed; width: 100%; }
.ev-find td, .ev-find td * { overflow-wrap: anywhere; word-break: normal !important; }
.ev-pager { align-items: center !important; gap: 10px !important; }
.ev-page p { margin: 0; text-align: center; font-size: 0.9em; }
.ev-section h3 { margin: 18px 0 2px; }
.rq-hidden { display: none !important; }
.ev-status { min-height: 60px; }
.rq-table tbody tr { cursor: pointer; }
.rq-table tbody tr:hover td { background: var(--background-fill-secondary, #f3f4f6); }
.rq-table tbody tr.rq-sel td { background: rgba(20, 184, 166, 0.14) !important; }
.rq-panel th { width: 24%; }
/* readability table: output columns blue, reference columns green (soft tints for light and dark mode) */
.ev-read td:nth-child(3), .ev-read th:nth-child(3), .ev-read td:nth-child(5), .ev-read th:nth-child(5) {
  background-color: rgba(59, 130, 246, 0.12) !important; }
.ev-read td:nth-child(4), .ev-read th:nth-child(4), .ev-read td:nth-child(6), .ev-read th:nth-child(6) {
  background-color: rgba(34, 197, 94, 0.13) !important; }
.ev-read-note p { font-size: 0.85em; opacity: 0.8; margin-top: -6px; }
.ev-output, .ev-output * { word-break: normal !important; overflow-wrap: break-word !important; hyphens: none; }
.clinical-header {
    border-left: 0.45rem solid #0F766E;
    border-radius: 0.65rem;
    padding: 1rem 1.15rem;
    background: linear-gradient(135deg, #F0FDFA 0%, #EFF6FF 100%);
    color: #0F172A;
}
.status-banner {
    border: 1px solid currentColor;
    border-radius: 0.55rem;
    font-weight: 650;
}
.status-banner { margin: 0.55rem 0; padding: 0.65rem 0.8rem; display: flex; align-items: center;
    justify-content: space-between; gap: 0.75rem; flex-wrap: wrap; }
.status-banner > span:first-child { flex: 1 1 0; min-width: 0; }
.next-step { margin: 0.1rem 0 0.6rem 0.2rem; color: var(--body-text-color-subdued); }
.status-badge { flex-shrink: 0; border: 1px solid currentColor; border-radius: 999px; padding: 0.1rem 0.7rem;
    font-size: 0.85em; background: rgba(255, 255, 255, 0.6); }
.status-approved { background: #DCFCE7; color: #166534; }
.status-pending { background: #FEF3C7; color: #92400E; }
.status-rejected { background: #FEE2E2; color: #991B1B; }
.status-neutral { background: #F1F5F9; color: #334155; }
.decision-help details { margin: 0.4rem 0 0.7rem; }
.decision-help summary {
    align-items: center;
    color: #334155;
    cursor: pointer;
    display: inline-flex;
    font-size: 0.92rem;
    font-weight: 650;
    gap: 0.45rem;
    list-style: none;
    padding: 0.2rem 0;
}
.decision-help summary::-webkit-details-marker { display: none; }
.decision-help .info-icon {
    align-items: center;
    border: 1.5px solid #475569;
    border-radius: 999px;
    display: inline-flex;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.78rem;
    height: 1.35rem;
    justify-content: center;
    line-height: 1;
    width: 1.35rem;
}
.decision-help summary:hover, .decision-help summary:focus-visible { color: #0F766E; }
.decision-help summary:focus-visible { outline: 2px solid #0F766E; outline-offset: 3px; border-radius: 0.25rem; }
.decision-help p {
    border-left: 0.25rem solid #0F766E;
    color: #334155;
    margin: 0.55rem 0 0;
    max-width: 68rem;
    padding: 0.55rem 0.75rem;
}
.block-title, .block-label {
    background: #F8FAFC !important;
    border-color: #CBD5E1 !important;
    color: #0F172A !important;
}
.block-title svg, .block-label svg {
    color: #0F766E !important;
    stroke: #0F766E !important;
}
.approved-action button {
    background: #15803D !important;
    border-color: #166534 !important;
    color: #FFFFFF !important;
}
.approved-action button:hover { background: #166534 !important; }
.pending-action button {
    background: #F59E0B !important;
    border-color: #B45309 !important;
    color: #422006 !important;
}
.pending-action button:hover { background: #D97706 !important; color: #FFFFFF !important; }
.blocking-note {
    border-left: 0.35rem solid #B91C1C;
    background: #FEF2F2;
    color: #7F1D1D;
    padding: 0.7rem 0.85rem;
}
.dark .clinical-header {
    background: linear-gradient(135deg, #134E4A 0%, #172554 100%);
    border-left-color: #5EEAD4;
    color: #F8FAFC;
}
.dark .status-approved { background: #14532D; color: #DCFCE7; }
.dark .status-pending { background: #78350F; color: #FEF3C7; }
.dark .status-rejected { background: #7F1D1D; color: #FEE2E2; }
.dark .status-neutral { background: #1E293B; color: #E2E8F0; }
.dark .blocking-note { background: #450A0A; color: #FECACA; border-left-color: #F87171; }
.dark .decision-help summary, .dark .decision-help p { color: #E2E8F0; }
.dark .decision-help .info-icon { border-color: #94A3B8; }
.dark .decision-help summary:hover, .dark .decision-help summary:focus-visible { color: #5EEAD4; }
.dark .decision-help summary:focus-visible { outline-color: #5EEAD4; }
.dark .decision-help p { border-left-color: #5EEAD4; }
.dark .block-title, .dark .block-label {
    background: #1E293B !important;
    border-color: #475569 !important;
    color: #F8FAFC !important;
}
.dark .block-title svg, .dark .block-label svg {
    color: #5EEAD4 !important;
    stroke: #5EEAD4 !important;
}
.dark .pending-action button {
    background: #B45309 !important;
    border-color: #F59E0B !important;
    color: #FFFFFF !important;
}
footer .show-api,
footer .built-with,
footer .divider {
    display: none !important;
}
@media (prefers-contrast: more) {
    .status-banner, .approved-action button, .pending-action button {
        border-width: 2px !important;
    }
}
/* Sub-tabs of each workspace as a left-side menu, so no tab is hidden behind the "..." overflow. */
.tabs .tabs { flex-direction: row; align-items: flex-start; gap: 1.25rem; }
.tabs .tabs > .tab-wrapper { flex: 0 0 15rem; height: auto; padding-bottom: 0; position: sticky; top: 0.5rem;
    align-items: stretch; border-right: 1px solid var(--border-color-primary); }
.tabs .tabs > .tab-wrapper > .tab-container { flex-direction: column; align-items: stretch; height: auto; width: 100%; }
.tabs .tabs > .tab-wrapper > .tab-container:after { display: none; }
.tabs .tabs > .tab-wrapper > .tab-container > button { height: auto; width: 100%; white-space: normal; text-align: left;
    justify-content: flex-start; padding: 0.6rem 0.9rem; line-height: 1.3; border-radius: 0.4rem 0 0 0.4rem; }
.tabs .tabs > .tab-wrapper > .tab-container > button.selected { background-color: var(--background-fill-secondary);
    font-weight: 600; }
.tabs .tabs > .tab-wrapper > .tab-container > button.selected:after { top: 0; bottom: 0; left: auto; right: -1px;
    width: 3px; height: auto; }
.tabs .tabs > .tab-wrapper > .overflow-menu { display: none; }
.tabs .tabs > .tabitem { flex: 1 1 auto; min-width: 0; }
/* Evaluation tab "Fact and evidence checks": a radio styled as a row of tabs (wraps instead of hiding tabs) */
.ev-switch .wrap { display: flex; flex-wrap: wrap; gap: 0 !important; border-bottom: 1px solid var(--border-color-primary); }
.ev-switch label { background: none !important; border: none !important; box-shadow: none !important;
    border-radius: 0 !important; padding: 0.55rem 0.9rem !important; margin-bottom: -1px; cursor: pointer;
    border-bottom: 3px solid transparent !important; color: var(--body-text-color-subdued); }
.ev-switch label.selected { border-bottom-color: var(--color-accent) !important; color: var(--color-accent);
    font-weight: 600; }
.ev-switch input[type=radio] { display: none; }
@media (max-width: 768px) {
    .tabs .tabs { flex-direction: column; }
    .tabs .tabs > .tab-wrapper { flex-basis: auto; width: 100%; position: static; border-right: none;
        border-bottom: 1px solid var(--border-color-primary); }
    .tabs .tabs > .tabitem { width: 100%; }
}
"""


def status_banner(kind: str, message: str, badge: str | None = None) -> str:
    labels = {
        "approved": "Completed",
        "pending": "Pending or provisional",
        "rejected": "Action blocked",
        "neutral": "Information",
    }
    safe_kind = kind if kind in labels else "neutral"
    return (
        f'<div class="status-banner status-{safe_kind}" role="status">'
        f"<span><strong>{labels[safe_kind]}:</strong> {html.escape(message)}</span>"
        + (f'<span class="status-badge">{html.escape(badge)}</span>' if badge else "")
        + "</div>"
    )


def decision_audit_details(message: str) -> str:
    return (
        '<div class="decision-help"><details>'
        '<summary>Audit details <span class="info-icon" aria-hidden="true">i</span></summary>'
        f"<p>{html.escape(message)}</p>"
        "</details></div>"
    )


def reviewer_values(name, role, specialty, organization, registration, qualifications, scope, comments, declaration_ack):
    return {
        "full_name": name,
        "professional_role": role,
        "clinical_specialty": specialty,
        "organization": organization,
        "registration_reference": registration,
        "declared_qualifications": qualifications,
        "scope_of_review": scope,
        "reviewer_comments": comments,
        "self_declaration_acknowledged": declaration_ack,
    }


def safe_call(operation):
    try:
        value = operation()
        return status_banner("approved", "Completed successfully."), value
    except ReviewError as exc:
        return status_banner("rejected", str(exc)), None
    except Exception as exc:
        return status_banner("rejected", f"Unexpected error: {exc}"), None


def select_issue(issue_id):
    if not issue_id:
        return {}
    try:
        return STORE.issue_view(issue_id)
    except ReviewError as exc:
        return {"error": str(exc)}


def select_issue_for_review(issue_id):
    """Load one finding and clear unsaved controls that belong to the prior finding."""
    detail = select_issue(issue_id)
    path = detail.get("json_field_path", "") if isinstance(detail, dict) else ""
    return detail, gr.update(value=None), "", "", gr.update(value="replace"), path, False, ""


def issue_table():
    latest = STORE.latest_decisions()
    return [
        [
            issue["issue_id"], issue.get("patient_id"), issue.get("severity"),
            latest.get(issue["issue_id"], {}).get("decision", "Awaiting decision"),
        ]
        for issue in STORE.issues()
    ]


def submit_issue(issue_id, decision, rationale, replacement, operation, correction_path, *reviewer):
    status, record = safe_call(lambda: STORE.record_issue_decision(
        issue_id, decision, rationale, replacement, reviewer_values(*reviewer), operation, correction_path
    ))
    if record:
        status = status_banner(
            "approved",
            f"Decision {record['decision_id']} appended for dataset v{record['dataset_version']} / {record['dataset_sha256'][:12]}…",
        )
    return status, select_issue(issue_id), issue_table(), STORE.status()


def correction_request_choices():
    return [item["issue_id"] for item in STORE.current_correction_requests()]


def preview_corrections(issue_ids, snapshot_ready=False):
    status, preview = safe_call(lambda: STORE.preview_correction_batch(issue_ids or []))
    eligible = bool(preview and preview.get("eligible_for_commit"))
    if preview:
        status = status_banner(
            "approved" if preview.get("eligible_for_commit") else "rejected",
            "Correction batch preview passed validation."
            if preview.get("eligible_for_commit") else
            "Correction batch preview failed validation; no dataset was changed.",
        )
    return (
        status,
        preview or {},
        preview.get("preview_id") if preview else "",
        eligible,
        gr.update(interactive=eligible and bool(snapshot_ready)),
    )


def create_safety_snapshot_callback(preview_ready=False):
    status, result = safe_call(STORE.create_safety_snapshot)
    if not result:
        return None, status, {}, False, gr.update(interactive=False)
    path, manifest = result
    return (
        str(path),
        status_banner("approved", "Safety snapshot created without changing the active dataset."),
        manifest,
        True,
        gr.update(interactive=bool(preview_ready)),
    )


def reset_batch_preparation():
    return {}, "", False, False, gr.update(interactive=False), ""


def apply_correction_batch_callback(preview_id, snapshot_file, confirmed, *reviewer):
    status, result = safe_call(lambda: STORE.apply_correction_batch(
        preview_id, snapshot_file, reviewer_values(*reviewer), confirmed
    ))
    if result:
        entry = result["version"]
        status = status_banner(
            "pending",
            f"Correction batch committed as immutable dataset v{entry['version']}. Fresh review and approval are required.",
        )
    choices = correction_request_choices()
    return (
        status,
        result or {},
        *dashboard_values(),
        gr.update(choices=choices, value=[]),
        STORE.dashboard().get("active_dataset") or {"status": "No active dataset"},
        issue_table(),
        False,
        False,
        gr.update(interactive=False),
    )


def export_correction_plan_callback():
    status, path = safe_call(STORE.export_correction_plan)
    if not path:
        return None, status
    return str(path), status_banner("approved", "Consolidated correction plan prepared.")


def apply_correction(issue_id, confirmed, *reviewer):
    status, event = safe_call(lambda: STORE.apply_requested_correction(
        issue_id, reviewer_values(*reviewer), confirmed
    ))
    if event:
        payload = event["payload"]
        status = status_banner(
            "pending",
            f"New immutable dataset v{payload['new_dataset_version']} created. "
            "All earlier decisions and approvals are historical for this version; review and sign off again.",
        )
    return status, select_issue(issue_id), issue_table(), STORE.status()


def run_validation():
    result, _ = STORE.technical_validation(record_audit=True)
    return result.as_dict(), STORE.status()


def upload_choices():
    return [row[0] for row in STORE.upload_rows()]


def version_choices():
    return [row[0] for row in STORE.version_rows()]


def active_dataset_file():
    return str(STORE.dataset_path()) if STORE.has_active_dataset() else None


def json_component_value(value):
    return {"status": value} if isinstance(value, str) else value


def dashboard_values():
    dashboard = STORE.dashboard()
    label = dashboard["lifecycle_label"]
    kind = "neutral" if label == "No active dataset" else (
        "rejected" if "failed" in label.lower() or "rejected" in label.lower() else
        "approved" if "approved freeze" in label.lower() else "pending"
    )
    badge = None
    if label.startswith("Active — "):
        # Show the dataset state as a badge on the right and the lifecycle detail as the message.
        badge, label = "Active", label[len("Active — "):]
    return (
        status_banner(kind, label, badge),
        dashboard["active_dataset"] or {"status": "No active dataset"},
        json_component_value(dashboard["latest_approved_freeze"]),
        json_component_value(dashboard["latest_provisional_freeze"]),
        dashboard["historical_versions"],
        active_dataset_file(),
    )


def preview_upload_bundle(dataset_file, schema_file, issues_file):
    status, result = safe_call(lambda: STORE.archive_and_validate_bundle(dataset_file, schema_file, issues_file))
    if result:
        kind = "approved" if result.get("validation_status") == "Passed" else "rejected"
        reason = ""
        if kind == "rejected":
            errors = result.get("technical_validation", {}).get("structural_errors", [])
            if errors:
                reason = f" Reason: {errors[0]}"
        status = status_banner(
            kind,
            f"Upload bundle {result['bundle_id']} was archived. Validation status: {result['validation_status']}.{reason} "
            "The active dataset was not changed.",
        )
    choices = upload_choices()
    selected = result.get("bundle_id") if result else (choices[0] if choices else None)
    return status, result or {}, gr.update(choices=choices, value=selected), STORE.upload_rows()


def activate_upload_bundle(bundle_id, confirmed):
    status, result = safe_call(lambda: STORE.activate_uploaded_bundle(bundle_id, confirmed))
    if not result:
        return (
            status, {}, *dashboard_values(), gr.update(), {}, issue_table(),
            STORE.dashboard().get("active_dataset") or {"status": "No active dataset"}, STORE.version_rows(), STORE.upload_rows(),
        )
    identifiers = [issue["issue_id"] for issue in STORE.issues()]
    selected = identifiers[0] if identifiers else None
    status = status_banner(
        "pending",
        f"Bundle {result['bundle_id']} activated as immutable dataset v{result['new_dataset_version']}. "
        "Earlier human decisions and approvals do not authorize this version.",
    )
    return (
        status,
        result,
        *dashboard_values(),
        gr.update(choices=identifiers, value=selected),
        select_issue(selected) if selected else {},
        issue_table(),
        STORE.dashboard().get("active_dataset") or {"status": "No active dataset"},
        STORE.version_rows(),
        STORE.upload_rows(),
    )


def submit_approval(decision, justification, signoff, *reviewer):
    status, record = safe_call(lambda: STORE.record_clinical_approval(
        decision, justification, reviewer_values(*reviewer), signoff
    ))
    if record:
        approved = record["approval_decision"] in {"Clinically Approved", "Clinically Approved with Limitations"}
        status = status_banner(
            "approved" if approved else "pending",
            f"Clinical decision successfully recorded: {record['approval_decision']}. "
            f"Approval ID {record['approval_id']} is bound to SHA-256 {record['dataset_sha256']}.",
        )
    return status, STORE.status()


def approval_preview(decision):
    status, summary = safe_call(lambda: STORE.generated_signoff_summary(decision))
    if not summary:
        return status, "", STORE.gate_report()
    return status_banner("neutral", "Structured sign-off summary generated for reviewer confirmation."), summary, STORE.gate_report()


def refresh_downloads():
    try:
        return [str(path) for path in STORE.export_review_files()], status_banner("approved", "Human-review files refreshed.")
    except Exception as exc:
        return [], status_banner("rejected", f"Could not prepare downloads: {exc}")


def create_freeze(kind):
    status, result = safe_call(lambda: STORE.freeze(kind))
    if not result:
        return None, status, STORE.status()
    path, manifest = result
    banner_kind = "approved" if kind == "approved" else "pending"
    return str(path), status_banner(
        banner_kind,
        f"{manifest['dataset_freeze']} package created for SHA-256 {manifest['dataset_sha256']}.",
    ), STORE.status()


def create_freeze_and_refresh(kind):
    package, banner, _ = create_freeze(kind)
    return package, banner, STORE.freeze_status(), *dashboard_values()


def freeze_tab_values():
    """Read the active freeze state when the user opens the tab.

    Gradio component ``value`` arguments are evaluated while the interface is
    built.  Without an explicit tab event, a long-running server can therefore
    display the dataset that was active at startup rather than the current
    active-version pointer.
    """
    gate = STORE.freeze_status()
    rows = [
        [
            freeze.get("freeze_id"),
            freeze.get("freeze_type"),
            freeze.get("created_at_utc"),
            freeze.get("manifest_verification", {}).get("status"),
            str(STORE.root / freeze["zip_file"]),
        ]
        for freeze in gate.get("existing_freezes", [])
    ]
    return gate, rows, gr.update(interactive=bool(gate.get("approved_freeze_eligible")))


def status_tab_values():
    return dashboard_values()


def upload_tab_values():
    choices = upload_choices()
    return gr.update(choices=choices, value=choices[0] if choices else None), STORE.upload_rows()


def review_tab_values():
    identifiers = [issue["issue_id"] for issue in STORE.issues()]
    selected = identifiers[0] if identifiers else None
    corrections = correction_request_choices()
    gate = STORE.gate_report()
    approval_choices = list(APPROVAL_DECISIONS) if gate.get("eligible_for_clinical_approval") else list(APPROVAL_DECISIONS[2:])
    return (
        gr.update(choices=identifiers, value=selected),
        select_issue(selected) if selected else {},
        issue_table(),
        STORE.dashboard().get("active_dataset") or {"status": "No active dataset"},
        gate,
        gr.update(choices=corrections, value=[]),
        gr.update(choices=approval_choices, value=None),
    )


def history_tab_values():
    versions = version_choices()
    uploads = upload_choices()
    return (
        STORE.version_rows(),
        gr.update(choices=versions, value=versions[0] if versions else None),
        STORE.upload_rows(),
        gr.update(choices=uploads, value=uploads[0] if uploads else None),
    )


def version_detail_values(label):
    if not label:
        return {}, []
    version = int(str(label).lower().lstrip("v"))
    detail = STORE.version_detail(version)
    return detail, detail.get("downloadable_files", [])


def upload_detail_values(bundle_id):
    if not bundle_id:
        return {}, []
    detail = STORE.upload_detail(bundle_id)
    return detail, detail.get("downloadable_files", [])


def history_refresh_values():
    versions = version_choices()
    uploads = upload_choices()
    return (
        STORE.version_rows(),
        gr.update(choices=versions, value=versions[0] if versions else None),
        STORE.upload_rows(),
        gr.update(choices=uploads, value=uploads[0] if uploads else None),
    )


def select_version_row(table, evt: gr.SelectData):
    if evt.index is None:
        return gr.update(), {}, []
    row_index = evt.index[0] if isinstance(evt.index, (tuple, list)) else evt.index
    label = table.iloc[row_index, 0] if hasattr(table, "iloc") else table[row_index][0]
    detail, files = version_detail_values(label)
    return gr.update(value=label), detail, files


def select_upload_row(table, evt: gr.SelectData):
    if evt.index is None:
        return gr.update(), {}, []
    row_index = evt.index[0] if isinstance(evt.index, (tuple, list)) else evt.index
    bundle_id = table.iloc[row_index, 0] if hasattr(table, "iloc") else table[row_index][0]
    detail, files = upload_detail_values(bundle_id)
    return gr.update(value=bundle_id), detail, files


def reactivate_historical_version(label, confirmed):
    if not label:
        return status_banner("rejected", "Select a historical version first."), *dashboard_values(), STORE.version_rows()
    version = int(str(label).lower().lstrip("v"))
    status, result = safe_call(lambda: STORE.reactivate_version(version, confirmed))
    if result:
        status = status_banner(
            "pending",
            f"Historical v{version:04d} was copied into new active v{result['new_dataset_version']:04d}. Fresh review and approval are required.",
        )
    return status, *dashboard_values(), STORE.version_rows()


def build_app():
    issue_ids = [issue["issue_id"] for issue in STORE.issues()]
    initial_dashboard = STORE.dashboard()
    initial_versions = STORE.version_rows()
    initial_uploads = STORE.upload_rows()
    initial_version_choices = version_choices()
    initial_upload_choices = upload_choices()
    controls_enabled = STORE.has_active_dataset()
    with gr.Blocks(
        title="Clinical Reference and NLP Evaluation",
        theme=CLINICAL_THEME,
        css=CLINICAL_CSS,
    ) as demo:
        gr.Markdown(
            "# Clinical Reference and NLP Evaluation\n"
            "A research prototype for clinical-reference governance and bilingual clinical NLP evaluation. "
            "**Automated evaluation does not replace clinical or language review.**\n\n"
            "One application with three separate workspaces: clinical reference sign-off, narrative release sign-off and NLP "
            "evaluation, built on the reference clinical narratives (EN/ES) from the approved v9 set. Clinical reference "
            "governance stays authoritative, and evaluation results never change approval or freeze state.",
            elem_classes=["clinical-header"],
        )
        with gr.Tab("Clinical Reference Sign-off and Freeze", id="reference_workspace"):
            with gr.Tab("Dataset Status and Validation", id="status") as status_tab:
                lifecycle_status = gr.HTML(dashboard_values()[0])
                upload_cta = gr.Button("Upload a reference bundle", variant="primary", visible=not controls_enabled)
                upload_cta.click(
                    None,
                    js="""() => {
                        const tab = [...document.querySelectorAll('[role=tab]')]
                            .find(node => node.textContent.trim() === 'Upload and Validate Bundle');
                        if (tab) tab.click();
                    }""",
                )
                gr.Markdown("## Active dataset")
                active_summary = gr.JSON(
                    value=initial_dashboard["active_dataset"] or {"status": "No active dataset"},
                    label="Active dataset lifecycle and approval gates",
                )
                current_dataset = gr.File(value=active_dataset_file(), label="Current immutable dataset version")
                validate_btn = gr.Button("Run technical validation", variant="primary")
                validation_json = gr.JSON(label="Executed structural and deterministic checks")
                gr.Markdown("## Latest frozen datasets")
                with gr.Row():
                    latest_approved = gr.JSON(value=json_component_value(initial_dashboard["latest_approved_freeze"]), label="Latest approved freeze")
                    latest_provisional = gr.JSON(value=json_component_value(initial_dashboard["latest_provisional_freeze"]), label="Latest provisional freeze")
                gr.Markdown("## Historical overview")
                version_overview = gr.Dataframe(
                    value=initial_versions,
                    headers=["Version", "Active", "Created", "Creation source", "Validation", "Review progress", "Clinical decision", "Provisional freezes", "Approved freezes", "Latest freeze date"],
                    interactive=False,
                    label="All immutable dataset versions",
                )
                status_outputs = [lifecycle_status, active_summary, latest_approved, latest_provisional, version_overview, current_dataset]

                def run_validation_and_refresh():
                    result, _ = STORE.technical_validation(record_audit=True)
                    return result.as_dict(), *dashboard_values()

                validate_btn.click(run_validation_and_refresh, outputs=[validation_json, *status_outputs])
                status_tab.select(status_tab_values, outputs=status_outputs)

            with gr.Tab("Upload and Validate Bundle", id="upload") as upload_tab:
                gr.Markdown(
                    "## Upload a future reference bundle\n"
                    "Upload the dataset, its authoritative JSON Schema, and the matching QA issue register together. "
                    "Files are stored under `runtime/uploads/`; the bundled `inputs/` files are never overwritten. "
                    "Activation creates a new immutable dataset version and makes all earlier decisions and approvals stale."
                )
                with gr.Row():
                    upload_dataset = gr.File(label="Reference dataset JSON *", file_types=[".json"], type="filepath")
                    upload_schema = gr.File(label="JSON Schema *", file_types=[".json"], type="filepath")
                    upload_issues = gr.File(label="QA issues JSON *", file_types=[".json"], type="filepath")
                preview_upload_btn = gr.Button("Archive and validate uploaded bundle", variant="primary")
                upload_report = gr.JSON(label="Upload and technical-validation report")
                upload_status = gr.Markdown()
                upload_bundle_select = gr.Dropdown(
                    choices=initial_upload_choices,
                    value=initial_upload_choices[0] if initial_upload_choices else None,
                    label="Archived upload bundle eligible for activation",
                )
                upload_registry_table = gr.Dataframe(
                    value=initial_uploads,
                    headers=["Bundle ID", "Uploaded", "Validation", "Activation", "Activated version"],
                    interactive=False,
                    label="Upload intake history",
                )
                upload_validation_event = preview_upload_btn.click(
                    preview_upload_bundle,
                    inputs=[upload_dataset, upload_schema, upload_issues],
                    outputs=[upload_status, upload_report, upload_bundle_select, upload_registry_table],
                )
                upload_confirm = gr.Checkbox(
                    value=False,
                    label="I confirm activation as a new dataset version. Existing decisions and approvals must not carry over.",
                )
                activate_upload_btn = gr.Button("Activate selected uploaded bundle", variant="primary")
                upload_tab.select(upload_tab_values, outputs=[upload_bundle_select, upload_registry_table])

            with gr.Tab("Clinical Review and Human Decisions", id="review") as review_tab:
                active_review_identity = gr.JSON(value=initial_dashboard["active_dataset"] or {"status": "No active dataset"}, label="Active dataset under review")
                gr.Markdown(
                    "## Reviewer identity and declared scope\n"
                    "Only reviewer name and professional role are required. Qualifications, scope, and registration details are optional and self-declared; "
                    "the application does not independently verify professional accreditation. Review timestamps are recorded in UTC."
                )
                with gr.Row():
                    reviewer_name = gr.Textbox(label="Reviewer full name *")
                    reviewer_role = gr.Textbox(label="Professional role *")
                    reviewer_specialty = gr.Textbox(label="Clinical specialty")
                with gr.Row():
                    reviewer_org = gr.Textbox(label="Organization or institution (optional)")
                    reviewer_registration = gr.Textbox(label="Professional registration/license reference (optional)")
                reviewer_qualifications = gr.Textbox(label="Declared qualifications (optional)", lines=2)
                reviewer_scope = gr.Textbox(label="Scope of clinical review (optional)", lines=2)
                reviewer_comments = gr.Textbox(label="Reviewer comments", lines=2)
                reviewer_declaration = gr.Checkbox(
                    value=False,
                    label="Optional: I confirm any qualifications and scope entered above are self-declared and not independently verified by the app.",
                )
                reviewer_inputs = [
                    reviewer_name, reviewer_role, reviewer_specialty, reviewer_org,
                    reviewer_registration, reviewer_qualifications, reviewer_scope,
                    reviewer_comments, reviewer_declaration,
                ]

                gr.Markdown("## Review individual QA findings")
                issue_grid = gr.Dataframe(
                    value=issue_table(),
                    headers=["Issue ID", "Patient ID", "Severity", "Current decision for exact dataset"],
                    datatype=["str", "str", "str", "str"],
                    interactive=False,
                    label="Finding register",
                )
                issue_select = gr.Dropdown(choices=issue_ids, value=issue_ids[0] if issue_ids else None, label="Issue to review", interactive=controls_enabled)
                issue_detail = gr.JSON(
                    value=select_issue(issue_ids[0]) if issue_ids else {},
                    label="Original finding, current dataset value/context, checksum, and decision history",
                )
                human_decision = gr.Radio(choices=list(ISSUE_DECISIONS), value=None, label="Human decision (no default)", interactive=controls_enabled)
                rationale = gr.Textbox(
                    label="Written rationale / limitation / information needed * when applicable",
                    lines=4,
                    placeholder="Major and critical issue acceptance also requires a documented clinical rationale.",
                )
                replacement = gr.Textbox(
                    label="Proposed value for an add or replace correction (JSON value or plain text)",
                    lines=3,
                    placeholder='Examples: null, "corrected text", ["item"]',
                )
                with gr.Row():
                    correction_operation = gr.Dropdown(
                        choices=["replace", "add", "remove"],
                        value="replace",
                        label="Correction operation",
                    )
                    correction_path = gr.Textbox(
                        value=issue_detail.value.get("json_field_path", "") if isinstance(issue_detail.value, dict) else "",
                        label="Correction JSON Pointer",
                    )
                save_issue = gr.Button("Record decision for this issue", variant="primary", interactive=controls_enabled)
                gr.HTML(
                    decision_audit_details(
                        "The decision is added to the append-only audit history and bound to this issue, "
                        "the active dataset version, and its SHA-256 checksum."
                    )
                )
                issue_status = gr.Markdown()
                save_issue.click(
                    submit_issue,
                    inputs=[issue_select, human_decision, rationale, replacement, correction_operation, correction_path, *reviewer_inputs],
                    outputs=[issue_status, issue_detail, issue_grid, active_review_identity],
                )

                gr.Markdown(
                    "### Review and apply confirmed corrections\n"
                    "Correction requests use explicit **replace**, **add**, or **remove** operations. Preview selected requests together, "
                    "create a safety snapshot, and commit them atomically as one new immutable version. No partial batch is permitted."
                )
                correction_confirm = gr.Checkbox(value=False, label="I explicitly confirm applying the complete validated batch as one new dataset version.")
                issue_select.change(
                    select_issue_for_review,
                    inputs=issue_select,
                    outputs=[issue_detail, human_decision, rationale, replacement, correction_operation, correction_path, correction_confirm, issue_status],
                )
                correction_requests = correction_request_choices()
                correction_batch_select = gr.CheckboxGroup(
                    choices=correction_requests,
                    value=[],
                    label="Current correction requests for the exact active dataset",
                )
                preview_batch_btn = gr.Button("Preview selected correction batch", elem_classes=["pending-action"])
                correction_preview_status = gr.Markdown()
                correction_preview = gr.JSON(label="Read-only correction preview, conflicts, hashes, and validation")
                correction_preview_id = gr.Textbox(visible=False)
                correction_preview_ready = gr.State(False)
                safety_snapshot_ready = gr.State(False)
                snapshot_btn = gr.Button("Create safety snapshot before correction")
                safety_snapshot_file = gr.File(label="Checksum-bound safety snapshot")
                safety_snapshot_status = gr.Markdown()
                safety_snapshot_manifest = gr.JSON(label="Safety snapshot manifest")
                correction_btn = gr.Button(
                    "Apply confirmed corrections and create one new version",
                    elem_classes=["pending-action"],
                    interactive=False,
                )
                correction_batch_select.change(
                    reset_batch_preparation,
                    outputs=[
                        correction_preview, correction_preview_id, correction_preview_ready,
                        safety_snapshot_ready, correction_btn, correction_preview_status,
                    ],
                )
                preview_batch_btn.click(
                    preview_corrections,
                    inputs=[correction_batch_select, safety_snapshot_ready],
                    outputs=[
                        correction_preview_status, correction_preview, correction_preview_id,
                        correction_preview_ready, correction_btn,
                    ],
                )
                snapshot_btn.click(
                    create_safety_snapshot_callback,
                    inputs=correction_preview_ready,
                    outputs=[
                        safety_snapshot_file, safety_snapshot_status, safety_snapshot_manifest,
                        safety_snapshot_ready, correction_btn,
                    ],
                )
                correction_result = gr.JSON(label="Batch correction commit result")
                correction_btn.click(
                    apply_correction_batch_callback,
                    inputs=[correction_preview_id, safety_snapshot_file, correction_confirm, *reviewer_inputs],
                    outputs=[
                        issue_status, correction_result, *status_outputs, correction_batch_select,
                        active_review_identity, issue_grid, correction_preview_ready,
                        safety_snapshot_ready, correction_btn,
                    ],
                )
                correction_plan_btn = gr.Button("Download consolidated correction plan")
                correction_plan_file = gr.File(label="Consolidated correction plan")
                correction_plan_status = gr.Markdown()
                correction_plan_btn.click(
                    export_correction_plan_callback,
                    outputs=[correction_plan_file, correction_plan_status],
                )

                gr.Markdown("## Final human clinical approval")
                initial_approval_choices = (
                    list(APPROVAL_DECISIONS)
                    if STORE.gate_report().get("eligible_for_clinical_approval")
                    else list(APPROVAL_DECISIONS[2:])
                )
                final_decision = gr.Radio(choices=initial_approval_choices, value=None, label="Clinical approval decision (no default)")
                approval_gate = gr.JSON(value=STORE.gate_report(), label="Current approval eligibility and exact blocking reasons")
                approval_preview_btn = gr.Button("Generate structured sign-off summary")
                final_justification = gr.Textbox(label="Structured approval justification / documented limitations", lines=10)
                approval_preview_status = gr.Markdown()
                approval_preview_btn.click(
                    approval_preview,
                    inputs=final_decision,
                    outputs=[approval_preview_status, final_justification, approval_gate],
                )
                gr.Markdown(f"> {SIGNOFF_DECLARATION}")
                final_signoff = gr.Checkbox(value=False, label="I explicitly acknowledge the human sign-off declaration above.")
                approval_btn = gr.Button("Record clinical decision for this dataset version", variant="primary", interactive=controls_enabled)
                gr.HTML(
                    decision_audit_details(
                        "The decision is added to the append-only audit history and bound to the active "
                        "dataset version and its SHA-256 checksum."
                    )
                )
                approval_status = gr.Markdown()
                approval_btn.click(
                    submit_approval,
                    inputs=[final_decision, final_justification, final_signoff, *reviewer_inputs],
                    outputs=[approval_status, active_review_identity],
                )

                review_tab.select(
                    review_tab_values,
                    outputs=[issue_select, issue_detail, issue_grid, active_review_identity, approval_gate, correction_batch_select, final_decision],
                )

                activation_event = activate_upload_btn.click(
                    activate_upload_bundle,
                    inputs=[upload_bundle_select, upload_confirm],
                    outputs=[upload_status, upload_report, *status_outputs, issue_select, issue_detail, issue_grid, active_review_identity, version_overview, upload_registry_table],
                )

            with gr.Tab("Freeze and Downloads", id="freeze") as freeze_tab:
                gr.Markdown(
                    "A **provisional freeze** preserves reproducibility while review is incomplete and is never represented as clinically approved. "
                    "An **approved freeze** is allowed only when technical validation passes and a current human approval exists for the exact dataset checksum."
                )
                freeze_gate = gr.JSON(value=STORE.freeze_status(), label="Active dataset freeze eligibility")
                with gr.Row():
                    provisional_btn = gr.Button("Create provisional freeze", elem_classes=["pending-action"])
                    approved_btn = gr.Button(
                        "Create approved freeze",
                        variant="primary",
                        elem_classes=["approved-action"],
                        interactive=bool(STORE.freeze_status().get("approved_freeze_eligible")),
                    )
                freeze_status = gr.Markdown()
                package_file = gr.File(label="Freeze ZIP package")
                active_freezes = gr.Dataframe(
                    value=[[f.get("freeze_id"), f.get("classification"), f.get("freeze_type"), f.get("created_at_utc"), f.get("manifest_verification", {}).get("status"), str(STORE.root / f["zip_file"])] for f in STORE.freeze_status().get("existing_freezes", [])],
                    headers=["Freeze ID", "Classification", "Type", "Created", "Manifest verification", "Package"],
                    interactive=False,
                    label="All freeze packages for the active version",
                )

                def freeze_result(kind):
                    package, banner, gate, *dashboard = create_freeze_and_refresh(kind)
                    rows = [[f.get("freeze_id"), f.get("classification"), f.get("freeze_type"), f.get("created_at_utc"), f.get("manifest_verification", {}).get("status"), str(STORE.root / f["zip_file"])] for f in gate.get("existing_freezes", [])]
                    return package, banner, gate, rows, *dashboard

                freeze_tab.select(freeze_tab_values, outputs=[freeze_gate, active_freezes, approved_btn])
                provisional_btn.click(lambda: freeze_result("provisional"), outputs=[package_file, freeze_status, freeze_gate, active_freezes, *status_outputs])
                approved_btn.click(lambda: freeze_result("approved"), outputs=[package_file, freeze_status, freeze_gate, active_freezes, *status_outputs])
                review_files_btn = gr.Button("Refresh downloadable human-review files")
                review_files = gr.File(label="Human review files", file_count="multiple")
                review_files_status = gr.Markdown()
                review_files_btn.click(refresh_downloads, outputs=[review_files, review_files_status])

            with gr.Tab("Version and Upload History", id="history") as history_tab:
                gr.Markdown("## Dataset versions\nHistorical inspection and downloads are read-only and never change the active version.")
                version_history = gr.Dataframe(
                    value=initial_versions,
                    headers=["Version", "Active", "Created", "Creation source", "Validation", "Review progress", "Clinical decision", "Provisional freezes", "Approved freezes", "Latest freeze date"],
                    interactive=False,
                    label="Dataset version registry",
                )
                version_select = gr.Dropdown(choices=initial_version_choices, value=initial_version_choices[0] if initial_version_choices else None, label="Inspect dataset version")
                initial_version_detail, initial_version_files = version_detail_values(version_select.value) if initial_version_choices else ({}, [])
                version_json = gr.JSON(value=initial_version_detail, label="Version metadata, review, approval and freeze history")
                version_files = gr.File(value=initial_version_files, label="Version-specific files and freeze packages", file_count="multiple")
                version_select.change(version_detail_values, inputs=version_select, outputs=[version_json, version_files])
                version_history.select(select_version_row, inputs=version_history, outputs=[version_select, version_json, version_files])
                reactivate_confirm = gr.Checkbox(value=False, label="I confirm creation of a new active version. Historical approval will not be inherited.")
                reactivate_btn = gr.Button("Create new version from this historical version", elem_classes=["pending-action"])
                reactivate_status = gr.Markdown()
                reactivation_event = reactivate_btn.click(
                    reactivate_historical_version,
                    inputs=[version_select, reactivate_confirm],
                    outputs=[reactivate_status, *status_outputs, version_history],
                )

                gr.Markdown("## Uploaded bundles\nEvery intake attempt remains archived, including failed and never-activated bundles.")
                upload_history = gr.Dataframe(
                    value=initial_uploads,
                    headers=["Bundle ID", "Uploaded", "Validation", "Activation", "Activated version"],
                    interactive=False,
                    label="Upload bundle registry",
                )
                upload_history_select = gr.Dropdown(choices=initial_upload_choices, value=initial_upload_choices[0] if initial_upload_choices else None, label="Inspect uploaded bundle")
                initial_upload_detail, initial_upload_files = upload_detail_values(upload_history_select.value) if initial_upload_choices else ({}, [])
                upload_json = gr.JSON(value=initial_upload_detail, label="Upload manifest and validation result")
                upload_files = gr.File(value=initial_upload_files, label="Archived upload files", file_count="multiple")
                upload_history_select.change(upload_detail_values, inputs=upload_history_select, outputs=[upload_json, upload_files])
                upload_history.select(select_upload_row, inputs=upload_history, outputs=[upload_history_select, upload_json, upload_files])
                version_overview.select(select_version_row, inputs=version_overview, outputs=[version_select, version_json, version_files])

                upload_validation_event.then(
                    history_refresh_values,
                    outputs=[version_history, version_select, upload_history, upload_history_select],
                )
                activation_event.then(
                    history_refresh_values,
                    outputs=[version_history, version_select, upload_history, upload_history_select],
                )
                reactivation_event.then(
                    history_refresh_values,
                    outputs=[version_history, version_select, upload_history, upload_history_select],
                )
                history_tab.select(
                    history_tab_values,
                    outputs=[version_history, version_select, upload_history, upload_history_select],
                )

        with gr.Tab("Narrative Release Review and Sign-off", id="narrative_review_workspace"):
            # Gate C decisions go to their own append-only log; the clinical reference and the evaluation
            # runtime are read-only here.
            from narrative_review_ui import add_narrative_review_tabs
            add_narrative_review_tabs()

        with gr.Tab("Clinical NLP Evaluation", id="evaluation_workspace"):
            # Evaluation artifacts use a separate runtime and the governance runtime is read-only.
            from evaluation_ui import add_evaluation_tabs
            add_evaluation_tabs()
        gr.Markdown(
            "Software accompanying: *Development and Comparative Evaluation of English–Spanish Plain-Language "
            "Clinical Narrative Generation with Human Oversight*",
            elem_classes=["app-footer"],
        )

    return demo


if __name__ == "__main__":
    build_app().launch(show_api=False)
