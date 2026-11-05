"""
Generates SHORT, punchy, FCA-aware marketing copy using the Google Gemini API
(or Ollama), grounded in the currently cached FCA Handbook/guidance text
(see fca_monitor.py).

Output style:
  * Post     -> hook headline + 2-3 punchy lines + one-line risk note + one-line CTA
  * Carousel -> 5 slides, one short idea per slide (hook -> value -> value -> risk -> CTA)
"""
import json
import re
import uuid
from functools import lru_cache

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
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)
from fca_monitor import get_sections_reference_text

# --------------------------------------------------------------------------- #
# Length limits (tweak these to make copy shorter / longer)
# --------------------------------------------------------------------------- #
POST_MAX_WORDS = 65             # whole post, including risk line + CTA
POST_HEADLINE_MAX_WORDS = 10
CAROUSEL_SLIDES = 5
CAROUSEL_SLIDE_MAX_WORDS = 18   # per slide (after the "Slide N:" label)
CAROUSEL_MAX_WORDS = 90         # whole carousel

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

SYSTEM_PROMPT_TEMPLATE = """You are a UK financial promotions copywriter creating SHORT, scroll-stopping social media content for a regulated financial adviser brand.

Write {format_type} content for the "{category}" category. Think punchy, modern and premium — like a modern financial adviser social brand on Instagram/LinkedIn, NOT a paragraph, essay or legal notice.

BREVITY IS THE #1 RULE:
{format_rules}

Style rules:
- Open with a strong hook: a bold question, a surprising truth, or a relatable moment.
- Short sentences. Plain words. One idea per line. No jargon, no filler, no waffle.
- Make it category-specific to {category} (real decisions, needs and moments), not generic finance talk.
- Emojis: none, or one at most per asset, and only if it adds meaning.
- No hashtags unless the adviser guideline asks for them.
- Every asset must end with a soft, professional CTA (e.g. "Talk to a regulated adviser.").

Compliance rules (keep them, but say them briefly):
- Never promise or imply guaranteed returns, outcomes or benefits.
- Balance any benefit with ONE short risk/suitability line (max 15 words).
- No urgency pressure, fake scarcity or hard-selling.
- No personal advice; invite the reader to speak with a regulated adviser.
- Category note: {category_note}

Variety rules:
- Every item in the array must be distinct and use a different hook, angle and structure.
- Never reuse the same hook or opening words across items.

Reference material (background only — never quote it or copy its wording):
{reference_text}

Additional adviser guideline: {guideline}

Output format:
- Return ONLY a JSON array of exactly {num_posts} strings.
- No markdown, no code fences, no commentary outside the array.
- Use "\\n" for line breaks inside each string.
- Each string is one complete {format_type} for the "{category}" category.
"""

POST_RULES = f"""- Each post is MAX {POST_MAX_WORDS} words in total.
- Structure (each part on its own line, separated by a blank line):
  1) Headline hook (max {POST_HEADLINE_MAX_WORDS} words)
  2) 2-3 punchy lines of value (max 15 words per line)
  3) One short risk line (max 15 words)
  4) One short CTA line (max 12 words)
- No paragraphs. No block of text longer than one sentence.
- Style example (do NOT copy it):
  "Retiring soon? Your pension has a plan too.\\n\\nKnow your options.\\nCompare your income choices.\\nPlan around your life, not a number.\\n\\nPension values can fall as well as rise.\\n\\nSpeak to a regulated adviser." """

CAROUSEL_RULES = f"""- Each carousel has EXACTLY {CAROUSEL_SLIDES} slides, MAX {CAROUSEL_SLIDE_MAX_WORDS} words per slide, {CAROUSEL_MAX_WORDS} words in total.
- Format each slide on its own line as "Slide N: <text>".
- Slide 1 = bold hook (a question or striking statement).
- Slides 2-3 = one clear, practical idea each (a tip, a question to ask, a common mistake).
- Slide 4 = short risk/suitability reminder.
- Slide 5 = soft CTA to speak with a regulated adviser.
- One idea per slide. No paragraphs.
- Style example (do NOT copy it):
  "Slide 1: Thinking about your pension?\\nSlide 2: Know what you want your money to do.\\nSlide 3: Compare options before you commit.\\nSlide 4: Values can fall as well as rise.\\nSlide 5: Speak to a regulated adviser." """


