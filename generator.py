"""
Generates FCA-aware marketing copy using the Google Gemini API, grounded in the
currently cached FCA Handbook/guidance text (see fca_monitor.py).
"""
import json
import uuid

import requests

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover - optional cloud dependency
    genai = None
    types = None

from config import (
    AI_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)
from fca_monitor import get_sections_reference_text

CATEGORY_NOTES = {
    "Retirement": "Do not imply guaranteed retirement income; note that pension/investment value can go down as well as up.",
    "Investment": "Must convey capital-at-risk; never promise or imply guaranteed returns; balance any benefit with a risk statement.",
    "Protection": "Be precise about what is and isn't covered; do not understate exclusions or underwriting requirements.",
    "Mortgage": "Where relevant, reflect that the home may be at risk if repayments are not kept up; avoid rate promises that could mislead.",
    "Employee Benefit": "Keep tone informational rather than a personal recommendation, since the audience is a broad workforce.",
    "Investment and Estate Planning": "Note that tax treatment depends on individual circumstances and may change; avoid guaranteed-outcome language, especially on IHT mitigation.",
    "Pension": "State that pension value can fall as well as rise, and that access is subject to age/scheme rules.",
    "Lifestyle": "Keep aspirational language honest — do not tie specific lifestyle outcomes to a financial product's performance.",
}

SYSTEM_PROMPT_TEMPLATE = """You are a UK financial promotions compliance copywriter writing {format_type} content for the "{category}" category.

Use the FCA guidance and category note carefully. The goal is to create compliant, publishable content that is clearly tailored to {category}.

Core rules:
- The target category is exactly: {category}.
- Write content that is unmistakably tailored to {category}; use category-specific examples, risks, benefits and wording.
- Do not reuse generic wording across categories or repeat the same hook, wording, or structure for every try.
- Do not produce the same message twice; vary the angle, examples and emphasis while staying compliant.
- Each item in the final JSON array must be distinct from the others and specifically written for {category}.
- Never promise or imply guaranteed returns, outcomes, or benefits.
- Always balance any stated benefit with a relevant risk statement.
- No urgent sales pressure or false urgency.
- Do not give personal advice; invite the reader to speak to a regulated adviser if appropriate.
- Category-specific note: {category_note}

Reference material (use only the most relevant extracts):
{reference_text}

Additional adviser guideline: {guideline}

Output format:
- Return only a JSON array of exactly {num_posts} strings.
- No markdown, no headings, no commentary outside the JSON array.
- Each string must be a complete final {format_type} asset for the "{category}" category.
- Make each asset different in wording and structure, not copy-pasted variations of the same text.
"""


