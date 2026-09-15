"""
Generates FCA-aware marketing copy using the Google Gemini API, grounded in the
currently cached FCA Handbook/guidance text (see fca_monitor.py).
"""
import json

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

SYSTEM_PROMPT_TEMPLATE = """You are a UK financial promotions compliance copywriter working for a \
UK-regulated Financial Adviser firm. You write {format_type} content for social media.

You must follow the FCA Handbook and related guidance, in particular:
- COBS 4 (Communicating with clients, including financial promotions): promotions must be \
clear, fair, not misleading, and appropriate for the target audience.
- PRIN (Principles for Businesses): due regard to the information needs of clients; \
communications must be clear, fair and not misleading.
- COBS 2.1: act honestly, fairly and professionally in accordance with the best interests of \
the client.
- FG15/4 (Social Media and Customer Communications) and FG22/5 (Consumer Duty): communications \
must support consumer understanding, remain balanced (risk stated alongside benefit), and must \
not be a disguised/unbalanced financial promotion.

Hard rules — never break these:
- Never promise, guarantee, or strongly imply a specific financial outcome or return.
- Always balance any stated benefit with an appropriate, category-relevant risk warning.
- No high-pressure or false-urgency tactics ("act now or miss out", countdown language, etc.).
- Do not give personalised financial advice in the text itself — invite the reader to seek \
regulated advice tailored to their circumstances.
- Use plain English; avoid unexplained jargon.
- Never imply FCA endorsement of the firm or its products.
- Category-specific note: {category_note}

Reference material — current cached extracts from the FCA Handbook/guidance pages being \
tracked by this app (may be partial; treat as the latest known wording):
{reference_text}

Additional adviser guideline for this batch (may be empty): {guideline}

Output requirements:
- Write content that is ready to publish as a real social media asset.
- Respond ONLY with a JSON array of exactly {num_posts} string(s).
- Each string must be one complete, final {format_type} asset for the "{category}" category.
- Do not add headings or commentary outside the JSON array.

For a Post asset:
- Write a polished social post with a clear hook, short body copy, clear benefit/risk balance, and a compliant closing CTA.
- Keep it natural for social media, ideally 80-150 words, written as one finished post.
- Include a plain-English explanation of the product/issue, a relevant risk statement, and a soft CTA such as: "If you'd like to discuss your circumstances, we can talk through the options."
- Avoid any claim that the reader must act immediately or that a result is guaranteed.

For a Carousel asset:
- Write a full carousel deck as a single string with numbered slides, e.g. 'Slide 1: ...\nSlide 2: ...\nSlide 3: ...'
- Use 4-6 slides with a clear topic, informative body copy, and a final slide that gives the relevant risk statement and a compliant CTA.
- Keep each slide concise and readable, with no sales pressure or urgent wording.
- Each slide should read like a real social carousel, not a paragraph dumped into slide labels.

No markdown fences, no preamble, no extra notes outside the JSON array.
"""


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
            "options": {"temperature": 0.4},
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
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        format_type=format_type,
        category=category,
        category_note=category_note,
        reference_text=reference_text[:12000],
        guideline=guideline or "(none provided)",
        num_posts=num_posts,
    )
    raw = _call_ollama(prompt)
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
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        format_type=format_type,
        category=category,
        category_note=category_note,
        reference_text=reference_text[:12000],
        guideline=guideline or "(none provided)",
        num_posts=num_posts,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            f"Generate {num_posts} compliant {format_type} text(s) for the "
            f"'{category}' category now."
        ),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=3000,
            temperature=0.4,
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

    return posts[: max(num_posts, 1)]


def generate_posts(format_type: str, category: str, guideline: str, num_posts: int) -> list:
    try:
        if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
            return _generate_with_gemini(format_type, category, guideline, num_posts)
        if AI_PROVIDER == "ollama":
            return _generate_with_ollama(format_type, category, guideline, num_posts)
        if GEMINI_API_KEY:
            return _generate_with_gemini(format_type, category, guideline, num_posts)
    except Exception:
        pass

    return _fallback_posts(format_type, category, guideline, num_posts)
