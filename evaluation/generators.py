"""Text generation backends for summarization and plain-language adaptation.

One interface, ``generate(task, language, source_text, method=..., params=...)``, used by the
Gradio tabs and by batch runs. Two backends:

  rule_baseline   deterministic extractive / lexical baselines (evaluation.transforms); always available
  hf_local        a local Hugging Face instruct model, loaded lazily on first use, greedy decoding

Nothing is downloaded while the UI renders: ``hf_local`` loads only from the local Hugging Face
cache unless ``CLINICAL_EVAL_ALLOW_DOWNLOAD=1``. Every call returns a result dict with an explicit
``status`` ("ok" or "error"); a failed generation never produces text that could be scored.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any

GENERATION_MODELS: dict[str, dict[str, Any]] = {
    "qwen2.5-1.5b-instruct": {
        "label": "Qwen2.5-1.5B-Instruct (local, EN/ES)",
        "repo_id": os.environ.get("CLINICAL_EVAL_GEN_REPO", "Qwen/Qwen2.5-1.5B-Instruct"),
        # Pinned Hugging Face commit (override with CLINICAL_EVAL_GEN_REVISION); recorded in every output.
        "revision": os.environ.get("CLINICAL_EVAL_GEN_REVISION", "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"),
        "license": "Apache-2.0",
        "size_gb": 3.1,
    },
    # Larger or alternative local models for comparison. All revisions are pinned commits and recorded in every
    # output. A GPU is recommended for the 3B-7B models (about 6-15 GB in 16-bit); only one model is kept loaded.
    "qwen2.5-3b-instruct": {
        "label": "Qwen2.5-3B-Instruct (local, EN/ES · GPU recommended)",
        "repo_id": "Qwen/Qwen2.5-3B-Instruct",
        "revision": "aa8e72537993ba99e69dfaafa59ed015b17504d1",
        "license": "Qwen Research License (non-commercial)",
        "size_gb": 6.2,
    },
    "qwen2.5-7b-instruct": {
        "label": "Qwen2.5-7B-Instruct (local, EN/ES · GPU required)",
        "repo_id": "Qwen/Qwen2.5-7B-Instruct",
        "revision": "a09a35458c702b33eeacc393d103063234e8bc28",
        "license": "Apache-2.0",
        "size_gb": 15.2,
    },
    "phi-3.5-mini-instruct": {
        "label": "Phi-3.5-mini-instruct 3.8B (local, multilingual · GPU recommended)",
        "repo_id": "microsoft/Phi-3.5-mini-instruct",
        "revision": "2fe192450127e6a83f7441aef6e3ca586c338b77",
        "license": "MIT",
        "size_gb": 7.6,
    },
    "salamandra-7b-instruct": {
        "label": "Salamandra-7B-instruct (local, Spanish/European languages · GPU required)",
        "repo_id": "BSC-LT/salamandra-7b-instruct",
        "revision": "a3ed5452fafb3698a0423b1a18bfb5888f4e611b",
        "license": "Apache-2.0",
        "size_gb": 15.5,
    },
}
DEFAULT_GENERATION_MODEL = "qwen2.5-1.5b-instruct"
PROMPT_VERSION = {"summarization": "summ_v1", "plain_language": "plain_v8_sectionwise_layout"}
DEFAULT_PARAMS = {"max_new_tokens": 400, "do_sample": False, "repetition_penalty": 1.05, "seed": 0}
TASKS = ("summarization", "plain_language")

_LOCK = threading.Lock()
_LOADED: dict[str, dict[str, Any]] = {}
_LOAD_ERRORS: dict[str, str] = {}

# ------------------------------------------------------------------ prompts
_SAFEGUARDS = {
    "HC0005": {
        "en": "Ibuprofen remains an active medication in the record; paracetamol is preferred for headaches. "
              "Do not say ibuprofen was stopped or discontinued.",
        "es": "El ibuprofeno sigue activo en el registro; se recomienda preferentemente paracetamol para la cefalea. "
              "No digas que el ibuprofeno se suspendió.",
    },
    "HC0010": {
        "en": "Do not invent an exact discharge or summary date. Intravenous unfractionated heparin was given, "
              "but its dose and exact time are not documented: do not state a dose or time.",
        "es": "No inventes una fecha exacta de alta o de resumen. Se administró heparina no fraccionada intravenosa, "
              "pero la dosis y la hora exacta no constan: no indiques dosis ni hora.",
    },
}

_SYSTEM = {
    "en": "You are a careful clinical writer. Use only facts stated in the source text. Never add diagnoses, "
          "medicines, doses, dates or instructions that are not in the source. Keep every medication name, dose, "
          "route, frequency and duration exactly as written. Keep diagnostic certainty (suspected stays suspected). "
          "Keep warning signs and follow-up plans.",
    "es": "Eres un redactor clínico cuidadoso. Usa solo los datos del texto fuente. No añadas diagnósticos, "
          "medicamentos, dosis, fechas ni instrucciones que no aparezcan en él. Mantén exactamente el nombre, la dosis, "
          "la vía, la frecuencia y la duración de cada medicamento. Mantén la certeza diagnóstica (lo sospechado sigue "
          "siendo sospechado). Conserva los signos de alarma y el plan de seguimiento.",
}

_TASK = {
    ("summarization", "en"): "Write a concise clinical summary in English for a clinician, in at most {words} words. "
                             "Cover the diagnoses with their certainty, key findings and investigations, treatment and "
                             "medication details, warning signs and follow-up. Plain prose, no headings.",
    ("summarization", "es"): "Escribe un resumen clínico conciso en español para un profesional sanitario, de como máximo "
                             "{words} palabras. Incluye los diagnósticos con su grado de certeza, hallazgos y pruebas clave, "
                             "tratamiento y medicación, signos de alarma y seguimiento. Prosa, sin encabezados.",
    ("plain_language", "en"): (
        "Rewrite the SOURCE as an explanation for the patient in plain English (6th-8th grade reading level), in about "
        "{words} words. Follow every rule.\n"
        "STRICT RULES\n"
        "1. Facts: use only facts stated in the SOURCE. Never use any fact from the EXAMPLE; it is a different patient "
        "and only shows style and structure. Never add diagnoses, medicines, doses, tests, dates, appointments or advice.\n"
        "2. Voice: speak to the patient as 'you'. Never write the patient's name, any identifier, 'the patient' or "
        "'she/he'.\n"
        "3. Headings, in this order and exactly as written: Why you came; What we found; What this means; "
        "Your treatment; Warning signs; Follow-up. Write each heading on its own line as plain text, with no Markdown "
        "symbols such as #, * or **.\n"
        "4. Medicines: for every medicine write its name, dose, how to take it (say 'by mouth' for oral), how often, for "
        "how long, any maximum daily dose, and 'only when needed' if it is as needed. Copy every number exactly.\n"
        "5. Warning signs: list every warning sign. Keep its urgency exactly as in the SOURCE: 'call emergency "
        "services', 'go for emergency care now', 'get seen the same day', or 'contact the clinic'. Never move a sign "
        "to a different level of urgency.\n"
        "6. Numbers, time intervals and dates stay exactly as written (24 hours stays 24 hours; 72 hours stays 72 "
        "hours). Do not say a measurement is high, low or normal unless the SOURCE says so.\n"
        "7. Certainty and conditions stay the same: 'suspected' stays suspected; 'if not improving' stays conditional; "
        "'no date booked' stays not booked.\n"
        "8. Plain words: explain each medical term in everyday words the first time it appears and do not use medical "
        "abbreviations. Do not copy sentences from the SOURCE; say each point again simply.\n"
        "9. If something is not documented, leave it out or say it was not recorded. Do not guess. Do not describe the "
        "form of a medicine (tablet, liquid) unless the SOURCE does.\n"
        "10. Output only the explanation, with no notes about these rules."
    ),
    ("plain_language", "es"): (
        "Reescriba el TEXTO FUENTE como una explicación para el paciente en español sencillo, en unas {words} palabras. "
        "Cumpla todas las reglas.\n"
        "REGLAS ESTRICTAS\n"
        "1. Datos: use solo datos del TEXTO FUENTE. No use nunca datos del EJEMPLO; es otro paciente y solo muestra el "
        "estilo y la estructura. No añada diagnósticos, medicamentos, dosis, pruebas, fechas, citas ni consejos.\n"
        "2. Trato: diríjase al paciente de usted. No escriba nunca su nombre, ningún identificador, 'el paciente' ni "
        "'la paciente'.\n"
        "3. Apartados, en este orden y tal cual: Por qué vino; Qué encontramos; Qué significa; Su tratamiento; Signos "
        "de alarma; Seguimiento. Escriba cada apartado en su propia línea como texto simple, sin símbolos Markdown "
        "como #, * o **.\n"
        "4. Medicamentos: para cada medicamento indique el nombre, la dosis, cómo tomarlo ('por boca' si es oral), cada "
        "cuánto, durante cuánto tiempo, la dosis máxima diaria si la hay y 'solo si lo necesita' si es a demanda. Copie "
        "cada cifra exactamente.\n"
        "5. Signos de alarma: incluya todos. Mantenga la urgencia exacta del TEXTO FUENTE: 'llame a emergencias', "
        "'acuda a urgencias ahora', 'consulte el mismo día' o 'contacte con la consulta'. No cambie nunca un signo de "
        "nivel de urgencia.\n"
        "6. Las cifras, los intervalos y las fechas no cambian (24 horas sigue siendo 24 horas; 72 horas sigue siendo "
        "72 horas). No diga que una medida es alta, baja o normal si el TEXTO FUENTE no lo dice.\n"
        "7. La certeza y las condiciones no cambian: lo sospechado sigue siendo sospechado; 'si no mejora' sigue siendo "
        "condicional; 'sin fecha' sigue sin fecha.\n"
        "8. Palabras sencillas: explique cada término médico con palabras cotidianas la primera vez y no use "
        "abreviaturas médicas. No copie frases del TEXTO FUENTE; diga cada punto de forma sencilla.\n"
        "9. Si algo no consta, omítalo o diga que no se registró. No lo invente. No describa la forma del medicamento "
        "(comprimido, jarabe) si el TEXTO FUENTE no la indica.\n"
        "10. Escriba solo la explicación, sin notas sobre estas reglas."
    ),
}


_EXAMPLE_FILE = Path(__file__).resolve().parent.parent / "reference_narratives" / "clinical_narratives_bilingual_v9_prompt4_1.json"
_EXAMPLE_CASES = ("HC0002", "HC0003")   # one-shot example; never the patient being adapted


def _example(task: str, language: str, case_id: str | None) -> tuple[str, str] | None:
    """(example case id, reference patient explanation) from a different patient, for plain-language one-shot prompting."""
    if task != "plain_language" or os.environ.get("CLINICAL_EVAL_ONE_SHOT", "0") != "1":
        return None
    try:
        records = json.loads(_EXAMPLE_FILE.read_text(encoding="utf-8"))["records"]
    except (OSError, ValueError, KeyError):
        return None
    for case in _EXAMPLE_CASES:
        if case != case_id and records.get(case, {}).get(f"patient_explanation_{language}"):
            return case, records[case][f"patient_explanation_{language}"]
    return None


def build_messages(task: str, language: str, source_text: str, case_id: str | None = None,
                   target_words: int = 180) -> list[dict[str, str]]:
    if task not in TASKS:
        raise ValueError(f"Generation is only defined for {TASKS}, not {task!r}")
    if language not in ("en", "es"):
        raise ValueError("language must be 'en' or 'es'")
    system = _SYSTEM[language]
    note = _SAFEGUARDS.get(case_id or "", {}).get(language)
    if note:
        system += ("\nCase-specific safeguard: " if language == "en" else "\nSalvaguarda específica del caso: ") + note
    label = "SOURCE" if language == "en" else "TEXTO FUENTE"
    user = _TASK[(task, language)].format(words=int(target_words))
    example = _example(task, language, case_id)
    if example:
        intro = ("EXAMPLE (a different patient: copy only its style, structure and level of detail, never its facts)"
                 if language == "en" else
                 "EJEMPLO (otro paciente: imite solo su estilo, estructura y nivel de detalle, nunca sus datos)")
        user += f"\n\n{intro}:\n\"\"\"\n{example[1].strip()}\n\"\"\""
    user += f"\n\n{label}:\n\"\"\"\n{source_text.strip()}\n\"\"\""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ------------------------------------------------------------------ hf_local backend
def _allow_download() -> bool:
    return os.environ.get("CLINICAL_EVAL_ALLOW_DOWNLOAD") == "1" and os.environ.get("HF_HUB_OFFLINE") != "1"


def _snapshot(key: str) -> str:
    from huggingface_hub import snapshot_download
    cfg = GENERATION_MODELS[key]
    return snapshot_download(cfg["repo_id"], revision=cfg["revision"], local_files_only=not _allow_download(),
                             allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "merges.txt", "vocab.*"])


def _device() -> str:
    from .models import device
    return device()


def load_model(key: str) -> dict[str, Any]:
    """Load (or return the cached) generation model. Never raises; returns a status dict."""
    if key not in GENERATION_MODELS:
        return {"status": "error", "reason": f"Unknown generation model {key!r}"}
    with _LOCK:
        if key in _LOADED:
            return model_status(key)
        _unload_others(key)
        t0 = time.time()
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            path = _snapshot(key)
            dev = _device()
            if dev == "cuda":
                dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            else:
                dtype = torch.float16 if dev == "mps" else torch.float32
            tok = AutoTokenizer.from_pretrained(path)
            model = AutoModelForCausalLM.from_pretrained(path, dtype=dtype) if _accepts_dtype() else \
                AutoModelForCausalLM.from_pretrained(path, torch_dtype=dtype)
            model.to(dev).eval()
            import transformers
            _LOADED[key] = {"tokenizer": tok, "model": model, "device": dev, "dtype": str(dtype).replace("torch.", ""),
                            "snapshot_path": path, "revision": os.path.basename(path.rstrip("/")),
                            "load_seconds": round(time.time() - t0, 2), "transformers": transformers.__version__,
                            "torch": torch.__version__}
            _LOAD_ERRORS.pop(key, None)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            if type(e).__name__ in ("LocalEntryNotFoundError", "OfflineModeIsEnabled", "EntryNotFoundError") \
                    or "local_files_only" in msg or "outgoing traffic has been disabled" in msg:
                msg = (f"Not downloaded yet. Run once with network access: "
                       f"CLINICAL_EVAL_GEN_MODELS={key} python setup_eval_models.py")
            _LOAD_ERRORS[key] = msg[:600]
            return model_status(key)
    return model_status(key)


def _unload_others(key: str) -> None:
    """Keep one generation model in memory: switching models frees the previous one (GPU memory included)."""
    for other in [k for k in _LOADED if k != key]:
        _LOADED.pop(other, None)
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def _accepts_dtype() -> bool:
    try:
        import transformers
        return int(transformers.__version__.split(".")[0]) >= 5
    except Exception:  # noqa: BLE001
        return False


def model_status(key: str) -> dict[str, Any]:
    cfg = GENERATION_MODELS.get(key, {})
    if key in _LOADED:
        m = _LOADED[key]
        return {"status": "loaded", "model": cfg.get("repo_id"), "revision": m["revision"], "device": m["device"],
                "dtype": m["dtype"], "load_seconds": m["load_seconds"]}
    if key in _LOAD_ERRORS:
        return {"status": "error", "model": cfg.get("repo_id"), "reason": _LOAD_ERRORS[key]}
    return {"status": "not_loaded", "model": cfg.get("repo_id"), "revision": cfg.get("revision"),
            "license": cfg.get("license"), "approx_size_gb": cfg.get("size_gb"),
            "note": "Loads on first use from the local Hugging Face cache (python setup_eval_models.py downloads it; "
                    "set CLINICAL_EVAL_GEN_MODELS=all to include the larger comparison models)."}


def _generate_hf(key: str, messages: list[dict[str, str]], params: dict[str, Any]) -> dict[str, Any]:
    st = load_model(key)
    if st["status"] != "loaded":
        return {"status": "error", "error": st.get("reason", "model not loaded")}
    m = _LOADED[key]
    import torch
    tok, model = m["tokenizer"], m["model"]
    prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    enc = tok(prompt, return_tensors="pt").to(m["device"])
    torch.manual_seed(int(params["seed"]))
    t0 = time.time()
    with _LOCK, torch.no_grad():
        out = model.generate(**enc, max_new_tokens=int(params["max_new_tokens"]), do_sample=False,
                             repetition_penalty=float(params["repetition_penalty"]),
                             pad_token_id=tok.eos_token_id)
    new = out[0][enc["input_ids"].shape[1]:]
    text = tok.decode(new, skip_special_tokens=True).strip()
    return {"status": "ok", "output": text, "prompt_tokens": int(enc["input_ids"].shape[1]),
            "output_tokens": int(new.shape[0]), "generation_seconds": round(time.time() - t0, 2),
            "truncated_at_max_tokens": int(new.shape[0]) >= int(params["max_new_tokens"])}


# ------------------------------------------------------------------ section-by-section plain-language adaptation
_SECTION_RULES = {
    "en": ("Rewrite this part of a clinical note for the patient in plain English (6th-8th grade reading level). "
           "Write only the text for the section \"{heading}\" of a patient explanation: {what} Use about {words} "
           "words. No heading, no Markdown symbols.\n"
           "STRICT RULES\n"
           "1. Use only facts in this PART. Add nothing: no diagnoses, medicines, doses, tests, dates, appointments or "
           "advice that are not in it.\n"
           "2. Speak to the patient as 'you'. Never write a name, 'the patient' or 'she/he'.\n"
           "3. Keep every medicine name, dose, how to take it (say 'by mouth' for oral), how often, for how long, any "
           "maximum daily dose and 'only when needed'. Keep every number, time interval and date exactly.\n"
           "4. Keep the urgency of each warning sign exactly: call emergency services, go for emergency care now, get "
           "seen the same day, or contact the clinic.\n"
           "5. Keep certainty and conditions: suspected stays suspected; 'if not improving' stays conditional; 'no "
           "date booked' stays not booked. Do not call a value high, low or normal unless the PART does.\n"
           "6. Explain medical terms in everyday words and write out abbreviations. Use short sentences."),
    "es": ("Reescriba esta parte de una nota clínica para el paciente en español sencillo. Escriba solo el texto del "
           "apartado \"{heading}\" de una explicación para el paciente: {what} Use unas {words} palabras. Sin "
           "título y sin símbolos Markdown.\n"
           "REGLAS ESTRICTAS\n"
           "1. Use solo datos de esta PARTE. No añada nada: ni diagnósticos, medicamentos, dosis, pruebas, fechas, citas "
           "ni consejos que no estén en ella.\n"
           "2. Diríjase al paciente de usted. No escriba nunca un nombre, 'el paciente' ni 'la paciente'.\n"
           "3. Mantenga el nombre de cada medicamento, la dosis, cómo tomarlo ('por boca' si es oral), cada cuánto, "
           "durante cuánto tiempo, la dosis máxima diaria y 'solo si lo necesita'. Mantenga exactas todas las cifras, "
           "intervalos y fechas.\n"
           "4. Mantenga exactamente la urgencia de cada signo de alarma: llamar a emergencias, acudir a urgencias ahora, "
           "consultar el mismo día o contactar con la consulta.\n"
           "5. Mantenga la certeza y las condiciones: lo sospechado sigue sospechado; 'si no mejora' sigue siendo "
           "condicional; 'sin fecha' sigue sin fecha. No diga que un valor es alto, bajo o normal si la PARTE no lo dice.\n"
           "6. Explique los términos médicos con palabras cotidianas y escriba las abreviaturas completas. Use frases "
           "cortas."),
}
_YOU = {
    "en": "\nAlways speak directly to the patient as 'you' (for example 'You came…', 'Take…'). Never write 'the patient', "
          "'he', 'she', 'I' or 'my' for the patient.",
    "es": "\nDiríjase siempre directamente al paciente de usted (por ejemplo 'Usted vino…', 'Tome…'). No escriba nunca "
          "'el paciente', 'la paciente', 'él', 'ella', 'yo' ni 'mi' para el paciente.",
}
_SECTION_WHAT = {
    "en": {"reason": "why you came and your relevant history.", "found": "what the examination and tests found.",
           "means": "what the diagnosis means.", "treatment": "first one line per medicine in the form '- name dose, by mouth, how often, for how long' "
                         "(add 'only when needed' and the maximum per day if the PART gives them), then the advice in "
                         "short sentences.",
           "warning": "each warning sign and exactly what to do, one per line starting with '- '.",
           "follow": "what happens next."},
    "es": {"reason": "por qué vino y sus antecedentes relevantes.", "found": "qué encontró la exploración y las pruebas.",
           "means": "qué significa el diagnóstico.", "treatment": "primero una línea por medicamento con la forma '- nombre dosis, por boca, cada cuánto, "
                         "durante cuánto tiempo' (añada 'solo si lo necesita' y el máximo al día si la PARTE los indica), "
                         "y después los consejos en frases cortas.",
           "warning": "cada signo de alarma y exactamente qué hacer, uno por línea empezando por '- '.",
           "follow": "qué pasará después."},
}


def _source_sections(source_text: str) -> list[tuple[str, str]]:
    """Split a professional narrative into (output section, text), in output order; urgent follow-up sentences
    become warning signs."""
    from .transforms import _HEADING, _PL_SECTION, _URGENT, _sentences
    parts: dict[str, list[str]] = {k: [] for k in ("reason", "found", "means", "treatment", "warning", "follow")}
    section = "reason"
    for line in source_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _HEADING.match(line) and len(line) < 60:
            section = next((sec for rx, sec in _PL_SECTION if rx.search(line)), "found")
            continue
        if section == "follow":
            for sentence in _sentences(line):
                parts["warning" if _URGENT.search(sentence) else "follow"].append(sentence)
        else:
            parts[section].append(line)
    return [(k, " ".join(v)) for k, v in parts.items() if v]


def generate_sectionwise(key: str, language: str, source_text: str, case_id: str | None,
                         p: dict[str, Any]) -> dict[str, Any]:
    import re
    from .transforms import _PL_HEADINGS, VOICE, plain_text
    system = _SYSTEM[language] + _YOU[language]
    note = _SAFEGUARDS.get(case_id or "", {}).get(language)
    if note:
        system += ("\nCase-specific safeguard: " if language == "en" else "\nSalvaguarda específica del caso: ") + note
    label = "PART" if language == "en" else "PARTE"
    blocks, calls, total_out, seconds, truncated = [], [], 0, 0.0, False
    for section, text in _source_sections(source_text):
        heading = _PL_HEADINGS[language][section]
        for pattern, repl in VOICE[language]:   # turn the note's third person towards the patient first
            text = re.sub(pattern, repl, text)
        n_words = len(text.split())
        words = max(25, int(n_words * 0.9))
        user = _SECTION_RULES[language].format(heading=heading, what=_SECTION_WHAT[language][section], words=words)
        user += f"\n\n{label}:\n\"\"\"\n{text}\n\"\"\""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        cp = dict(p, max_new_tokens=min(int(p["max_new_tokens"]), int(n_words * 3) + 80))
        res = _generate_hf(key, messages, cp)
        if res.get("status") != "ok":
            return res
        calls.append({"section": section, "messages": messages, "output": res["output"],
                      "output_tokens": res["output_tokens"], "max_new_tokens": cp["max_new_tokens"]})
        total_out += res["output_tokens"]
        seconds += res["generation_seconds"]
        truncated = truncated or res["truncated_at_max_tokens"]
        body, _ = plain_text(res["output"])
        # drop a heading the model repeated at the top of its section ("Treatment", "Follow-up:")
        lines = body.splitlines()
        while lines and (not lines[0].strip() or re.fullmatch(
                rf"\W*(?:{re.escape(heading)}|{'|'.join(map(re.escape, _PL_HEADINGS[language].values()))})\W*",
                lines[0].strip(), flags=re.I)):
            lines.pop(0)
        blocks.append(f"{heading}\n" + "\n".join(lines).strip())
    return {"status": "ok", "output": "\n\n".join(blocks), "output_tokens": total_out,
            "generation_seconds": round(seconds, 2), "truncated_at_max_tokens": truncated,
            "strategy": "sectionwise", "section_calls": calls}


# ------------------------------------------------------------------ public interface
def methods(task: str) -> list[tuple[str, str]]:
    """(label, value) choices for a task's method dropdown."""
    base = {"summarization": "Rule baseline: clinically weighted extractive",
            "plain_language": "Rule baseline: lexical simplification",
            "anonymization": "Deterministic synthetic-identifier replacement"}[task]
    out = [(base, "rule_baseline")]
    if task in TASKS:
        out += [(cfg["label"], f"hf_local:{k}") for k, cfg in GENERATION_MODELS.items()]
    return out


