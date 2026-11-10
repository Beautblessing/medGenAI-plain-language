"""Structured clinical fact inventory (generation method steps 1-4).

Walks the whole approved record and emits one fact per clinically meaningful item,
tagged with its JSON path and an epistemic tag:
  positive   documented present/abnormal/performed finding or event
  negative   documented absent finding ("presence: absent", "No ..." exam statements)
  unknown    explicitly missing or uncertain information (null dose, unknown dates, diagnostic_uncertainty)
  planned    planned/ordered/scheduled/recommended item not yet completed
  pending    investigation done but result not available
  administered  medication actually given (administration event)
  prescribed    active/historical medication order
Each fact carries an English statement used as an NLI premise. The statement is
rendered from the record's own values only.
"""


def _join(*parts):
    return " ".join(str(p) for p in parts if p not in (None, "", [])).strip()


def build_inventory(pid, r):
    F = []

    def add(path, section, tag, statement, value=None):
        F.append({"fact_id": f"{pid}-F{len(F)+1:03d}", "patient_id": pid, "source_path": f"/{pid}{path}", "section": section,
                  "epistemic_tag": tag, "statement_en": statement.rstrip(".") + ".", "value": value})

    pi = r["patient_information"]
    add("/patient_information/full_name", "identity", "positive", f"The patient is {pi['full_name']}, {pi['sex'].lower()}, aged {pi['age']}, born {pi['date_of_birth']}", pi["full_name"])
    add("/patient_information/encounter_date", "identity", "positive", f"The encounter date is {pi['encounter_date']}", pi["encounter_date"])
    rc = r["reason_for_consultation"]
    add("/reason_for_consultation/chief_complaint", "reason", "positive", rc["chief_complaint"], rc["chief_complaint"])
    h = r["relevant_medical_history"]
    for k in ["past_medical_history", "past_surgical_history", "family_history", "social_history"]:
        if not h[k]:
            add(f"/relevant_medical_history/{k}", "history", "unknown", f"No {k.replace('_', ' ')} is documented", None)
        for i, x in enumerate(h[k]):
            add(f"/relevant_medical_history/{k}/{i}", "history", "positive", f"History: {x}", x)
    if not h["allergies"]:
        add("/relevant_medical_history/allergies", "history", "unknown", "No allergies are documented", None)
    for i, a in enumerate(h["allergies"]):
        add(f"/relevant_medical_history/allergies/{i}", "history", "positive", _join("Reported allergy to", a["substance"], "with", a["reaction"]), a)
    cs = r["current_symptoms"]
    for i, s in enumerate(cs["symptoms"]):
        if s["presence"] == "absent":
            add(f"/current_symptoms/symptoms/{i}", "symptoms", "negative", s["description"] or f"No {s['name']}", s["name"])
        else:
            add(f"/current_symptoms/symptoms/{i}", "symptoms", "positive", _join("The patient has", s["name"].lower(), "for" if s["duration"] else "", s["duration"]), s["name"])
    if cs.get("overall_severity"):
        add("/current_symptoms/overall_severity", "symptoms", "positive", f"Overall severity: {cs['overall_severity']}", cs["overall_severity"])
    if cs.get("symptom_onset"):
        add("/current_symptoms/symptom_onset", "symptoms", "positive", f"Symptom onset: {cs['symptom_onset']}", cs["symptom_onset"])
    ce = r["clinical_examination_and_investigations"]
    vs = ce["vital_signs"]
    names = {"blood_pressure_mmHg": ("Blood pressure", "mmHg"), "heart_rate_bpm": ("Heart rate", "bpm"), "respiratory_rate_bpm": ("Respiratory rate", "breaths per minute"),
             "temperature_celsius": ("Temperature", "°C"), "oxygen_saturation_percent": ("Oxygen saturation", "%"), "weight_kg": ("Weight", "kg"),
             "height_m": ("Height", "m"), "bmi_kg_m2": ("BMI", "kg/m²")}
    for k, v in vs.items():
        if v is not None:
            add(f"/clinical_examination_and_investigations/vital_signs/{k}", "examination", "positive", f"{names[k][0]} {v} {names[k][1]}", v)
    for i, x in enumerate(ce["physical_examination"]):
        tag = "negative" if x.lower().startswith("no ") else "positive"
        add(f"/clinical_examination_and_investigations/physical_examination/{i}", "examination", tag, f"Examination: {x}", x)
    for i, t in enumerate(ce["laboratory_tests"]):
        p = f"/clinical_examination_and_investigations/laboratory_tests/{i}"
        if t["status"] == "result_pending":
            add(p, "investigations", "pending", f"{t['test_name']} was sent and the result is pending", t["status"])
        else:
            add(p, "investigations", "positive", _join(t["test_name"] + ":", t["result"], t["unit"], f"({t['interpretation']})" if t["interpretation"] else ""), t["result"])
    for i, t in enumerate(ce["imaging_studies"]):
        p = f"/clinical_examination_and_investigations/imaging_studies/{i}"
        if t["status"] in ("ordered", "planned"):
            add(p, "investigations", "planned", f"{t['modality']} of the {t['body_region'].lower()} was ordered and has not been performed", t["status"])
        else:
            add(p, "investigations", "positive", _join(t["modality"] + ":", t["findings"]), t["findings"])
    for i, t in enumerate(ce["other_investigations"]):
        p = f"/clinical_examination_and_investigations/other_investigations/{i}"
        if t["status"] in ("ordered", "planned"):
            add(p, "investigations", "planned", f"{t['name']} was ordered and is pending", t["status"])
        else:
            add(p, "investigations", "positive", _join(t["name"] + ":", t["findings"]), t["findings"])
    dx = r["clinical_assessment_and_diagnosis"]
    pd = dx["primary_diagnosis"]
    add("/clinical_assessment_and_diagnosis/primary_diagnosis", "assessment", "positive" if pd["status"] == "confirmed" else "unknown",
        f"Primary diagnosis ({pd['status']}): {pd['condition']}", pd["status"])
    for i, s in enumerate(dx["secondary_diagnoses"]):
        add(f"/clinical_assessment_and_diagnosis/secondary_diagnoses/{i}", "assessment", "positive" if s["status"] == "confirmed" else "unknown",
            f"Secondary diagnosis ({s['status']}): {s['condition']}", s["status"])
    if dx.get("diagnostic_uncertainty"):
        add("/clinical_assessment_and_diagnosis/diagnostic_uncertainty", "assessment", "unknown", dx["diagnostic_uncertainty"], dx["diagnostic_uncertainty"])
    ct = r["care_and_treatment"]
    for i, c in enumerate(ct["care_interventions"]):
        add(f"/care_and_treatment/care_interventions/{i}", "treatment", "positive" if c["status"] in ("provided", "completed") else "planned", f"{c['description']} ({c['status']})", c["status"])
    for i, c in enumerate(ct["therapeutic_procedures"]):
        add(f"/care_and_treatment/therapeutic_procedures/{i}", "treatment", "positive" if c["status"] == "completed" else "planned",
            _join(c["procedure_name"], f"was {c['status']}", f"on {c['performed_date']}" if c["performed_date"] else "", "; outcome:", c["outcome"]), c["status"])
    for i, c in enumerate(ct["treatment_recommendations"]):
        add(f"/care_and_treatment/treatment_recommendations/{i}", "treatment", "planned", f"Recommendation: {c}", c)
    for i, m in enumerate(r["medication_management"]["medications"]):
        p = f"/medication_management/medications/{i}"
        if m["lifecycle_status"] == "administered":
            ev = m["administration_events"][0] if m["administration_events"] else {}
            dose = ev.get("dose_administered") or m["dose"]
            add(p, "medications", "administered", _join(m["name"], dose if dose else "(dose not recorded)", m["route"].lower(), "was given on", ev.get("date")), dose)
            if not dose:
                add(p + "/administration_events/0/dose_administered", "medications", "unknown", f"The administered dose of {m['name'].lower()} is not recorded", None)
        else:
            add(p, "medications", "prescribed", _join(m["name"], m["dose"], m["route"].lower(), m["frequency"].lower() if m["frequency"] else "", f"for {m['duration'].lower()}" if m["duration"] else "",
                f"({m['lifecycle_status']})"), m["dose"])
            if m["actual_start_date"] is None and m["lifecycle_status"] == "active":
                add(p + "/actual_start_date", "medications", "unknown", f"The original start date of {m['name'].lower()} is not recorded", None)
    pr = r["clinical_progress"]
    add("/clinical_progress/clinical_course", "progress", "positive" if "not yet" not in pr["clinical_course"] else "unknown", pr["clinical_course"], pr["clinical_course"])
    for i, w in enumerate(r["warnings_and_red_flags"]["warning_signs"]):
        add(f"/warnings_and_red_flags/warning_signs/{i}", "warnings", "positive", f"{w['sign']} ({w['urgency']}): {w['recommended_action']}", w["urgency"])
    for i, f in enumerate(r["follow_up_and_monitoring_plan"]["follow_up_items"]):
        add(f"/follow_up_and_monitoring_plan/follow_up_items/{i}", "follow_up", "planned",
            _join((f["specialty"] or f["type"].replace("_", " ")), "follow-up in", f["interval"], "to", (f["purpose"] or "").lower(),
                  f"(scheduled for {f['scheduled_date']})" if f["scheduled_date"] else "(no scheduled date recorded)"), f["status"])
    return F
