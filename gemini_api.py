"""
Gemini Interactions API client shared by generation and compliance review.

    pip install -U "google-genai>=2.3.0"

Why it is fast
  * JSON output mode is enabled, with an optional schema for callers that require one.
  * One cached client (TLS connection reused), store=False (stateless call, nothing is saved server-side).
Public API: generate_interaction(model, system_prompt, user_prompt, max_output_tokens, response_schema=None) -> str
"""
from functools import lru_cache

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL

TIMEOUT_MS = 90_000
GEMINI_TIMEOUT_MS = TIMEOUT_MS
THINKING_LEVEL = "low"
HEADROOM_TOKENS = 512
MAX_OUTPUT_CAP = 4000

POSTS_SCHEMA = {
    "type": "object",
    "properties": {"posts": {"type": "array", "items": {"type": "string"}}},
    "required": ["posts"],
}

last_usage: dict = {}


@lru_cache(maxsize=20)
def _client(timeout_ms: int = TIMEOUT_MS):
    return genai.Client(
        api_key=GEMINI_API_KEY or None,
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=timeout_ms,
        ),
    )


def generate_interaction(
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    response_schema: dict | None = None,
    timeout_ms: int = TIMEOUT_MS,
) -> str:
    """Make one stateless Interactions API request and return its text output."""
    client = _client() if timeout_ms == TIMEOUT_MS else _client(timeout_ms)
    gen_cfg = {
        "max_output_tokens": min(
            MAX_OUTPUT_CAP,
            int(max_output_tokens) + HEADROOM_TOKENS,
        )
    }
    if model.startswith("gemini-3") and THINKING_LEVEL in {"low", "medium", "high"}:
        gen_cfg["thinking_level"] = THINKING_LEVEL

    response_format = {
        "type": "text",
        "mime_type": "application/json",
    }
    if response_schema is not None:
        response_format["schema"] = response_schema

    interaction = client.interactions.create(
        model=model,
        input=user_prompt,
        system_instruction=system_prompt,
        generation_config=gen_cfg,
        api_version="v1beta",
        response_format=response_format,
        store=False,
    )

    usage = getattr(interaction, "usage", None)
    if usage is not None:
        last_usage.update(
            input=getattr(usage, "total_input_tokens", None),
            output=getattr(usage, "total_output_tokens", None),
            thinking=getattr(usage, "total_thought_tokens", None),
        )

    text = getattr(interaction, "output_text", "") or ""
    if not text.strip():
        raise RuntimeError(f"Gemini returned no text (status={getattr(interaction, 'status', 'unknown')}).")
    return text


def warm_up(ping: bool = True) -> None:
    """Build the client and optionally make a small request to establish a connection."""
    client = _client()
    if ping:
        client.interactions.create(
            model=GEMINI_MODEL,
            input="ok",
            generation_config={"max_output_tokens": 16},
            api_version="v1beta",
            store=False,
        )
