"""Independent post-generation compliance validation."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json

import requests

from config import (
    AI_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)
from fca_monitor import get_sections_reference_text
from gemini_api import generate_interaction


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

BATCH_VALIDATION_PROMPT = """You are an independent UK financial promotions compliance reviewer.
Review each supplied draft independently against the FCA reference material and rules below.
Do not compare drafts or let one draft's issues affect another draft's result.

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
Drafts to review, in order:
{drafts_json}

Return ONLY valid JSON in this exact shape, with one result per draft in the same order:
{{
  "results": [
    {{"passed": true or false, "issues": ["specific issue"], "summary": "brief review summary"}}
  ]
}}
Set passed to false whenever a material issue is present. Use an empty issues array only
when that draft passes all rules. Return exactly {draft_count} results.
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
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        cleaned = cleaned[start_idx : end_idx + 1]

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


def _fallback_validation(reason: str) -> dict:
    """Fail closed when an independent AI compliance review is unavailable."""
    return {
        "passed": False,
        "issues": [reason],
        "summary": "Independent compliance validation could not be completed; content was not approved.",
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
            reference_text = (
                "FCA Handbook Core Guidance (COBS 4 Financial Promotions & PRIN Principles for Businesses):\n"
                "- Communications must be clear, fair, and not misleading.\n"
                "- Never promise or imply guaranteed returns or outcomes.\n"
                "- Capital and pension investment values can go down as well as up.\n"
                "- Any stated benefit must be balanced with clear risk statements.\n"
                "- Avoid high pressure or false urgency."
            )

        prompt = VALIDATION_PROMPT.format(
            category_note=CATEGORY_NOTES.get(category, ""),
            reference_text=reference_text[:12000],
            format_type=format_type,
            category=category,
            post_text=post_text,
        )

        if (AI_PROVIDER == "gemini" or GEMINI_API_KEY) and AI_PROVIDER != "ollama":
            raw = generate_interaction(
                GEMINI_MODEL,
                prompt,
                "Return the independent compliance review for this draft.",
                max_output_tokens=1000,
            )
            return _parse_result(raw)
        elif AI_PROVIDER == "ollama":
            raw = _call_ollama_json(prompt)
            return _parse_result(raw)
        
        return _fallback_validation("No AI compliance validator is configured.")
    except Exception as exc:
        return _fallback_validation(f"AI compliance validation failed: {exc}")


def validate_posts(posts: list, format_type: str, category: str) -> list:
    """Validate posts independently and preserve input order."""
    if not posts:
        return []

    if (AI_PROVIDER == "gemini" or GEMINI_API_KEY) and AI_PROVIDER != "ollama":
        try:
            reference_text = get_sections_reference_text()
            if not reference_text:
                reference_text = (
                    "FCA Handbook Core Guidance (COBS 4 Financial Promotions & PRIN Principles for Businesses):\n"
                    "- Communications must be clear, fair, and not misleading.\n"
                    "- Never promise or imply guaranteed returns or outcomes.\n"
                    "- Capital and pension investment values can go down as well as up.\n"
                    "- Any stated benefit must be balanced with clear risk statements.\n"
                    "- Avoid high pressure or false urgency."
                )

            prompt = BATCH_VALIDATION_PROMPT.format(
                category_note=CATEGORY_NOTES.get(category, ""),
                reference_text=reference_text[:12000],
                format_type=format_type,
                category=category,
                drafts_json=json.dumps(posts, ensure_ascii=False),
                draft_count=len(posts),
            )
            raw = generate_interaction(
                GEMINI_MODEL,
                prompt,
                "Return one independent compliance review for each draft, in order.",
                max_output_tokens=min(2000, max(384, len(posts) * 120 + 100)),
            )
            result_json = raw.strip()
            start_idx = result_json.find("{")
            end_idx = result_json.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                result_json = result_json[start_idx : end_idx + 1]
            payload = json.loads(result_json)
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list) or len(results) != len(posts):
                raise ValueError("Validator response did not contain one result per draft")
            return [_parse_result(json.dumps(result)) for result in results]
        except Exception as exc:
            return [
                _fallback_validation(f"AI compliance validation failed: {exc}")
                for _ in posts
            ]

    validate = partial(validate_post, format_type=format_type, category=category)
    with ThreadPoolExecutor(max_workers=min(4, len(posts))) as executor:
        return list(executor.map(validate, posts))