def generate(task: str, language: str, source_text: str, *, method: str, case_id: str | None = None,
             params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate one output. Returns {status, output, method, model, parameters, messages, ...}."""
    if not source_text or not source_text.strip():
        return {"status": "error", "error": "Source text is empty.", "method": method}
    p = {**DEFAULT_PARAMS, **(params or {})}
    if method.startswith("hf_local:"):
        key = method.split(":", 1)[1]
        target_words = int(p.pop("target_words", 180))
        messages = build_messages(task, language, source_text, case_id, target_words)
        p["target_words"] = target_words
        try:
            if task == "plain_language" and os.environ.get("CLINICAL_EVAL_PL_STRATEGY", "sectionwise") == "sectionwise":
                res = generate_sectionwise(key, language, source_text, case_id, p)
                messages = [c["messages"] for c in res.get("section_calls", [])] or messages
            else:
                res = _generate_hf(key, messages, p)
        except Exception as e:  # noqa: BLE001  (out-of-memory, device errors…)
            res = {"status": "error", "error": f"{type(e).__name__}: {e}"[:600]}
        if res.get("status") == "ok" and not res["output"].strip():
            res = {"status": "error", "error": "The model returned empty text."}
        info = _LOADED.get(key, {})
        res.update({"method": method, "model": GENERATION_MODELS[key]["repo_id"],
                    "model_revision": info.get("revision"), "device": info.get("device"),
                    "dtype": info.get("dtype"), "library_versions": {"transformers": info.get("transformers"),
                                                                     "torch": info.get("torch")},
                    "parameters": p, "prompt_version": PROMPT_VERSION.get(task),
                    "one_shot_example_case": (_example(task, language, case_id) or (None,))[0], "messages": messages})
        return res
    raise ValueError(f"Unknown generation method {method!r}")