# --------------------------------------------------------------------------- #
# Prompt builders
# --------------------------------------------------------------------------- #
def _build_system_prompt(format_type: str, category: str, category_note: str, reference_text: str, guideline: str, num_posts: int) -> str:
    """Build a compact, category-specific prompt that enforces short-form output."""
    format_rules = CAROUSEL_RULES if format_type == "Carousel" else POST_RULES
    return SYSTEM_PROMPT_TEMPLATE.format(
        format_type=format_type,
        category=category,
        category_note=category_note or "(none)",
        format_rules=format_rules,
        reference_text=(reference_text or "(No additional FCA text available)")[:1500],
        guideline=(guideline or "(none provided)")[:300],
        num_posts=num_posts,
    )


def _build_user_prompt(
    format_type: str,
    category: str,
    num_posts: int,
    variation_token: str | None = None,
    angle_seed: int | None = None,
) -> str:
    """Add a generation-specific seed so every retry uses a fresh angle."""
    variation_token = variation_token or uuid.uuid4().hex[:8]
    angle_seed = angle_seed if angle_seed is not None else int(variation_token[:2], 16)
    angle_options = [
        "a smart question that challenges an assumption",
        "a common mistake and how to avoid it",
        "a quick checklist of things to consider",
        "a relatable life moment that triggers this decision",
        "a myth vs reality",
        "benefit balanced with realistic risk",
    ]
    angles = [
        angle_options[(angle_seed + offset) % len(angle_options)]
        for offset in range(min(num_posts, len(angle_options)))
    ]
    serialised_angles = "; ".join(f"{i + 1}) {angle}" for i, angle in enumerate(angles))

    if format_type == "Carousel":
        limit = f"exactly {CAROUSEL_SLIDES} slides, max {CAROUSEL_SLIDE_MAX_WORDS} words each"
    else:
        limit = f"max {POST_MAX_WORDS} words each"

    return (
        f"Create {num_posts} distinct, SHORT {format_type.lower()} assets for the '{category}' category ({limit}). "
        f"Generation attempt {variation_token}. Use these angles in order: {serialised_angles}. "
        f"Keep it punchy and scannable — no paragraphs. Each must be tailored to {category} "
        f"and must not reuse the same hook, wording or structure as another."
    )


# --------------------------------------------------------------------------- #
# Parsing / cleaning / validation
# --------------------------------------------------------------------------- #
def _normalise_item(item) -> str:
    """Turn whatever the model returned for one asset into a clean string."""
    if isinstance(item, str):
        text = item
    elif isinstance(item, list):
        text = "\n".join(str(part) for part in item)
    elif isinstance(item, dict):
        text = "\n".join(str(value) for value in item.values())
    else:
        return ""
    text = text.replace("\\n", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_posts(raw: str) -> list[str]:
    """Extract a JSON array of assets from raw model output (tolerates fences / chatter)."""
    raw = (raw or "").strip()
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    candidates = [cleaned]
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, list) and data:
            items = [_normalise_item(item) for item in data]
            return [item for item in items if item]

    single = _normalise_item(raw)
    return [single] if single else []


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’%£$-]+\b", text))


