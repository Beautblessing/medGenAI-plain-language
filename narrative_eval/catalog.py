"""Curated critical-fact catalog for Layer C (deterministic fact checks).

Each entry is anchored to a JSON path in the approved reference record and has an
English and a Spanish case-insensitive regular expression. A missing match is a
candidate omission for human review, never proof of a clinical error.

scope:  all      -> required in all four outputs
        clinical -> required in both professional notes (technical detail a patient text may omit)
        patient  -> required in both patient explanations only
family: one of identity, diagnosis, certainty, medication, numeric, investigation,
        procedure, symptom, warning, followup, progress, history, missing_fact

IMPORTANT LIMITATION: the catalog and the narratives were written by the same model.
Detector wording may therefore share blind spots with the narratives; the calibration
set and human review exist to expose this.
"""

M = "/medication_management/medications/"
W = "/warnings_and_red_flags/warning_signs/"
F = "/follow_up_and_monitoring_plan/follow_up_items/"
L = "/clinical_examination_and_investigations/laboratory_tests/"
I = "/clinical_examination_and_investigations/imaging_studies/"
O = "/clinical_examination_and_investigations/other_investigations/"
V = "/clinical_examination_and_investigations/vital_signs/"
DX = "/clinical_assessment_and_diagnosis/primary_diagnosis/"
DX2 = "/clinical_assessment_and_diagnosis/secondary_diagnoses/"
S = "/current_symptoms/symptoms/"
EMERG_EN = r"(call emergency services|emergency)"
EMERG_ES = r"(servicios de emergencia|emergencias?)"
URG_EN = r"urgent"
URG_ES = r"urgente"

