"""Shared Google Gemini Interactions API client."""
from functools import lru_cache

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover - optional cloud dependency
    genai = None
    types = None

from config import GEMINI_API_KEY

GEMINI_TIMEOUT_MS = 30_000
GEMINI_API_VERSION = "v1beta"


@lru_cache(maxsize=1)
def _client():
    if genai is None or types is None:
        raise RuntimeError("Google GenAI SDK is not installed.")
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(
            api_version=GEMINI_API_VERSION,
            timeout=GEMINI_TIMEOUT_MS,
        ),
    )


def generate_interaction(
    model: str,
    system_instruction: str,
    user_input: str,
    max_output_tokens: int,
) -> str:
    """Generate JSON text through Gemini's current Interactions API."""
    interaction = _client().interactions.create(
        model=model,
        input=user_input,
        system_instruction=system_instruction,
        generation_config={
            "max_output_tokens": max_output_tokens,
            "thinking_level": "minimal",
        },
        api_version=GEMINI_API_VERSION,
        response_mime_type="application/json",
    )
    text = getattr(interaction, "output_text", "")
    if not text:
        raise RuntimeError("Gemini Interactions API returned no text output.")
    return text


def warm_up() -> None:
    _client()