def _is_concise(post: str, format_type: str) -> bool:
    """True when the asset respects the short-form limits."""
    if not isinstance(post, str) or not post.strip():
        return False

    if format_type == "Carousel":
        slides = [line.strip() for line in post.splitlines() if line.strip()]
        if not 3 <= len(slides) <= CAROUSEL_SLIDES + 1:
            return False
        total = 0
        for slide in slides:
            body = re.sub(r"^\s*slide\s*\d+\s*[:.\-]\s*", "", slide, flags=re.IGNORECASE)
            words = _word_count(body)
            if words > CAROUSEL_SLIDE_MAX_WORDS + 4:  # small tolerance
                return False
            total += words
        return total <= CAROUSEL_MAX_WORDS + 15

    first_line = next((line for line in post.splitlines() if line.strip()), "")
    if _word_count(first_line) > POST_HEADLINE_MAX_WORDS + 4:
        return False
    return _word_count(post) <= POST_MAX_WORDS + 10


def _dedupe_posts(posts: list[str]) -> list[str]:
    """Remove repeated text while preserving the first unique instance."""
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


def _max_tokens_for(format_type: str, num_posts: int) -> int:
    """Keep the output budget close to the amount of copy actually required."""
    per_item = 115 if format_type == "Carousel" else 75
    return min(1200, max(256, num_posts * per_item + 100))


# --------------------------------------------------------------------------- #
# Fallback copy (short, compliant, used only if the AI can't produce enough)
# --------------------------------------------------------------------------- #
def _cta_line(guideline: str) -> str:
    g = (guideline or "").strip()
    if g and _word_count(g) <= 14:
        return g
    return "Speak to a regulated adviser."


def _format_fallback_post(category: str, guideline: str, index: int) -> str:
    cat = category.lower()
    hooks = [
        f"Thinking about {cat}? Start with the right questions.",
        f"{category} choices are personal. Your plan should be too.",
        f"Before your next {cat} decision, pause.",
        f"Not sure where to begin with {cat}?",
        f"{category}: clarity first, decisions second.",
    ]
    bodies = [
        "Know your goals.\nUnderstand the trade-offs.\nPlan with your timeline in mind.",
        "Compare your options.\nCheck the risks as well as the benefits.\nChoose what fits your life.",
        "Ask what it does.\nAsk what it costs.\nAsk what could go wrong.",
        "Your goals matter.\nYour timescale matters.\nSo does your comfort with risk.",
        "Look at the bigger picture.\nTake time to weigh it up.\nGet the facts before you act.",
    ]
    risks = [
        "Values can go down as well as up.",
        "Outcomes aren't guaranteed and depend on your circumstances.",
        "Tax treatment depends on individual circumstances.",
    ]
    return (
        f"{hooks[index % len(hooks)]}\n\n"
        f"{bodies[index % len(bodies)]}\n\n"
        f"{risks[index % len(risks)]}\n\n"
        f"{_cta_line(guideline)}"
    )


def _format_fallback_carousel(category: str, guideline: str, index: int) -> str:
    cat = category.lower()
    hooks = [
        f"Thinking about {cat}?",
        f"{category}: are you asking the right questions?",
        f"3 things to check before your {cat} decision",
        f"Common {cat} mistakes to avoid",
        f"{category} made simpler",
    ]
    mids = [
        ("Know what you want your money to do.", "Compare options before you commit."),
        ("Look at your timescale, not just today.", "Weigh benefits against the risks."),
        ("Ask what it costs and how it works.", "Ask what could go wrong."),
        ("Rushing a decision can limit your choices.", "Take time to understand the trade-offs."),
        ("Start with your goals.", "Build the plan around your life."),
    ]
    risk = "Values can go down as well as up. Nothing is guaranteed."
    slide_2, slide_3 = mids[index % len(mids)]
    return "\n".join([
        f"Slide 1: {hooks[index % len(hooks)]}",
        f"Slide 2: {slide_2}",
        f"Slide 3: {slide_3}",
        f"Slide 4: {risk}",
        f"Slide 5: {_cta_line(guideline)}",
    ])


def _fallback_posts(format_type: str, category: str, guideline: str, num_posts: int, start_index: int = 0) -> list:
    """Return compliant, short default copy."""
    posts = []
    for index in range(start_index, start_index + max(num_posts, 1)):
        if format_type == "Carousel":
            posts.append(_format_fallback_carousel(category, guideline, index))
        else:
            posts.append(_format_fallback_post(category, guideline, index))
    return posts


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
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


