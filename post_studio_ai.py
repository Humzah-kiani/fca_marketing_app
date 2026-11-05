"""
Integrated design UI for the FCA marketing app.
Run with:   streamlit run app.py

The app's generated and validated content is passed into this module, which
adds the following design features on top of post_studio.py:
  * AI copywriting (Claude API, optional) + built-in offline writer as fallback.
  * A design director: every generation picks a NEW random template, palette, fonts, text style,
    decorative shapes, gradient and effects. Each carousel slide gets its own separate layout.
  * Smart logo placement: candidate positions are scored against text, contact bar, shapes and image
    busyness, and the best spot is used (story safe-zones respected).
  * Click-to-edit canvas: click any text / logo / contact bar / shape on the live canvas and its
    editor opens right beside it (contact phone, e-mail and website are editable and can be applied to all slides).
  * Guidelines: platform, goal, tone, audience, word limits, must-include / avoid words, brand colours,
    safe zones, decoration level, contact rules... plus an automatic guidelines check of every slide.
"""

from __future__ import annotations

import dataclasses
import hashlib
import random
import re
import uuid
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
# Guidelines / constants
# ---------------------------------------------------------------------------

PLATFORMS = {
    "Instagram Post 4:5": ("portrait", "Best feed reach. Keep key text inside the centre 80%."),
    "Instagram Square": ("square", "Classic 1:1. Works well on every platform."),
    "Instagram Story / Reel": ("story", "Keep text out of the top 14% and bottom 20% (app UI covers them)."),
    "LinkedIn Post": ("landscape", "Professional tone, short headline, clear value."),
    "Facebook Post": ("landscape", "Large readable text; mobile first."),
    "Pinterest Pin": ("pinterest", "Tall 2:3 pins, headline-first design."),
    "X / Twitter": ("twitter", "Wide 16:9, very short headline."),
    "YouTube Thumbnail": ("youtube", "Huge text (max ~5 words) and high contrast."),
}
GOALS = ["Awareness", "Lead generation", "Sales / promotion", "Education", "Engagement", "Announcement"]
TONES = ["Friendly & confident", "Professional & trustworthy", "Bold & energetic", "Calm & reassuring",
         "Luxury & elegant", "Playful & fun", "Inspirational"]
LOGO_MODES = ["Smart (auto)", "Top left", "Top right", "Bottom left", "Bottom right", "Top center", "Bottom center"]

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


@dataclass
class Guidelines:
    topic: str = "5 simple ways to protect your family's financial future"
    kind: str = "Carousel"
    slides: int = 5
    platform: str = "Instagram Post 4:5"
    goal: str = "Education"
    tone: str = "Friendly & confident"
    audience: str = "Working families aged 30-50"
    brand_name: str = "Studio Brand"
    voice_notes: str = ""
    must_include: str = ""
    avoid: str = ""
    cta: str = "Book a free consultation"
    max_headline_words: int = 8
    max_body_words: int = 24
    language: str = "English"
    mood: str = "auto"
    use_brand_colors: bool = False
    brand_primary: str = "#3b3fb0"
    brand_secondary: str = "#ffbe0b"
    cohesive: bool = True
    safe_zones: bool = True
    decor: str = "Medium"
    show_contact: bool = True
    contact_on: str = "Last slide only"
    phone: str = "+44 20 0000 0000"
    email: str = "hello@yourbrand.com"
    website: str = "www.yourbrand.com"
    logo_mode: str = "Smart (auto)"
    hashtags: bool = True
    seed: int = 0


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_EMOJI = re.compile("[\U00010000-\U0010ffff\u2190-\u21ff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d]")


def _clean(s: str) -> str:
    return _EMOJI.sub("", str(s or "")).strip()


def _limit(s: str, n: int) -> str:
    w = s.split()
    return s if len(w) <= n or n <= 0 else " ".join(w[:n]).rstrip(",;:- ")


def detect_mood(text: str) -> str:
    t = f" {text.lower()} "
    best = max(KEYWORDS, key=lambda m: sum(k in t for k in KEYWORDS[m]))
    return best if any(k in t for k in KEYWORDS[best]) else random.choice(list(MOODS))


# ---------------------------------------------------------------------------
# Placeholder copy for an empty studio before the app has generated content.
# ---------------------------------------------------------------------------

