"""
post_studio_ai.py  (v4 - AI content, AI art direction & multi-post gallery)
==========================================================================
Integrated design UI for the FCA marketing app.   Run with:   streamlit run app.py

What this module does on top of post_studio.py
----------------------------------------------
AI content
  * Claude writes the copy automatically: N distinct single posts (each a different angle) or one carousel of N slides,
    plus caption and hashtags. Without an API key a built-in offline writer is used, so the app never breaks.
  * Content approved in the app's Generate tab is still loaded automatically and kept word-for-word
    (it can contain several posts: "Post 1:", "Post 2:" ... or a `generated_posts` list in session state).

AI design
  * Claude acts as art director: for every post it chooses a layout, palette, font pairing, art style, icon and an image
    prompt - and every post in a batch gets a clearly different design.
  * Backgrounds: OpenAI images (API key), Pollinations FLUX (free, online), offline generative art (free, instant)
    or flat colours. If an online image fails, offline art is used for that slide automatically.
  * New editorial composition engine: hero, split, card, frame, centre and editorial layouts; text is sized to fill its
    area (not shrunk into a corner), contrast is measured on the real pixels, story safe zones are respected,
    and the logo / brand mark is placed where it does not touch content.

Studio
  * "Single posts" shows every generated post in a gallery, each with its own unique design and its own text.
  * Carousel slides (strip, page dots, swipe arrow) are only shown when "Carousel" is selected.
  * Click-to-edit canvas, quick editors, guidelines check, new look per post / slide, ZIP of all posts with captions.

API keys are read from st.secrets or environment variables (ANTHROPIC_API_KEY, OPENAI_API_KEY) or typed in the
"AI engine" panel. Keys are kept only in the session.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import io
import json
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

import post_studio as ps

try:
    from streamlit_image_coordinates import streamlit_image_coordinates
    HAS_CLICK = True
except ImportError:  # pragma: no cover
    streamlit_image_coordinates = None
    HAS_CLICK = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORMS = {
    "Instagram Post 4:5": ("portrait", "Best feed reach. Key text stays inside the centre 80%."),
    "Instagram Square": ("square", "Classic 1:1. Works on every platform."),
    "Instagram Story / Reel": ("story", "Text is kept out of the top 16% and bottom 24% (app UI covers them)."),
    "LinkedIn Post": ("landscape", "Professional tone, short headline, clear value."),
    "Facebook Post": ("landscape", "Large readable text; mobile first."),
    "Pinterest Pin": ("pinterest", "Tall 2:3 pins, headline-first design."),
    "X / Twitter": ("twitter", "Wide 16:9, very short headline."),
    "YouTube Thumbnail": ("youtube", "Huge text (max ~5 words) and high contrast."),
}
KINDS = ["Single posts", "Carousel"]
GOALS = ["Awareness", "Lead generation", "Sales / promotion", "Education", "Engagement", "Announcement"]
TONES = ["Friendly & confident", "Professional & trustworthy", "Bold & energetic", "Calm & reassuring",
         "Luxury & elegant", "Playful & fun", "Inspirational"]
LOGO_MODES = ["Smart (auto)", "Top left", "Top right", "Bottom left", "Bottom right", "Top center", "Bottom center"]
DECOR_LEVELS = ["None", "Subtle", "Rich"]

LAYOUTS = {
    "hero_bottom": "Hero - picture with headline at the bottom",
    "hero_top": "Hero - picture with headline at the top",
    "center": "Centred statement over the picture",
    "split": "Split - picture and text panel side by side",
    "card": "Floating card over the picture",
    "frame": "Magazine frame - inset picture, text below",
    "editorial": "Editorial - tinted picture, accent bar, big type",
}
FONT_PAIRS = {
    "Modern sans": ("display", "body"),
    "Editorial serif": ("serif", "body"),
    "Bold serif": ("elegant_serif", "body"),
    "Condensed impact": ("condensed", "body"),
    "Friendly round": ("rounded", "body"),
    "Luxury serif": ("elegant_serif", "serif"),
    "Tech": ("display", "mono"),
}
IMAGE_PROVIDERS = ["Offline generative art (free, instant)", "OpenAI images (API key)",
                   "Pollinations FLUX (free, online)", "Flat colours only"]
DEFAULT_MODEL = "claude-sonnet-5-5"

MOODS = {
    "trustworthy": ["royal", "periwinkle", "nordic_frost", "ocean_deep", "emerald"],
    "energetic": ["candy_pop", "sunset_glow", "retro_wave", "mustard"],
    "calm": ["forest_mist", "emerald", "pastel_dream", "nordic_frost"],
    "luxury": ["monochrome", "mustard", "emerald", "plum"],
    "techy": ["neon_cyber", "aurora", "retro_wave", "monochrome"],
    "playful": ["candy_pop", "pastel_dream", "sunset_glow", "plum"],
}
KEYWORDS = {
    "trustworthy": ["insurance", "protect", "pension", "mortgage", "financ", "legal", "bank", "retire", "invest", "health"],
    "energetic": ["sale", "offer", "launch", "fitness", "event", "discount", "promo", "win"],
    "calm": ["wellness", "yoga", "mind", "sleep", "nature", "organic", "eco", "relax"],
    "luxury": ["luxury", "premium", "exclusive", "wealth", "gold", "elite"],
    "techy": [" ai", "tech", "software", "app ", "crypto", "digital", "code", "data"],
    "playful": ["kids", "party", "fun", "food", "cake", "game", "birthday"],
}
MOTION_FOR_MOOD = {"trustworthy": "calm", "energetic": "energetic", "calm": "calm", "luxury": "elegant",
                   "techy": "techy", "playful": "playful"}


@dataclass
class Guidelines:
    topic: str = "5 simple ways to protect your family's financial future"
    kind: str = "Single posts"                            # Single posts | Carousel
    count: int = 3                                        # number of single posts
    slides: int = 5                                       # number of carousel slides
    platform: str = "Instagram Post 4:5"
    goal: str = "Education"
    tone: str = "Friendly & confident"
    audience: str = "Working families aged 30-50"
    brand_name: str = "Studio Brand"
    voice_notes: str = ""
    must_include: str = ""
    avoid: str = ""
    cta: str = "Book a free consultation"
    max_headline_words: int = 9
    max_body_words: int = 28
    language: str = "English"
    mood: str = "auto"
    use_brand_colors: bool = False
    brand_primary: str = "#3b3fb0"
    brand_secondary: str = "#ffbe0b"
    safe_zones: bool = True
    decor: str = "Subtle"
    show_contact: bool = True
    contact_on: str = "Last slide only"
    phone: str = "+44 20 0000 0000"
    email: str = "hello@yourbrand.com"
    website: str = "www.yourbrand.com"
    logo_mode: str = "Smart (auto)"
    hashtags: bool = True
    seed: int = 0
    art_style: str = "Auto (AI chooses)"
    layout: str = "Auto (AI chooses)"
    image_provider: str = IMAGE_PROVIDERS[0]
    ai_key: str = ""
    ai_model: str = DEFAULT_MODEL
    image_key: str = ""
    image_model: str = "gpt-image-1"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_EMOJI = re.compile("[\U00010000-\U0010ffff←-⇿☀-➿⬀-⯿️‍]")


def _clean(s) -> str:
    return re.sub(r"\s+", " ", _EMOJI.sub("", str(s or ""))).strip().strip('"').strip()


def _limit(s: str, n: int) -> str:
    w = s.split()
    return s if len(w) <= n or n <= 0 else " ".join(w[:n]).rstrip(",;:- ")


def detect_mood(text: str) -> str:
    t = f" {text.lower()} "
    best = max(KEYWORDS, key=lambda m: sum(k in t for k in KEYWORDS[m]))
    return best if any(k in t for k in KEYWORDS[best]) else "trustworthy"


def _count_for(g: Guidelines) -> int:
    return max(1, int(g.slides if g.kind == "Carousel" else g.count))


# ---------------------------------------------------------------------------
# Content from the app's Generate tab (kept word-for-word)
# ---------------------------------------------------------------------------

_LABELS = {"headline": "headline", "title": "headline", "hook": "headline", "body": "body", "text": "body",
           "copy": "body", "cta": "cta", "call to action": "cta", "badge": "badge", "tag": "badge"}


def _split_head(text: str, maxw: int) -> tuple[str, str]:
    """Headline = first line / sentence / clause that is short enough; the rest stays as body (nothing is lost)."""
    text = text.strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return "", ""
    limit = maxw + 3
    if len(lines[0].split()) <= limit or (len(lines) > 1 and len(lines[0].split()) <= 2 * maxw):
        return lines[0].rstrip(":"), "\n".join(lines[1:])
    flat = " ".join(lines)
    for pat in (r"(?<=[.!?])\s+", r"(?<=[:;–—])\s+", r"(?<=,)\s+"):
        parts = re.split(pat, flat, maxsplit=1)
        if len(parts) == 2 and 2 <= len(parts[0].split()) <= limit:
            return parts[0].rstrip(",;:"), parts[1]
    words = flat.split()
    return " ".join(words[:maxw]), " ".join(words[maxw:])


def _parse_block(block: str, maxw: int) -> dict:
    out = {"badge": "", "headline": "", "body": "", "cta": ""}
    free = []
    for line in block.splitlines():
        m = re.match(r"\s*\**\s*(headline|title|hook|body|text|copy|cta|call to action|badge|tag)\s*\**\s*[:\-–]\s*(.*)", line, re.I)
        if m:
            key = _LABELS[m.group(1).lower()]
            out[key] = (out[key] + " " + m.group(2)).strip()
        elif line.strip():
            free.append(line)
    if free:
        if out["headline"]:
            out["body"] = (out["body"] + "\n" + "\n".join(free)).strip()
        else:
            h, b = _split_head("\n".join(free), maxw)
            out["headline"] = out["headline"] or h
            out["body"] = (b + ("\n" + out["body"] if out["body"] else "")).strip()
    return out


def _strip_meta(text: str) -> tuple[str, str, list]:
    """Pulls 'Caption:' and 'Hashtags:' lines out of a generated post."""
    caption, tags, keep = "", [], []
    for line in text.splitlines():
        m = re.match(r"\s*(caption|hashtags?)\s*:\s*(.*)", line, re.I)
        if m and m.group(1).lower().startswith("hash"):
            tags += re.findall(r"#\w+", m.group(2))
        elif m:
            caption = m.group(2).strip()
        elif re.fullmatch(r"\s*(#\w+\s*)+", line or "") and line.strip():
            tags += re.findall(r"#\w+", line)
        else:
            keep.append(line)
    return "\n".join(keep).strip(), caption, tags


def parse_app_content(text: str, format_type: str, maxw: int, posts: list | None = None) -> list[dict]:
    """App text -> list of post copies: [{"slides": [...], "caption", "hashtags", "mood", "source"}]."""
    if posts:
        blocks = [str(p) for p in posts if str(p).strip()]
    elif format_type == "Carousel":
        blocks = [text]
    else:
        parts = re.split(r"(?im)^\s*(?:#+\s*)?(?:\*\*)?post\s*\d+\s*(?:\*\*)?\s*[:.\-–)]\s*", text)
        parts = [p for p in parts if p.strip()]
        if len(parts) <= 1:
            parts = [p for p in re.split(r"\n\s*(?:-{3,}|={3,}|\*{3,})\s*\n", text) if p.strip()]
        blocks = parts or [text]

    out = []
    for block in blocks:
        body_text, caption, tags = _strip_meta(block)
        if format_type == "Carousel":
            chunks, cur = [], None
            for line in body_text.splitlines():
                m = re.match(r"\s*(?:\*\*)?slide\s+\d+\s*(?:\*\*)?\s*[:.\-–]\s*(.*)", line, re.I)
                if m:
                    cur = [m.group(1)]
                    chunks.append(cur)
                elif cur is not None:
                    cur.append(line)
            slides_txt = ["\n".join(c) for c in chunks] or [p for p in re.split(r"\n\s*\n", body_text) if p.strip()]
        else:
            slides_txt = [body_text]
        slides = [_parse_block(s, maxw) for s in slides_txt if s.strip()]
        slides = [s for s in slides if s["headline"] or s["body"]]
        if slides:
            out.append({"slides": slides, "caption": caption, "hashtags": tags, "mood": "", "source": "app"})
    return out or [{"slides": [{"badge": "", "headline": "Your post", "body": "", "cta": ""}],
                    "caption": "", "hashtags": [], "mood": "", "source": "app"}]


def _app_copy_payload(text: str, format_type: str, max_headline_words: int) -> dict:
    """Convert app-generated copy into one design-studio payload without losing its body."""
    copy = parse_app_content(text, format_type, max_headline_words)[0]
    return {**copy, "source": "fca_app"}


# ---------------------------------------------------------------------------
# Offline writer (used when no AI key is set or the AI call fails)
# ---------------------------------------------------------------------------

_ANGLES = [
    ("Guide", "{T}", "The few steps that make the biggest difference, without the jargon."),
    ("Myth", "Myth: it can wait until later", "Starting early costs less and gives you more choice. Small steps today count."),
    ("Checklist", "Your 60-second checklist", "Review what you have, spot the gaps and set one clear next step this week."),
    ("Did you know?", "Most people check this too late", "A short review now can save stress, time and money when it matters most."),
    ("Question", "Would your plan cope with a surprise?", "Life changes fast. Make sure your plan keeps up with it."),
    ("Guide", "3 questions to ask before you decide", "What do you need, what can you afford and what happens if things change?"),
    ("Insight", "Peace of mind is a plan, not luck", "Clear goals and regular reviews turn worry into confidence."),
    ("Reminder", "Small changes, big difference", "One decision this month can protect the people who matter most."),
    ("Story", "What we wish everyone knew sooner", "The best time to plan was yesterday. The next best time is today."),
    ("Action", "Take the first step today", "A friendly, no-pressure conversation is all it takes to begin."),
]
_TIPS = [("Start with a clear goal", "Write down what success looks like before you begin."),
         ("Know what you already have", "List your current cover, savings and plans in one place."),
         ("Find the gaps", "Compare what you have with what your family would really need."),
         ("Keep it simple", "One clear plan beats five half-finished ones."),
         ("Review every year", "Life changes. Your plan should change with it."),
         ("Get expert help", "A short conversation can save you time and money."),
         ("Make it automatic", "Set it up once so good habits happen by default."),
         ("Talk it through at home", "Make sure the people you protect know the plan.")]


def offline_copy(g: Guidelines) -> list[dict]:
    t = _limit(g.topic.strip().rstrip(".") or "Your plan", g.max_headline_words + 2)
    short = t[0].upper() + t[1:]
    cta = g.cta or "Learn more"
    tags = ["#" + re.sub(r"\W", "", w.title()) for w in t.split()[:3] if len(w) > 3] + ["#tips"]
    if g.kind == "Carousel":
        n = max(2, g.slides)
        slides = [{"badge": "Guide", "headline": _limit(g.topic.strip().rstrip("."), g.max_headline_words + 3) or short,
                   "body": f"A simple guide for {g.audience or 'you'}. Swipe to see how.", "cta": "Swipe to learn"}]
        for i in range(n - 2):
            h, b = _TIPS[i % len(_TIPS)]
            slides.append({"badge": f"Step {i + 1}", "headline": h, "body": b, "cta": ""})
        slides.append({"badge": "Next step", "headline": "Ready to get started?", "body": f"Talk to {g.brand_name} today.", "cta": cta})
        return [{"slides": slides, "caption": f"{short}. Save this for later and share it with someone who needs it.",
                 "hashtags": tags, "mood": "", "source": "offline"}]
    out = []
    order = list(range(len(_ANGLES)))
    random.Random(g.seed or uuid.uuid4().int).shuffle(order)
    for k in range(max(1, g.count)):
        badge, head, body = _ANGLES[order[k % len(order)]] if k else _ANGLES[0]
        if k and head == "{T}":
            badge, head, body = _ANGLES[order[0]]
        head = head.format(T=short)
        out.append({"slides": [{"badge": badge, "headline": head, "body": body, "cta": cta}],
                    "caption": f"{head} - {body}", "hashtags": tags, "mood": "", "source": "offline"})
    return out


# ---------------------------------------------------------------------------
# AI providers (plain HTTPS, no extra packages)
# ---------------------------------------------------------------------------

def secret(name: str) -> str:
    try:
        import streamlit as st
        v = st.secrets.get(name)
        if v:
            return str(v)
    except Exception:
        pass
    return os.environ.get(name, "")


def _http(url: str, payload: dict | None, headers: dict, timeout: int = 120) -> bytes:
    data = json.dumps(payload).encode() if payload is not None else None
    hdr = {"user-agent": "post-studio/4.0", **headers}
    if payload is not None:
        hdr["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdr, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from None


def _parse_json(text: str) -> dict:
    t = re.sub(r"```(?:json)?", "", text).strip()
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e <= s:
        raise RuntimeError("The AI reply did not contain JSON.")
    return json.loads(t[s:e + 1])


def claude_json(system: str, prompt: str, key: str, model: str, max_tokens: int = 6000) -> dict:
    raw = _http("https://api.anthropic.com/v1/messages",
                {"model": model or DEFAULT_MODEL, "max_tokens": max_tokens, "system": system,
                 "messages": [{"role": "user", "content": prompt}]},
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    data = json.loads(raw.decode())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return _parse_json(text)


def ai_key(g: Guidelines) -> str:
    return (g.ai_key or secret("ANTHROPIC_API_KEY")).strip()


def image_key(g: Guidelines) -> str:
    return (g.image_key or secret("OPENAI_API_KEY")).strip()


_COPY_SYSTEM = ("You are an elite social-media copywriter for regulated brands. You write short, specific, human copy "
                "that stops the scroll, never over-promises and reads perfectly on a designed image. Reply with JSON only.")


def ai_copy(g: Guidelines) -> list[dict]:
    """Claude writes the posts. Returns the same structure as offline_copy()."""
    n = _count_for(g)
    what = (f"ONE carousel with exactly {n} slides" if g.kind == "Carousel"
            else f"{n} DIFFERENT single posts (each post = exactly 1 slide)")
    prompt = f"""Write {what} for {g.platform}.

