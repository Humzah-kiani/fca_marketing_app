"""
Optimized FCA-aware copy generator (Gemini / Ollama). Same public API:
    generate_posts(format_type, category, guideline, num_posts) -> list[str]
Optional: call warm_up() once at app start to remove first-request latency.

Speed work
  * Gemini thinking disabled + JSON mode (no truncated/garbled output -> no fallbacks).
  * Realistic token budgets (old budget could cut JSON mid-way).
  * Over-long copy is trimmed locally instead of discarded.
  * Multi-post requests run as parallel Gemini calls.
  * Cached Gemini client, FCA reference text and Ollama model lookup; request timeouts.
  * Only 2 models tried, and only for transient errors.
  * Shorter prompt (fewer input tokens).
"""
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import requests

from config import (
    AI_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)
from fca_monitor import get_sections_reference_text
from gemini_api import generate_interaction

# --------------------------------------------------------------------------- #
# Knobs
# --------------------------------------------------------------------------- #
PARALLEL = True               # split multi-post requests into parallel Gemini calls
MAX_PARALLEL = 4
MAX_MODEL_TRIES = 2
OLLAMA_TIMEOUT_S = 90
REFERENCE_CHARS = 600

POST_MAX_WORDS = 65
POST_HEADLINE_MAX_WORDS = 10
CAROUSEL_SLIDES = 5
CAROUSEL_SLIDE_MAX_WORDS = 18
CAROUSEL_MAX_WORDS = 90

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

SYSTEM_PROMPT_TEMPLATE = """You are a UK financial promotions copywriter writing SHORT, scroll-stopping social content for a regulated adviser brand. Format: {format_type}. Category: "{category}". Punchy, modern, premium — never a paragraph or legal notice.

BREVITY IS RULE #1:
{format_rules}

Style: strong hook (bold question, surprising truth or relatable moment); short sentences, plain words, one idea per line; specific to {category}; at most one emoji per asset (or none); no hashtags unless the guideline asks; end with a soft CTA.

Compliance: never promise or imply guaranteed returns/outcomes; balance benefits with ONE short risk line (max 15 words); no urgency or hard-selling; no personal advice - invite the reader to speak with a regulated adviser. Category note: {category_note}

Variety: every item must use a different hook, angle and structure.

Reference (background only, never quote): {reference_text}
Adviser guideline: {guideline}

Output: ONLY a JSON array of exactly {num_posts} strings. No markdown or commentary. Use "\\n" for line breaks inside strings."""

POST_RULES = f"""- Each post MAX {POST_MAX_WORDS} words, each part on its own line separated by a blank line:
  1) Headline hook (max {POST_HEADLINE_MAX_WORDS} words)
  2) 2-3 punchy value lines (max 15 words each)
  3) One short risk line (max 15 words)
  4) One short CTA line (max 12 words)
- Example (do NOT copy): "Retiring soon? Your pension has a plan too.\\n\\nKnow your options.\\nCompare your income choices.\\n\\nPension values can fall as well as rise.\\n\\nSpeak to a regulated adviser." """

CAROUSEL_RULES = f"""- EXACTLY {CAROUSEL_SLIDES} slides, MAX {CAROUSEL_SLIDE_MAX_WORDS} words each, one per line as "Slide N: <text>".
- 1 = bold hook; 2-3 = one practical idea each; 4 = short risk reminder; 5 = soft CTA to speak with a regulated adviser.
- Example (do NOT copy): "Slide 1: Thinking about your pension?\\nSlide 2: Know what you want your money to do.\\nSlide 3: Compare options before you commit.\\nSlide 4: Values can fall as well as rise.\\nSlide 5: Speak to a regulated adviser." """


# --------------------------------------------------------------------------- #
# Cached helpers
# --------------------------------------------------------------------------- #
_REF = {"t": 0.0, "v": ""}


def _reference_text() -> str:
    now = time.monotonic()
    if not _REF["v"] or now - _REF["t"] > 600:
        _REF["v"], _REF["t"] = (get_sections_reference_text() or ""), now
    return _REF["v"] or ("(No cached FCA reference text yet - run 'Run compliance check now' "
                         "on the Compliance Monitor tab once.)")


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
def _build_system_prompt(format_type, category, category_note, reference_text, guideline, num_posts) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        format_type=format_type,
        category=category,
        category_note=category_note or "(none)",
        format_rules=CAROUSEL_RULES if format_type == "Carousel" else POST_RULES,
        reference_text=(reference_text or "(none)")[:REFERENCE_CHARS],
        guideline=(guideline or "(none)")[:300],
        num_posts=num_posts,
    )