def _build_system_prompt(format_type: str, category: str, category_note: str, reference_text: str, guideline: str, num_posts: int) -> str:
    """Build a shorter prompt that is still category-specific but avoids slow, bloated inputs."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        format_type=format_type,
        category=category,
        category_note=category_note,
        reference_text=(reference_text or "(No additional FCA text available)")[:2000],
        guideline=(guideline or "(none provided)")[:500],
        num_posts=num_posts,
    )


def _build_user_prompt(
    format_type: str,
    category: str,
    num_posts: int,
    variation_token: str | None = None,
    angle_seed: int | None = None,
) -> str:
    """Add a generation-specific seed so every retry uses a fresh angle instead of the same content."""
    variation_token = variation_token or uuid.uuid4().hex[:8]
    angle_seed = angle_seed if angle_seed is not None else int(variation_token[:2], 16)
    angle_options = [
        "practical decision-making",
        "risk-aware education",
        "common misconceptions",
        "personal circumstances and suitability",
        "benefits balanced with realistic risk",
    ]
    angles = [
        angle_options[(angle_seed + offset) % len(angle_options)]
        for offset in range(min(num_posts, 5))
    ]
    serialised_angles = "; ".join(f"{i + 1}) {angle}" for i, angle in enumerate(angles))
    return (
        f"Create {num_posts} distinct {format_type.lower()} assets for the '{category}' category. "
        f"This is generation attempt {variation_token}. Use these different angles: {serialised_angles}. "
        f"Each output must be clearly tailored to {category}, not a generic template, and must not repeat the same hook, wording, structure, or examples as earlier attempts."
    )


def _dedupe_posts(posts: list[str]) -> list[str]:
    """Remove repeated text while preserving the first unique instance for each item."""
    unique_posts: list[str] = []
    seen: set[str] = set()
    for post in posts:
        if not isinstance(post, str):
            continue
        key = " ".join(post.lower().split())
        if key in seen:
            continue
        seen.add(key)
        unique_posts.append(post)
    return unique_posts


def _format_fallback_post(category: str, guideline: str, index: int) -> str:
    """Return a polished, compliant social-post template."""
    headline = [
        f"{category}: a decision worth thinking through carefully",
        f"Understanding the options before making a {category.lower()} decision",
        f"What to consider before choosing a {category.lower()} approach",
    ]
    body = [
        "Making a financial decision can feel overwhelming, especially when there are several options to weigh up. The most important step is to understand the benefits, the risks and how the decision fits with your broader goals.",
        "For many people, a good starting point is clarity. Knowing what a product or approach does, how it works, and what could affect outcomes helps you make a more informed decision.",
        "Financial decisions are personal and can depend on your goals, timescale, attitude to risk and tax position. Getting the right information first can help you think through what may be suitable for you.",
    ]
    risk = (
        "Financial products can go down as well as up, and tax treatment depends on individual circumstances. "
        "Any decision should be based on your own objectives and risk profile."
    )
    cta = guideline.strip() if guideline.strip() else "If you'd like to discuss your circumstances, we can talk through the options and help you think about what may be suitable."
    return (
        f"{headline[index % len(headline)]}\n\n"
        f"{body[index % len(body)]}\n\n"
        f"{risk}\n\n"
        f"{cta}"
    )


def _format_fallback_carousel(category: str, guideline: str, index: int) -> str:
    """Return a compliant carousel deck in slide-ready format."""
    guidance = guideline.strip() or "If you'd like to understand how this may fit with your circumstances, we can discuss the options with you."
    heading = [
        f"{category}: what to think about",
        f"The key question is fit",
        f"Benefits and trade-offs",
        f"Risk matters",
        f"Next steps",
    ]
    slide_1 = f"Slide 1: {heading[index % len(heading)]}"
    slide_2 = (
        "Slide 2: Before making a financial decision, it helps to understand what the option is designed to do, "
        "who it may be suitable for, and what it does not do."
    )
    slide_3 = (
        "Slide 3: A clear view of the benefits matters, but so does understanding the risks, time horizon and any tax or legal considerations."
    )
    slide_4 = (
        "Slide 4: Financial products can go down as well as up, and outcomes can depend on your personal circumstances, market conditions and timing."
    )
    slide_5 = f"Slide 5: {guidance}"
    return "\n".join([slide_1, slide_2, slide_3, slide_4, slide_5])


def _fallback_posts(format_type: str, category: str, guideline: str, num_posts: int) -> list:
    """Return compliant, safe default copy when no AI provider is available."""
    posts = []
    for index in range(max(num_posts, 1)):
        if format_type == "Carousel":
            posts.append(_format_fallback_carousel(category, guideline, index))
        else:
            posts.append(_format_fallback_post(category, guideline, index))
    return posts


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


def _call_ollama(prompt: str) -> str:
    model_name = _resolve_ollama_model()
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 1.1, "top_p": 0.95},
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("response", "")


def _generate_with_ollama(format_type: str, category: str, guideline: str, num_posts: int) -> list:
    reference_text = get_sections_reference_text()
    if not reference_text:
        reference_text = (
            "(No cached FCA reference text yet — run 'Run compliance check now' on the "
            "Compliance Monitor tab at least once before generating copy.)"
        )

    category_note = CATEGORY_NOTES.get(category, "")
    prompt = _build_system_prompt(
        format_type=format_type,
        category=category,
        category_note=category_note,
        reference_text=reference_text,
        guideline=guideline,
        num_posts=num_posts,
    )
    variation_token = uuid.uuid4().hex[:8]
    raw = _call_ollama(
        prompt + "\n\n" + _build_user_prompt(
            format_type,
            category,
            num_posts,
            variation_token=variation_token,
            angle_seed=int(variation_token[:2], 16),
        )
    )
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        posts = json.loads(cleaned)
        if not isinstance(posts, list) or not posts:
            raise ValueError("Response was not a non-empty JSON array")
    except Exception:
        posts = [cleaned or raw]
    posts = _dedupe_posts(posts)
    return posts[: max(num_posts, 1)]


def _generate_with_gemini(format_type: str, category: str, guideline: str, num_posts: int) -> list:
    if genai is None or types is None:
        raise RuntimeError("Google GenAI SDK is not installed.")

    client = genai.Client(api_key=GEMINI_API_KEY)
    reference_text = get_sections_reference_text()
    if not reference_text:
        reference_text = (
            "(No cached FCA reference text yet — run 'Run compliance check now' on the "
            "Compliance Monitor tab at least once before generating copy.)"
        )

    category_note = CATEGORY_NOTES.get(category, "")
    system_prompt = _build_system_prompt(
        format_type=format_type,
        category=category,
        category_note=category_note,
        reference_text=reference_text,
        guideline=guideline,
        num_posts=num_posts,
    )

    variation_token = uuid.uuid4().hex[:8]
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_build_user_prompt(
            format_type,
            category,
            num_posts,
            variation_token=variation_token,
            angle_seed=int(variation_token[:2], 16),
        ),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=1400,
            temperature=0.9,
            top_p=0.9,
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

    raw = raw.strip()
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        posts = json.loads(cleaned)
        if not isinstance(posts, list) or not posts:
            raise ValueError("Response was not a non-empty JSON array")
    except Exception:
        posts = [raw]

    posts = _dedupe_posts(posts)
    return posts[: max(num_posts, 1)]


def _retry_unique_posts(generator_func, format_type: str, category: str, guideline: str, num_posts: int, max_attempts: int = 2):
    """Keep retrying only briefly with fresh angles, to reduce latency while still enforcing uniqueness."""
    collected: list[str] = []
    seen: set[str] = set()
    for attempt in range(max_attempts):
        candidate_posts = generator_func(format_type, category, guideline, num_posts)
        for post in candidate_posts:
            if not isinstance(post, str):
                continue
            key = " ".join(post.lower().split())
            if key in seen:
                continue
            seen.add(key)
            collected.append(post)
        if len(collected) >= max(num_posts, 1):
            return collected[: max(num_posts, 1)]
    return collected[: max(num_posts, 1)]


def generate_posts(format_type: str, category: str, guideline: str, num_posts: int) -> list:
    target_count = max(num_posts, 1)

    if AI_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing. Add it in Streamlit Cloud > Settings > Secrets.")
        try:
            return _retry_unique_posts(_generate_with_gemini, format_type, category, guideline, target_count)
        except Exception as exc:
            raise RuntimeError(f"Gemini generation failed: {exc}") from exc

    if AI_PROVIDER == "ollama":
        try:
            return _retry_unique_posts(_generate_with_ollama, format_type, category, guideline, target_count)
        except Exception as exc:
            raise RuntimeError(f"Ollama generation failed: {exc}") from exc

    if GEMINI_API_KEY:
        try:
            return _retry_unique_posts(_generate_with_gemini, format_type, category, guideline, target_count)
        except Exception as exc:
            raise RuntimeError(f"Gemini generation failed: {exc}") from exc

    raise RuntimeError("No AI provider is configured. Add GEMINI_API_KEY or set AI_PROVIDER to ollama.")
