"""
Central configuration, loaded from environment variables (or a local .env file).
Copy .env.example to .env and fill in real values before running the app.
"""
import os

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - installed in deployment environments
    def load_dotenv():
        return False

load_dotenv()


def _setting(name, default=""):
    """Read environment variables first, then Streamlit Cloud secrets."""
    value = os.getenv(name)
    if value is not None:
        return value
    try:
        import streamlit as st

        return st.secrets.get(name, default)
    except (ImportError, RuntimeError, KeyError, FileNotFoundError):
        return default


# --- Database -----------------------------------------------------------
DATABASE_URL = _setting(
    "DATABASE_URL"
)

# --- AI provider selection --------------------------------------------------
# Best practical free option for restricted networks is local Ollama.
AI_PROVIDER = _setting("AI_PROVIDER", "gemini").lower()

# --- Google Gemini API ----------------------------------------------------
GEMINI_API_KEY = _setting("GEMINI_API_KEY")

def _normalise_gemini_model(model_name: str) -> str:
    """Map retired Gemini model names to a supported 3.x alternative."""
    raw = (model_name or "").strip()
    if not raw:
        return "gemini-3.8-flash"
    if raw.startswith("models/"):
        raw = raw.replace("models/", "", 1)
    retired = {"gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"}
    if raw in retired or raw.startswith("gemini-2.5-"):
        return "gemini-3.8-flash"
    return raw


# Use currently supported Gemini models. Newer accounts reject retired 2.5 model
# names, so prefer the 3.x family and keep a safe backup from the same generation.
GEMINI_MODEL = _normalise_gemini_model(_setting("GEMINI_MODEL", "gemini-3.8-flash"))
GEMINI_FALLBACK_MODELS = [
    _normalise_gemini_model(model.strip())
    for model in _setting(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.8-flash-lite",
    ).split(",")
    if model.strip()
]

# --- Local Ollama API ------------------------------------------------------
# Use a lightweight local LLM for more stable downloads and lower RAM usage.
# Recommended small model for this project: qwen2.5:3b-instruct
OLLAMA_BASE_URL = _setting("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = _setting("OLLAMA_MODEL", "qwen2.5:3b-instruct")

# --- FCA monitoring -------------------------------------------------------
# How often (in hours) the scheduled monitor_cli.py job should be run.
# This is informational only for wiring up your own cron / scheduler —
# see README.md.
FCA_CHECK_INTERVAL_HOURS = int(_setting("FCA_CHECK_INTERVAL_HOURS", "24"))