def _call_ollama(prompt: str, max_tokens: int) -> str:
    model_name = _resolve_ollama_model()
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.75, "top_p": 0.9, "num_predict": max_tokens},
        },
        timeout=90,
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
        ),
        max_tokens=_max_tokens_for(format_type, num_posts),
    )
    posts = _dedupe_posts(_parse_posts(raw))
    return posts[: max(num_posts, 1)]


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #
def _gemini_model_candidates() -> list[str]:
    """Return configured model candidates without an extra API discovery request."""
    candidates: list[str] = []
    seen: set[str] = set()
    configured = [GEMINI_MODEL] + list(GEMINI_FALLBACK_MODELS)
    for model_name in configured:
        model_name = (model_name or "").strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        candidates.append(model_name)

    return candidates


def _is_transient_gemini_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "api_key_invalid" in message or "api key not valid" in message:
        return False
    return any(
        token in message
        for token in [
            "503",
            "503 unavailable",
            "500",
            "internal error",
            "unavailable",
            "rate limit",
            "429",
            "404",
            "400",
            "invalid_argument",
            "response modalities",
            "not found",
            "no longer available",
            "resource exhausted",
            "overloaded",
            "temporarily unavailable",
        ]
    )


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

    last_error: Exception | None = None
    for model_name in _gemini_model_candidates():
        try:
            variation_token = uuid.uuid4().hex[:8]
            response = client.models.generate_content(
                model=model_name,
                contents=_build_user_prompt(
                    format_type,
                    category,
                    num_posts,
                    variation_token=variation_token,
                    angle_seed=int(variation_token[:2], 16),
                ),
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=_max_tokens_for(format_type, num_posts),
                    temperature=0.9,
                    top_p=0.9,
                    response_mime_type="application/json",
                ),
            )

            raw = getattr(response, "text", "")
            if not raw and hasattr(response, "candidates"):
                parts = []
                for candidate in response.candidates or []:
                    content = getattr(candidate, "content", None)
                    for part in getattr(content, "parts", []) or []:
                        text = getattr(part, "text", "")
                        if text:
                            parts.append(text)
                raw = "".join(parts)

            posts = _dedupe_posts(_parse_posts(raw or ""))
            return posts[: max(num_posts, 1)]
        except Exception as exc:  # pragma: no cover - exercised by fallback tests
            last_error = exc
            if not _is_transient_gemini_error(exc):
                raise

    if last_error is not None:
        raise last_error

    raise RuntimeError("Gemini generation failed with no usable model candidates.")


# --------------------------------------------------------------------------- #
# Retry + enforcement
# --------------------------------------------------------------------------- #
def _retry_unique_posts(generator_func, format_type: str, category: str, guideline: str, num_posts: int, max_attempts: int = 1):
    """
    Make one AI request, keep valid unique assets, and fill any gaps locally.
    This avoids multiple slow model round-trips for a single user action.
    """
    target = max(num_posts, 1)
    collected: list[str] = []
    seen: set[str] = set()

    for _ in range(max_attempts):
        needed = target - len(collected)
        candidate_posts = generator_func(format_type, category, guideline, needed)
        for post in candidate_posts:
            if not isinstance(post, str):
                continue
            key = " ".join(post.lower().split())
            if key in seen:
                continue
            if not _is_concise(post, format_type):
                continue
            seen.add(key)
            collected.append(post)
        if len(collected) >= target:
            return collected[:target]

    if len(collected) < target:
        needed = target - len(collected)
        for filler in _fallback_posts(format_type, category, guideline, needed + 5, start_index=uuid.uuid4().int % 5):
            key = " ".join(filler.lower().split())
            if key in seen:
                continue
            seen.add(key)
            collected.append(filler)
            if len(collected) >= target:
                break

    return collected[:target]


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