CATALOG = {
"HC0001": [
 ("dx", DX+"condition", "all", "diagnosis", r"streptococcal tonsillopharyngitis", r"amigdalofaringitis( aguda)? estreptocócica", "Acute streptococcal tonsillopharyngitis"),
 ("dx_cert", DX+"status", "all", "certainty", r"confirm", r"confirmad", "Diagnosis confirmed"),
 ("strep_test", L+"0/result", "all", "investigation", r"(rapid|quick|strep|antigen|swab)[^.]{0,80}(positive|confirm)|positive[^.]{0,60}(strep|antigen)", r"(rápid|antígeno|estrept|frotis)[^.]{0,80}(positiv|confirm)|positiv[^.]{0,60}(estrept|antígeno)", "Rapid strep antigen positive"),
 ("centor", O+"0/findings", "clinical", "investigation", r"centor[^.]{0,40}4", r"centor[^.]{0,40}4", "Modified Centor score 4"),
 ("amox", M+"0/dose", "all", "medication", r"amoxicillin[^.]{0,60}500 mg", r"amoxicilina[^.]{0,60}500 mg", "Amoxicillin 500 mg"),
 ("amox_route", M+"0/route", "all", "medication", r"amoxicillin[^.]{0,80}(orally|by mouth)", r"amoxicilina[^.]{0,80}(por vía oral|por boca)", "Amoxicillin oral"),
 ("amox_freq", M+"0/frequency", "all", "medication", r"amoxicillin[^.]{0,80}every 8 hours", r"amoxicilina[^.]{0,80}cada 8 horas", "Amoxicillin every 8 hours"),
 ("amox_dur", M+"0/duration", "all", "medication", r"10 days", r"10 días", "Amoxicillin 10 days"),
 ("para", M+"1/dose", "all", "medication", r"paracetamol[^.]{0,40}1 g", r"paracetamol[^.]{0,40}1 g", "Paracetamol 1 g"),
 ("para_max", M+"1/frequency", "all", "medication", r"3 g", r"3 g", "Paracetamol max 3 g per day"),
 ("fever", S+"2/name", "all", "numeric", r"38\.9", r"38,9", "Fever up to 38.9 °C at home"),
 ("w_breath", W+"0/sign", "all", "warning", r"(difficulty|noisy|trouble)[^.]{0,20}breath", r"dificultad para respirar|respiración ruidosa", "Emergency: difficulty/noisy breathing"),
 ("w_drool", W+"1/sign", "all", "warning", r"drool", r"babe", "Emergency: drooling / cannot swallow saliva"),
 ("w_rash", W+"4/sign", "all", "warning", r"rash", r"erupción", "Urgent: rash after amoxicillin"),
 ("fu", F+"0/interval", "all", "followup", r"72 hours", r"72 horas", "Reassess at 72 hours if not improving"),
],
"HC0002": [
 ("dx", DX+"condition", "all", "diagnosis", r"hypertension", r"hipertensión", "Essential hypertension"),
 ("dx_stage", DX+"condition", "all", "diagnosis", r"stage 2", r"estadio 2", "Stage 2"),
 ("dx_cert", DX+"status", "all", "certainty", r"confirm", r"confirmad", "Hypertension confirmed"),
 ("ram", M+"0/dose", "all", "medication", r"ramipril 2\.5 mg", r"ramipril 2,5 mg", "Ramipril 2.5 mg"),
 ("ram_route", M+"0/route", "all", "medication", r"ramipril[^.]{0,40}(orally|by mouth)", r"ramipril[^.]{0,40}por vía oral", "Ramipril oral"),
 ("ram_freq", M+"0/frequency", "all", "medication", r"ramipril[^.]{0,60}once (daily|a day)", r"ramipril[^.]{0,60}una vez al día", "Ramipril once daily"),
 ("bp_office", V+"blood_pressure_mmHg", "all", "numeric", r"154/96", r"154/96", "Office BP 154/96 mmHg"),
 ("bp_home", O+"0/findings", "all", "numeric", r"152/94", r"152/94", "Home BP mean 152/94 mmHg"),
 ("ecg", O+"1/findings", "all", "investigation", r"(no signs of left ventricular hypertrophy|no thickening|no LVH)", r"(sin signos de hipertrofia|ningún engrosamiento|sin HVI)", "ECG without LVH"),
 ("egfr", L+"1/result", "clinical", "numeric", r"92 mL/min", r"92 mL/min", "eGFR 92"),
 ("salt", "/care_and_treatment/non_pharmacological_treatment", "all", "medication", r"5 g", r"5 g", "Salt below 5 g per day"),
 ("w_angio", W+"3/sign", "all", "warning", r"angioedema", r"angioedema", "Emergency: lip/tongue/face swelling (angioedema)"),
 ("w_180", W+"0/sign", "all", "warning", r"180/110", r"180/110", "Urgent: severe headache with BP >= 180/110"),
 ("fu_4w", F+"0/interval", "all", "followup", r"4 weeks", r"4 semanas", "Review in 4 weeks"),
 ("fu_lab", F+"1/purpose", "all", "followup", r"creatinine", r"creatinina", "Repeat creatinine and potassium"),
],
"HC0003": [
 ("dx", DX+"condition", "all", "diagnosis", r"type 2 diabetes", r"diabetes( mellitus)? tipo 2", "Type 2 diabetes"),
 ("dx_cert", DX+"status", "clinical", "certainty", r"confirm", r"confirmad", "Diabetes confirmed"),
 ("hba1c", L+"0/result", "all", "numeric", r"7\.8 ?%", r"7,8 ?%", "HbA1c 7.8%"),
 ("fpg", L+"1/result", "all", "numeric", r"156", r"156", "Fasting glucose 156 mg/dL"),
 ("ldl", L+"5/result", "all", "numeric", r"128", r"128", "LDL 128 mg/dL"),
 ("met", M+"0/dose", "all", "medication", r"metformin[^.]{0,60}500 mg", r"metformina[^.]{0,60}500 mg", "Metformin 500 mg"),
 ("met_ph1", M+"0/frequency", "all", "medication", r"(first 7 days|first week)[^.]{0,80}evening meal", r"(primeros 7 días|primera semana)[^.]{0,80}cena", "Metformin phase 1: once daily with evening meal for 7 days"),
 ("met_ph2", M+"0/frequency", "all", "medication", r"day 8[^.]{0,60}twice", r"día 8[^.]{0,60}dos veces", "Metformin phase 2: twice daily from day 8"),
 ("ator", M+"1/dose", "all", "medication", r"atorvastatin[^.]{0,20}\b20 mg", r"atorvastatina[^.]{0,20}\b20 mg", "Atorvastatin 20 mg"),
 ("retina", O+"0/status", "all", "investigation", r"(not yet|pending|not been done|not done yet)", r"(pendiente|aún no|todavía no)", "Retinal screening ordered, pending"),
 ("ketones", L+"6/result", "all", "investigation", r"ketones", r"cetonas|cetonuria", "Urine ketones negative"),
 ("w_emerg", W+"0/urgency", "all", "warning", EMERG_EN, EMERG_ES, "Emergency warnings present"),
 ("w_foot", W+"3/sign", "all", "warning", r"foot", r"pie", "Foot wound/redness/swelling: contact team"),
 ("fu_3m", F+"0/interval", "all", "followup", r"3 months", r"3 meses", "Review with HbA1c in 3 months"),
],
"HC0004": [
 ("dx_asthma", DX+"condition", "all", "diagnosis", r"asthma", r"asma", "Allergic asthma"),
 ("dx_rhin", DX+"condition", "all", "diagnosis", r"rhinitis", r"rinitis", "Persistent allergic rhinitis"),
 ("dx_mite", DX+"condition", "all", "diagnosis", r"dust mite", r"ácaros", "House dust mite sensitisation"),
 ("dx_conj", DX2+"0/condition", "all", "diagnosis", r"conjunctivitis", r"conjuntivitis", "Allergic conjunctivitis"),
 ("control", "/clinical_assessment_and_diagnosis/clinical_assessment", "all", "certainty", r"partly", r"parcialmente", "Asthma partly controlled"),
 ("bf", M+"0/strength", "all", "medication", r"160/4\.5", r"160/4,5", "Budesonide/formoterol 160/4.5 micrograms"),
 ("bf_bid", M+"0/frequency", "all", "medication", r"twice (daily|a day)", r"dos veces al día", "Maintenance 1 inhalation twice daily"),
 ("bf_max", M+"0/frequency", "all", "medication", r"12 (inhalations|puffs)", r"12 inhalaciones", "Max 12 inhalations/day"),
 ("mom", M+"1/dose", "all", "medication", r"2 sprays in each nostril", r"2 pulverizaciones en cada fosa nasal", "Mometasone 2 sprays each nostril"),
 ("cet", M+"2/dose", "all", "medication", r"cetirizine 10 mg", r"cetirizina 10 mg", "Cetirizine 10 mg"),
 ("salb_stop", M+"3/lifecycle_status", "all", "medication", r"salbutamol[^.]{0,60}(stop|replac)|(stop|replac)[^.]{0,60}salbutamol", r"salbutamol[^.]{0,60}(suspend|sustitu)|(deje de usar|sustitución)[^.]{0,60}salbutamol", "Salbutamol stopped/replaced"),
 ("plan", "/care_and_treatment/care_interventions", "all", "medication", r"action plan", r"plan de acción", "Written asthma action plan"),
 ("feno", O+"1/findings", "all", "investigation", r"FeNO", r"FeNO", "FeNO raised"),
 ("spiro", O+"0/findings", "clinical", "numeric", r"14 ?%", r"14 ?%", "FEV1 +14% after bronchodilator"),
 ("w_4h", W+"1/sign", "all", "warning", r"4 hours", r"4 horas", "Reliever > every 4 hours"),
 ("w_sentence", W+"0/sign", "all", "warning", r"sentence", r"frase", "Emergency: cannot speak in full sentences"),
 ("fu_6w", F+"0/interval", "all", "followup", r"6 weeks", r"6 semanas", "Review in 6 weeks"),
],
"HC0005": [
 ("dx", DX+"condition", "all", "diagnosis", r"gastro-?esophageal reflux", r"reflujo gastroesofágico", "GERD"),
 ("dx_susp", DX+"status", "all", "certainty", r"suspect|suggest|likely|provisional|not a confirmed|not confirmed", r"sospech|probable|provisional|no confirmado", "GERD suspected, not confirmed"),
 ("hb", L+"0/result", "all", "numeric", r"13\.6", r"13,6", "Haemoglobin 13.6 g/dL"),
 ("ome", M+"0/dose", "all", "medication", r"omeprazole 20 mg", r"omeprazol 20 mg", "Omeprazole 20 mg"),
 ("ome_time", M+"0/frequency", "all", "medication", r"30 minutes before breakfast", r"30 minutos antes del desayuno", "30 minutes before breakfast"),
 ("ome_dur", M+"0/duration", "all", "medication", r"8 weeks", r"8 semanas", "Omeprazole 8 weeks"),
 ("ibu", M+"1/dose", "all", "medication", r"ibuprofen 400 mg", r"ibuprofeno 400 mg", "Ibuprofen 400 mg recorded (QA-005)"),
 ("para_pref", "/care_and_treatment/treatment_recommendations", "all", "medication", r"paracetamol[^.]{0,40}instead of ibuprofen", r"paracetamol en (lugar|vez) de(l)? ibuprofeno", "Paracetamol recommended instead of ibuprofen"),
 ("w_exert", W+"3/sign", "all", "warning", r"chest pain[^.]{0,30}(exertion|effort)", r"dolor (torácico|en el pecho)[^.]{0,30}esfuerzo", "Emergency: exertional chest pain"),
 ("w_blood", W+"1/sign", "all", "warning", r"vomit(ing)? blood", r"vómitos con sangre|vomita sangre", "Emergency: vomiting blood / black stools"),
 ("fu_8w", F+"0/interval", "all", "followup", r"8 weeks", r"8 semanas", "Review in 8 weeks"),
],
"HC0006": [
 ("dx", DX+"condition", "all", "diagnosis", r"mechanical lower back pain", r"(lumbalgia|dolor lumbar) mecánic", "Acute mechanical low back pain"),
 ("pain", "/current_symptoms/overall_severity", "all", "numeric", r"6(/| out of )10", r"6( sobre |/)10", "Pain 6/10"),
 ("noimg", "/clinical_assessment_and_diagnosis/clinical_assessment", "all", "investigation", r"(imaging is not indicated|do not need an X-ray)", r"(no están indicadas pruebas de imagen|no necesita radiografías)", "Imaging not indicated"),
 ("ibu", M+"0/dose", "all", "medication", r"ibuprofen 400 mg", r"ibuprofeno 400 mg", "Ibuprofen 400 mg"),
 ("ibu_freq", M+"0/frequency", "all", "medication", r"every 8 hours with food", r"cada 8 horas con alimentos", "Every 8 hours with food"),
 ("ibu_dur", M+"0/duration", "all", "medication", r"7 days", r"7 días", "Up to 7 days"),
 ("lift", "/care_and_treatment/treatment_recommendations", "all", "medication", r"heavy lifting for 2 weeks", r"(cargar|levantar) peso durante 2 semanas", "Avoid heavy lifting for 2 weeks"),
 ("w_groin", W+"0/sign", "all", "warning", r"groin", r"ingle", "Emergency: groin/buttock numbness"),
 ("w_bladder", W+"1/sign", "all", "warning", r"bladder or bowel", r"vejiga o del intestino", "Emergency: loss of bladder/bowel control"),
 ("w_weak", W+"2/sign", "all", "warning", r"weakness in one or both legs", r"debilidad en una o ambas piernas", "Emergency: leg weakness"),
 ("fu_2w", F+"0/interval", "all", "followup", r"2 weeks", r"2 semanas", "Review at 2 weeks if not improving"),
],
"HC0007": [
 ("dx", DX+"condition", "all", "diagnosis", r"cystitis", r"cistitis", "Uncomplicated cystitis"),
 ("dx_uncomp", DX+"condition", "all", "diagnosis", r"uncomplicated", r"no complicada", "Uncomplicated"),
 ("allergy", "/relevant_medical_history/allergies/0/substance", "all", "history", r"penicillin", r"penicilina", "Penicillin allergy"),
 ("allergy_rx", "/relevant_medical_history/allergies/0/reaction", "clinical", "history", r"rash", r"erupción", "Reaction: skin rash"),
 ("nitrite", L+"1/result", "all", "investigation", r"nitrite", r"nitrito", "Nitrites positive"),
 ("preg", L+"3/result", "all", "investigation", r"pregnancy test[^.]{0,20}negative", r"(embarazo)[^.]{0,30}negativ", "Pregnancy test negative"),
 ("culture", L+"4/status", "all", "investigation", r"(culture)[^.]{0,200}(pending|not back)", r"urocultivo[^.]{0,200}(pendiente|aún no)", "Urine culture pending"),
 ("nitro", M+"0/dose", "all", "medication", r"nitrofurantoin[^.]{0,60}100 mg", r"nitrofurantoína[^.]{0,60}100 mg", "Nitrofurantoin MR 100 mg"),
 ("nitro_freq", M+"0/frequency", "all", "medication", r"twice (daily|a day) with food", r"dos veces al día con alimentos", "Twice daily with food"),
 ("nitro_dur", M+"0/duration", "all", "medication", r"5 days", r"5 días", "5 days"),
 ("w_fever", W+"0/sign", "all", "warning", r"fever or chills", r"fiebre o escalofríos", "Urgent: fever or chills"),
 ("w_urgent", W+"0/urgency", "all", "warning", URG_EN, URG_ES, "Urgent same-day warnings present"),
 ("fu", F+"0/interval", "all", "followup", r"48[–-]72 hours|48 to 72 hours", r"48[–-]72 horas|48 a 72 horas", "Reassess at 48-72 h if symptoms persist"),
],
"HC0008": [
 ("dx", DX+"condition", "all", "diagnosis", r"iron-deficiency anaemia", r"anemia ferropénica|anemia por (déficit|falta) de hierro", "Iron-deficiency anaemia"),
 ("dx_cert", DX+"status", "all", "certainty", r"confirm", r"confirmad", "Anaemia confirmed"),
 ("cause_susp", DX2+"0/status", "all", "certainty", r"(suspected|probable|most likely|not yet confirmed)", r"(sospech|probable|lo más probable|aún no está confirmado)", "Menstrual cause suspected"),
 ("hb", L+"0/result", "all", "numeric", r"9\.8 g/dL", r"9,8 g/dL", "Hb 9.8 g/dL"),
 ("ferr", L+"3/result", "all", "numeric", r"ferritin[^.]{0,10} 6 ng/mL", r"ferritina[^.]{0,10} 6 ng/mL", "Ferritin 6 ng/mL"),
 ("coeliac", L+"6/result", "all", "investigation", r"(transglutaminase[^.]{0,30}negative|coeliac[^.]{0,30}negative)", r"(antitransglutaminasa[^.]{0,30}negativ|celiaquía[^.]{0,20}negativ)", "Coeliac serology negative"),
 ("us", I+"0/status", "all", "investigation", r"ultrasound[^.]{0,80}(not yet|not been done|pending|still needs|requested|ordered)", r"ecografía[^.]{0,80}(aún no|todavía no|pendiente|solicitad|pedido)", "Pelvic ultrasound ordered, not done"),
 ("fe", M+"0/dose", "all", "medication", r"325 mg[^.]{0,30}65 mg", r"325 mg[^.]{0,30}65 mg", "Ferrous sulfate 325 mg (65 mg elemental)"),
 ("fe_empty", M+"0/frequency", "all", "medication", r"empty stomach", r"en ayunas", "On an empty stomach"),
 ("fe_dur", M+"0/duration", "all", "medication", r"(3 more months|further 3 months)", r"3 meses más", "Further 3 months after normalisation"),
 ("gyn", "/care_and_treatment/treatment_recommendations", "all", "followup", r"gynaecolog", r"ginecolog", "Gynaecology referral recommended"),
 ("w_pad", W+"3/sign", "all", "warning", r"pad every hour", r"compresa cada hora", "Urgent: bleeding soaking a pad every hour"),
 ("w_faint", W+"1/sign", "all", "warning", r"faint", r"desma", "Emergency: fainting"),
 ("fu_4w", F+"0/interval", "all", "followup", r"4 weeks", r"4 semanas", "Review in 4 weeks"),
],
"HC0009": [
 ("dx", DX+"condition", "all", "diagnosis", r"squamous cell carcinoma", r"carcinoma escamoso", "Glottic squamous cell carcinoma"),
 ("stage", DX+"condition", "all", "diagnosis", r"cT1a N0 M0", r"cT1a N0 M0", "Stage cT1a N0 M0"),
 ("dx_cert", DX+"status", "all", "certainty", r"confirm", r"confirmad", "Cancer confirmed by biopsy"),
 ("smoke", DX2+"0/condition", "all", "history", r"smok", r"(fumador|fumar|tabaco)", "Active smoker"),
 ("ct", I+"0/findings", "all", "investigation", r"(no (nodal|cartilage)|not spread|no cervical lymphadenopathy)", r"(sin (invasión|diseminación|adenopatías)|no se ha extendido)", "Staging CT: no spread"),
 ("biopsy", "/care_and_treatment/therapeutic_procedures/0/performed_date", "all", "procedure", r"14/10/2026", r"14/10/2026", "Biopsy performed 14/10/2026"),
 ("not_started", "/clinical_progress/clinical_course", "all", "progress", r"(has not yet started|has not started yet|not yet started)", r"(aún no ha comenzado|todavía no ha (comenzado|empezado))", "Definitive treatment not started"),
 ("unchanged", "/clinical_progress/clinical_course", "all", "progress", r"(unchanged|has not changed)", r"(sin cambios|no ha cambiado)", "Hoarseness unchanged since biopsy"),
 ("rt", "/care_and_treatment/treatment_recommendations", "all", "procedure", r"radiotherapy", r"radioterapia", "Option: radiotherapy"),
 ("laser", "/care_and_treatment/treatment_recommendations", "all", "procedure", r"laser", r"láser", "Option: transoral laser microsurgery"),
 ("amlo", M+"0/dose", "all", "medication", r"amlodipine 5 mg", r"amlodipino 5 mg", "Amlodipine 5 mg"),
 ("board", F+"0/scheduled_date", "all", "followup", r"06/11/2026", r"06/11/2026", "Tumour board scheduled 06/11/2026"),
 ("ent", F+"1/interval", "all", "followup", r"2 weeks", r"2 semanas", "ENT within 2 weeks"),
 ("w_breath", W+"0/sign", "all", "warning", r"(difficulty|noisy) breathing", r"dificultad para respirar|respiración ruidosa", "Emergency: breathing"),
 ("w_lump", W+"3/sign", "all", "warning", r"lump in (the|your) neck|neck lump", r"bulto[^.]{0,20}cuello", "New neck lump: contact ENT"),
],
"HC0010": [
 ("dx", DX+"condition", "all", "diagnosis", r"STEMI|ST-elevation myocardial infarction", r"IAMCEST|infarto[^.]{0,40}elevación del ST", "Inferior STEMI"),
 ("dx_rca", DX+"condition", "all", "diagnosis", r"right coronary artery", r"coronaria derecha", "Right coronary artery occlusion"),
 ("aps_susp", DX2+"0/status", "all", "certainty", r"(suspect|probabl)", r"(sospech|probable)", "APS suspected only"),
 ("aps_12w", "/clinical_assessment_and_diagnosis/diagnostic_uncertainty", "all", "certainty", r"12 weeks", r"12 semanas", "Repeat antibodies at >= 12 weeks"),
 ("trop", L+"0/result", "all", "numeric", r"980|troponin", r"980|troponina", "Troponin 980 ng/L (raised)"),
 ("ef", I+"1/findings", "all", "numeric", r"50 ?%", r"50 ?%", "LVEF about 50%"),
 ("la", L+"3/result", "all", "investigation", r"lupus anticoagulant[^.]{0,80}(not interpretable|could not be read)", r"anticoagulante lúpico[^.]{0,80}(no interpretable|no se pudo interpretar)", "Lupus anticoagulant uninterpretable on heparin"),
 ("asa_load", M+"4/administration_events/0/dose_administered", "all", "medication", r"(aspirin|acetylsalicylic acid) 300 mg", r"(aspirina|ácido acetilsalicílico) 300 mg", "Aspirin 300 mg given"),
 ("tic_load", M+"5/administration_events/0/dose_administered", "all", "medication", r"ticagrelor 180 mg", r"ticagrelor 180 mg", "Ticagrelor 180 mg given"),
 ("ufh", M+"6/route", "all", "medication", r"(intravenous|into a vein)[^.]{0,40}heparin|heparin[^.]{0,30}(intravenous|into a vein)", r"heparina[^.]{0,30}(intravenosa|en vena)", "UFH IV given"),
 ("asa", M+"0/dose", "all", "medication", r"(aspirin|acetylsalicylic acid) 100 mg", r"(aspirina|ácido acetilsalicílico) 100 mg", "Aspirin 100 mg daily"),
 ("tic", M+"1/dose", "all", "medication", r"ticagrelor 90 mg", r"ticagrelor 90 mg", "Ticagrelor 90 mg"),
 ("tic_freq", M+"1/frequency", "all", "medication", r"ticagrelor 90 mg[^.;\n]{0,40}twice", r"ticagrelor 90 mg[^.;\n]{0,40}dos veces", "Ticagrelor twice daily"),
 ("tic_dur", M+"1/duration", "all", "medication", r"12 months", r"12 meses", "Ticagrelor 12 months"),
 ("ator", M+"2/dose", "all", "medication", r"atorvastatin 80 mg", r"atorvastatina 80 mg", "Atorvastatin 80 mg"),
 ("biso", M+"3/dose", "all", "medication", r"bisoprolol 2\.5 mg", r"bisoprolol 2,5 mg", "Bisoprolol 2.5 mg"),
 ("stent", "/care_and_treatment/therapeutic_procedures/0/procedure_name", "all", "procedure", r"stent", r"stent", "PCI with drug-eluting stent"),
 ("no_stop", "/care_and_treatment/treatment_recommendations", "all", "medication", r"(not stop|do not stop)", r"(no suspender|no deje)", "Do not stop antiplatelets without cardiology"),
 ("72h", "/clinical_progress/clinical_course", "all", "progress", r"72 hours", r"72 horas", "Retrospective first-72-hour course"),
 ("w_cp5", W+"0/sign", "all", "warning", r"more than 5 minutes", r"más de 5 minutos", "Emergency: chest pain > 5 minutes"),
 ("w_leg", W+"3/sign", "all", "warning", r"swelling of a leg|leg swelling", r"hinchazón dolorosa (de|en) una pierna", "Urgent: painful leg swelling"),
 ("fu_card", F+"0/interval", "all", "followup", r"4 weeks after", r"4 semanas después", "Cardiology 4 weeks after discharge"),
 ("fu_haem", F+"1/interval", "all", "followup", r"at least 12 weeks", r"al menos 12 semanas", "Haematology repeat testing >= 12 weeks"),
 ("rehab", F+"2/specialty", "all", "followup", r"rehabilitation", r"rehabilitación", "Cardiac rehabilitation after discharge"),
],
}