_ANGLES = [
    "a smart question that challenges an assumption",
    "a common mistake and how to avoid it",
    "a quick checklist of things to consider",
    "a relatable life moment that triggers this decision",
    "a myth vs reality",
    "benefit balanced with realistic risk",
]


def _build_user_prompt(format_type, category, num_posts, variation_token=None, angle_seed=None) -> str:
    variation_token = variation_token or uuid.uuid4().hex[:8]
    angle_seed = angle_seed if angle_seed is not None else int(variation_token[:2], 16)
    angles = "; ".join(f"{i + 1}) {_ANGLES[(angle_seed + i) % len(_ANGLES)]}" for i in range(min(num_posts, len(_ANGLES))))
    limit = (f"exactly {CAROUSEL_SLIDES} slides, max {CAROUSEL_SLIDE_MAX_WORDS} words each"
             if format_type == "Carousel" else f"max {POST_MAX_WORDS} words each")
    return (f"Create {num_posts} distinct SHORT {format_type.lower()} assets for '{category}' ({limit}). "
            f"Seed {variation_token}. Angles in order: {angles}. Punchy, scannable, no repeated hooks.")


# --------------------------------------------------------------------------- #
# Parsing + local trimming
# --------------------------------------------------------------------------- #
def _normalise_item(item) -> str:
    if isinstance(item, str):
        text = item
    elif isinstance(item, list):
        text = "\n".join(str(p) for p in item)
    elif isinstance(item, dict):
        text = "\n".join(str(v) for v in item.values())
    else:
        return ""
    text = text.replace("\\n", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_posts(raw: str) -> list[str]:
    raw = (raw or "").strip()
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    candidates = [cleaned]
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, dict):  # JSON mode sometimes wraps the array
            data = next((v for v in data.values() if isinstance(v, list)), None)
        if isinstance(data, list) and data:
            items = [_normalise_item(i) for i in data]
            return [i for i in items if i]
    single = _normalise_item(raw)
    return [single] if single else []


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’%£$-]+\b", text))


def _cut(text: str, n: int) -> str:
    words = text.split()
    return text if len(words) <= n else " ".join(words[:n]).rstrip(",;:- ")


def _coerce(post, format_type: str):
    """Trim an asset to the short-form limits instead of discarding it. None only if unusable."""
    if not isinstance(post, str) or not post.strip():
        return None
    lines = [l.strip() for l in post.replace("\r", "").split("\n") if l.strip()]

    if format_type == "Carousel":
        bodies = [re.sub(r"^\s*slide\s*\d+\s*[:.\-]\s*", "", l, flags=re.IGNORECASE) for l in lines]
        bodies = [b for b in bodies if b][:CAROUSEL_SLIDES]
        if len(bodies) < 3:
            return None
        return "\n".join(f"Slide {i}: {_cut(b, CAROUSEL_SLIDE_MAX_WORDS)}" for i, b in enumerate(bodies, 1))

    if len(lines) < 3:
        return None
    head = _cut(lines[0], POST_HEADLINE_MAX_WORDS)
    tail, mid = lines[-2:], lines[1:-2]
    budget = POST_MAX_WORDS - _word_count(head) - sum(_word_count(t) for t in tail)
    keep: list[str] = []
    for m in mid:
        wc = _word_count(m)
        if wc > budget:
            break
        keep.append(m)
        budget -= wc
    if mid and not keep:
        keep = [_cut(mid[0], 8)]
    return "\n\n".join(p for p in (head, "\n".join(keep), tail[0], tail[1]) if p)


def _dedupe_posts(posts: list[str]) -> list[str]:
    out, seen = [], set()
    for p in posts:
        if not isinstance(p, str):
            continue
        key = " ".join(p.lower().split())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _max_tokens_for(format_type: str, num_posts: int) -> int:
    """Enough room for the JSON (the old 75/post budget could truncate output)."""
    per_item = 170 if format_type == "Carousel" else 120
    return min(2000, max(384, num_posts * per_item + 150))


# --------------------------------------------------------------------------- #
# Fallback copy (instant, local)
# --------------------------------------------------------------------------- #
def _cta_line(guideline: str) -> str:
    g = (guideline or "").strip()
    return g if g and _word_count(g) <= 14 else "Speak to a regulated adviser."