Topic: {g.topic}
Goal: {g.goal}
Tone: {g.tone}
Audience: {g.audience}
Brand: {g.brand_name}
Language: {g.language}
Call to action to use: {g.cta or "(choose a natural one)"}
Voice notes: {g.voice_notes or "-"}
Must include (somewhere): {g.must_include or "-"}
Never use these words: {g.avoid or "-"}

Rules
- headline: max {g.max_headline_words} words, concrete and punchy, no full stop at the end.
- body: max {g.max_body_words} words, one clear idea, plain English.
- badge: 1-3 words (e.g. "Tip", "Myth", "Step 2", "Did you know?").
- cta: 2-5 words or "".
- No emojis, hashtags or quotation marks in headline, body, badge or cta.
- Single posts: every post must use a different angle (tip, myth-buster, question, checklist, statistic-style insight,
  short story, reminder ...) and a different headline structure.
- Carousel: slide 1 is a strong hook with cta "Swipe to learn"; middle slides are one idea each with badges
  "Step 1", "Step 2" ...; the last slide sums up and uses the call to action.
- caption: 1-3 sentences for the post caption{"; hashtags: 5-8 relevant hashtags" if g.hashtags else "; hashtags: []"}.
- mood: one of {list(MOODS)}.

Return JSON exactly like:
{{"posts": [{{"caption": "", "hashtags": ["#example"], "mood": "trustworthy",
  "slides": [{{"badge": "", "headline": "", "body": "", "cta": ""}}]}}]}}"""
    data = claude_json(_COPY_SYSTEM, prompt, ai_key(g), g.ai_model)
    posts = []
    for p in data.get("posts", []):
        slides = [{k: _clean(s.get(k, "")) for k in ("badge", "headline", "body", "cta")} for s in p.get("slides", []) if isinstance(s, dict)]
        slides = [s for s in slides if s["headline"]]
        if slides:
            posts.append({"slides": slides, "caption": _clean(p.get("caption", "")),
                          "hashtags": [h if h.startswith("#") else "#" + h for h in p.get("hashtags", []) if isinstance(h, str)],
                          "mood": p.get("mood", ""), "source": "ai"})
    if not posts:
        raise RuntimeError("The AI returned no posts.")
    if g.kind == "Carousel":
        posts = posts[:1]
    else:
        posts = [dict(p, slides=p["slides"][:1]) for p in posts][:n]
    return posts


_ART_SYSTEM = ("You are an award-winning art director for social media. You design premium, uncluttered, modern posts "
               "with strong hierarchy, generous negative space and WCAG-readable contrast. Reply with JSON only.")


def ai_briefs(g: Guidelines, copies: list[dict]) -> list[dict]:
    """Claude art-directs every post (layout, palette, fonts, art style, image prompts). Returns raw briefs."""
    fmt = PLATFORMS[g.platform][0]
    W, H = ps.FORMATS.get(fmt, ps.FORMATS["square"])
    summary = []
    for i, c in enumerate(copies):
        summary.append({"post": i + 1, "slides": [{"headline": s["headline"], "body": s["body"][:160]} for s in c["slides"]]})
    brand = (f"Use the brand colours {g.brand_primary} and {g.brand_secondary} as the base of every palette."
             if g.use_brand_colors else "Choose palettes freely; every post must use a different hue family.")
    prompt = f"""Art-direct these {len(copies)} social {"carousel" if g.kind == "Carousel" else "posts"} for {g.platform} ({W}x{H}px).
Topic: {g.topic}. Tone: {g.tone}. Audience: {g.audience}. Brand: {g.brand_name}.
{brand}

Posts:
{json.dumps(summary, ensure_ascii=False)}

For EACH post return a design. Every design in this batch must look clearly different from the others
(different layout, different palette hue family, different art style and different font pairing where possible).

Allowed values
- layout: {list(LAYOUTS)}
- art_style: {ps.ART_STYLES}
- fonts: {list(FONT_PAIRS)}
- icon: {list(ps.ICON_GLYPHS)}
- mood: {list(MOODS)}
- palette: hex colours; bg = main background, accent = decorative colour, highlight = button / badge colour.
  dark = true when bg is dark (light text) and false when bg is light (dark text).
- image_prompts: one per slide of that post. A vivid, specific scene for a premium background image that matches the
  slide's message (editorial photography or refined 3D illustration), in the palette colours. State where calm empty
  space must be left for the text (hero_bottom: lower half; hero_top: upper half; center: middle; card / editorial:
  anywhere, softly blurred). Never ask for text, letters, numbers, logos or watermarks.