def fallback_copy(g: Guidelines, n: int) -> dict:
    t = g.topic.strip().rstrip(".") or "Your topic"
    short = _limit(t, 9)
    tips = ["Start with a clear goal", "Know who you are speaking to", "Keep it simple and visual", "Be consistent every week",
            "Measure what works", "Ask an expert for help", "Review and improve regularly", "Take the first step today"]
    bodies = ["Write down what success looks like before you begin.", "Understand needs first, then shape your message.",
              "One idea per slide makes your point stick.", "Small steady actions beat occasional big ones.",
              "Track results and double down on what works.", "A short conversation can save you time and money.",
              "Revisit your plan every few months.", "Momentum starts with one decision."]
    cta = g.cta or "Learn more"
    if n == 1:
        slides = [{"badge": g.goal.split()[0], "headline": short, "body": f"A simple guide for {g.audience or 'you'}.", "cta": cta}]
    else:
        slides = [{"badge": "Guide", "headline": short, "body": "Swipe to see how it works.", "cta": "Swipe for more"}]
        for i in range(n - 2):
            slides.append({"badge": f"Tip {i + 1}", "headline": tips[i % len(tips)], "body": bodies[i % len(bodies)], "cta": ""})
        slides.append({"badge": "Next step", "headline": "Ready to get started?", "body": f"Talk to {g.brand_name} today.", "cta": cta})
    return {"slides": slides, "mood": "", "caption": f"{t}. {g.goal} by {g.brand_name}.",
            "hashtags": ["#tips", "#guide"], "source": "fallback"}

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
    return ps.TextLayer(text="   \u2022   ".join(parts), x=0.05, y=y, w=0.90,
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
        out.append(("bg", "Background photo", box))
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


# ---------------------------------------------------------------------------
# Smart logo placement + contact colour fitting
# ---------------------------------------------------------------------------

def smart_logo(spec, rng: random.Random | None = None, mode: str = "Smart (auto)") -> None:
    """Scores candidate positions (overlap with content, busyness, corner preference) and picks the best."""
    rng = rng or random.Random()
    lg = spec.logo
    if not lg.visible:
        return
    W, H = _fmt_size(spec)
    aspect = lg.image.width / lg.image.height if lg.image is not None else 1.0
    w = 0.15 if H > W * 1.2 else 0.13
    if w * W / aspect / H > 0.12:
        w = 0.12 * H * aspect / W
    hf = w * W / aspect / H
    story = spec.fmt in ("story", "reel")
    top, bot, m = (0.155 if story else 0.04), (0.78 if story else 0.96) - hf, 0.045
    cands = {"Top left": (m, top), "Top right": (1 - m - w, top), "Bottom left": (m, bot), "Bottom right": (1 - m - w, bot),
             "Top center": ((1 - w) / 2, top), "Bottom center": ((1 - w) / 2, bot)}
    if mode in cands:
        best = cands[mode]
    else:
        pref = {"Top left": 0.0, "Top right": 0.25, "Bottom right": 0.5, "Bottom left": 0.45, "Top center": 0.7, "Bottom center": 1.0}
        obstacles = []
        for key, _l, b in element_boxes(spec, skip_logo=True):
            if key.startswith("text:") or key == "contact":
                obstacles.append((b, 12.0))
            elif key.startswith("shape:"):
                obstacles.append((b, 3.0))
        lum = np.asarray(_plain_render(spec).convert("L"), dtype=np.float32) / 255.0
        ih, iw = lum.shape
        best, best_s = cands["Top left"], 1e9
        for name, (x, y) in cands.items():
            box, area = (x, y, x + w, y + hf), w * hf
            s = sum(_overlap(box, ob) / area * wt for ob, wt in obstacles)
            reg = lum[int(y * ih): max(int(y * ih) + 2, int((y + hf) * ih)), int(x * iw): max(int(x * iw) + 2, int((x + w) * iw))]
            s += 6.0 * float(reg.std()) + pref[name] + rng.random() * 0.25
            if s < best_s:
                best, best_s = (x, y), s
    lg.auto, lg.x, lg.y, lg.w = False, round(best[0], 3), round(best[1], 3), round(w, 3)


def fit_contact_color(spec) -> None:
    ct, cl = spec.contact, _contact_layer(spec)
    if cl is None:
        return
    W, H = _fmt_size(spec)
    b = _text_box(cl, W, H)
    im = _plain_render(spec)
    iw, ih = im.size
    x0, y0 = int(b[0] * iw), int(b[1] * ih)
    crop = im.crop((x0, y0, max(int(b[2] * iw), x0 + 1), max(int(b[3] * ih), y0 + 1)))
    mean = tuple(int(v) for v in np.asarray(crop).reshape(-1, 3).mean(0))
    ct.auto_color, ct.color = False, ps._contrast_ink(mean)


# ---------------------------------------------------------------------------
# Design director
# ---------------------------------------------------------------------------

PAIRS = [("display", "body"), ("serif", "body"), ("elegant_serif", "body"), ("condensed", "body"),
         ("rounded", "body"), ("display", "serif"), ("condensed", "serif"), ("rounded", "rounded")]
TEXT_TEMPLATES = [k for k in ps.TEMPLATE_LABELS if k not in ps._PHOTO_TEMPLATES]
WIDE_OK = ["banner", "minimal_frame", "split_diagonal", "wave", "spotlight", "arc", "geometric_diag", "blocks", "cut_corner", "floating_card"]
PHOTO_FULL = ["banner", "minimal_frame", "spotlight", "glassmorphism", "floating_card", "cut_corner"]
CENTERED = {"spotlight", "glassmorphism", "minimal_frame", "burst", "polaroid"}


def pick_template(rng, used, fmt, photo):
    pool = PHOTO_FULL if photo else (WIDE_OK if fmt in ("landscape", "youtube", "twitter") else TEXT_TEMPLATES)
    fresh = [t for t in pool if t not in used[-3:]] or pool
    return rng.choice(fresh)


def brand_palette(g, rng):
    a, b = ps._from_hex(g.brand_primary, (60, 70, 200)), ps._from_hex(g.brand_secondary, (255, 190, 60))
    dark = ps._mix(a, (0, 0, 0), 0.55)
    if rng.random() < 0.5:
        return ps.make_palette(a, dark, b, b, key="custom", label="Brand")
    return ps.make_palette(dark, a, b, ps._mix(b, (255, 255, 255), 0.3), key="custom", label="Brand")


def pick_palette(g, rng, mood):
    if g.use_brand_colors:
        return brand_palette(g, rng)
    if rng.random() < 0.5:
        pool = MOODS.get(mood) or [k for k in ps.THEMES if k != "custom"]
        return dataclasses.replace(ps.THEMES[rng.choice(pool)])
    return ps.random_palette(rng)


def variant_palette(p, rng):
    r = rng.random()
    if r < 0.35:
        return dataclasses.replace(p)
    if r < 0.60:
        return ps.make_palette(p.card, p.bg, p.peek, p.cta_bg, key="custom", label="Variant")
    if r < 0.80:
        return ps.make_palette(p.bg, p.card, p.cta_bg, p.peek, key="custom", label="Variant")
    return ps.make_palette(p.bg, p.card, ps._mix(p.peek, (255, 255, 255), 0.25), p.cta_bg, key="custom", label="Variant")


def _layout_in(spec, area, gap: float = 0.022) -> None:
    W, H = _fmt_size(spec)
    x0, y0, x1, y1 = area
    layers = [t for t in spec.text_layers if t.kind in ("headline", "body", "cta") and t.visible and t.text.strip()]
    if not layers:
        return
    for t in layers:
        t.x, t.w = x0, x1 - x0
    for _ in range(18):
        total = sum(ps.measure_text_layer(t, W, H) / H for t in layers) + gap * (len(layers) - 1)
        if total <= (y1 - y0):
            break
        for t in layers:
            t.style.size = max(12, int(t.style.size * 0.93))
    y = y0
    for t in layers:
        hh = ps.measure_text_layer(t, W, H) / H
        t.y = max(y, y1 - hh) if t.kind == "cta" else y
        y = t.y + hh + gap


def apply_text_preset(style, name: str) -> None:
    for k, v in ps.TEXT_PRESETS[name].items():
        setattr(style, k, style.color if v == "@color" else v)


def add_decor(spec, rng, area, reserved_extra, count: int) -> None:
    W, H = _fmt_size(spec)
    geo = ps.template_geometry(spec.template, spec.fmt)
    reserved = [(area[0] - 0.015, area[1] - 0.08, area[2] + 0.015, area[3] + 0.02), *reserved_extra,
                (0, geo.contact_y - 0.01, 1, min(1.0, geo.contact_y + 0.07))]
    if geo.photo_box:
        reserved.append(geo.photo_box)
    kinds = ["star", "burst", "ring", "pill", "triangle", "diamond", "hexagon", "ellipse", "arrow"]
    for _ in range(count):
        for _try in range(16):
            kind = rng.choice(kinds)
            if kind == "pill":
                wf = rng.uniform(0.15, 0.3)
                hf = wf * 0.18 * W / H
            elif kind == "arrow":
                wf = rng.uniform(0.1, 0.2)
                hf = wf * 0.6 * W / H
            else:
                wf = rng.uniform(0.07, 0.2)
                hf = wf * W / H
            x = rng.choice([rng.uniform(-0.03, 0.04), rng.uniform(0.05, max(0.06, 0.95 - wf)), 1 - wf + rng.uniform(-0.04, 0.04)])
            y = rng.choice([rng.uniform(-0.03, 0.04), rng.uniform(0.05, max(0.06, 0.95 - hf)), 1 - hf + rng.uniform(-0.04, 0.04)])
            box = (x, y, x + wf, y + hf)
            if any(_overlap(box, r) > 0 for r in reserved):
                continue
            spec.shapes.append(ps.ShapeLayer(kind=kind, x=round(x, 3), y=round(y, 3), w=round(wf, 3), h=round(hf, 3),
                                             color_role=rng.choice(["peek", "cta", "peek"]), alpha=round(rng.uniform(0.45, 0.95), 2),
                                             rotation=rng.choice([0, rng.uniform(-35, 35)]), filled=rng.random() < 0.7,
                                             stroke=int(rng.uniform(5, 12)), label=kind.title()))
            reserved.append(box)
            break


def build_slide(c, g, rng, idx, n, fmt, pal, used, contact, logo_img, logo_name, photo=None, logo_mode="Smart (auto)"):
    last, cover = (idx == n - 1 and n > 1), idx == 0
    tpl = pick_template(rng, used, fmt, photo is not None)
    used.append(tpl)
    headline = _limit(_clean(c.get("headline", "")), g.max_headline_words) or "Your headline"
    body = _limit(_clean(c.get("body", "")), g.max_body_words * 2 if g.max_body_words else 0)
    cta = _clean(c.get("cta", ""))
    spec = ps.new_post(headline, body, cta, fmt=fmt, template=tpl)
    spec.custom_palette, spec.theme, spec.name = dataclasses.replace(pal), "custom", f"Slide {idx + 1}"
    geo = ps.template_geometry(tpl, fmt)

    # typography
    hl = next(t for t in spec.text_layers if t.kind == "headline")
    bd = next((t for t in spec.text_layers if t.kind == "body"), None)
    ct_l = next((t for t in spec.text_layers if t.kind == "cta"), None)
    fam_h, fam_b = rng.choice(PAIRS)
    hl.style.family = fam_h
    hl.style.size = (84 if (cover or n == 1) else 66) - (12 if len(headline) > 38 else 0)
    if bd:
        bd.style.family, bd.style.size = fam_b, 32
    words = len(headline.split())
    preset = rng.choices(["Clean", "Soft Shadow", "Bold Pop", "Spaced Caps", "Highlighter"], weights=[4, 3, 2, 2 if words <= 4 else 0, 1])[0]
    apply_text_preset(hl.style, preset)
    if preset == "Spaced Caps":
        hl.style.letter_spacing = 4.0
    if tpl in CENTERED or rng.random() < 0.2:
        for t in spec.text_layers:
            t.style.align = "center"
    if ct_l:
        s = ct_l.style
        s.highlight, s.highlight_color, s.color_role = True, geo.accent, "custom"
        s.color, s.size, s.uppercase, s.letter_spacing = ps._contrast_ink(geo.accent), 30, True, 2.0

    # safe-zone aware layout
    x0, y0, x1, y1 = geo.text_area
    if fmt in ("story", "reel") and g.safe_zones:
        y0, y1 = max(y0, 0.17), min(y1, 0.74)
    area = (x0, y0, x1, y1)
    _layout_in(spec, area)

    # badge
    badge = _clean(c.get("badge", "")) or (f"{idx + 1:02d}" if n > 1 else "")
    reserved = []
    if badge and y0 - 0.06 >= 0.02:
        st = ps.TextStyle(family="display", size=22, color=ps._contrast_ink(geo.accent), bold=True, highlight=True,
                          highlight_color=geo.accent, uppercase=True, letter_spacing=3.0, align=spec.text_layers[0].style.align)
        b = ps.TextLayer(text=badge, x=x0, y=y0 - 0.06, w=x1 - x0, style=st, label="Badge")
        spec.text_layers.append(b)
        reserved.append(_text_box(b, *_fmt_size(spec)))
    spec.sel_text = hl.id

    # decoration, gradient, effects, photo
    add_decor(spec, rng, area, reserved, {"None": 0, "Light": 1, "Medium": 2, "Rich": 4}.get(g.decor, 2))
    if photo is not None:
        bg = spec.background
        bg.image, bg.placement = photo, "full"
        bg.focus_x = idx / (n - 1) if n > 1 else 0.5
        bg.zoom = round(rng.uniform(1.0, 1.5), 2)
        bg.tint, bg.tint_alpha = (0, 0, 0), round(rng.uniform(0.3, 0.45), 2)
        spec.effects.look = rng.choice(["none", "vivid", "warm", "cool", "dreamy"])
    elif rng.random() < 0.38:
        spec.gradient = ps.Gradient(True, pal.bg, ps._mix(pal.bg, pal.peek, rng.uniform(0.25, 0.5)), round(rng.uniform(0, 360)),
                                    "radial" if rng.random() < 0.3 else "linear")
    spec.effects.grain = rng.choice([0, 0, 0.05, 0.1])
    if ps._lum(pal.bg) < 0.5:
        spec.effects.vignette = rng.choice([0, 0, 0.15, 0.3])

    # contact + logo
    spec.contact = ps._clone(contact)
    spec.logo = ps.ImageLayer(image=logo_img, name=logo_name)
    fit_contact_color(spec)
    smart_logo(spec, rng, logo_mode)
    return spec


def build_carousel(copy: dict, g: Guidelines, rng, logo_img=None, photo=None) -> ps.CarouselSpec:
    fmt, slides = PLATFORMS[g.platform][0], copy["slides"]
    n = len(slides)
    mood = copy.get("mood") if copy.get("mood") in MOODS else (g.mood if g.mood in MOODS else detect_mood(g.topic))
    base = pick_palette(g, rng, mood)
    contact = ps.ContactInfo(phone=g.phone, email=g.email, website=g.website, show_phone=bool(g.phone), show_email=bool(g.email),
                             show_website=bool(g.website), enabled=g.show_contact, align="center", size=20)
    used, out = [], []
    for i, c in enumerate(slides):
        pal = variant_palette(base, rng) if g.cohesive else pick_palette(g, rng, mood)
        ctc = ps._clone(contact)
        ctc.enabled = g.show_contact and (n == 1 or g.contact_on == "All slides" or i == n - 1)
        out.append(build_slide(c, g, rng, i, n, fmt, pal, used, ctc, logo_img, g.brand_name, photo, g.logo_mode))
    return ps.CarouselSpec(slides=out, indicator=rng.choice(["dots", "bar", "numbers"]) if n > 1 else "none", swipe=n > 1)


def slide_copy(spec) -> dict:
    def t(kind):
        l = next((x for x in spec.text_layers if x.kind == kind and x.text.strip()), None)
        return l.text if l else ""
    return {"headline": t("headline"), "body": t("body"), "cta": t("cta"),
            "badge": next((x.text for x in spec.text_layers if x.label == "Badge"), "")}


def restyle(car, idx: int, g: Guidelines, rng, pal=None, keep_palette: bool = False) -> None:
    old = car.slides[idx]
    n = len(car.slides)
    if pal is None:
        pal = old.get_palette() if keep_palette else variant_palette(pick_palette(g, rng, detect_mood(g.topic)), rng)
    used = [s.template for j, s in enumerate(car.slides) if j != idx]
    new = build_slide(slide_copy(old), g, rng, idx, n, old.fmt, pal, used, old.contact, old.logo.image, old.logo.name,
                      old.background.image, g.logo_mode)
    new.name, new.duration, new.logo.visible = old.name, old.duration, old.logo.visible
    car.slides[idx] = new


# ---------------------------------------------------------------------------
# Guidelines checker
# ---------------------------------------------------------------------------

def _rl(c):
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _ratio(a, b) -> float:
    la, lb = _rl(a), _rl(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def check_guidelines(spec, g: Guidelines) -> list[tuple[bool, str]]:
    res: list[tuple[bool, str]] = []
    W, H = _fmt_size(spec)
    story = spec.fmt in ("story", "reel")
    geo, pal = ps.template_geometry(spec.template, spec.fmt), spec.get_palette()
    im = _plain_render(spec)
    iw, ih = im.size
    layers = [l for l in spec.text_layers if l.visible and l.text.strip()]
    cl = _contact_layer(spec)
    boxes = [(l, _text_box(l, W, H)) for l in layers]
    if cl:
        boxes.append((cl, _text_box(cl, W, H)))

    off = [l.label for l, b in boxes if b[0] < 0.04 or b[2] > 0.96 or b[3] > 0.97 or b[1] < 0.02]
    res.append((not off, "All text inside the safe margins" if not off else f"Outside safe margin: {', '.join(off)}"))
    if story:
        bad = [l.label for l, b in boxes if b[1] < 0.14 or b[3] > 0.80]
        res.append((not bad, "Story/Reel UI zones are clear" if not bad else f"In story UI zone: {', '.join(bad)}"))
    small = [l.label for l in layers if l.style.size < 22 and l.label != "Badge"]
    res.append((not small, "Font sizes are readable" if not small else f"Text too small: {', '.join(small)}"))
    clash = [f"{a.label}/{b.label}" for i, (a, ba) in enumerate(boxes) for (b, bb) in boxes[i + 1:] if _overlap(ba, bb) > 0.0008]
    res.append((not clash, "No overlapping text" if not clash else f"Text overlaps: {', '.join(clash[:3])}"))
    low = []
    for l, b in boxes:
        if l.style.highlight:
            continue
        x0, y0 = int(b[0] * iw), int(b[1] * ih)
        crop = im.crop((x0, y0, max(int(b[2] * iw), x0 + 1), max(int(b[3] * ih), y0 + 1)))
        mean = tuple(np.asarray(crop).reshape(-1, 3).mean(0))
        col = spec.contact.color if (cl is not None and l is cl and not spec.contact.auto_color) else ps._role_color(l.style, geo, pal)
        if l is cl and spec.contact.auto_color:
            col = geo.contact_ink
        if _ratio(col, mean) < 3.0:
            low.append(getattr(l, "label", "Contact"))
    res.append((not low, "Text contrast is good" if not low else f"Low contrast: {', '.join(low)}"))
    hl = next((l for l in layers if l.kind == "headline"), None)
    if hl:
        n = len(hl.text.split())
        res.append((n <= g.max_headline_words, f"Headline length {n}/{g.max_headline_words} words"))
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
    d.rectangle(r, outline=(0, 200, 255, 255), width=3)
    for cx, cy in ((r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3])):
        d.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), fill=(0, 200, 255, 255))
    return im