# Documented negatives (symptom polarity). Pattern = the symptom term; the containing
# sentence must carry a negation cue. Path points to the 'presence: absent' entry.
# contractions ("you don't have a cough") are negations too; added after plain-language outputs were flagged wrongly
NEG = r"\b(no|not|denies|deny|denying|denied|without|none|nor|never|asymptomatic)\b|n't\b|n’t\b"
NEG_ES = r"\b(no|niega|sin|ni|ningún|ninguno|ninguna|nunca)\b"
NEGATIVES = {
 "HC0001": [(S+"3", r"cough", r"\btos\b", "No cough"), (S+"4", r"(nasal congestion|blocked nose|stuffy nose)", r"congestión nasal|nariz tapada", "No nasal congestion"), (S+"5", r"ear pain|earache", r"(dolor|otalgia)[^.]{0,10}(oído|de oídos)|otalgia", "No ear pain")],
 "HC0002": [(S+"0", r"headache", r"(cefalea|dolor de cabeza)", "No headache"), (S+"0", r"chest pain", r"(dolor torácico|dolor en el pecho)", "No chest pain")],
 "HC0003": [(S+"3", r"weight loss", r"pérdida de peso", "No weight loss"), (S+"4", r"blurred vision", r"visión borrosa", "No blurred vision")],
 "HC0004": [],
 "HC0005": [(S+"3", r"(dysphagia|trouble swallowing|difficulty swallowing)", r"(disfagia|dificultad para tragar)", "No dysphagia"), (S+"4", r"weight loss", r"pérdida de peso", "No weight loss"), (S+"6", r"(melaena|black stools)", r"(melenas|heces negras)", "No melaena")],
 "HC0006": [(S+"2", r"(numbness or tingling in (the|your) legs)", r"(entumecimiento (u|ni) hormigueo en las piernas)", "No leg numbness/tingling"), (S+"3", r"(bladder or bowel)", r"(vesical|vejiga)", "No bladder/bowel dysfunction"), (S+"5", r"fever", r"fiebre", "No fever")],
 "HC0007": [(S+"4", r"fever", r"fiebre", "No fever"), (S+"5", r"(flank pain|pain in your side)", r"(dolor en el flanco|dolor en el costado)", "No flank pain"), (S+"6", r"vaginal discharge", r"flujo vaginal", "No vaginal discharge")],
 "HC0008": [(S+"3", r"chest pain", r"(dolor torácico|dolor en el pecho)", "No chest pain"), (S+"4", r"black stools", r"heces negras", "No black stools"), (S+"6", r"weight loss", r"pérdida de peso", "No weight loss")],
 "HC0009": [(S+"1", r"(difficulty|pain|trouble)[^.]{0,20}swallow", r"(dificultad|dolor)[^.]{0,20}tragar", "No dysphagia/odynophagia"), (S+"4", r"(haemoptysis|coughing up blood)", r"(hemoptisis|sangre al toser)", "No haemoptysis"), (S+"6", r"(neck lump|lump in your neck)", r"bultos? en el cuello", "No neck lump")],
 "HC0010": [(S+"3", r"(syncope|did not faint)", r"(síncope|no se desmayó)", "No syncope")],
}