Return JSON exactly like:
{{"designs": [{{"layout": "hero_bottom", "art_style": "mesh_glow", "fonts": "Modern sans", "icon": "shield",
  "mood": "trustworthy", "dark": true, "palette": {{"bg": "#0f1d3a", "accent": "#5ee0c8", "highlight": "#ffd166"}},
  "image_prompts": ["..."]}}]}}"""
    data = claude_json(_ART_SYSTEM, prompt, ai_key(g), g.ai_model)
    return [d for d in data.get("designs", []) if isinstance(d, dict)]


def _image_size(fmt: str, provider: str) -> tuple[int, int]:
    W, H = ps.FORMATS.get(fmt, ps.FORMATS["square"])
    r = H / W
    if provider.startswith("OpenAI"):
        return (1024, 1536) if r > 1.15 else ((1536, 1024) if r < 0.87 else (1024, 1024))
    k = 1024 / max(W, H)
    return int(W * k) // 16 * 16, int(H * k) // 16 * 16


_NO_TEXT = " No text, no words, no letters, no numbers, no logos, no watermark, no UI."


def ai_image(prompt: str, fmt: str, g: Guidelines, seed: int) -> Image.Image:
    provider = g.image_provider
    w, h = _image_size(fmt, provider)
    prompt = prompt.strip() + _NO_TEXT
    if provider.startswith("OpenAI"):
        model = g.image_model or "gpt-image-1"
        payload = {"model": model, "prompt": prompt, "size": f"{w}x{h}", "n": 1}
        if model.startswith("dall-e"):
            payload["response_format"] = "b64_json"
            payload["size"] = {"1024x1536": "1024x1792", "1536x1024": "1792x1024"}.get(payload["size"], payload["size"])
        raw = _http("https://api.openai.com/v1/images/generations", payload,
                    {"authorization": f"Bearer {image_key(g)}"}, 180)
        item = json.loads(raw.decode())["data"][0]
        if item.get("b64_json"):
            import base64
            data = base64.b64decode(item["b64_json"])
        else:
            data = _http(item["url"], None, {}, 120)
    else:
        q = urllib.parse.quote(prompt[:900])
        data = _http(f"https://image.pollinations.ai/prompt/{q}?width={w}&height={h}&seed={seed % 10**8}&nologo=true&model=flux",
                     None, {}, 180)
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.convert("RGB")


# ---------------------------------------------------------------------------
# Art direction: briefs (AI or offline), always unique within a batch
# ---------------------------------------------------------------------------

ART_SUBJECTS: dict[str, dict] = {
    "protection": dict(words=["insurance", "protect", "secure", "safety", "cover", "risk", "shield", "trust", "guard", "claim"],
                       icons=["shield", "umbrella", "lock", "check"], scene="a calm family home at golden hour, warm light",
                       styles={"mesh_glow": 3, "waves": 3, "landscape": 2, "papercut": 2, "rays": 1}),
    "family": dict(words=["family", "kids", "child", "parent", "children", "baby", "partner", "together", "generation"],
                   icons=["family", "heart", "house", "umbrella"], scene="a happy family walking in a sunlit park, soft focus",
                   styles={"landscape": 3, "blobs": 3, "mesh_glow": 2, "rays": 2, "papercut": 2}),
    "money": dict(words=["money", "saving", "savings", "pension", "retire", "invest", "wealth", "income", "tax", "budget",
                         "mortgage", "debt", "finance", "financial", "cash", "fund", "interest", "portfolio"],
                  icons=["coins", "chart", "target", "clock"], scene="a refined desk with a notebook and plant, morning light",
                  styles={"skyline": 3, "waves": 3, "facets": 2, "mesh_glow": 2, "topo": 1, "papercut": 2}),
    "growth": dict(words=["growth", "grow", "scale", "success", "goal", "plan", "future", "rise", "boost", "strategy", "progress"],
                   icons=["chart", "rocket", "target", "star"], scene="a winding path up a green hill towards sunrise",
                   styles={"landscape": 3, "rays": 3, "mesh_glow": 2, "papercut": 2, "waves": 2, "aurora": 1}),
    "home": dict(words=["house", "home", "property", "rent", "buy", "moving", "housing"],
                 icons=["house", "lock", "check"], scene="a modern bright living room with large windows",
                 styles={"landscape": 3, "skyline": 3, "blobs": 2, "papercut": 2, "mesh_glow": 1}),
    "health": dict(words=["health", "wellness", "fitness", "yoga", "medical", "care", "mind", "sleep", "mental", "calm"],
                   icons=["heart", "leaf", "check", "star"], scene="soft morning light over a calm lake",
                   styles={"blobs": 3, "bokeh": 3, "waves": 2, "mesh_glow": 2, "papercut": 2}),
    "nature": dict(words=["eco", "green", "nature", "organic", "sustain", "planet", "garden", "outdoor", "earth"],
                   icons=["leaf", "heart", "star"], scene="lush green leaves with dew drops, macro, soft light",
                   styles={"landscape": 4, "blobs": 2, "papercut": 2, "bokeh": 2}),
    "tech": dict(words=["tech", "digital", "software", "data", "cyber", "cloud", "online", "code", "automation", "crypto"],
                 icons=["bulb", "lock", "rocket", "target", "chart"], scene="abstract glowing glass shapes in a dark studio",
                 styles={"synth": 3, "stars": 3, "topo": 2, "aurora": 2, "facets": 2, "mesh_glow": 2}),
    "learning": dict(words=["learn", "tips", "guide", "how to", "education", "course", "skill", "knowledge", "ideas", "ways", "steps", "myth"],
                     icons=["bulb", "target", "check", "star"], scene="an open notebook and coffee on a wooden table, top light",
                     styles={"mesh_glow": 3, "facets": 2, "papercut": 3, "halftone": 2, "blobs": 2}),
    "celebrate": dict(words=["congrat", "celebrat", "birthday", "anniversary", "winner", "party", "festival", "thank", "welcome", "launch"],
                      icons=["star", "heart", "rocket"], scene="soft confetti and warm bokeh lights",
                      styles={"halftone": 3, "blobs": 3, "rays": 3, "bokeh": 2, "papercut": 2}),
    "luxury": dict(words=["luxury", "premium", "exclusive", "elite", "elegant", "heritage", "legacy", "private", "bespoke"],
                   icons=["star", "check", "lock"], scene="dark marble and brushed gold details, studio light",
                   styles={"stars": 3, "bokeh": 3, "aurora": 2, "mesh_glow": 3, "waves": 2}),
}


def art_subject(g: Guidelines, copy: dict) -> str:
    blob = " " + " ".join([g.topic, g.goal, g.tone] + [" ".join(str(v) for v in s.values()) for s in copy.get("slides", [])]).lower() + " "
    scores = {k: sum(blob.count(w) for w in v["words"]) for k, v in ART_SUBJECTS.items()}
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    return {"luxury": "luxury", "techy": "tech", "playful": "celebrate", "energetic": "growth", "calm": "health"}.get(
        copy.get("mood") or g.mood, "learning")


def brand_palette(g, rng):
    a, b = ps._from_hex(g.brand_primary, (60, 70, 200)), ps._from_hex(g.brand_secondary, (255, 190, 60))
    if rng.random() < 0.55:
        bg = ps._mix(a, (0, 0, 0), rng.uniform(0.35, 0.6))
        return ps.make_palette(bg, ps._mix(bg, (0, 0, 0), 0.3), ps._mix(a, (255, 255, 255), 0.35), b, key="custom", label="Brand")
    bg = ps._mix(a, (255, 255, 255), rng.uniform(0.86, 0.93))
    return ps.make_palette(bg, (255, 255, 255), a, b, key="custom", label="Brand")


def _palette_from(bg, accent, highlight, dark: bool):
    if dark and ps._lum(bg) > 0.35:
        bg = ps._mix(bg, (0, 0, 0), 0.55)
    if not dark and ps._lum(bg) < 0.75:
        bg = ps._mix(bg, (255, 255, 255), 0.82)
    card = ps._mix(bg, (0, 0, 0), 0.3) if dark else (255, 255, 255)
    return ps.make_palette(bg, card, accent, highlight, key="custom", label="AI palette")


def _offline_palette(g, rng, mood, hue: float | None):
    if g.use_brand_colors:
        return brand_palette(g, rng)
    if hue is None or rng.random() < 0.35:
        pool = MOODS.get(mood) or [k for k in ps.THEMES if k != "custom"]
        return dataclasses.replace(ps.THEMES[rng.choice(pool)])
    dark = rng.random() < 0.62
    base = ps._hsv(hue, rng.uniform(0.55, 0.85), rng.uniform(0.75, 0.95))
    accent = ps._hsv(hue + rng.choice([0.08, -0.08, 0.5, 0.42]), 0.6, 0.98)
    hi = ps._hsv(hue + rng.choice([0.5, 0.12, 0.33]), 0.8, 1.0)
    bg = ps._hsv(hue, 0.7, 0.22) if dark else ps._mix(base, (255, 255, 255), 0.88)
    return _palette_from(bg, accent if dark else base, hi, dark)


def _pick_unused(rng, options, used, weights=None):
    fresh = [o for o in options if o not in used] or list(options)
    if weights:
        w = [weights.get(o, 1) for o in fresh]
        return rng.choices(fresh, weights=w)[0]
    return rng.choice(fresh)


def _layout_ok(layout: str, fmt: str) -> bool:
    return not (fmt in ("landscape", "twitter", "youtube") and layout == "hero_top")


def finish_brief(raw: dict, g: Guidelines, copy: dict, rng, used: dict, fmt: str) -> dict:
    """Validates an AI brief (or an empty dict) and fills every gap with a fresh offline choice."""
    subject = art_subject(g, copy)
    mood = raw.get("mood") if raw.get("mood") in MOODS else (copy.get("mood") if copy.get("mood") in MOODS else
                                                             (g.mood if g.mood in MOODS else detect_mood(g.topic)))
    layouts = [k for k in LAYOUTS if _layout_ok(k, fmt)]
    layout = g.layout if g.layout in LAYOUTS else raw.get("layout")
    if layout not in layouts or (layout in used["layout"] and len(used["layout"]) < len(layouts) and g.layout not in LAYOUTS):
        layout = _pick_unused(rng, layouts, used["layout"])
    fonts = raw.get("fonts") if raw.get("fonts") in FONT_PAIRS else None
    if not fonts or (fonts in used["fonts"] and len(used["fonts"]) < len(FONT_PAIRS)):
        fonts = _pick_unused(rng, list(FONT_PAIRS), used["fonts"])

    pal_raw = raw.get("palette") if isinstance(raw.get("palette"), dict) else None
    if pal_raw:
        bg_try = ps._from_hex(str(pal_raw.get("bg", "")), (20, 30, 60))
        if any(sum(abs(a - b) for a, b in zip(bg_try, u)) < 60 for u in used.setdefault("bg", [])):
            pal_raw = None                              # too close to another post's palette -> make a fresh one
    if pal_raw and not g.use_brand_colors:
        bg = ps._from_hex(str(pal_raw.get("bg", "")), (20, 30, 60))
        acc = ps._from_hex(str(pal_raw.get("accent", "")), (94, 234, 212))
        hi = ps._from_hex(str(pal_raw.get("highlight", "")), (255, 209, 102))
        dark = bool(raw.get("dark", ps._lum(bg) < 0.5))
        pal = _palette_from(bg, acc, hi, dark)
    else:
        used["hue"] = (used.get("hue", rng.random()) + 0.618034) % 1.0
        pal = _offline_palette(g, rng, mood, used["hue"])
    dark = ps._lum(pal.bg) < 0.5
    used.setdefault("bg", []).append(pal.bg)

    style = g.art_style if g.art_style in ps.ART_STYLES else raw.get("art_style")
    if style not in ps.ART_STYLES or (style in used["style"] and g.art_style not in ps.ART_STYLES):
        weights = dict(ART_SUBJECTS[subject]["styles"])
        options = [s for s in ps.ART_STYLES if dark or s not in ps.DARK_ONLY]
        for s in options:
            weights.setdefault(s, 0.4)
        style = _pick_unused(rng, options, used["style"], weights)
    if not dark and style in ps.DARK_ONLY and g.art_style not in ps.ART_STYLES:
        style = _pick_unused(rng, [s for s in ps.ART_STYLES if s not in ps.DARK_ONLY], used["style"])
    icon = raw.get("icon") if raw.get("icon") in ps.ICON_GLYPHS else rng.choice(ART_SUBJECTS[subject]["icons"])

    n = len(copy["slides"])
    prompts = [str(p) for p in (raw.get("image_prompts") or []) if str(p).strip()]
    while len(prompts) < n:
        s = copy["slides"][len(prompts)]
        prompts.append(f"{ART_SUBJECTS[subject]['scene']}, evoking '{s['headline']}', premium editorial style, "
                       f"{'deep moody' if dark else 'bright airy'} tones of {ps._hex(pal.bg)} and {ps._hex(pal.peek)}, "
                       f"generous empty space for text, shallow depth of field")
    inner = _pick_unused(rng, [l for l in ("editorial", "card", "split", "frame", "center") if _layout_ok(l, fmt) and l != layout], [])

    for k, v in (("layout", layout), ("fonts", fonts), ("style", style)):
        used[k].append(v)
    return {"layout": layout, "inner": inner, "fonts": fonts, "pal": pal, "dark": dark, "style": style, "icon": icon,
            "mood": mood, "motion": MOTION_FOR_MOOD.get(mood, "calm"), "prompts": prompts[:n],
            "badge_style": rng.choice(["pill", "pill", "text"]), "source": "ai" if raw else "offline"}


def make_briefs(g: Guidelines, copies: list[dict], rng, notes: list) -> list[dict]:
    raws: list[dict] = []
    if ai_key(g):
        try:
            raws = ai_briefs(g, copies)
        except Exception as exc:
            notes.append(f"AI art direction unavailable ({exc}); used the built-in art director.")
    used = {"layout": [], "fonts": [], "style": []}
    fmt = PLATFORMS[g.platform][0]
    return [finish_brief(raws[i] if i < len(raws) else {}, g, c, rng, used, fmt) for i, c in enumerate(copies)]


def make_art(brief: dict, g: Guidelines, fmt: str, n: int, seed: int, notes: list) -> list:
    """One background per slide: AI images when an online provider is chosen, offline art otherwise / as fallback."""
    provider = g.image_provider
    if provider.startswith("Flat"):
        return [None] * n
    W, H = ps.FORMATS.get(fmt, ps.FORMATS["square"])
    w, h = int(W * 0.6), int(H * 0.6)
    pal, dark, style = brief["pal"], brief["dark"], brief["style"]
    if n > 1 and style not in ("halftone", "synth", "rays"):
        big = ps.generate_art(style, (w * n, h), pal, dark, seed)
        offline = [big.crop((i * w, 0, (i + 1) * w, h)) for i in range(n)]
    else:
        offline = [ps.generate_art(style, (w, h), pal, dark, seed + i * 7919) for i in range(n)]
    online = provider.startswith("OpenAI") or provider.startswith("Pollinations")
    if not online:
        return offline
    if provider.startswith("OpenAI") and not image_key(g):
        notes.append("OpenAI images need an API key - used offline art instead.")
        return offline
    out = list(offline)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(ai_image, brief["prompts"][i], fmt, g, seed + i): i for i in range(n)}
        for f, i in futs.items():
            try:
                out[i] = f.result()
            except Exception as exc:
                notes.append(f"An AI image could not be created ({exc}) - offline art was used instead.")
    return out


# ---------------------------------------------------------------------------
# Geometry helpers: element boxes, hit-testing
# ---------------------------------------------------------------------------

def _fmt_size(spec): return ps.FORMATS.get(spec.fmt, ps.FORMATS["square"])


def _text_box(layer, W, H):
    try:
        _f, lines, tw, _sz, _mw, line_h, _sp = ps._prepare_text(layer, W, H, W / 1080.0)
        widest, hgt = max((tw(l) for l in lines), default=0.0), len(lines) * line_h
    except Exception:
        widest, hgt = layer.w * W, 0.1 * H
    wf = min(max(widest / W, 0.02), layer.w)
    x, al = layer.x, layer.style.align
    if al == "center":
        x += (layer.w - wf) / 2
    elif al == "right":
        x += layer.w - wf
    return (x, layer.y, x + wf, layer.y + max(hgt, 8) / H)


def _contact_layer(spec):
    ct = spec.contact
    if not ct.enabled:
        return None
    parts = [p for p, on in ((ct.phone, ct.show_phone), (ct.email, ct.show_email), (ct.website, ct.show_website)) if on and p]
    if not parts:
        return None
    W, H = _fmt_size(spec)
    geo = ps.template_geometry(spec.template, spec.fmt)
    y = ct.y if ct.y is not None else geo.contact_y
    y = min(y, 1 - (ct.size * 1.7 * W / 1080) / H)
    return ps.TextLayer(text="   •   ".join(parts), x=0.05, y=y, w=0.90,
                        style=ps.TextStyle(family="body", size=ct.size, bold=False, align=ct.align))


def _logo_box(spec):
    W, H = _fmt_size(spec)
    lg = spec.logo
    aspect = lg.image.width / lg.image.height if lg.image is not None else 1.0
    geo = ps.template_geometry(spec.template, spec.fmt)
    x, y = geo.logo_xy if lg.auto else (lg.x, lg.y)
    return (x, y, x + lg.w, y + lg.w * W / aspect / H)


def element_boxes(spec, skip_logo: bool = False):
    """[(key, label, (x0,y0,x1,y1))] bottom -> top, fractions of the canvas."""
    W, H = _fmt_size(spec)
    geo = ps.template_geometry(spec.template, spec.fmt)
    out, bg = [], spec.background
    if bg.image is not None:
        full = bg.placement == "full" or (bg.placement == "auto" and spec.template not in ps._PHOTO_TEMPLATES)
        box = (0, 0, 1, 1) if full else (bg.box if bg.placement == "custom" else (geo.photo_box or bg.box))
        out.append(("bg", "Background picture", box))
    for p in spec.pictures:
        if p.visible and p.image is not None:
            out.append((f"pic:{p.id}", f"Picture: {p.label}", p.box))
    for s in spec.shapes:
        if s.visible:
            out.append((f"shape:{s.id}", f"Shape: {s.label}", (s.x, s.y, s.x + s.w, s.y + s.h)))
    for t in spec.text_layers:
        if t.visible and t.text.strip():
            out.append((f"text:{t.id}", f"{t.label}: {t.text[:24]}", _text_box(t, W, H)))
    cl = _contact_layer(spec)
    if cl:
        out.append(("contact", "Contact bar", _text_box(cl, W, H)))
    if spec.logo.visible and not skip_logo:
        out.append(("logo", "Logo", _logo_box(spec)))
    return out


def all_options(spec):
    items = element_boxes(spec)
    keys = {k for k, _, _ in items}
    labels = {k: l for k, l, _ in items}
    for t in spec.text_layers:
        if f"text:{t.id}" not in keys:
            labels[f"text:{t.id}"] = f"(hidden) {t.label}"
    if not spec.contact.enabled:
        labels["contact"] = "Contact bar (off)"
    if not spec.logo.visible:
        labels["logo"] = "Logo (hidden)"
    order = [k for k, _, _ in reversed(items)] + [k for k in labels if k not in keys]
    return order, labels


def hit_test(spec, fx: float, fy: float, pad: float = 0.012):
    for key, _l, b in reversed(element_boxes(spec)):
        if b[0] - pad <= fx <= b[2] + pad and b[1] - pad <= fy <= b[3] + pad:
            return key
    return None


def _overlap(a, b) -> float:
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def _plain_render(spec, scale: float = 0.2) -> Image.Image:
    tmp = ps._clone(spec)
    tmp.logo.visible = False
    return ps.render(tmp, scale=scale, include_text=False).convert("RGB")


def _mean_under(img: Image.Image, box) -> tuple:
    iw, ih = img.size
    x0, y0 = int(max(0, box[0]) * iw), int(max(0, box[1]) * ih)
    x1, y1 = max(x0 + 1, int(min(1, box[2]) * iw)), max(y0 + 1, int(min(1, box[3]) * ih))
    return tuple(int(v) for v in np.asarray(img.crop((x0, y0, x1, y1)), np.float32).reshape(-1, 3).mean(0))


# ---------------------------------------------------------------------------
# Contrast helpers
# ---------------------------------------------------------------------------

def _rl(c):
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _ratio(a, b) -> float:
    la, lb = _rl(a), _rl(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


LIGHT, DARK = (255, 255, 255), (17, 19, 28)


def _best_ink(mean) -> tuple:
    return LIGHT if _ratio(LIGHT, mean) >= _ratio(DARK, mean) else DARK


# ---------------------------------------------------------------------------
# Brand mark, smart logo placement, contact colour
# ---------------------------------------------------------------------------

def brand_mark(name: str, color, accent, backdrop=None) -> Image.Image:
    """Clean wordmark used when no logo is uploaded: accent diamond + brand name in spaced caps.
    backdrop = colour of a soft pill behind it (used over busy pictures)."""
    text = (name or "Brand").upper()
    S = 3
    f = ps._load_ttf(ps._resolve_weight("display", True, False), 46 * S, "bold")
    sp = 5 * S
    tw = sum(f.getlength(ch) for ch in text) + sp * (len(text) - 1)
    h = 64 * S
    dia = 30 * S
    px = 30 * S if backdrop else 0
    if backdrop:
        h = 92 * S
    w = int(dia + 22 * S + tw + 8 * S + 2 * px)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cy = h / 2
    if backdrop:
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h / 2, fill=(*backdrop, 225))
    d.polygon([(px + dia / 2, cy - dia / 2), (px + dia, cy), (px + dia / 2, cy + dia / 2), (px, cy)], fill=(*accent, 255))
    x = px + dia + 22 * S
    for ch in text:
        d.text((x, cy), ch, font=f, fill=(*color, 255), anchor="lm")
        x += f.getlength(ch) + sp
    return im.resize((w // S, h // S), Image.Resampling.LANCZOS)


def smart_logo(spec, rng: random.Random | None = None, mode: str = "Smart (auto)") -> None:
    """Scores candidate corners (overlap with content, busyness, preference) and picks the best one."""
    rng = rng or random.Random()
    lg = spec.logo
    if not lg.visible:
        return
    W, H = _fmt_size(spec)
    aspect = lg.image.width / lg.image.height if lg.image is not None else 1.0
    w = min(0.30, max(0.10, 0.042 * aspect)) if aspect > 1.6 else (0.13 if H > W * 1.2 else 0.11)
    if w * W / aspect / H > 0.11:
        w = 0.11 * H * aspect / W
    hf = w * W / aspect / H
    story = spec.fmt in ("story", "reel")
    top, bot, m = (0.06 if not story else 0.10), (0.94 if not story else 0.80) - hf, 0.06
    cl = _contact_layer(spec)
    if cl is not None:
        bot = min(bot, cl.y - 0.025 - hf)
    cands = {"Top left": (m, top), "Top right": (1 - m - w, top), "Bottom left": (m, bot), "Bottom right": (1 - m - w, bot),
             "Top center": ((1 - w) / 2, top), "Bottom center": ((1 - w) / 2, bot)}
    if mode in cands:
        best = cands[mode]
    else:
        pref = {"Top left": 0.0, "Top right": 0.2, "Bottom right": 0.6, "Bottom left": 0.6, "Top center": 0.5, "Bottom center": 1.0}
        obstacles = []
        for key, label, b in element_boxes(spec, skip_logo=True):
            if key.startswith("text:") or key == "contact":
                obstacles.append(((b[0] - 0.02, b[1] - 0.02, b[2] + 0.02, b[3] + 0.02), 12.0))
            elif key.startswith("shape:"):
                obstacles.append((b, 2.0 if label.endswith("Card") else 4.0))
            elif key.startswith("pic:") and not any(t in label for t in ("Scrim", "AI art")):
                obstacles.append((b, 6.0))
        lum = np.asarray(_plain_render(spec).convert("L"), dtype=np.float32) / 255.0
        ih, iw = lum.shape
        best, best_s = cands["Top left"], 1e9
        for name, (x, y) in cands.items():
            box, area = (x, y, x + w, y + hf), w * hf
            s = sum(_overlap(box, ob) / area * wt for ob, wt in obstacles)
            reg = lum[int(y * ih): max(int(y * ih) + 2, int((y + hf) * ih)), int(x * iw): max(int(x * iw) + 2, int((x + w) * iw))]
            s += 6.0 * float(reg.std()) + pref[name] + rng.random() * 0.2
            if s < best_s:
                best, best_s = (x, y), s
    lg.auto, lg.x, lg.y, lg.w = False, round(best[0], 3), round(best[1], 3), round(w, 3)


def fit_contact_color(spec) -> None:
    ct, cl = spec.contact, _contact_layer(spec)
    if cl is None:
        return
    W, H = _fmt_size(spec)
    ct.auto_color, ct.color = False, _best_ink(_mean_under(_plain_render(spec), _text_box(cl, W, H)))


def _fit_brand_mark(spec, brand: str) -> None:
    """Re-inks the generated wordmark for the spot it landed on."""
    lg = spec.logo
    if lg.src != "brand-mark" or not lg.visible:
        return
    img = _plain_render(spec, 0.3)
    box = _logo_box(spec)
    mean = _mean_under(img, box)
    iw, ih = img.size
    crop = np.asarray(img.convert("L").crop((int(box[0] * iw), int(box[1] * ih), max(int(box[0] * iw) + 2, int(box[2] * iw)),
                                             max(int(box[1] * ih) + 2, int(box[3] * ih)))), np.float32) / 255.0
    pal = spec.get_palette()
    if crop.std() > 0.09:                       # busy picture: put the mark on a soft pill
        back = ps._mix(pal.bg, (0, 0, 0), 0.35) if ps._lum(pal.bg) < 0.5 else ps._mix(pal.bg, (255, 255, 255), 0.6)
        ink = _best_ink(back)
        acc = pal.cta_bg if _ratio(pal.cta_bg, back) > 1.8 else ink
        lg.image = brand_mark(brand, ink, acc, back)
        return
    ink = _best_ink(mean)
    acc = pal.cta_bg if _ratio(pal.cta_bg, mean) > 1.8 else ink
    lg.image = brand_mark(brand, ink, acc)


# ---------------------------------------------------------------------------
# Composition engine
# ---------------------------------------------------------------------------

HEAD_MAX = {"square": 96, "portrait": 100, "story": 104, "reel": 104, "pinterest": 100,
            "landscape": 74, "youtube": 86, "twitter": 82}


def _style(family, size, color, bold=True, align="left", **kw) -> ps.TextStyle:
    return ps.TextStyle(family=family, size=int(size), color=tuple(color), bold=bold, align=align, color_role="custom", **kw)


def _role(layer) -> str:
    return layer.kind if layer.kind in ("headline", "body", "cta") else "badge"


def _pad_px(layer, u) -> float:
    return layer.style.highlight_pad * u if layer.style.highlight else 0.0


_GAPS = {("badge", "headline"): 30, ("headline", "body"): 26, ("body", "cta"): 48, ("headline", "cta"): 48, ("badge", "body"): 26}


def _gap(a, b, u) -> float:
    g = _GAPS.get((_role(a), _role(b)), 30)
    if _role(a) == "headline":
        g = max(g, a.style.size * 0.36)          # big headlines need proportionally more air below them
    return g * u


def _block_h(layer, W, H) -> float:
    """Real pixel height of a text block: line pitch for all but the last line + the ink depth of the last line."""
    f, lines, _tw, size_px, _mw, line_h, _sp = ps._prepare_text(layer, W, H, W / 1080.0)
    if not lines:
        return 0.0
    if layer.style.highlight:                    # pills: the button box is size + padding (padding added by the caller)
        return (len(lines) - 1) * line_h + size_px
    ink_bottom = f.getbbox(lines[-1].upper() if layer.style.uppercase else lines[-1])[3]
    return (len(lines) - 1) * line_h + max(size_px * 0.8, ink_bottom)


def _stack_height(layers, W, H) -> float:
    u = W / 1080
    tot = sum(_block_h(l, W, H) + _pad_px(l, u) for l in layers)
    return tot + sum(_gap(a, b, u) for a, b in zip(layers, layers[1:]))


def fit_stack(layers, area, anchor: str, W: int, H: int, max_lines: int, min_head: int = 40, min_body: int = 26):
    """Sizes headline/body to fill `area` (largest size that fits) and stacks the layers. Returns the stack box."""
    u = W / 1080
    x0, y0, x1, y1 = area
    head = next(l for l in layers if l.kind == "headline")
    body = next((l for l in layers if l.kind == "body"), None)
    for l in layers:
        pad = _pad_px(l, u) / W if l.style.align == "left" else 0.0
        l.x, l.w = x0 + pad, (x1 - x0) - 2 * pad
    room = (y1 - y0) * H
    body_base = body.style.size if body is not None else 0
    for _ in range(80):
        lines = len(ps._prepare_text(head, W, H, u)[1])
        if _stack_height(layers, W, H) <= room and lines <= max_lines:
            break
        if head.style.size <= min_head and (body is None or body.style.size <= min_body):
            break
        head.style.size = max(min_head, int(head.style.size * 0.95))
        if body is not None and lines <= max_lines:      # body follows the headline, keeping a calm 0.4 ratio
            body.style.size = max(min_body, min(body_base, round(head.style.size * 0.42)))
    tot = _stack_height(layers, W, H)
    start = y0 * H if anchor == "top" else (y1 * H - tot if anchor == "bottom" else ((y0 + y1) * H - tot) / 2)
    y = start
    for a, b in zip(layers, layers[1:] + [None]):
        pad = _pad_px(a, u)
        a.y = (y + pad / 3) / H
        y += _block_h(a, W, H) + pad
        if b is not None:
            y += _gap(a, b, u)
    return (x0, start / H, x1, (start + tot) / H)


def _add_pic(spec, img, box, label, corner=0.0, shadow=False):
    spec.pictures.append(ps.BackgroundLayer(image=img, box=tuple(box), label=label, corner=corner, shadow=shadow,
                                            placement="custom"))


def compose_slide(c: dict, brief: dict, g: Guidelines, fmt: str, idx: int, n: int, art, contact, logo_img,
                  rng: random.Random, layout: str | None = None) -> ps.PostSpec:
    W, H = ps.FORMATS.get(fmt, ps.FORMATS["square"])
    u = W / 1080
    pal, dark = brief["pal"], brief["dark"]
    layout = layout or brief["layout"]
    wide, story, carousel = W / H > 1.25, fmt in ("story", "reel"), n > 1
    hero_size = not carousel or idx == 0

    headline = _clean(c.get("headline")) or "Your headline"
    body = str(c.get("body") or "").strip()
    cta, badge = _clean(c.get("cta")), _clean(c.get("badge"))

    spec = ps.new_post(headline, "", "", fmt=fmt, template="ai_canvas")
    spec.custom_palette, spec.theme = dataclasses.replace(pal), "custom"
    spec.name = f"Slide {idx + 1}" if carousel else ""
    spec.text_layers = []
    spec.motion = ps.Motion(mood=brief.get("motion", "calm"))

    # --- safe area ----------------------------------------------------------
    ct = ps._clone(contact)
    ct.size, ct.align, ct.show_email = (19 if wide else 21), "center", ct.show_email and not wide
    ct.y = 0.77 if story else (0.885 if carousel else 0.925)
    spec.contact = ct
    m = 0.06 if wide else 0.08
    top = 0.17 if story and g.safe_zones else (0.10 if wide else 0.09)
    bottom = (ct.y - 0.035) if ct.enabled else (0.74 if story else (0.86 if carousel else 0.9))
    if story and g.safe_zones:
        bottom = min(bottom, 0.74)

    # --- background, art, scrims, panels ------------------------------------
    full = layout in ("hero_bottom", "hero_top", "center", "card", "editorial")
    if art is None and layout in ("split", "frame"):
        art = ps.make_gradient((540, 540), pal.peek, pal.cta_bg, rng.uniform(20, 160)).convert("RGB")
    if art is not None and full:
        spec.background.image, spec.background.placement = art, "full"
    elif art is None:
        spec.gradient = ps.Gradient(True, pal.bg, ps._mix(pal.bg, pal.peek, 0.32), round(rng.uniform(30, 150)),
                                    "radial" if rng.random() < 0.3 else "linear")
    scrim_col = ps._mix(pal.bg, (0, 0, 0), 0.55) if dark else ps._mix(pal.bg, (255, 255, 255), 0.55)

    def scrim(side, strength, start, end, center=(0.5, 0.5)):
        if art is not None:
            _add_pic(spec, ps.scrim_image(side, scrim_col, strength, start, end, center, aspect=W / H), (0, 0, 1, 1), "Scrim")

    anchor, align = "bottom", "left"
    if layout == "hero_bottom":
        scrim("bottom", 0.94, 0.12, 0.78)
        area = (m, 0.32, 1 - m, bottom)
    elif layout == "hero_top":
        scrim("top", 0.94, 0.22, 0.88)
        area, anchor = (m, top, 1 - m, 0.66), "top"
    elif layout == "center":
        scrim("flat", 0.30, 0, 1)
        scrim("radial", 0.62, 0.05, 0.75)
        area, anchor, align = (0.1, top + 0.04, 0.9, bottom - 0.02), "center", "center"
    elif layout == "card":
        scrim("flat", 0.12, 0, 1)
        if wide:
            area, anchor = (m + 0.04, top + 0.07, 0.62, bottom - 0.07), "center"
        elif carousel and idx > 0:
            area, anchor = (m + 0.06, top + 0.12, 1 - m - 0.06, bottom - 0.06), "center"
        else:
            area, anchor = (m + 0.06, 0.30, 1 - m - 0.06, bottom - 0.05), "bottom"
    elif layout == "editorial":
        scrim("flat", 0.80, 0, 1)
        area, anchor = (m + 0.05, top + 0.05, 0.9 if not wide else 0.78, bottom - 0.02), "center"
    elif layout == "split":
        if wide:
            right = rng.random() < 0.5
            box = (0.5, 0, 1, 1) if right else (0, 0, 0.5, 1)
            area, anchor = ((0.06, 0.14, 0.45, 0.86) if right else (0.55, 0.14, 0.94, 0.86)), "center"
            accent_bar = (0.5 - 0.004, 0.42, 0.5 + 0.004, 0.58)
        else:
            cut = 0.46 if story else 0.5
            box = (0, 0, 1, cut)
            area, anchor = (m, cut + 0.07, 1 - m, bottom), "center"
            accent_bar = (m, cut - 0.004, m + 0.16, cut + 0.004)
        _add_pic(spec, art, box, "AI art")
        spec.shapes.append(ps.ShapeLayer(kind="rect", x=accent_bar[0], y=accent_bar[1], w=accent_bar[2] - accent_bar[0],
                                         h=accent_bar[3] - accent_bar[1], color=pal.cta_bg, color_role="cta", label="Accent bar"))
    else:  # frame
        if wide:
            box = (0.04, 0.07, 0.49, 0.93)
            area, anchor = (0.55, 0.14, 0.94, 0.86), "center"
        else:
            box = (m, top - 0.02 if story else 0.06, 1 - m, 0.47 if story else 0.53)
            area, anchor = (m, box[3] + 0.06, 1 - m, bottom), "center"
        _add_pic(spec, art, box, "AI art", corner=0.06, shadow=True)

    # --- text layers -------------------------------------------------------
    fam_h, fam_b = FONT_PAIRS.get(brief["fonts"], ("display", "body"))
    hmax = HEAD_MAX.get(fmt, 96) * (1.0 if hero_size else 0.84)
    if layout in ("card", "frame", "split") or (wide and layout == "editorial"):
        hmax *= 0.88
    layers = []
    if badge:
        if brief.get("badge_style") == "text":
            st_b = _style("display", 28 if not wide else 24, DARK, align=align, uppercase=True, letter_spacing=4.0)
        else:
            st_b = _style("display", 25 if not wide else 22, DARK, align=align, uppercase=True, letter_spacing=2.5,
                          highlight=True, highlight_pad=18, highlight_radius=0.5)
        layers.append(ps.TextLayer(text=badge, kind="custom", label="Badge", style=st_b))
    hl = ps.TextLayer(text=headline, kind="headline", label="Headline",
                      style=_style(fam_h, hmax, LIGHT, align=align, leading=1.08, uppercase=fam_h == "condensed"))
    layers.append(hl)
    if body:
        layers.append(ps.TextLayer(text=body, kind="body", label="Body Copy",
                                   style=_style(fam_b, 30 if wide else 34, LIGHT, bold=False, align=align, leading=1.38)))
    if cta:
        layers.append(ps.TextLayer(text=cta, kind="cta", label="CTA",
                                   style=_style("display", 26 if wide else 29, DARK, align=align, leading=1.0, letter_spacing=0.5,
                                                highlight=True, highlight_pad=28, highlight_radius=0.5)))
    max_lines = 3 if wide else 4
    sb = fit_stack(layers, area, anchor, W, H, max_lines, min_head=34 if wide else 40, min_body=22 if wide else 26)

    if layout == "card":
        px, py = 0.05, 0.045 * W / H
        cx0, cx1 = (sb[0] - px, sb[2] + px)
        card = ps.ShapeLayer(kind="rounded", x=round(cx0, 3), y=round(sb[1] - py, 3), w=round(cx1 - cx0, 3),
                             h=round(sb[3] - sb[1] + 2 * py, 3), color=pal.card, color_role="card", alpha=0.93,
                             radius=0.12 if not wide else 0.08, shadow=True, label="Card")
        spec.shapes.append(card)
    if layout == "editorial":
        spec.shapes.append(ps.ShapeLayer(kind="rect", x=round(area[0] - 0.045, 3), y=round(sb[1], 3), w=0.009,
                                         h=round(sb[3] - sb[1], 3), color=pal.cta_bg, color_role="cta", label="Accent bar"))
    if not badge:
        bw = 0.12 if not wide else 0.08
        bx = (sb[0] + sb[2] - bw) / 2 if align == "center" else sb[0]
        by = sb[1] - 0.035
        if layout != "editorial" and by > top - 0.03:
            spec.shapes.append(ps.ShapeLayer(kind="pill", x=round(bx, 3), y=round(by, 3), w=bw, h=round(8 * u / H, 4),
                                             color=pal.cta_bg, color_role="cta", label="Accent line"))

    spec.text_layers = layers
    spec.sel_text = hl.id

    # --- colour: measured on the real pixels under each block -------------
    plain = _plain_render(spec, 0.3)
    for l in layers:
        mean = _mean_under(plain, _text_box(l, W, H))
        ink = _best_ink(mean)
        s = l.style
        if l.kind == "headline":
            s.color = ink
            if full and layout not in ("card",) and art is not None:
                s.shadow, s.shadow_color, s.shadow_blur, s.shadow_dx, s.shadow_dy, s.shadow_opacity = \
                    True, (0, 0, 0) if ink == LIGHT else (255, 255, 255), 18.0, 0.0, 3.0, 0.28
        elif l.kind == "body":
            soft = ps._mix(ink, mean, 0.16)
            s.color = soft if _ratio(soft, mean) >= 4.5 else ink
        else:
            pill = s.highlight
            acc = pal.cta_bg if l.kind == "cta" else pal.peek
            if l.kind != "cta" and _ratio(acc, mean) < 1.8:
                acc = pal.cta_bg
            if _ratio(acc, mean) < 1.6:
                acc = ink
            if pill:
                s.highlight_color = acc
                s.color = _best_ink(acc)
            else:
                s.color = acc if _ratio(acc, mean) >= 3.0 else ink

    # --- add-ons: ghost numerals, icon, subtle decor -----------------------
    ink_main = hl.style.color
    if carousel and 0 < idx < n - 1 and anchor != "top":
        gh = ps.ghost_number(f"{idx:02d}", int(H * (0.2 if not wide else 0.3)), ink_main, alpha=0.34)
        gw, ghh = gh.width / W, gh.height / H
        gy = top - 0.02
        if gy + ghh < sb[1] - 0.02:
            gx = 1 - m - gw if align != "center" else (1 - gw) / 2
            _add_pic(spec, gh, (gx, gy, gx + gw, gy + ghh), "Ghost number")
    if layout == "editorial" and hero_size and g.decor != "None":
        iw = 0.13 if not wide else 0.08
        ih = iw * W / H
        if sb[1] - ih - 0.03 > top - 0.02:
            ic = ps.icon_medallion(brief["icon"], 300, pal.cta_bg, ps._mix(pal.cta_bg, pal.peek, 0.5), _best_ink(pal.cta_bg),
                                   pal.peek if _ratio(pal.peek, pal.cta_bg) > 1.6 else _best_ink(pal.cta_bg), LIGHT)
            _add_pic(spec, ic, (area[0], sb[1] - ih - 0.03, area[0] + iw, sb[1] - 0.03), "Icon add-on")
    if g.decor != "None" and layout not in ("frame",):
        count = 1 if g.decor == "Subtle" else 2
        kinds = rng.sample(["dots", "plus", "rings", "sparkle", "diamonds"], count)
        for k in kinds:
            dw = 0.16 if not wide else 0.1
            dh = dw * W / H * (0.5 if k == "diamonds" else 1.0)
            spots = [(1 - m - dw + 0.03, top - 0.03), (1 - m - dw + 0.03, bottom - dh), (m - 0.03, bottom - dh)]
            for x, y in spots:
                box = (x, y, x + dw, y + dh)
                if _overlap(box, (sb[0] - 0.03, sb[1] - 0.05, sb[2] + 0.03, sb[3] + 0.03)) == 0 and \
                        not any(_overlap(box, p.box) > 0 for p in spec.pictures if p.label not in ("Scrim", "AI art")):
                    col = pal.peek if _ratio(pal.peek, scrim_col) > 1.5 else ink_main
                    _add_pic(spec, ps.decor_image(k, 240, int(240 * dh * H / (dw * W)), col, 0.55, rng.randrange(1, 10**6)),
                             box, "Decor add-on")
                    break

    # --- contact + logo ----------------------------------------------------
    if logo_img is not None:
        spec.logo = ps.ImageLayer(image=logo_img, name=g.brand_name)
    else:
        spec.logo = ps.ImageLayer(image=brand_mark(g.brand_name, LIGHT, pal.cta_bg), name=g.brand_name, src="brand-mark")
    fit_contact_color(spec)
    smart_logo(spec, rng, g.logo_mode)
    _fit_brand_mark(spec, g.brand_name)
    return spec


def build_post(copy: dict, brief: dict, g: Guidelines, rng, logo_img=None, photo=None, notes: list | None = None) -> ps.CarouselSpec:
    """One post (single slide) or one carousel (n slides) with its own unique design."""
    notes = notes if notes is not None else []
    fmt = PLATFORMS[g.platform][0]
    slides = copy["slides"]
    n = len(slides)
    arts = [photo] * n if photo is not None else make_art(brief, g, fmt, n, rng.randrange(1, 2 ** 31), notes)
    contact = ps.ContactInfo(phone=g.phone, email=g.email, website=g.website, show_phone=bool(g.phone), show_email=bool(g.email),
                             show_website=bool(g.website), enabled=g.show_contact, align="center", size=20)
    out = []
    g_local = g
    for i, c in enumerate(slides):
        ctc = ps._clone(contact)
        ctc.enabled = g.show_contact and (n == 1 or g.contact_on == "All slides" or i == n - 1)
        layout = brief["layout"] if (i == 0 or n == 1) else ("center" if i == n - 1 else brief["inner"])
        out.append(compose_slide(c, brief, g_local, fmt, i, n, arts[i], ctc, logo_img, rng, layout))
        if i == 0 and n > 1 and g.logo_mode == "Smart (auto)":     # the logo keeps one corner across the carousel
            lg = out[0].logo
            corner = ("Top" if lg.y < 0.5 else "Bottom") + (" left" if lg.x < 0.3 else (" right" if lg.x > 0.55 else " center"))
            g_local = dataclasses.replace(g, logo_mode=corner if corner in LOGO_MODES else "Top left")
    return ps.CarouselSpec(slides=out, indicator=rng.choice(["dots", "bar", "numbers"]) if n > 1 else "none", swipe=n > 1)


def build_batch(copies: list[dict], g: Guidelines, rng, logo_img=None, photo=None, progress=None) -> dict:
    """Designs every post of a batch. progress(fraction, message) is optional."""
    notes: list[str] = []
    if progress:
        progress(0.15, "Art-directing every design...")
    briefs = make_briefs(g, copies, rng, notes)
    posts = []
    for i, (c, b) in enumerate(zip(copies, briefs)):
        if progress:
            progress(0.2 + 0.75 * i / max(1, len(copies)), f"Designing {'carousel' if g.kind == 'Carousel' else f'post {i + 1} of {len(copies)}'}...")
        posts.append(build_post(c, b, g, rng, logo_img, photo, notes))
    if progress:
        progress(1.0, "Done")
    notes = list(dict.fromkeys(notes))
    return {"kind": g.kind, "posts": posts, "copies": copies, "briefs": briefs, "active": 0, "notes": notes,
            "id": uuid.uuid4().hex[:8]}


def slide_copy(spec) -> dict:
    def t(kind):
        l = next((x for x in spec.text_layers if x.kind == kind and x.text.strip()), None)
        return l.text if l else ""
    return {"headline": t("headline"), "body": t("body"), "cta": t("cta"),
            "badge": next((x.text for x in spec.text_layers if x.label == "Badge"), "")}


def redesign(car: ps.CarouselSpec, g: Guidelines, rng, idx: int | None = None, keep_palette: bool = False,
             notes: list | None = None) -> ps.CarouselSpec:
    """New unique look for a whole post / carousel (idx=None) or one slide. Text is kept."""
    notes = notes if notes is not None else []
    copy = {"slides": [slide_copy(s) for s in car.slides], "mood": ""}
    used = {"layout": [], "fonts": [], "style": []}
    brief = finish_brief({}, g, copy, rng, used, car.slides[0].fmt)
    if keep_palette:
        brief["pal"] = dataclasses.replace(car.slides[car.active_index].get_palette())
        brief["dark"] = ps._lum(brief["pal"].bg) < 0.5
    keep_logo = [s.logo for s in car.slides]
    fresh = build_post(copy, brief, g, rng, None, None, notes)
    for s, lg in zip(fresh.slides, keep_logo):
        if lg.src != "brand-mark" and lg.image is not None:
            s.logo = ps.ImageLayer(image=lg.image, name=lg.name, src=lg.src)
            smart_logo(s, rng, g.logo_mode)
    if idx is None:
        fresh.active_index = car.active_index
        return fresh
    old = car.slides[idx]
    new = fresh.slides[idx]
    new.name, new.duration, new.contact = old.name, old.duration, old.contact
    car.slides[idx] = new
    return car


# ---------------------------------------------------------------------------
# Guidelines checker
# ---------------------------------------------------------------------------

def check_guidelines(spec, g: Guidelines) -> list[tuple[bool, str]]:
    res: list[tuple[bool, str]] = []
    W, H = _fmt_size(spec)
    story = spec.fmt in ("story", "reel")
    geo, pal = ps.template_geometry(spec.template, spec.fmt), spec.get_palette()
    im = _plain_render(spec)
    layers = [l for l in spec.text_layers if l.visible and l.text.strip()]
    cl = _contact_layer(spec)
    boxes = [(l, _text_box(l, W, H)) for l in layers]
    if cl:
        boxes.append((cl, _text_box(cl, W, H)))

    off = [getattr(l, "label", "Contact") for l, b in boxes if b[0] < 0.03 or b[2] > 0.97 or b[3] > 0.98 or b[1] < 0.02]
    res.append((not off, "All text inside the safe margins" if not off else f"Outside safe margin: {', '.join(off)}"))
    if story:
        bad = [getattr(l, "label", "Contact") for l, b in boxes if b[1] < 0.14 or b[3] > 0.80]
        res.append((not bad, "Story/Reel UI zones are clear" if not bad else f"In story UI zone: {', '.join(bad)}"))
    small = [l.label for l in layers if l.style.size < 20 and l.label != "Badge"]
    res.append((not small, "Font sizes are readable" if not small else f"Text too small: {', '.join(small)}"))
    clash = [f"{a.label}/{b.label}" for i, (a, ba) in enumerate(boxes) for (b, bb) in boxes[i + 1:] if _overlap(ba, bb) > 0.0008]
    res.append((not clash, "No overlapping text" if not clash else f"Text overlaps: {', '.join(clash[:3])}"))
    low = []
    for l, b in boxes:
        if l.style.highlight:
            if _ratio(l.style.color, l.style.highlight_color) < 3.0:
                low.append(getattr(l, "label", "Text"))
            continue
        mean = _mean_under(im, b)
        col = spec.contact.color if (l is cl and not spec.contact.auto_color) else ps._role_color(l.style, geo, pal)
        if l is cl and spec.contact.auto_color:
            col = geo.contact_ink
        if _ratio(col, mean) < 3.0:
            low.append(getattr(l, "label", "Contact"))
    res.append((not low, "Text contrast is good (WCAG 3:1+)" if not low else f"Low contrast: {', '.join(low)}"))
    hl = next((l for l in layers if l.kind == "headline"), None)
    if hl:
        n = len(hl.text.split())
        res.append((n <= g.max_headline_words + 3, f"Headline length {n} words (target {g.max_headline_words})"))
    alltext = " ".join(l.text for l in layers).lower()
    bad_words = [w.strip() for w in g.avoid.split(",") if w.strip() and w.strip().lower() in alltext]
    res.append((not bad_words, "No banned words" if not bad_words else f"Banned words used: {', '.join(bad_words)}"))
    missing = [w.strip() for w in g.must_include.split(",") if w.strip() and w.strip().lower() not in alltext]
    if g.must_include.strip():
        res.append((not missing, "Required words present" if not missing else f"Missing: {', '.join(missing)}"))
    if spec.logo.visible:
        lb = _logo_box(spec)
        hit = [k for k, _l, b in element_boxes(spec, skip_logo=True) if (k.startswith("text:") or k == "contact") and _overlap(lb, b) > 0]
        res.append((not hit, "Logo does not cover content" if not hit else "Logo overlaps text/contact - use Smart place"))
    else:
        res.append((False, "Logo is hidden"))
    return res


# ---------------------------------------------------------------------------
# Canvas selection outline
# ---------------------------------------------------------------------------

def _outline(img: Image.Image, spec, focus: str | None) -> Image.Image:
    box = next((b for k, _l, b in element_boxes(spec) if k == focus), None) if focus else None
    if not box:
        return img
    im = img.copy()
    d = ImageDraw.Draw(im, "RGBA")
    w, h = im.size
    r = (box[0] * w - 6, box[1] * h - 6, box[2] * w + 6, box[3] * h + 6)
    d.rectangle(r, outline=(79, 140, 255, 255), width=2)
    for cx, cy in ((r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3])):
        d.rectangle((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 255, 255, 255), outline=(79, 140, 255, 255))
    return im


# ---------------------------------------------------------------------------
# Batch state (gallery of posts <-> the editor's ss["carousel"])
# ---------------------------------------------------------------------------

def _writeback(ss) -> None:
    """The editor works on ss['carousel']; keep the batch pointing at the latest version (undo may replace it)."""
    b = ss.get("batch")
    if b and "carousel" in ss:
        b["posts"][b["active"]] = ss["carousel"]


def _reset_history(ss) -> None:
    ss["hist"], ss["redo"], ss["last_sig"], ss["prev_snap"], ss["exports"] = [], [], None, None, {}


def _set_batch(st, batch: dict) -> None:
    ss = st.session_state
    ss["batch"] = batch
    ss["carousel"] = batch["posts"][0]
    ss["thumbs"] = {}
    _reset_history(ss)
    ps._bump(st)


def _cb_pick(st, i: int) -> None:
    ss = st.session_state
    _writeback(ss)
    b = ss["batch"]
    b["active"] = i
    ss["carousel"] = b["posts"][i]
    _reset_history(ss)
    ps._bump(st)


def _cb_redesign_post(st, i: int) -> None:
    ss = st.session_state
    _writeback(ss)
    b, g = ss["batch"], ss["guide"]
    notes: list[str] = []
    b["posts"][i] = redesign(b["posts"][i], g, random.Random(uuid.uuid4().int), None, False, notes)
    if i == b["active"]:
        ss["carousel"] = b["posts"][i]
        _reset_history(ss)
    if notes:
        ss["flash_ai"] = notes[-1]
    ps._bump(st)


def _cb_delete_post(st, i: int) -> None:
    ss = st.session_state
    _writeback(ss)
    b = ss["batch"]
    if len(b["posts"]) <= 1:
        return
    for key in ("posts", "copies", "briefs"):
        if i < len(b[key]):
            b[key].pop(i)
    b["active"] = min(b["active"] if b["active"] < i else max(0, b["active"] - 1), len(b["posts"]) - 1)
    ss["carousel"] = b["posts"][b["active"]]
    _reset_history(ss)
    ps._bump(st)


def _cb_restyle(st, car, all_slides: bool, keep: bool):
    ss = st.session_state
    ps._commit(st)
    g = ss["guide"]
    rng = random.Random(uuid.uuid4().int)
    if all_slides:
        ss["carousel"] = redesign(car, g, rng, None, keep)
    else:
        redesign(car, g, rng, car.active_index, keep)
    _writeback(ss)
    ps._bump(st)


def _caption_text(batch: dict, i: int) -> str:
    c = batch["copies"][i] if i < len(batch.get("copies", [])) else {}
    tags = " ".join(c.get("hashtags", []))
    return (c.get("caption", "") + ("\n\n" + tags if tags else "")).strip()


def export_batch_zip(batch: dict, fmt: str = "PNG", scale: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        captions = []
        for i, car in enumerate(batch["posts"]):
            imgs = ps.render_carousel(car, scale)
            for j, im in enumerate(imgs):
                data, _m, ext = ps.export_image(im, fmt, 92)
                name = f"post_{i + 1:02d}.{ext}" if len(imgs) == 1 else f"carousel_{i + 1:02d}/slide_{j + 1:02d}.{ext}"
                zf.writestr(name, data)
            cap = _caption_text(batch, i)
            if cap:
                captions.append(f"--- Post {i + 1} ---\n{cap}\n")
        if captions:
            zf.writestr("captions.txt", "\n".join(captions))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Streamlit: callbacks for the quick editor
# ---------------------------------------------------------------------------

def _rerun(st) -> None:
    (getattr(st, "rerun", None) or st.experimental_rerun)()


def apply_text_preset(style, name: str) -> None:
    for k, v in ps.TEXT_PRESETS[name].items():
        setattr(style, k, style.color if v == "@color" else v)


def _cb_style_preset(st, layer, key):
    name = st.session_state.get(key)
    if name in ps.TEXT_PRESETS:
        ps._commit(st)
        apply_text_preset(layer.style, name)
        ps._bump(st)


def _cb_del_text(st, spec, layer):
    ps._commit(st)
    spec.text_layers[:] = [t for t in spec.text_layers if t is not layer]
    ps._bump(st)


def _cb_add_text(st, spec, key):
    ps._commit(st)
    tl = ps.make_text_layer(st.session_state.get(key, "Headline"), spec.get_palette())
    spec.text_layers.append(tl)
    st.session_state["pending_focus"] = f"text:{tl.id}"
    ps._bump(st)


def spec_ratio(spec) -> float:
    W, H = _fmt_size(spec)
    return W / H


def _cb_add_shape(st, spec, key):
    ps._commit(st)
    kind = st.session_state.get(key, "star")
    sh = ps.ShapeLayer(kind=kind, x=0.7, y=0.1, w=0.16, h=0.16 * spec_ratio(spec), label=kind.title())
    spec.shapes.append(sh)
    st.session_state["pending_focus"] = f"shape:{sh.id}"
    ps._bump(st)


def _cb_del_shape(st, spec, sh):
    ps._commit(st)
    spec.shapes[:] = [s for s in spec.shapes if s is not sh]
    ps._bump(st)


def _cb_del_pic(st, spec, pic):
    ps._commit(st)
    spec.pictures[:] = [p for p in spec.pictures if p is not pic]
    ps._bump(st)


def _cb_contact_all(st, car, spec):
    ss = st.session_state
    ps._commit(st)
    g, ct = ss["guide"], spec.contact
    g.phone, g.email, g.website = ct.phone, ct.email, ct.website
    targets = [s for c in (ss["batch"]["posts"] if ss.get("batch") else [car]) for s in c.slides]
    for s in targets:
        if s is spec:
            continue
        c = s.contact
        c.phone, c.email, c.website = ct.phone, ct.email, ct.website
        c.show_phone, c.show_email, c.show_website = ct.show_phone, ct.show_email, ct.show_website
        fit_contact_color(s)
    ps._bump(st)


def _cb_contact_color(st, spec):
    ps._commit(st)
    fit_contact_color(spec)
    ps._bump(st)


def _cb_smart_logo(st, spec, mode):
    ps._commit(st)
    smart_logo(spec, random.Random(), mode)
    _fit_brand_mark(spec, st.session_state["guide"].brand_name)
    ps._bump(st)


def _cb_logo_all(st, car, spec):
    ss = st.session_state
    ps._commit(st)
    if spec.logo.src != "brand-mark":
        ss["logo_img"] = spec.logo.image
    targets = [s for c in (ss["batch"]["posts"] if ss.get("batch") else [car]) for s in c.slides]
    for s in targets:
        if s is not spec:
            s.logo.image, s.logo.name, s.logo.src = spec.logo.image, spec.logo.name, spec.logo.src
            smart_logo(s, random.Random(), ss["guide"].logo_mode)
            _fit_brand_mark(s, ss["guide"].brand_name)
    ss["thumbs"] = {}
    ps._bump(st)


# ---------------------------------------------------------------------------
# Streamlit: quick editors (open beside the canvas when an element is clicked)
# ---------------------------------------------------------------------------

def _edit_text(st, spec, layer):
    ss = st.session_state
    k = lambda n: f"aiT_{layer.id}_{n}_{ss['gen']}"
    s = layer.style
    layer.text = st.text_area("Text", layer.text, key=k("t"), height=100)
    c1, c2 = st.columns(2)
    s.size = ps._sl(c1, "Size", 10, 300, int(s.size), 1, k("sz"))
    fams = list(ps.FONT_FAMILIES)
    s.family = c2.selectbox("Font", fams, index=fams.index(s.family) if s.family in fams else 0,
                            format_func=lambda x: ps.FONT_FAMILIES[x], key=k("f"))
    t = st.columns(4)
    s.bold, s.italic = t[0].checkbox("Bold", s.bold, key=k("b")), t[1].checkbox("Italic", s.italic, key=k("i"))
    s.uppercase, s.highlight = t[2].checkbox("CAPS", s.uppercase, key=k("c")), t[3].checkbox("Button", s.highlight, key=k("h"))
    s.align = ps._sb(st, "Align", ["left", "center", "right"], s.align, k("al"))
    custom = st.checkbox("Custom colour", s.color_role == "custom", key=k("cc"))
    if custom:
        s.color_role, s.color = "custom", ps._cp(st, "Text colour", s.color, k("col"))
    else:
        roles = ["ink", "muted", "accent", "on_bg"]
        s.color_role = st.selectbox("Template colour", roles, index=roles.index(s.color_role) if s.color_role in roles else 0, key=k("role"))
    if s.highlight:
        s.highlight_color = ps._cp(st, "Button / highlight colour", s.highlight_color, k("hc"))
    st.selectbox("Style preset", list(ps.TEXT_PRESETS), index=None, placeholder="Apply a ready-made style...", key=k("pre"),
                 on_change=_cb_style_preset, args=(st, layer, k("pre")))
    p = st.columns(3)
    layer.x = ps._sl(p[0], "X", -0.3, 1.2, layer.x, 0.005, k("x"))
    layer.y = ps._sl(p[1], "Y", -0.3, 1.2, layer.y, 0.005, k("y"))
    layer.w = ps._sl(p[2], "Box width", 0.1, 1.2, layer.w, 0.01, k("w"))
    s.opacity = ps._sl(st, "Opacity", 0.05, 1.0, s.opacity, 0.05, k("op"))
    st.button("🗑 Delete this text", key=k("del"), on_click=_cb_del_text, args=(st, spec, layer))


def _edit_contact(st, car, spec):
    ss = st.session_state
    ct = spec.contact
    k = lambda n: f"aiC_{spec.id}_{n}_{ss['gen']}"
    ct.enabled = st.checkbox("Show contact bar on this slide", ct.enabled, key=k("on"))
    ct.phone = st.text_input("Phone", ct.phone, key=k("ph"))
    ct.email = st.text_input("Email", ct.email, key=k("em"))
    ct.website = st.text_input("Website", ct.website, key=k("web"))
    f = st.columns(3)
    ct.show_phone, ct.show_email = f[0].checkbox("Phone", ct.show_phone, key=k("sp")), f[1].checkbox("Email", ct.show_email, key=k("se"))
    ct.show_website = f[2].checkbox("Website", ct.show_website, key=k("sw"))
    m = st.columns(2)
    ct.align = ps._sb(m[0], "Align", ["left", "center", "right"], ct.align, k("al"))
    ct.size = ps._sl(m[1], "Size", 12, 40, int(ct.size), 1, k("sz"))
    custom_y = st.checkbox("Custom vertical position", ct.y is not None, key=k("cy"))
    ct.y = ps._sl(st, "Y", 0.0, 1.0, ct.y if ct.y is not None else 0.92, 0.005, k("y")) if custom_y else None
    if not ct.auto_color:
        ct.color = ps._cp(st, "Text colour", ct.color, k("col"))
    b = st.columns(2)
    b[0].button("Auto colour", key=k("ac"), on_click=_cb_contact_color, args=(st, spec))
    b[1].button("Apply to all posts", key=k("all"), on_click=_cb_contact_all, args=(st, car, spec), type="primary")


def _edit_logo(st, car, spec):
    ss = st.session_state
    lg = spec.logo
    k = lambda n: f"aiL_{spec.id}_{n}_{ss['gen']}"
    lg.visible = st.checkbox("Show logo", lg.visible, key=k("vis"))
    if lg.src == "brand-mark":
        st.caption("This is an auto-generated brand mark. Upload your logo to replace it.")
    up = st.file_uploader("Upload / replace logo", type=["png", "jpg", "jpeg", "webp"], key=k("up"))
    if up is not None and lg.src != ps._tag(up):
        im = ps._ingest(up)
        if im is not None:
            lg.image, lg.src = im, ps._tag(up)
            smart_logo(spec, random.Random(), ss["guide"].logo_mode)
    mode = st.selectbox("Placement", LOGO_MODES, key=k("mode"))
    st.button("📍 Smart place / apply", key=k("sp"), on_click=_cb_smart_logo, args=(st, spec, mode))
    c = st.columns(3)
    lg.auto = False
    lg.x = ps._sl(c[0], "X", 0.0, 1.0, lg.x, 0.005, k("x"))
    lg.y = ps._sl(c[1], "Y", 0.0, 1.0, lg.y, 0.005, k("y"))
    lg.w = ps._sl(c[2], "Size", 0.04, 0.6, lg.w, 0.005, k("w"))
    ps._style_controls(st, lg, k)
    st.button("Use this logo on all posts", key=k("all"), on_click=_cb_logo_all, args=(st, car, spec))


def _edit_shape(st, spec, sh):
    ss = st.session_state
    k = lambda n: f"aiS_{sh.id}_{n}_{ss['gen']}"
    sh.kind = ps._sb(st, "Shape", ps.SHAPE_KINDS, sh.kind, k("kind"))
    roles = ["peek", "card", "cta", "ink", "bg", "custom"]
    sh.color_role = st.selectbox("Colour source", roles, index=roles.index(sh.color_role) if sh.color_role in roles else 0, key=k("role"))
    if sh.color_role == "custom":
        sh.color = ps._cp(st, "Colour", sh.color, k("col"))
    sh.filled = st.checkbox("Filled", sh.filled, key=k("fill"))
    p = st.columns(2)
    sh.x, sh.y = ps._sl(p[0], "X", -0.3, 1.2, sh.x, 0.005, k("x")), ps._sl(p[1], "Y", -0.3, 1.2, sh.y, 0.005, k("y"))
    p = st.columns(2)
    sh.w, sh.h = ps._sl(p[0], "Width", 0.01, 1.5, sh.w, 0.005, k("w")), ps._sl(p[1], "Height", 0.01, 1.5, sh.h, 0.005, k("h"))
    p = st.columns(2)
    sh.rotation = ps._sl(p[0], "Rotation", -180.0, 180.0, sh.rotation, 1.0, k("rot"))
    sh.alpha = ps._sl(p[1], "Opacity", 0.05, 1.0, sh.alpha, 0.05, k("al"))
    st.button("🗑 Delete shape", key=k("del"), on_click=_cb_del_shape, args=(st, spec, sh))


def _quick_editor(st, car, spec, focus: str) -> None:
    ss = st.session_state
    kind, _, oid = focus.partition(":")
    if kind == "text":
        layer = next((t for t in spec.text_layers if t.id == oid), None)
        if layer:
            st.markdown(f"**Editing · {layer.label}**")
            _edit_text(st, spec, layer)
    elif kind == "contact":
        st.markdown("**Editing · Contact details**")
        _edit_contact(st, car, spec)
    elif kind == "logo":
        st.markdown("**Editing · Logo**")
        _edit_logo(st, car, spec)
    elif kind == "shape":
        sh = next((s for s in spec.shapes if s.id == oid), None)
        if sh:
            st.markdown(f"**Editing · {sh.label}**")
            _edit_shape(st, spec, sh)
    elif kind == "pic":
        pic = next((p for p in spec.pictures if p.id == oid), None)
        if pic:
            st.markdown(f"**Editing · {pic.label}**")
            ps._picture_controls(st, pic, lambda n: f"aiP_{pic.id}_{n}_{ss['gen']}")
            st.button("🗑 Delete picture", key=f"aiPd_{pic.id}_{ss['gen']}", on_click=_cb_del_pic, args=(st, spec, pic))
    elif kind == "bg":
        bg = spec.background
        st.markdown("**Editing · Background picture**")
        k = lambda n: f"aiB_{spec.id}_{n}_{ss['gen']}"
        labels = {"auto": "Auto", "full": "Full-bleed", "custom": "Custom box"}
        bg.placement = st.selectbox("Placement", list(labels), index=list(labels).index(bg.placement) if bg.placement in labels else 0,
                                    format_func=lambda x: labels[x], key=k("pl"))
        ps._picture_controls(st, bg, k, with_box=bg.placement == "custom")


# ---------------------------------------------------------------------------
# Streamlit: content from the app's Generate tab
# ---------------------------------------------------------------------------

def _app_text(ss) -> str:
    posts = ss.get("generated_posts")
    if posts:
        return "\n\n".join(str(p) for p in posts)
    return ss.get("generated_post_text") or ""


def _app_copies(ss, g: Guidelines) -> list[dict]:
    fmt = ss.get("generated_post_format", "Post")
    posts = ss.get("generated_posts")
    if posts:
        return parse_app_content(_app_text(ss), fmt, g.max_headline_words, posts)
    text = _app_text(ss)
    copies = parse_app_content(text, fmt, g.max_headline_words)
    return [_app_copy_payload(text, fmt, g.max_headline_words)] if len(copies) == 1 else copies


def _sync_fca_generated_content(st) -> None:
    """Designs new content from the Generate tab automatically (once per new text)."""
    ss = st.session_state
    text = _app_text(ss)
    if not text:
        return
    format_type = ss.get("generated_post_format", "Post")
    category = ss.get("generated_post_category", "Protection")
    source_id = hashlib.sha256(f"{format_type}\0{category}\0{text}".encode()).hexdigest()
    if ss.get("studio_app_copy_id") == source_id:
        return
    ss["studio_app_copy_id"] = source_id
    g: Guidelines = ss["guide"]
    g.kind = "Carousel" if format_type == "Carousel" else "Single posts"
    g.topic = category
    copies = _app_copies(ss, g)
    if g.kind == "Carousel":
        g.slides = len(copies[0]["slides"])
    else:
        g.count = len(copies)
    batch = build_batch(copies, g, random.Random(uuid.uuid4().int), ss.get("logo_img"), ss.get("photo_img"))
    batch["source"] = "app"
    _set_batch(st, batch)
    ss["ai_note"] = f"Designed the latest {format_type.lower()} content from the Generate tab."


# ---------------------------------------------------------------------------
# Streamlit: create panel
# ---------------------------------------------------------------------------

_CSS = """
<style>
:root{--ps-accent:#4f46e5;--ps-accent-2:#7c3aed;--ps-ink:#0f172a;--ps-muted:#64748b;--ps-line:rgba(100,116,139,.22)}
.ps-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;margin:.2rem 0 1rem 0}
.ps-hero h1{font-size:1.9rem;font-weight:800;letter-spacing:-.02em;margin:0;line-height:1.15}
.ps-hero h1 span{background:linear-gradient(90deg,var(--ps-accent),var(--ps-accent-2));-webkit-background-clip:text;background-clip:text;color:transparent}
.ps-hero p{margin:.25rem 0 0 0;color:var(--ps-muted);font-size:.95rem}
.ps-chip{display:inline-flex;align-items:center;gap:.35rem;padding:.18rem .6rem;border-radius:999px;font-size:.78rem;font-weight:600;
 border:1px solid var(--ps-line);margin:0 .3rem .3rem 0;white-space:nowrap}