# ---------------------------------------------------------------------------
# Streamlit: callbacks
# ---------------------------------------------------------------------------

def _rerun(st) -> None:
    (getattr(st, "rerun", None) or st.experimental_rerun)()


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


def _cb_add_shape(st, spec, key):
    ps._commit(st)
    kind = st.session_state.get(key, "star")
    sh = ps.ShapeLayer(kind=kind, x=0.7, y=0.1, w=0.16, h=0.16 * spec_ratio(spec), label=kind.title())
    spec.shapes.append(sh)
    st.session_state["pending_focus"] = f"shape:{sh.id}"
    ps._bump(st)


def spec_ratio(spec) -> float:
    W, H = _fmt_size(spec)
    return W / H


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
    ss["g_phone"], ss["g_email"], ss["g_website"] = ct.phone, ct.email, ct.website
    for s in car.slides:
        if s is spec:
            continue
        c = s.contact
        c.phone, c.email, c.website = ct.phone, ct.email, ct.website
        c.show_phone, c.show_email, c.show_website = ct.show_phone, ct.show_email, ct.show_website
        c.size, c.align = ct.size, ct.align
        fit_contact_color(s)
    ps._bump(st)


def _cb_contact_color(st, spec):
    ps._commit(st)
    fit_contact_color(spec)
    ps._bump(st)