# Suspected conditions: a sentence that names the condition and asserts confirmation
# without a negation/hedge is a certainty contradiction.
SUSPECTED = {
 "HC0005": [(DX+"status", r"reflux", r"reflujo", "GERD suspected")],
 "HC0008": [(DX2+"0/status", r"(heavy (menstrual|periods))", r"(reglas abundantes|sangrado menstrual abundante)", "Menstrual cause suspected")],
 "HC0010": [(DX2+"0/status", r"antiphospholipid syndrome", r"síndrome antifosfolípido", "APS suspected")],
}
CONFIRM_EN = r"\bconfirm(ed|s)?\b"
CONFIRM_ES = r"\bconfirmad[oa]s?\b|\bconfirma\b"
HEDGE_EN = r"(not|un|only|suspect|probabl|possibl|may|likely|requires|pending|provisional|can only|until)"
HEDGE_ES = r"(no |sin |solo|sospech|probable|posible|puede|requiere|pendiente|provisional|aún|todavía)"

# Clinical progress: documented vs not yet assessed.
PROGRESS = {
 "HC0009": {"documented": True, "path": "/clinical_progress/clinical_course"},
 "HC0010": {"documented": True, "path": "/clinical_progress/clinical_course"},
}
NOT_ASSESSED_EN = r"(not yet (been )?(assessed|documented)|response not yet|progress not yet|response to (the new )?treatment not yet|not yet assessed)"
NOT_ASSESSED_ES = r"(aún no valorad|todavía no valorad|evolución aún no|respuesta aún no|aún no valorada)"
NO_PROGRESS_INFO_EN = r"no (progress )?information (is )?available|no information (about|on) (her|his|your) progress"
NO_PROGRESS_INFO_ES = r"no (hay|se dispone de) información|sin información sobre la evolución"

