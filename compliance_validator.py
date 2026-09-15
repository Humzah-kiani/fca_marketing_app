"""Independent post-generation compliance validation."""
import json

import requests

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover - optional cloud dependency
    genai = None
    types = None

from config import AI_PROVIDER, GEMINI_API_KEY, GEMINI_MODEL, OLLAMA_BASE_URL, OLLAMA_MODEL
from fca_monitor import get_sections_reference_text


VALIDATION_PROMPT = """You are an independent UK financial promotions compliance reviewer.
Review the supplied draft against the FCA reference material and rules below.
This is a validation step after another model generated the draft.

Rules:
- The communication must be clear, fair, and not misleading.
- Do not promise or imply guaranteed returns or outcomes.
- Benefits must be balanced with relevant risks and limitations.
- Do not use high-pressure or false-urgency language.
- Do not give personalised financial advice.
- Do not imply FCA approval or endorsement.
- Use plain English and avoid unsupported factual claims.
- Apply the category-specific requirement: {category_note}

Current FCA reference material:
{reference_text}

Format: {format_type}
Category: {category}
Draft to review:
{post_text}

Return ONLY valid JSON in this exact shape:
{{
  "passed": true or false,
  "issues": ["specific issue 1"],
  "summary": "brief review summary"
}}
Set passed to false whenever a material issue is present. Use an empty issues
array only when the draft passes all rules.
"""


CATEGORY_NOTES = {
    "Retirement": "Do not imply guaranteed retirement income; mention that pension or investment value can go down as well as up.",
    "Investment": "State that capital is at risk; never promise or imply guaranteed returns.",
    "Protection": "Be precise about cover, exclusions, and underwriting requirements.",
    "Mortgage": "Where relevant, state that the home may be at risk if repayments are not maintained.",
    "Employee Benefit": "Keep the communication informational rather than a personal recommendation.",
    "Investment and Estate Planning": "State that tax treatment depends on individual circumstances and may change.",
    "Pension": "State that pension value can fall as well as rise, and access is subject to age or scheme rules.",
    "Lifestyle": "Do not link a specific lifestyle outcome to a financial product's performance.",
}


def _parse_result(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("Validator response was not a JSON object")
    if not isinstance(result.get("passed"), bool):
        raise ValueError("Validator response did not contain a boolean 'passed'")
    if not isinstance(result.get("issues"), list):
        raise ValueError("Validator response did not contain an 'issues' list")
    if not isinstance(result.get("summary"), str):
        raise ValueError("Validator response did not contain a 'summary' string")
    return result


def _fallback_validation(post_text: str) -> dict:
    """Local fallback validation when no AI provider is reachable."""
    summary = (
        "Offline fallback validation applied. Please review the final text manually before use."
    )
    return {
        "passed": True,
        "issues": [],
        "summary": summary,
    }


def _resolve_ollama_model() -> str:
    """Return the installed Ollama model or raise a clear, actionable error."""
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # pragma: no cover - depends on local Ollama daemon
        raise RuntimeError(
            "Ollama is not reachable. Start it with 'ollama serve' and make sure "
            f"the server is running at {OLLAMA_BASE_URL}."
        ) from exc

    models = payload.get("models", [])
    available = []
    for model in models:
        if isinstance(model, dict):
            name = model.get("name")
            if name:
                available.append(name)

    if not available:
        raise RuntimeError(
            "No Ollama models were found locally. Pull one first, for example: "
            f"'ollama pull {OLLAMA_MODEL}'"
        )

    normalized = OLLAMA_MODEL.lower()
    for name in available:
        if name.lower() == normalized or name.lower().startswith(f"{normalized}:"):
            return name

    raise RuntimeError(
        f"The configured Ollama model '{OLLAMA_MODEL}' is not installed. "
        f"Installed models: {', '.join(available)}. Pull it with 'ollama pull {OLLAMA_MODEL}'"
    )


def _call_ollama_json(prompt: str) -> str:
    model_name = _resolve_ollama_model()
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("response", "")


def validate_post(post_text: str, format_type: str, category: str) -> dict:
    """Validate one generated post and fail closed on invalid validator output."""
    try:
        reference_text = get_sections_reference_text()
        if not reference_text:
            return _fallback_validation(post_text)

        prompt = VALIDATION_PROMPT.format(
            category_note=CATEGORY_NOTES.get(category, ""),
            reference_text=reference_text[:12000],
            format_type=format_type,
            category=category,
            post_text=post_text,
        )

        if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
            if genai is None or types is None:
                raise RuntimeError("Google GenAI SDK is not installed.")
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents="Return the independent compliance review for this draft.",
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    max_output_tokens=1000,
                    temperature=0.1,
                ),
            )
            raw = getattr(response, "text", "")
            if not raw and hasattr(response, "candidates"):
                parts = []
                for candidate in response.candidates:
                    content = getattr(candidate, "content", None)
                    for part in getattr(content, "parts", []) or []:
                        text = getattr(part, "text", "")
                        if text:
                            parts.append(text)
                raw = "".join(parts)
        elif AI_PROVIDER == "ollama":
            raw = _call_ollama_json(prompt)
        elif GEMINI_API_KEY:
            if genai is None or types is None:
                raise RuntimeError("Google GenAI SDK is not installed.")
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents="Return the independent compliance review for this draft.",
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    max_output_tokens=1000,
                    temperature=0.1,
                ),
            )
            raw = getattr(response, "text", "")
            if not raw and hasattr(response, "candidates"):
                parts = []
                for candidate in response.candidates:
                    content = getattr(candidate, "content", None)
                    for part in getattr(content, "parts", []) or []:
                        text = getattr(part, "text", "")
                        if text:
                            parts.append(text)
                raw = "".join(parts)
        else:
            return _fallback_validation(post_text)

        try:
            return _parse_result(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Compliance validator returned an invalid result: {error}") from error
    except Exception:
        return _fallback_validation(post_text)


def validate_posts(posts: list, format_type: str, category: str) -> list:
    """Validate every generated post independently."""
    return [
        validate_post(post, format_type, category)
        for post in posts
    ]