def _cb_smart_logo(st, spec, mode):
    ps._commit(st)
    smart_logo(spec, random.Random(), mode)
    ps._bump(st)


def _cb_logo_all(st, car, spec):
    ss = st.session_state
    ps._commit(st)
    ss["logo_img"] = spec.logo.image
    for s in car.slides:
        if s is not spec:
            s.logo.image, s.logo.name = spec.logo.image, spec.logo.name
            smart_logo(s, random.Random(), ss["guide"].logo_mode)
    ps._bump(st)


def _cb_restyle(st, car, all_slides: bool, keep: bool):
    ss = st.session_state
    ps._commit(st)
    g = ss["guide"]
    rng = random.Random(uuid.uuid4().int)
    if all_slides:
        base = car.slides[car.active_index].get_palette() if keep else pick_palette(g, rng, detect_mood(g.topic))
        for i in range(len(car.slides)):
            restyle(car, i, g, rng, pal=variant_palette(base, rng))
    else:
        restyle(car, car.active_index, g, rng, keep_palette=keep)
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
    b[1].button("Apply to ALL slides", key=k("all"), on_click=_cb_contact_all, args=(st, car, spec), type="primary")


def _edit_logo(st, car, spec):
    ss = st.session_state
    lg = spec.logo
    k = lambda n: f"aiL_{spec.id}_{n}_{ss['gen']}"
    lg.visible = st.checkbox("Show logo", lg.visible, key=k("vis"))
    up = st.file_uploader("Replace logo", type=["png", "jpg", "jpeg", "webp"], key=k("up"))
    if up is not None and lg.src != ps._tag(up):
        im = ps._ingest(up)
        if im is not None:
            lg.image, lg.src = im, ps._tag(up)
            smart_logo(spec, random.Random(), ss["guide"].logo_mode)
    if lg.image is None:
        lg.name = st.text_input("Placeholder name", lg.name, key=k("nm"))
    mode = st.selectbox("Placement", LOGO_MODES, key=k("mode"))
    st.button("📍 Smart place / apply", key=k("sp"), on_click=_cb_smart_logo, args=(st, spec, mode))
    c = st.columns(3)
    lg.auto = False
    lg.x = ps._sl(c[0], "X", 0.0, 1.0, lg.x, 0.005, k("x"))
    lg.y = ps._sl(c[1], "Y", 0.0, 1.0, lg.y, 0.005, k("y"))
    lg.w = ps._sl(c[2], "Size", 0.04, 0.6, lg.w, 0.005, k("w"))
    ps._style_controls(st, lg, k)
    st.button("Use this logo on ALL slides", key=k("all"), on_click=_cb_logo_all, args=(st, car, spec))


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
            st.markdown(f"**✏️ Editing: {layer.label}**")
            _edit_text(st, spec, layer)
    elif kind == "contact":
        st.markdown("**✏️ Editing: Contact info**")
        _edit_contact(st, car, spec)
    elif kind == "logo":
        st.markdown("**✏️ Editing: Logo**")
        _edit_logo(st, car, spec)
    elif kind == "shape":
        sh = next((s for s in spec.shapes if s.id == oid), None)
        if sh:
            st.markdown(f"**✏️ Editing: {sh.label}**")
            _edit_shape(st, spec, sh)
    elif kind == "pic":
        pic = next((p for p in spec.pictures if p.id == oid), None)
        if pic:
            st.markdown(f"**✏️ Editing: {pic.label}**")
            ps._picture_controls(st, pic, lambda n: f"aiP_{pic.id}_{n}_{ss['gen']}")
            st.button("🗑 Delete picture", key=f"aiPd_{pic.id}_{ss['gen']}", on_click=_cb_del_pic, args=(st, spec, pic))
    elif kind == "bg":
        bg = spec.background
        st.markdown("**✏️ Editing: Background photo**")
        k = lambda n: f"aiB_{spec.id}_{n}_{ss['gen']}"
        labels = {"auto": "Auto", "full": "Full-bleed", "custom": "Custom box"}
        bg.placement = st.selectbox("Placement", list(labels), index=list(labels).index(bg.placement) if bg.placement in labels else 0,
                                    format_func=lambda x: labels[x], key=k("pl"))
        ps._picture_controls(st, bg, k, with_box=bg.placement == "custom")