# Historical QA limitations (Prompt 4.1 detector set; written for this run and logged).
LIMITATIONS = {
 "QA-005": {"patient": "HC0005", "source_path": "/HC0005/medication_management/medications/1/lifecycle_status",
   "statement": "Ibuprofen remains recorded as active; paracetamol is recommended preferentially; no discontinuation documented.",
   "required": {"en": [r"ibuprofen", r"(not|does not|no) (say|document|record)[^.]{0,80}(stopped|discontinu)|no discontinuation[^.]{0,40}documented|whether[^.]{0,40}(stopped|continues)"],
                "es": [r"ibuprofeno", r"no (consta|dice|documenta)[^.]{0,80}(suspend|suspensión)|si se suspendió"]},
   "forbidden": {"en": [r"(?<!whether )(?<!if )ibuprofen (was|has been) (stopped|discontinued)", r"\bstop (taking )?(the )?ibuprofen", r"continue (taking )?ibuprofen"],
                 "es": [r"(?<!si )(se suspendió|se ha suspendido) el ibuprofeno", r"deje de tomar (el )?ibuprofeno", r"siga tomando (el )?ibuprofeno"]}},
 "QA-010": {"patient": "HC0010", "source_path": "/HC0010/clinical_progress/clinical_course",
   "statement": "Retrospective 72-hour course through approximately 03/11/2026; no separate progress-note or discharge date documented.",
   "required": {"en": [r"72 hours", r"\bno\b[^.]{0,60}discharge date|discharge date[^.]{0,40}not (documented|recorded|given)|(not|does not)[^.]{0,60}discharge date"],
                "es": [r"72 horas", r"no[^.]{0,60}fecha[^.]{0,60}alta"]},
   "forbidden": {"en": [r"discharged on \d", r"discharge (date|on)[: ]+\d"], "es": [r"alta (el|del) \d", r"dado de alta el"]}},
 "QA-011": {"patient": "HC0010", "source_path": "/HC0010/medication_management/medications/6/administration_events/0/dose_administered",
   "statement": "Intravenous unfractionated heparin was given; dose and exact administration time are not recorded.",
   "required": {"en": [r"heparin", r"(dose|amount)[^.]{0,80}not[^.]{0,30}(recorded|documented)"],
                "es": [r"heparina", r"no consta(n)?[^.]{0,40}dosis|dosis[^.]{0,60}no (consta|se registr)"]},
   "forbidden": {"en": [r"heparin[^.\n]{0,40}\d+\s*(units|IU|U/kg|mg)", r"\d+\s*(units|IU|U/kg)[^.\n]{0,30}heparin"],
                 "es": [r"heparina[^.\n]{0,40}\d+\s*(unidades|UI|U/kg|mg)", r"\d+\s*(unidades|UI|U/kg)[^.\n]{0,30}heparina"]}},
}