.ps-chip.ok{background:rgba(16,185,129,.12);color:#047857;border-color:rgba(16,185,129,.35)}
.ps-chip.off{background:rgba(100,116,139,.10);color:var(--ps-muted)}
.ps-chip.warn{background:rgba(245,158,11,.12);color:#b45309;border-color:rgba(245,158,11,.35)}
.ps-step{font-size:.72rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--ps-accent);margin-bottom:.15rem}
.ps-title{font-size:1.15rem;font-weight:700;margin:0 0 .5rem 0}
.ps-card-title{font-weight:700;font-size:.92rem;line-height:1.3;margin:.35rem 0 .1rem 0;min-height:2.4em;
 display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ps-meta{color:var(--ps-muted);font-size:.78rem;margin-bottom:.35rem}
.ps-empty{border:1.5px dashed var(--ps-line);border-radius:16px;padding:2.2rem 1.5rem;text-align:center;color:var(--ps-muted)}
.ps-empty b{color:inherit;font-size:1.05rem}
div[data-testid="stImage"] img{border-radius:12px;box-shadow:0 1px 2px rgba(15,23,42,.08),0 8px 24px rgba(15,23,42,.10)}
</style>
"""


def _box(st):
    try:
        return st.container(border=True)
    except TypeError:
        return st.container()


def _chip(text: str, kind: str) -> str:
    return f'<span class="ps-chip {kind}">{text}</span>'


def _engine_status(g: Guidelines) -> str:
    copy = _chip("✓ Claude copy & art direction", "ok") if ai_key(g) else _chip("Offline writer & art director", "off")
    p = g.image_provider
    if p.startswith("OpenAI"):
        img = _chip("✓ OpenAI images", "ok") if image_key(g) else _chip("OpenAI key missing → offline art", "warn")
    elif p.startswith("Pollinations"):
        img = _chip("✓ Pollinations FLUX images", "ok")
    elif p.startswith("Flat"):
        img = _chip("Flat colours", "off")
    else:
        img = _chip("Offline generative art", "off")
    return copy + img


def _ui_engine(st, g: Guidelines) -> None:
    with st.expander("⚙️ AI engine", expanded=not ai_key(g) and not st.session_state.get("batch")):
        st.caption("Keys stay in this browser session only. You can also set ANTHROPIC_API_KEY / OPENAI_API_KEY in "
                   "Streamlit secrets or the environment.")
        a1, a2 = st.columns([3, 2])
        g.ai_key = a1.text_input("Claude API key (writes the copy and art-directs every design)", g.ai_key, type="password",
                                 key="g_aikey", placeholder="Using secret / environment" if secret("ANTHROPIC_API_KEY") else "sk-ant-...")
        g.ai_model = a2.text_input("Claude model", g.ai_model or DEFAULT_MODEL, key="g_aimodel")
        b1, b2, b3 = st.columns([3, 2, 2])
        g.image_provider = b1.selectbox("Background images", IMAGE_PROVIDERS,
                                        index=IMAGE_PROVIDERS.index(g.image_provider) if g.image_provider in IMAGE_PROVIDERS else 0,
                                        key="g_imgprov", help="Online images are unique AI pictures for every slide; offline art is "
                                                              "instant and free. Failed images fall back to offline art.")
        if g.image_provider.startswith("OpenAI"):
            g.image_key = b2.text_input("OpenAI API key", g.image_key, type="password", key="g_imgkey",
                                        placeholder="Using secret / environment" if secret("OPENAI_API_KEY") else "sk-...")
            g.image_model = b3.text_input("Image model", g.image_model or "gpt-image-1", key="g_imgmodel")
        styles_ui = ["Auto (AI chooses)"] + list(ps.ART_STYLES)
        layouts_ui = ["Auto (AI chooses)"] + list(LAYOUTS)
        c1, c2 = st.columns(2)
        g.art_style = c1.selectbox("Offline art style", styles_ui, index=styles_ui.index(g.art_style) if g.art_style in styles_ui else 0,
                                   key="g_astyle")
        g.layout = c2.selectbox("Layout", layouts_ui, index=layouts_ui.index(g.layout) if g.layout in layouts_ui else 0,
                                format_func=lambda k: LAYOUTS.get(k, k), key="g_layout")


def _ui_rules(st, ss, g: Guidelines) -> None:
    with st.expander("🎨 Brand, rules & contact details"):
        r1, r2, r3 = st.columns(3)
        g.brand_name = r1.text_input("Brand / business name", g.brand_name, key="g_brand")
        moods = ["auto"] + list(MOODS)
        g.mood = r2.selectbox("Design mood", moods, index=moods.index(g.mood) if g.mood in moods else 0, key="g_mood")
        g.decor = r3.selectbox("Decoration", DECOR_LEVELS, index=DECOR_LEVELS.index(g.decor) if g.decor in DECOR_LEVELS else 1,
                               key="g_decor")
        d1, d2 = st.columns(2)
        g.must_include = d1.text_input("Must include words (comma separated)", g.must_include, key="g_must")
        g.avoid = d2.text_input("Words to avoid (comma separated)", g.avoid, key="g_avoid")
        e1, e2, e3 = st.columns(3)
        g.max_headline_words = e1.slider("Max headline words", 3, 16, int(g.max_headline_words), key="g_mhw")
        g.max_body_words = e2.slider("Max body words", 8, 60, int(g.max_body_words), key="g_mbw")
        g.language = e3.text_input("Language", g.language, key="g_lang")
        g.voice_notes = st.text_input("Voice notes (optional)", g.voice_notes, key="g_voice",
                                      placeholder="e.g. warm, no jargon, UK spelling, never promise returns")
        f1, f2, f3 = st.columns(3)
        g.logo_mode = f1.selectbox("Logo placement", LOGO_MODES, index=LOGO_MODES.index(g.logo_mode), key="g_logo_mode")
        g.safe_zones = f2.checkbox("Respect story safe zones", g.safe_zones, key="g_safe")
        g.hashtags = f3.checkbox("Write hashtags", g.hashtags, key="g_tags")
        g.use_brand_colors = st.checkbox("Use my brand colours", g.use_brand_colors, key="g_ubc")
        if g.use_brand_colors:
            q1, q2 = st.columns(2)
            g.brand_primary = q1.color_picker("Primary", g.brand_primary, key="g_bp")
            g.brand_secondary = q2.color_picker("Secondary", g.brand_secondary, key="g_bs")
        st.markdown("**Contact details**")
        g.show_contact = st.checkbox("Add contact bar", g.show_contact, key="g_showc")
        i1, i2, i3 = st.columns(3)
        g.phone = i1.text_input("Phone", g.phone, key="g_phone")
        g.email = i2.text_input("Email", g.email, key="g_email")
        g.website = i3.text_input("Website", g.website, key="g_website")
        if g.kind == "Carousel":
            g.contact_on = st.radio("Show contact on", ["Last slide only", "All slides"],
                                    index=["Last slide only", "All slides"].index(g.contact_on), horizontal=True, key="g_conton")
        st.markdown("**Brand assets**")
        u1, u2 = st.columns(2)
        lg = u1.file_uploader("Logo (PNG with transparency is best)", type=["png", "jpg", "jpeg", "webp"], key="g_logo")
        if lg is not None and ss.get("logo_tag") != ps._tag(lg):
            im = ps._ingest(lg)
            if im is not None:
                ss["logo_img"], ss["logo_tag"] = im, ps._tag(lg)
        ph = u2.file_uploader("Own background photo (optional, replaces AI art)", type=["png", "jpg", "jpeg", "webp"], key="g_photo")
        if ph is not None and ss.get("photo_tag") != ps._tag(ph):
            im = ps._ingest(ph)
            if im is not None:
                ss["photo_img"], ss["photo_tag"] = im.convert("RGB"), ps._tag(ph)
        if ss.get("photo_img") is not None and st.button("Remove photo", key="g_photo_rm"):
            ss["photo_img"] = ss["photo_tag"] = None


def _run_generation(st, g: Guidelines, use_app: bool) -> None:
    ss = st.session_state
    rng = random.Random(g.seed or uuid.uuid4().int)
    notes: list[str] = []
    bar = st.progress(0.0, text="Starting...")
    try:
        if use_app:
            copies = _app_copies(ss, g)
            if g.kind == "Single posts" and ss.get("generated_post_format") == "Carousel":
                copies = [{"slides": [s], "caption": "", "hashtags": [], "mood": "", "source": "app"} for s in copies[0]["slides"]]
            if g.kind == "Carousel" and len(copies) > 1:
                copies = [{"slides": [c["slides"][0] for c in copies], "caption": "", "hashtags": [], "mood": "", "source": "app"}]
        elif ai_key(g):
            bar.progress(0.05, text="Claude is writing your copy...")
            try:
                copies = ai_copy(g)
            except Exception as exc:
                notes.append(f"AI copywriting unavailable ({exc}); used the built-in writer.")
                copies = offline_copy(g)
        else:
            copies = offline_copy(g)
        batch = build_batch(copies, g, rng, ss.get("logo_img"), ss.get("photo_img"),
                            progress=lambda p, m: bar.progress(min(1.0, p), text=m))
        batch["notes"] = notes + batch["notes"]
        batch["source"] = "app" if use_app else ("ai" if copies and copies[0].get("source") == "ai" else "offline")
        _set_batch(st, batch)
        bar.empty()
        n = len(batch["posts"])
        msg = (f"Carousel ready - {len(batch['posts'][0].slides)} slides" if g.kind == "Carousel"
               else f"{n} unique post{'s' if n != 1 else ''} ready")
        (getattr(st, "toast", None) or st.success)(f"✨ {msg}")
    except Exception as exc:
        bar.empty()
        st.error(f"Couldn't generate the designs: {exc}")


def _ui_create(st) -> None:
    ss = st.session_state
    g: Guidelines = ss["guide"]
    app_text = _app_text(ss)
    with _box(st):
        st.markdown('<div class="ps-step">Step 1 · Create</div><div class="ps-title">What should we make?</div>', unsafe_allow_html=True)
        st.markdown(_engine_status(g), unsafe_allow_html=True)
        sources = (["Approved content from the Generate tab", "Write new content with AI"] if app_text
                   else ["Write new content with AI"])
        src = st.radio("Content source", sources, horizontal=True, key="g_src", label_visibility="collapsed") if len(sources) > 1 else sources[0]
        use_app = src.startswith("Approved")
        if use_app:
            st.caption(f"Approved {ss.get('generated_post_format', 'Post').lower()} content for "
                       f"**{ss.get('generated_post_category', 'your category')}** is used word-for-word.")
            with st.expander("Show approved content"):
                st.text(app_text[:3000])
        else:
            g.topic = st.text_area("Topic or brief", g.topic, key="g_topic", height=80,
                                   placeholder="e.g. Why income protection matters for self-employed parents")
        k1, k2 = st.columns([2, 3])
        g.kind = k1.radio("Output", KINDS, index=KINDS.index(g.kind) if g.kind in KINDS else 0, horizontal=True, key="g_kind",
                          help="Single posts: every post is a separate design. Carousel: one post with swipeable slides.")
        if not use_app:
            if g.kind == "Carousel":
                g.slides = k2.slider("Number of slides", 3, 10, int(_clamp_int(g.slides, 3, 10)), key="g_nslides")
            else:
                g.count = k2.slider("Number of posts", 1, 12, int(_clamp_int(g.count, 1, 12)), key="g_nposts")
        plats = list(PLATFORMS)
        p1, p2, p3 = st.columns(3)
        g.platform = p1.selectbox("Platform / size", plats, index=plats.index(g.platform), key="g_plat", help=PLATFORMS[g.platform][1])
        g.goal = p2.selectbox("Goal", GOALS, index=GOALS.index(g.goal) if g.goal in GOALS else 0, key="g_goal")
        g.tone = p3.selectbox("Tone", TONES, index=TONES.index(g.tone) if g.tone in TONES else 0, key="g_tone")
        if not use_app:
            q1, q2 = st.columns(2)
            g.audience = q1.text_input("Audience", g.audience, key="g_aud")
            g.cta = q2.text_input("Call to action", g.cta, key="g_cta")
        _ui_engine(st, g)
        _ui_rules(st, ss, g)
        if use_app:
            label = "✨ Design approved content"
        elif g.kind == "Carousel":
            label = f"✨ Generate carousel · {g.slides} slides"
        else:
            label = f"✨ Generate {g.count} unique post{'s' if g.count != 1 else ''}"
        go = st.button(label, type="primary", key="g_go", use_container_width=True,
                       disabled=not (use_app or g.topic.strip()))
        if not use_app and not g.topic.strip():
            st.caption("Add a topic to start.")
    if go:
        _run_generation(st, g, use_app)


def _clamp_int(v, lo, hi) -> int:
    return int(max(lo, min(hi, int(v))))


# ---------------------------------------------------------------------------
# Streamlit: results gallery (single posts) and carousel strip
# ---------------------------------------------------------------------------

def _thumb(ss, car: ps.CarouselSpec, scale: float = 0.3) -> Image.Image:
    cache = ss.setdefault("thumbs", {})
    key = hashlib.md5(ps._signature(car).encode()).hexdigest()
    if key not in cache:
        if len(cache) > 60:
            cache.clear()
        cache[key] = ps.render(car.slides[0], scale=scale)
    return cache[key]


def _ui_results(st) -> None:
    ss = st.session_state
    b = ss.get("batch")
    if not b:
        st.markdown('<div class="ps-empty"><b>Your designs will appear here</b><br>'
                    'Choose single posts or a carousel above, then press Generate. '
                    'Every post gets its own AI-directed design with your text on it.</div>', unsafe_allow_html=True)
        return
    for note in b.get("notes", [])[:3]:
        st.info(note, icon="ℹ️")
    flash = ss.pop("flash_ai", None)
    if flash:
        st.info(flash, icon="ℹ️")
    posts = b["posts"]
    with _box(st):
        is_car = b["kind"] == "Carousel"
        h1, h2 = st.columns([3, 2])
        h1.markdown(f'<div class="ps-step">Step 2 · Review</div><div class="ps-title">'
                    f'{"Your carousel" if is_car else f"{len(posts)} unique post" + ("s" if len(posts) != 1 else "")}</div>',
                    unsafe_allow_html=True)
        with h2:
            c1, c2 = st.columns(2)
            zfmt = c1.selectbox("Format", ["PNG", "JPEG"], key="zip_fmt", label_visibility="collapsed")
            if c2.button("Prepare ZIP", key="zip_all", use_container_width=True, help="All posts at full size + captions.txt"):
                with st.spinner("Rendering everything..."):
                    _writeback(ss)
                    ss["batch_zip"] = (export_batch_zip(b, zfmt), b["id"], ps._signature(ss["carousel"]))
            z = ss.get("batch_zip")
            if z and z[1] == b["id"]:
                st.download_button("⬇ Download all (ZIP)", z[0], file_name="posts.zip", mime="application/zip",
                                   key="zip_dl", use_container_width=True, type="primary")
        if is_car:
            st.caption("Swipe order is shown below. Click a slide to edit it.")
            ps._ui_strip(st, ss["carousel"], None)
            return
        per = 3 if posts[0].slides[0].fmt in ("landscape", "twitter", "youtube") else 4
        for start in range(0, len(posts), per):
            cols = st.columns(per)
            for col, i in zip(cols, range(start, min(len(posts), start + per))):
                car = posts[i]
                spec = car.slides[0]
                active = i == b["active"]
                brief = b["briefs"][i] if i < len(b.get("briefs", [])) else {}
                with col:
                    ps._img(st, _thumb(ss, car))
                    head = next((t.text for t in spec.text_layers if t.kind == "headline"), spec.name or f"Post {i + 1}")
                    st.markdown(f'<div class="ps-card-title">{"● " if active else ""}{head}</div>'
                                f'<div class="ps-meta">Post {i + 1} · {LAYOUTS.get(brief.get("layout", ""), "").split(" - ")[0]}'
                                f' · {brief.get("fonts", "")}</div>', unsafe_allow_html=True)
                    a, r, d = st.columns([3, 1, 1])
                    a.button("✓ Editing" if active else "Edit", key=f"pick_{i}_{ss['gen']}", on_click=_cb_pick, args=(st, i),
                             type="primary" if active else "secondary", use_container_width=True)
                    r.button("🎲", key=f"rd_{i}_{ss['gen']}", help="New unique design (text kept)", on_click=_cb_redesign_post,
                             args=(st, i), use_container_width=True)
                    d.button("🗑", key=f"del_{i}_{ss['gen']}", help="Remove this post", on_click=_cb_delete_post, args=(st, i),
                             use_container_width=True, disabled=len(posts) <= 1)


# ---------------------------------------------------------------------------
# Streamlit: canvas with click-to-select
# ---------------------------------------------------------------------------

def _canvas(st, car, spec, fkey: str) -> None:
    ss = st.session_state
    img = ps.render(spec, scale=ss.get("preview_scale", 0.55), guides=ss.get("guides", False),
                    page=(car.active_index, len(car.slides)), indicator=car.indicator, swipe=car.swipe)
    img = _outline(img, spec, ss.get(fkey))
    W, H = _fmt_size(spec)
    dispw = int(min(620, 680 * W / H))
    if HAS_CLICK:
        val = streamlit_image_coordinates(img, key=f"cv_{spec.id}_{ss['gen']}", width=dispw)
        lck = f"lc_{spec.id}_{ss['gen']}"
        if val and val.get("unix_time") != ss.get(lck):
            ss[lck] = val.get("unix_time")
            hit = hit_test(spec, val["x"] / max(1, val["width"]), val["y"] / max(1, val["height"]))
            if hit and hit != ss.get(fkey):
                ss["pending_focus"] = hit
                _rerun(st)
        st.caption("👆 Click any text, logo, contact bar, shape or picture to edit it.")
    else:
        ps._img(st, img)
        st.caption("Tip: `pip install streamlit-image-coordinates` lets you click elements on the canvas to edit them.")
    st.caption(f"{spec.fmt} · {W}×{H}px")


def _ui_caption(st, ss) -> None:
    b = ss.get("batch")
    if not b:
        return
    i = b["active"]
    if i >= len(b.get("copies", [])):
        return
    c = b["copies"][i]
    with st.expander("📝 Caption & hashtags"):
        c["caption"] = st.text_area("Caption", c.get("caption", ""), key=f"cap_{b['id']}_{i}", height=90)
        tags = st.text_input("Hashtags", " ".join(c.get("hashtags", [])), key=f"tags_{b['id']}_{i}")
        c["hashtags"] = re.findall(r"#\w+", tags)
        if c["caption"] or c["hashtags"]:
            st.code(_caption_text(b, i), language=None)


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

def post_studio_ai_ui() -> None:
    import streamlit as st

    try:
        st.set_page_config(layout="wide", page_title="AI Post, Carousel & Reel Studio", page_icon="🎨")
    except Exception:
        pass
    ss = st.session_state
    st.markdown(ps._CSS + _CSS, unsafe_allow_html=True)
    st.markdown('<div class="ps-hero"><div><h1><span>AI</span> Post, Carousel &amp; Reel Studio</h1>'
                '<p>AI-written copy, AI-directed designs - every post unique, every word readable.</p></div></div>',
                unsafe_allow_html=True)

    ss.setdefault("gen", 0)
    ss.setdefault("hist", [])
    ss.setdefault("redo", [])
    ss.setdefault("exports", {})
    ss.setdefault("guide", Guidelines())
    for k, v in dataclasses.asdict(Guidelines()).items():          # sessions from older versions
        if not hasattr(ss["guide"], k):
            setattr(ss["guide"], k, v)

    _writeback(ss)
    _sync_fca_generated_content(st)
    _ui_create(st)
    _ui_results(st)

    if not ss.get("batch"):
        return
    _writeback(ss)
    car: ps.CarouselSpec = ss["carousel"]
    car.active_index = int(ps._clamp(car.active_index, 0, len(car.slides) - 1))
    ps._commit(st)

    gaps = ps.missing_fonts()
    if gaps:
        st.warning(f"⚠️ {len(gaps)} fonts weren't found ({', '.join(gaps)}). Put the `assets/fonts/` folder next to post_studio.py.")

    spec = car.slides[car.active_index]
    fkey = f"focus_{spec.id}"
    if "pending_focus" in ss:
        ss[fkey] = ss.pop("pending_focus")

    def K(name: str) -> str:
        return f"{name}_{spec.id}_{ss['gen']}"

    b = ss["batch"]
    with _box(st):
        what = (f"Slide {car.active_index + 1} of {len(car.slides)}" if len(car.slides) > 1 else f"Post {b['active'] + 1} of {len(b['posts'])}")
        st.markdown(f'<div class="ps-step">Step 3 · Edit</div><div class="ps-title">{what}</div>', unsafe_allow_html=True)
        if len(car.slides) > 1:
            ps._ui_toolbar(st, car, spec, K)
        r = st.columns([2, 2, 3])
        keep = r[2].checkbox("Keep current colours", False, key=f"keep_{ss['gen']}")
        r[0].button("🎲 New look: this slide" if len(car.slides) > 1 else "🎲 New look", key=f"rs1_{ss['gen']}",
                    on_click=_cb_restyle, args=(st, car, False, keep), use_container_width=True)
        if len(car.slides) > 1:
            r[1].button("🎲 New look: all slides", key=f"rsA_{ss['gen']}", on_click=_cb_restyle, args=(st, car, True, keep),
                        use_container_width=True)

        left, right = st.columns([6, 5], gap="large")
        with left:
            canvas_ct = st.container()
        with right:
            mode = st.radio("Editor", ["Quick edit", "Advanced"], horizontal=True, key=f"mode_{ss['gen']}", label_visibility="collapsed")
            if mode == "Quick edit":
                order, labels = all_options(spec)
                if ss.get(fkey) not in order and order:
                    ss[fkey] = next((f"text:{t.id}" for t in spec.text_layers if t.kind == "headline" and f"text:{t.id}" in order), order[0])
                if order:
                    st.selectbox("Selected element", order, key=fkey, format_func=lambda o: labels.get(o, o))
                a = st.columns([2, 1, 2, 1])
                a[0].selectbox("Add text", ps.TEXT_KINDS, key=f"addk_{spec.id}", label_visibility="collapsed")
                a[1].button("➕", key=f"addt_{spec.id}_{ss['gen']}", help="Add text", on_click=_cb_add_text, args=(st, spec, f"addk_{spec.id}"))
                a[2].selectbox("Add shape", ps.SHAPE_KINDS, index=ps.SHAPE_KINDS.index("star"), key=f"adds_{spec.id}", label_visibility="collapsed")
                a[3].button("➕", key=f"addsh_{spec.id}_{ss['gen']}", help="Add shape", on_click=_cb_add_shape, args=(st, spec, f"adds_{spec.id}"))
                if order:
                    _quick_editor(st, car, spec, ss[fkey])
            else:
                tabs = st.tabs(["Design", "Text", "Elements", "Images", "Effects"])
                for tab, fn in zip(tabs, (ps._ui_design, ps._ui_text, ps._ui_elements, ps._ui_images, ps._ui_effects)):
                    with tab:
                        fn(st, car, spec, K)
            with st.expander("✅ Guidelines check"):
                for ok, msg in check_guidelines(spec, ss["guide"]):
                    st.write(("✅ " if ok else "⚠️ ") + msg)
            _ui_caption(st, ss)
        with canvas_ct:
            _canvas(st, car, spec, fkey)

    with _box(st):
        st.markdown('<div class="ps-step">Step 4 · Export</div>', unsafe_allow_html=True)
        ps._ui_export(st, car, spec, K)
    _writeback(ss)
    ps._commit(st)                 # record this run's edits first, so Undo is available immediately
    ps._ui_sidebar(st, ss["carousel"])


if __name__ == "__main__":
    post_studio_ai_ui()