def _format_fallback_post(category: str, guideline: str, index: int) -> str:
    cat = category.lower()
    hooks = [f"Thinking about {cat}? Start with the right questions.", f"{category} choices are personal. Your plan should be too.",
             f"Before your next {cat} decision, pause.", f"Not sure where to begin with {cat}?",
             f"{category}: clarity first, decisions second."]
    bodies = ["Know your goals.\nUnderstand the trade-offs.\nPlan with your timeline in mind.",
              "Compare your options.\nCheck the risks as well as the benefits.\nChoose what fits your life.",
              "Ask what it does.\nAsk what it costs.\nAsk what could go wrong.",
              "Your goals matter.\nYour timescale matters.\nSo does your comfort with risk.",
              "Look at the bigger picture.\nTake time to weigh it up.\nGet the facts before you act."]
    risks = ["Values can go down as well as up.", "Outcomes aren't guaranteed and depend on your circumstances.",
             "Tax treatment depends on individual circumstances."]
    return (f"{hooks[index % len(hooks)]}\n\n{bodies[index % len(bodies)]}\n\n"
            f"{risks[index % len(risks)]}\n\n{_cta_line(guideline)}")


def _format_fallback_carousel(category: str, guideline: str, index: int) -> str:
    cat = category.lower()
    hooks = [f"Thinking about {cat}?", f"{category}: are you asking the right questions?",
             f"3 things to check before your {cat} decision", f"Common {cat} mistakes to avoid", f"{category} made simpler"]
    mids = [("Know what you want your money to do.", "Compare options before you commit."),
            ("Look at your timescale, not just today.", "Weigh benefits against the risks."),
            ("Ask what it costs and how it works.", "Ask what could go wrong."),
            ("Rushing a decision can limit your choices.", "Take time to understand the trade-offs."),
            ("Start with your goals.", "Build the plan around your life.")]
    s2, s3 = mids[index % len(mids)]
    return "\n".join([f"Slide 1: {hooks[index % len(hooks)]}", f"Slide 2: {s2}", f"Slide 3: {s3}",
                      "Slide 4: Values can go down as well as up. Nothing is guaranteed.", f"Slide 5: {_cta_line(guideline)}"])


def _fallback_posts(format_type, category, guideline, num_posts, start_index=0) -> list:
    fn = _format_fallback_carousel if format_type == "Carousel" else _format_fallback_post
    return [fn(category, guideline, i) for i in range(start_index, start_index + max(num_posts, 1))]


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _resolve_ollama_model() -> str:
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Ollama is not reachable. Start it with 'ollama serve' and make sure "
                           f"the server is running at {OLLAMA_BASE_URL}.") from exc
    available = [m["name"] for m in payload.get("models", []) if isinstance(m, dict) and m.get("name")]
    if not available:
        raise RuntimeError(f"No Ollama models were found locally. Pull one first: 'ollama pull {OLLAMA_MODEL}'")
    wanted = OLLAMA_MODEL.lower()
    for name in available:
        if name.lower() == wanted or name.lower().startswith(f"{wanted}:"):
            return name
    raise RuntimeError(f"The configured Ollama model '{OLLAMA_MODEL}' is not installed. "
                       f"Installed models: {', '.join(available)}. Pull it with 'ollama pull {OLLAMA_MODEL}'")


def _call_ollama(prompt: str, max_tokens: int) -> str:
    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={"model": _resolve_ollama_model(), "prompt": prompt, "stream": False, "keep_alive": "30m",
              "options": {"temperature": 0.75, "top_p": 0.9, "num_predict": max_tokens, "num_ctx": 2048}},
        timeout=OLLAMA_TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json().get("response", "")


def _generate_with_ollama(format_type, category, guideline, num_posts) -> list:
    system = _build_system_prompt(format_type, category, CATEGORY_NOTES.get(category, ""), _reference_text(), guideline, num_posts)
    token = uuid.uuid4().hex[:8]
    raw = _call_ollama(system + "\n\n" + _build_user_prompt(format_type, category, num_posts, token, int(token[:2], 16)),
                       _max_tokens_for(format_type, num_posts))
    return _dedupe_posts(_parse_posts(raw))[: max(num_posts, 1)]


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #
def _gemini_model_candidates() -> list[str]:
    seen, out = set(), []
    for name in [GEMINI_MODEL] + list(GEMINI_FALLBACK_MODELS):
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _is_transient_gemini_error(exc: Exception) -> bool:
    """Only overload / rate-limit / timeout / missing-model errors justify another model."""
    msg = str(exc).lower()
    if "api_key_invalid" in msg or "api key not valid" in msg:
        return False
    return any(t in msg for t in ("503", "504", "500", "429", "404", "unavailable", "overloaded", "rate limit", "timed out",
                                  "timeout", "deadline", "resource exhausted", "internal error", "not found",
                                  "no longer available"))