DRUG_LEXICON = {
 "amoxicillin": ["amoxicilina"], "paracetamol": ["paracetamol"], "ramipril": ["ramipril"], "metformin": ["metformina"],
 "atorvastatin": ["atorvastatina"], "budesonide": ["budesonida"], "formoterol": ["formoterol"], "mometasone": ["mometasona"],
 "cetirizine": ["cetirizina"], "salbutamol": ["salbutamol"], "omeprazole": ["omeprazol"], "ibuprofen": ["ibuprofeno"],
 "nitrofurantoin": ["nitrofurantoína"], "ferrous sulfate": ["sulfato ferroso"], "amlodipine": ["amlodipino"],
 "aspirin": ["aspirina", "ácido acetilsalicílico"], "ticagrelor": ["ticagrelor"], "bisoprolol": ["bisoprolol"],
 "heparin": ["heparina"], "rivaroxaban": ["rivaroxabán"],
}
FOREIGN_DRUGS = [
 "clopidogrel", "prasugrel", "warfarin", "warfarina", "apixaban", "apixabán", "enoxaparin", "enoxaparina", "insulin", "insulina",
 "prednisone", "prednisona", "prednisolone", "prednisolona", "azithromycin", "azitromicina", "ciprofloxacin", "ciprofloxacino",
 "fosfomycin", "fosfomicina", "lisinopril", "enalapril", "losartan", "losartán", "simvastatin", "simvastatina", "rosuvastatin",
 "rosuvastatina", "metoprolol", "naproxen", "naproxeno", "codeine", "codeína", "tramadol", "morphine", "morfina", "pantoprazole",
 "pantoprazol", "montelukast", "loratadine", "loratadina", "nitroglycerin", "nitroglicerina", "dapagliflozin", "empagliflozin",
 "sitagliptin", "cefalexin", "clarithromycin", "claritromicina", "ferrous fumarate", "dexamethasone", "dexametasona",
 "penicillin v", "phenoxymethylpenicillin", "fenoximetilpenicilina", "trimethoprim", "trimetoprima", "cephalexin",
]