def _app_copy_payload(text: str, format_type: str, max_headline_words: int) -> dict:
    if format_type == "Carousel":
        slides = []
        for line in text.splitlines():
            match = re.match(r"\s*Slide\s+\d+\s*:\s*(.*)", line, re.IGNORECASE)
            if match:
                slides.append(match.group(1).strip())
            elif slides and line.strip():
                slides[-1] = f"{slides[-1]} {line.strip()}".strip()
        if not slides:
            slides = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    else:
        slides = [text.strip()]

    content = []
    for slide_text in slides:
        lines = [line.strip() for line in slide_text.splitlines() if line.strip()]
        if not lines:
            continue
        first_words = lines[0].split()
        split_at = max(1, max_headline_words)
        headline = " ".join(first_words[:split_at])
        remaining = [" ".join(first_words[split_at:]), *lines[1:]]
        content.append({
            "badge": "",
            "headline": headline,
            "body": "\n".join(part for part in remaining if part),
            "cta": "",
        })

    return {
        "slides": content or [{"badge": "", "headline": "Your post", "body": "", "cta": ""}],
        "mood": "",
        "caption": "",
        "hashtags": [],
        "source": "fca_app",
    }


def _sync_fca_generated_content(st) -> None:
    ss = st.session_state
    text = ss.get("generated_post_text")
    if not text:
        return

    format_type = ss.get("generated_post_format", "Post")
    category = ss.get("generated_post_category", "Protection")
    source_id = hashlib.sha256(f"{format_type}\0{category}\0{text}".encode()).hexdigest()
    if ss.get("studio_app_copy_id") == source_id:
        return

    g: Guidelines = ss["guide"]
    g.kind = "Carousel" if format_type == "Carousel" else "Single post"
    copy = _app_copy_payload(text, format_type, g.max_headline_words)
    g.slides = len(copy["slides"])
    g.topic = category
    max_body_words = max((len(slide["body"].split()) + 1) // 2 for slide in copy["slides"])
    g.max_body_words = max(g.max_body_words, max_body_words)
    ps._commit(st)
    ss["carousel"] = build_carousel(
        copy, g, random.Random(uuid.uuid4().int), ss.get("logo_img"), ss.get("photo_img")
    )
    ss["copy_meta"] = copy
    ss["ai_note"] = "Loaded the latest FCA-app generated content for design."
    ss["exports"] = {}
    ss["studio_app_copy_id"] = source_id
    ps._bump(st)


# ---------------------------------------------------------------------------
# Streamlit: generator panel
# ---------------------------------------------------------------------------

def _ui_generator(st) -> None:
    ss = st.session_state
    g: Guidelines = ss["guide"]
    with st.expander("Design settings", expanded=ss.get("first_run", True)):
        text = ss.get("generated_post_text", "")
        if text:
            st.caption(
                f"Designing the latest FCA-app generated {ss.get('generated_post_format', 'Post').lower()} "
                f"for {ss.get('generated_post_category', 'Protection')}."
            )
            st.text_area(
                "Generated copy",
                value=text,
                key=f"studio_copy_{ss.get('studio_app_copy_id', 'latest')}",
                height=120,
                disabled=True,
            )
        else:
            st.info("Generate and validate content in the Generate tab first; it will load here automatically.")

        plats = list(PLATFORMS)
        g.platform = st.selectbox("Platform / format", plats, index=plats.index(g.platform), key="g_plat")
        st.caption(f"💡 {PLATFORMS[g.platform][1]}")

        with st.expander("Design and review guidelines", expanded=False):
            moods = ["auto"] + list(MOODS)
            g.mood = st.selectbox("Design mood", moods, index=moods.index(g.mood) if g.mood in moods else 0, key="g_mood")
            g.brand_name = st.text_input("Brand / business name", g.brand_name, key="g_brand")
            d1, d2 = st.columns(2)
            g.must_include = d1.text_input("Must include words (comma separated)", g.must_include, key="g_must")
            g.avoid = d2.text_input("Words to avoid (comma separated)", g.avoid, key="g_avoid")
            e1, e2 = st.columns(2)
            g.max_headline_words = e1.slider("Max headline words", 3, 16, int(g.max_headline_words), key="g_mhw")
            g.max_body_words = e2.slider("Max body words", 8, 60, int(g.max_body_words), key="g_mbw")
            st.markdown("**Design rules**")
            f1, f2, f3 = st.columns(3)
            decs = ["None", "Light", "Medium", "Rich"]
            g.decor = f1.selectbox("Decoration level", decs, index=decs.index(g.decor), key="g_decor")
            g.logo_mode = f2.selectbox("Logo placement", LOGO_MODES, index=LOGO_MODES.index(g.logo_mode), key="g_logo_mode")
            g.seed = int(f3.number_input("Design seed (0 = new unique design every time)", 0, 10**9, int(g.seed), key="g_seed"))
            h1, h2, h3 = st.columns(3)
            g.cohesive = h1.checkbox("Cohesive carousel colours", g.cohesive, key="g_coh", help="Off = every slide gets a totally different palette")
            g.safe_zones = h2.checkbox("Respect story safe zones", g.safe_zones, key="g_safe")
            h3.caption("Copy remains as approved in the Generate tab.")
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
            g.contact_on = st.radio("Show contact on", ["Last slide only", "All slides"], index=["Last slide only", "All slides"].index(g.contact_on),
                                    horizontal=True, key="g_conton")
            st.markdown("**Brand assets**")
            u1, u2 = st.columns(2)
            lg = u1.file_uploader("Logo (PNG with transparency is best)", type=["png", "jpg", "jpeg", "webp"], key="g_logo")
            if lg is not None and ss.get("logo_tag") != ps._tag(lg):
                im = ps._ingest(lg)
                if im is not None:
                    ss["logo_img"], ss["logo_tag"] = im, ps._tag(lg)
            ph = u2.file_uploader("Background photo (optional, spans the carousel)", type=["png", "jpg", "jpeg", "webp"], key="g_photo")
            if ph is not None and ss.get("photo_tag") != ps._tag(ph):
                im = ps._ingest(ph)
                if im is not None:
                    ss["photo_img"], ss["photo_tag"] = im.convert("RGB"), ps._tag(ph)
            if ss.get("photo_img") is not None and st.button("Remove photo", key="g_photo_rm"):
                ss["photo_img"] = ss["photo_tag"] = None

        b1, b2 = st.columns([1, 3])
        go = b1.button("🎨 Redesign latest content", type="primary", key="g_go", disabled=not bool(text))
        if ss.get("ai_note"):
            b2.info(ss["ai_note"])
        if go:
            with st.spinner("Designing every slide..."):
                copy = _app_copy_payload(text, ss.get("generated_post_format", "Post"), g.max_headline_words)
                rng = random.Random(g.seed or uuid.uuid4().int)
                ps._commit(st)
                ss["carousel"] = build_carousel(copy, g, rng, ss.get("logo_img"), ss.get("photo_img"))
                ss["copy_meta"] = copy
                ss["exports"] = {}
                ss["first_run"] = False
                ps._bump(st)
            _rerun(st)


# ---------------------------------------------------------------------------
# Streamlit: canvas with click-to-select
# ---------------------------------------------------------------------------

def _canvas(st, car, spec, fkey: str) -> None:
    ss = st.session_state
    img = ps.render(spec, scale=ss.get("preview_scale", 0.55), guides=ss.get("guides", False),
                    page=(car.active_index, len(car.slides)), indicator=car.indicator, swipe=car.swipe)
    img = _outline(img, spec, ss.get(fkey))
    W, H = _fmt_size(spec)
    dispw = int(min(640, 700 * W / H))
    st.subheader("Live canvas")
    if HAS_CLICK:
        st.caption("👆 Click any text, logo, contact bar or shape on the canvas to edit it on the right.")
        val = streamlit_image_coordinates(img, key=f"cv_{spec.id}_{ss['gen']}", width=dispw)
        lck = f"lc_{spec.id}_{ss['gen']}"
        if val and val.get("unix_time") != ss.get(lck):
            ss[lck] = val.get("unix_time")
            hit = hit_test(spec, val["x"] / max(1, val["width"]), val["y"] / max(1, val["height"]))
            if hit and hit != ss.get(fkey):
                ss["pending_focus"] = hit
                _rerun(st)
    else:
        ps._img(st, img)
        st.caption("Install `streamlit-image-coordinates` to click elements on the canvas. Use the selector on the right meanwhile.")
    st.caption(f"{spec.fmt} · {W}×{H}px · template: {spec.template}")


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
    st.markdown(ps._CSS, unsafe_allow_html=True)
    st.markdown('<h1 class="studio-title">Post, Carousel &amp; Reel Studio</h1>', unsafe_allow_html=True)

    ss.setdefault("gen", 0)
    ss.setdefault("hist", [])
    ss.setdefault("redo", [])
    ss.setdefault("exports", {})
    ss.setdefault("guide", Guidelines())
    ss.setdefault("first_run", True)
    if "carousel" not in ss:
        g0 = ss["guide"]
        ss["carousel"] = build_carousel(fallback_copy(g0, g0.slides if g0.kind == "Carousel" else 1), g0, random.Random(uuid.uuid4().int))
    _sync_fca_generated_content(st)
    ps._commit(st)

    _ui_generator(st)

    car: ps.CarouselSpec = ss["carousel"]
    car.active_index = int(ps._clamp(car.active_index, 0, len(car.slides) - 1))
    ps._commit(st)

    gaps = ps.missing_fonts()
    if gaps:
        st.warning(f"⚠️ {len(gaps)} fonts weren't found ({', '.join(gaps)}). Put the `assets/fonts/` folder next to post_studio.py.")

    ps._ui_sidebar(st, car)
    strip_ct = st.container()
    spec = car.slides[car.active_index]
    fkey = f"focus_{spec.id}"
    if "pending_focus" in ss:
        ss[fkey] = ss.pop("pending_focus")

    def K(name: str) -> str:
        return f"{name}_{spec.id}_{ss['gen']}"

    st.divider()
    ps._ui_toolbar(st, car, spec, K)

    r = st.columns([2, 2, 2, 3])
    keep = r[3].checkbox("Keep current colours when restyling", False, key=f"keep_{ss['gen']}")
    r[0].button("🎲 New look: this slide", key=f"rs1_{ss['gen']}", on_click=_cb_restyle, args=(st, car, False, keep))
    r[1].button("🎲 New look: all slides", key=f"rsA_{ss['gen']}", on_click=_cb_restyle, args=(st, car, True, keep))

    left, right = st.columns([6, 5], gap="medium")
    with left:
        canvas_ct = st.container()
        export_ct = st.container()
    with right:
        mode = st.radio("Editor", ["Quick edit (click canvas)", "Advanced studio"], horizontal=True, key=f"mode_{ss['gen']}")
        if mode.startswith("Quick"):
            order, labels = all_options(spec)
            if ss.get(fkey) not in order and order:
                ss[fkey] = order[0]
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
        with st.expander("✅ Guidelines check for this slide"):
            for ok, msg in check_guidelines(spec, ss["guide"]):
                st.write(("✅ " if ok else "⚠️ ") + msg)

    with canvas_ct:
        _canvas(st, car, spec, fkey)
    with export_ct:
        ps._ui_export(st, car, spec, K)
    with strip_ct:
        ps._ui_strip(st, car)


if __name__ == "__main__":
    post_studio_ai_ui()