def _gemini_text(model: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    return generate_interaction(model, system_prompt, user_prompt, max_tokens)


def _gemini_call(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    last = None
    for model in _gemini_model_candidates()[:MAX_MODEL_TRIES]:
        try:
            return _gemini_text(model, system_prompt, user_prompt, max_tokens)
        except Exception as exc:
            last = exc
            if not _is_transient_gemini_error(exc):
                raise
    raise last or RuntimeError("Gemini generation failed with no usable model candidates.")


def _split(n: int, workers: int) -> list[int]:
    k = max(1, min(n, workers))
    base, extra = divmod(n, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


def _generate_with_gemini(format_type, category, guideline, num_posts) -> list:
    num_posts = max(num_posts, 1)
    system = _build_system_prompt(format_type, category, CATEGORY_NOTES.get(category, ""), _reference_text(), guideline, num_posts)
    sizes = _split(num_posts, MAX_PARALLEL) if PARALLEL and num_posts > 1 else [num_posts]
    base_angle = int(uuid.uuid4().hex[:2], 16)
    offsets = [sum(sizes[:i]) for i in range(len(sizes))]

    def job(size: int, offset: int) -> list[str]:
        token = uuid.uuid4().hex[:8]
        sys_prompt = system.replace(f"exactly {num_posts} strings", f"exactly {size} strings")
        raw = _gemini_call(
            sys_prompt,
            _build_user_prompt(format_type, category, size, token, base_angle + offset),
            _max_tokens_for(format_type, size),
        )
        posts = _parse_posts(raw)
        if len(posts) != size:
            raise RuntimeError(f"Gemini returned {len(posts)} of {size} requested posts.")
        return posts

    if len(sizes) == 1:
        posts = _dedupe_posts(job(sizes[0], 0))
        if len(posts) != num_posts:
            raise RuntimeError("Gemini returned duplicate posts instead of the requested number of distinct posts.")
        return posts

    posts, errors = [], []
    with ThreadPoolExecutor(max_workers=len(sizes)) as pool:
        futures = [pool.submit(job, s, o) for s, o in zip(sizes, offsets)]
        for fut in futures:
            try:
                posts += fut.result()
            except Exception as exc:
                errors.append(exc)
    if errors:
        raise errors[0]
    unique_posts = _dedupe_posts(posts)
    if len(unique_posts) != num_posts:
        raise RuntimeError("Gemini returned duplicate posts instead of the requested number of distinct posts.")
    return unique_posts


# --------------------------------------------------------------------------- #
# Collect generated output and optionally top up local-provider results.
# --------------------------------------------------------------------------- #
def _retry_unique_posts(
    generator_func, format_type, category, guideline, num_posts, allow_local_top_up=True
):
    target = max(num_posts, 1)
    collected: list[str] = []
    seen: set[str] = set()

    for post in generator_func(format_type, category, guideline, target):
        fixed = _coerce(post, format_type)
        if not fixed:
            continue
        key = " ".join(fixed.lower().split())
        if key not in seen:
            seen.add(key)
            collected.append(fixed)

    if len(collected) < target and allow_local_top_up:
        for filler in _fallback_posts(format_type, category, guideline, target + 5, start_index=uuid.uuid4().int % 5):
            key = " ".join(filler.lower().split())
            if key not in seen:
                seen.add(key)
                collected.append(filler)
                if len(collected) >= target:
                    break
    elif len(collected) < target:
        raise RuntimeError(f"AI returned {len(collected)} of {target} requested posts.")
    return collected[:target]


def _run_gemini(format_type, category, guideline, target):
    try:
        return _retry_unique_posts(
            _generate_with_gemini,
            format_type,
            category,
            guideline,
            target,
            allow_local_top_up=False,
        )
    except Exception as exc:
        raise RuntimeError(f"Gemini generation failed: {exc}") from exc


def generate_posts(format_type: str, category: str, guideline: str, num_posts: int) -> list:
    target = max(num_posts, 1)

    if AI_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing. Add it in Streamlit Cloud > Settings > Secrets.")
        return _run_gemini(format_type, category, guideline, target)

    if AI_PROVIDER == "ollama":
        try:
            return _retry_unique_posts(_generate_with_ollama, format_type, category, guideline, target)
        except Exception as exc:
            raise RuntimeError(f"Ollama generation failed: {exc}") from exc

    if GEMINI_API_KEY:
        return _run_gemini(format_type, category, guideline, target)

    raise RuntimeError("No AI provider is configured. Add GEMINI_API_KEY or set AI_PROVIDER to ollama.")


def warm_up() -> None:
    """Call once at app start (e.g. inside st.cache_resource) so the first user request is fast."""
    _reference_text()
    if AI_PROVIDER == "ollama":
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            timeout=OLLAMA_TIMEOUT_S,
            json={"model": _resolve_ollama_model(), "prompt": "", "keep_alive": "30m", "stream": False},
        )
        response.raise_for_status()
    elif GEMINI_API_KEY:
        from gemini_api import warm_up as warm_up_gemini

        warm_up_gemini()