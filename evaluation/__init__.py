"""Shared clinical NLP evaluation engine (no Gradio imports)."""
from .engine import ENGINE_VERSION, evaluate  # noqa: F401
from .references import ReferenceStore, load_references  # noqa: F401
