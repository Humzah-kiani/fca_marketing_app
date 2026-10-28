"""
post_studio.py  (v2 - advanced edition)
=======================================

A social-post, carousel and reel studio built on Pillow, NumPy, Streamlit and ImageIO.

What's new compared to v1
-------------------------
Rendering engine
  * Anti-aliased vector shapes (templates are no longer jagged) and true alpha blending.
  * 18 vector templates (8 new) + 14 palettes, gradient backgrounds (linear / radial).
  * Text: auto colour roles per template, shadow / glow, outline, gradient fill, letter
    spacing, uppercase, opacity, rotation, auto-fit-to-box, style presets.
  * Free vector shapes (rect, pill, star, burst, arrow, ...) with gradient, rotation, shadow.
  * Pictures: unlimited overlays with crop focus/zoom, rounded/circle masks, borders,
    shadow, rotation, brightness / contrast / saturation / blur / tint.
  * Whole-post looks (vivid, warm, noir, ...), vignette, film grain, top/bottom fades.
  * Contact bar, carousel page indicators (dots / bar / numbers) and swipe hint.
  * Smart colour: random palettes, colour harmonies, palette-from-image, auto contrast.
  * Auto-layout: text is stacked and shrunk to fit each template's text area.

Studio
  * Undo / redo, save / open whole projects (.json), bulk-create slides from text,
    template gallery, safe-zone guides, lazy exports (PNG / JPEG / WEBP, ZIP, PDF, reel).

Reels
  * Streaming frame renderer (low memory), Ken Burns modes, text-reveal animation,
    5 transitions, per-slide durations, 720p / full-size, MP4 with automatic GIF fallback.
"""

from __future__ import annotations

import base64
import colorsys
import copy
import dataclasses
import io
import json
import math
import os
import random
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterator, Sequence

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

try:  # imageio v2 API works for both imageio 2.x and 3.x
    import imageio.v2 as imageio
    HAS_IMAGEIO = True
except ImportError:  # pragma: no cover
    try:
        import imageio  # type: ignore
        HAS_IMAGEIO = True
    except ImportError:
        imageio = None  # type: ignore
        HAS_IMAGEIO = False

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
Box = tuple[float, float, float, float]

__all__ = [
    "Palette", "TextStyle", "TextLayer", "ShapeLayer", "ImageLayer", "BackgroundLayer",
    "Gradient", "Effects", "ContactInfo", "PostSpec", "CarouselSpec", "Geometry",
    "THEMES", "FORMATS", "TEMPLATES", "TEMPLATE_LABELS", "CATEGORY_PRESETS",
    "FONT_FAMILIES", "GRADIENT_PRESETS", "LOOKS", "TEXT_PRESETS",
    "new_post", "render", "default_logo", "build_post_canvas", "post_studio_ui",
    "render_carousel", "export_reel_video", "iter_reel_frames", "auto_layout",
    "random_palette", "harmony_palette", "palette_from_image", "make_palette",
    "project_to_bytes", "project_from_bytes", "export_image", "export_carousel_zip",
    "export_carousel_pdf", "missing_fonts",
]

# ---------------------------------------------------------------------------
# Small colour / math helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _lum(c: Sequence[int]) -> float:
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


def _mix(a: Sequence[int], b: Sequence[int], t: float) -> RGB:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def _contrast_ink(bg: Sequence[int], dark: RGB = (20, 20, 30), light: RGB = (255, 255, 255)) -> RGB:
    return dark if _lum(bg) > 0.56 else light


def _hsv(h: float, s: float, v: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, _clamp(s, 0, 1), _clamp(v, 0, 1))
    return (int(r * 255), int(g * 255), int(b * 255))


def _from_hex(s: str, fallback: RGB = (0, 0, 0)) -> RGB:
    s = s.lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback


def _hex(c: Sequence[int]) -> str:
    return "#%02x%02x%02x" % tuple(int(x) for x in c[:3])


def _ease(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

@dataclass
class Palette:
    key: str
    label: str
    bg: RGB
    card: RGB
    peek: RGB
    ink: RGB
    ink_muted: RGB
    on_bg: RGB
    cta_bg: RGB
    cta_ink: RGB
    pale: RGB
    pale_ink: RGB


def make_palette(bg: RGB, card: RGB, peek: RGB, cta: RGB | None = None, ink: RGB | None = None,
                 key: str = "custom", label: str = "Custom Palette") -> Palette:
    """Builds a complete palette from 3-4 base colours, deriving readable text colours."""
    ink = ink or _contrast_ink(card)
    cta = cta or peek
    pale = _mix(bg, (255, 255, 255), 0.86)
    return Palette(key, label, bg, card, peek, ink, _mix(ink, card, 0.28), _contrast_ink(bg),
                   cta, _contrast_ink(cta), pale, _contrast_ink(pale))


THEMES: dict[str, Palette] = {
    "periwinkle": Palette("periwinkle", "Periwinkle / Charcoal",
        bg=(107, 111, 196), card=(62, 59, 74), peek=(198, 194, 250),
        ink=(255, 255, 255), ink_muted=(216, 214, 224), on_bg=(255, 255, 255),
        cta_bg=(198, 194, 250), cta_ink=(48, 45, 60),
        pale=(238, 236, 252), pale_ink=(46, 44, 58)),
    "royal": Palette("royal", "Royal Blue / Mint",
        bg=(16, 70, 139), card=(126, 227, 152), peek=(138, 224, 246),
        ink=(12, 26, 20), ink_muted=(38, 66, 50), on_bg=(255, 255, 255),
        cta_bg=(12, 38, 72), cta_ink=(255, 255, 255),
        pale=(240, 250, 243), pale_ink=(14, 40, 26)),
    "plum": Palette("plum", "Plum / Coral",
        bg=(122, 87, 118), card=(232, 131, 107), peek=(214, 210, 208),
        ink=(255, 255, 255), ink_muted=(252, 232, 226), on_bg=(255, 255, 255),
        cta_bg=(255, 255, 255), cta_ink=(154, 72, 52),
        pale=(250, 236, 230), pale_ink=(96, 46, 34)),
    "mustard": Palette("mustard", "Charcoal / Mustard",
        bg=(34, 34, 40), card=(242, 194, 48), peek=(88, 58, 138),
        ink=(28, 26, 22), ink_muted=(74, 66, 40), on_bg=(255, 255, 255),
        cta_bg=(28, 26, 22), cta_ink=(242, 194, 48),
        pale=(250, 240, 210), pale_ink=(34, 32, 28)),
    "emerald": Palette("emerald", "Emerald / Soft Gold",
        bg=(18, 53, 36), card=(240, 235, 210), peek=(52, 114, 83),
        ink=(18, 40, 28), ink_muted=(70, 90, 78), on_bg=(255, 255, 255),
        cta_bg=(18, 53, 36), cta_ink=(240, 235, 210),
        pale=(240, 245, 242), pale_ink=(18, 53, 36)),
    "neon_cyber": Palette("neon_cyber", "Cyberpunk / Neon Cyan",
        bg=(15, 15, 26), card=(30, 30, 50), peek=(0, 242, 254),
        ink=(255, 255, 255), ink_muted=(180, 190, 210), on_bg=(255, 255, 255),
        cta_bg=(0, 242, 254), cta_ink=(15, 15, 26),
        pale=(40, 40, 70), pale_ink=(0, 242, 254)),
    "sunset_glow": Palette("sunset_glow", "Sunset / Orange Amber",
        bg=(45, 20, 35), card=(255, 107, 107), peek=(255, 217, 61),
        ink=(255, 255, 255), ink_muted=(255, 220, 210), on_bg=(255, 255, 255),
        cta_bg=(255, 217, 61), cta_ink=(45, 20, 35),
        pale=(70, 30, 50), pale_ink=(255, 217, 61)),
    "pastel_dream": Palette("pastel_dream", "Pastel Violet / Soft Pink",
        bg=(230, 224, 248), card=(255, 255, 255), peek=(247, 186, 207),
        ink=(40, 35, 60), ink_muted=(100, 90, 120), on_bg=(40, 35, 60),
        cta_bg=(247, 186, 207), cta_ink=(40, 35, 60),
        pale=(240, 235, 252), pale_ink=(40, 35, 60)),
    "monochrome": Palette("monochrome", "Monochrome Studio",
        bg=(20, 20, 20), card=(40, 40, 40), peek=(100, 100, 100),
        ink=(255, 255, 255), ink_muted=(180, 180, 180), on_bg=(255, 255, 255),
        cta_bg=(255, 255, 255), cta_ink=(20, 20, 20),
        pale=(60, 60, 60), pale_ink=(255, 255, 255)),
    "nordic_frost": Palette("nordic_frost", "Nordic Frost / Slate",
        bg=(216, 226, 236), card=(30, 41, 59), peek=(56, 189, 248),
        ink=(255, 255, 255), ink_muted=(203, 213, 225), on_bg=(30, 41, 59),
        cta_bg=(56, 189, 248), cta_ink=(30, 41, 59),
        pale=(241, 245, 249), pale_ink=(30, 41, 59)),
    "aurora": make_palette((12, 20, 48), (24, 38, 84), (94, 234, 212), (167, 139, 250),
                           key="aurora", label="Aurora / Teal Violet"),
    "candy_pop": make_palette((255, 122, 182), (255, 255, 255), (255, 214, 102), (88, 80, 236),
                              key="candy_pop", label="Candy Pop / Pink Yellow"),
    "ocean_deep": make_palette((7, 59, 76), (255, 253, 245), (17, 138, 178), (239, 71, 111),
                               key="ocean_deep", label="Ocean Deep / Coral Pop"),
    "retro_wave": make_palette((38, 14, 66), (74, 27, 112), (255, 56, 140), (255, 190, 11),
                               key="retro_wave", label="Retro Wave / Magenta Gold"),
    "forest_mist": make_palette((226, 236, 224), (47, 72, 52), (168, 198, 134), (214, 112, 66),
                                key="forest_mist", label="Forest Mist / Sage"),
    "custom": Palette("custom", "Custom Palette Bar",
        bg=(25, 25, 30), card=(45, 45, 55), peek=(255, 105, 180),
        ink=(255, 255, 255), ink_muted=(200, 200, 210), on_bg=(255, 255, 255),
        cta_bg=(255, 105, 180), cta_ink=(255, 255, 255),
        pale=(240, 240, 245), pale_ink=(20, 20, 30)),
}


def harmony_palette(base: RGB, mode: str = "complementary", dark: bool = True) -> Palette:
    """Generates a full palette from one base colour using classic colour-wheel harmonies."""
    h, s, _v = colorsys.rgb_to_hsv(*[x / 255 for x in base])
    s = max(s, 0.5)
    offs = {"complementary": (0.5, 0.5), "analogous": (0.08, -0.08), "triadic": (0.333, 0.667),
            "split": (0.42, 0.58), "mono": (0.0, 0.0)}.get(mode, (0.5, 0.5))
    if dark:
        bg, card = _hsv(h, s * 0.8, 0.20), _hsv(h, s * 0.6, 0.33)
    else:
        bg, card = _hsv(h, s * 0.28, 0.96), (255, 255, 255)
    peek = _hsv(h + offs[0], 0.72 if mode != "mono" else 0.45, 0.98)
    cta = _hsv(h + offs[1], 0.85, 1.0 if mode != "mono" else 0.85)
    return make_palette(bg, card, peek, cta, key="custom", label=f"{mode.title()} harmony")


def random_palette(rng: random.Random | None = None) -> Palette:
    rng = rng or random.Random()
    base = _hsv(rng.random(), rng.uniform(0.55, 0.95), rng.uniform(0.7, 1.0))
    mode = rng.choice(["complementary", "analogous", "triadic", "split", "mono"])
    return harmony_palette(base, mode, dark=rng.random() < 0.6)


def palette_from_image(img: Image.Image) -> Palette:
    """Extracts a palette from an image (dominant colours -> bg / card / accent / cta)."""
    small = img.convert("RGB").resize((96, 96))
    q = small.quantize(7, method=Image.Quantize.MEDIANCUT)
    raw = (q.getpalette() or [])[:21]
    cols = [tuple(raw[i:i + 3]) for i in range(0, len(raw) - 2, 3)]
    cols = [c for c in cols if len(c) == 3] or [(40, 40, 60), (80, 80, 120), (240, 120, 160)]

    def sat(c): return colorsys.rgb_to_hsv(*[x / 255 for x in c])[1]

    by_lum = sorted(cols, key=_lum)
    by_sat = sorted(cols, key=sat, reverse=True)
    bg = by_lum[0]
    card = by_lum[1] if len(by_lum) > 1 else _mix(bg, (255, 255, 255), 0.15)
    if _lum(bg) > 0.35:
        bg = _mix(bg, (0, 0, 0), 0.55)
    peek = by_sat[0]
    cta = by_sat[1] if len(by_sat) > 1 else peek
    return make_palette(bg, card, peek, cta, label="From image")


def _accent_for(surface: RGB, p: Palette) -> RGB:
    """Picks the accent text colour (CTA / peek / ink) that reads best on `surface`."""
    for cand in (p.cta_bg, p.peek):
        if abs(_lum(cand) - _lum(surface)) > 0.35:
            return cand
    return _contrast_ink(surface)


GRADIENT_PRESETS: dict[str, tuple[RGB, RGB, float]] = {
    "Aurora": ((20, 30, 80), (94, 234, 212), 60.0),
    "Sunset": ((255, 94, 98), (255, 195, 113), 45.0),
    "Ocean": ((0, 82, 212), (67, 206, 162), 120.0),
    "Candy": ((255, 117, 140), (190, 120, 255), 30.0),
    "Midnight": ((15, 12, 41), (48, 43, 99), 90.0),
    "Mint": ((17, 153, 142), (190, 245, 160), 70.0),
    "Gold Rush": ((60, 40, 10), (245, 190, 60), 135.0),
    "Berry": ((106, 17, 203), (255, 70, 150), 50.0),
}

CATEGORY_PRESETS: dict[str, dict[str, str]] = {
    "Protection": {"theme": "periwinkle", "template": "offset_card"},
    "Retirement": {"theme": "royal", "template": "card_photo"},
    "Pension": {"theme": "emerald", "template": "photo_side"},
    "Mortgage": {"theme": "plum", "template": "banner"},
    "Lifestyle": {"theme": "mustard", "template": "arc"},
    "Bold Promo": {"theme": "candy_pop", "template": "blocks"},
    "Night Launch": {"theme": "retro_wave", "template": "burst"},
    "Calm Wellness": {"theme": "forest_mist", "template": "wave"},
}

FORMATS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "reel": (1080, 1920),
    "landscape": (1200, 628),
    "pinterest": (1000, 1500),
    "youtube": (1280, 720),
    "twitter": (1600, 900),
}

# ---------------------------------------------------------------------------
# Fonts (bundled in ./assets/fonts, with graceful fallbacks)
# ---------------------------------------------------------------------------

FONT_FAMILIES: dict[str, str] = {
    "display": "Poppins (modern sans)",
    "serif": "Pagella (classic serif)",
    "body": "Liberation Sans (clean sans)",
    "elegant_serif": "Bookman (bold serif)",
    "rounded": "Adventor (geometric round)",
    "condensed": "Heros Condensed (narrow)",
    "mono": "Liberation Mono (typewriter)",
    "handwritten": "Chorus (calligraphic script)",
}


def _stack(prefix: str, *fallbacks: str) -> dict[str, tuple[str, ...]]:
    return {
        "regular": (f"{prefix}-regular", f"{prefix}-Regular", *fallbacks),
        "bold": (f"{prefix}-bold", f"{prefix}-Bold", *fallbacks),
        "italic": (f"{prefix}-italic", f"{prefix}-Italic", *fallbacks),
        "bold_italic": (f"{prefix}-bolditalic", f"{prefix}-BoldItalic", *fallbacks),
    }


_BUNDLED: dict[str, dict[str, tuple[str, ...]]] = {
    "display": _stack("Poppins", "LiberationSans-Regular", "DejaVuSans"),
    "serif": _stack("texgyrepagella", "DejaVuSerif"),
    "body": _stack("LiberationSans", "DejaVuSans"),
    "elegant_serif": _stack("texgyrebonum", "DejaVuSerif"),
    "rounded": _stack("texgyreadventor", "DejaVuSans"),
    "condensed": _stack("texgyreheroscn", "DejaVuSansCondensed"),
    "mono": _stack("LiberationMono", "DejaVuSansMono"),
    "handwritten": {w: ("texgyrechorus-mediumitalic", "DejaVuSans-Oblique")
                    for w in ("regular", "bold", "italic", "bold_italic")},
}

_FONT_STACKS: dict[tuple[str, str], Sequence[str]] = {
    (fam, weight): names for fam, stacks in _BUNDLED.items() for weight, names in stacks.items()
}

_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIRS = (
    os.path.join(_HERE, "assets", "fonts"), os.path.join(_HERE, "fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts", "C:/Windows/Fonts",
)


def _norm_font_key(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


@lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for root in FONT_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for name in files:
                stem, ext = os.path.splitext(name)
                if ext.lower() in (".ttf", ".otf"):
                    index.setdefault(_norm_font_key(stem), os.path.join(dirpath, name))
    return index


@lru_cache(maxsize=64)
def _resolve(family: str, weight: str) -> str | None:
    index = _font_index()
    for cand in _FONT_STACKS.get((family, weight), ()):
        hit = index.get(_norm_font_key(cand))
        if hit:
            return hit
    return None


@lru_cache(maxsize=64)
def _resolve_any(weight: str) -> str | None:
    index = _font_index()
    if not index:
        return None
    for stem, path in index.items():
        if weight in stem:
            return path
    return next(iter(index.values()))


def missing_fonts() -> list[str]:
    """Families whose regular weight did not resolve to a real font file."""
    return [label for key, label in FONT_FAMILIES.items() if _resolve(key, "regular") is None]


@lru_cache(maxsize=1024)
def _load_ttf(path: str | None, size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    size = max(6, int(size))
    for p in (path, _resolve_any(weight)):
        if p:
            try:
                return ImageFont.truetype(p, size=size)
            except OSError:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _resolve_weight(family: str, bold: bool, italic: bool) -> str | None:
    order = (
        ["bold_italic", "italic", "bold", "regular"] if bold and italic else
        ["italic", "regular"] if italic else
        ["bold", "regular"] if bold else ["regular"]
    )
    for weight in order:
        path = _resolve(family, weight)
        if path:
            return path
    return None


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def cover_crop(img: Image.Image, size: tuple[int, int], focus: tuple[float, float] = (0.5, 0.5),
               zoom: float = 1.0) -> Image.Image:
    """Fills `size` with `img` (cover), choosing the crop with `focus` and `zoom`."""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    src = img if img.mode == "RGBA" else img.convert("RGBA")
    iw, ih = src.size
    scale = max(w / iw, h / ih) * max(1.0, zoom)
    cw, ch = w / scale, h / scale
    x0 = _clamp((iw - cw) * focus[0], 0, max(0, iw - cw))
    y0 = _clamp((ih - ch) * focus[1], 0, max(0, ih - ch))
    return src.resize((w, h), Image.Resampling.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))


def make_gradient(size: tuple[int, int], a: RGB, b: RGB, angle: float = 90.0, kind: str = "linear") -> Image.Image:
    """RGB gradient. angle 0 = left->right, 90 = top->bottom. kind: linear | radial."""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    xs = ((np.arange(w, dtype=np.float32) + 0.5) / w - 0.5) * w
    ys = ((np.arange(h, dtype=np.float32) + 0.5) / h - 0.5) * h
    if kind == "radial":
        t = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2) / (0.5 * math.hypot(w, h))
    else:
        th = math.radians(angle)
        dx, dy = math.cos(th), math.sin(th)
        extent = abs(w * dx) + abs(h * dy) or 1.0
        t = (xs[None, :] * dx + ys[:, None] * dy) / extent + 0.5
    t = np.clip(t, 0, 1)[..., None]
    arr = np.asarray(a, np.float32) * (1 - t) + np.asarray(b, np.float32) * t
    arr += np.random.default_rng(3).random((h, w, 1), dtype=np.float32)  # dither: no banding
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def default_logo(name: str = "LOGO", palette: Palette | None = None, size: int = 320) -> Image.Image:
    pal = palette or THEMES["periwinkle"]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ss = 3
    big = Image.new("L", (size * ss, size * ss), 0)
    bd = ImageDraw.Draw(big)
    bd.ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
    mask = big.resize((size, size), Image.Resampling.BOX)
    img.paste((*pal.card, 255), (0, 0, size, size), mask)
    ring = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(ring).ellipse((size * ss * 0.045, size * ss * 0.045, size * ss * 0.955, size * ss * 0.955),
                                 outline=255, width=max(2, size * ss // 45))
    img.paste((*pal.peek, 255), (0, 0, size, size), ring.resize((size, size), Image.Resampling.BOX))
    initials = "".join(w[0] for w in name.strip().split()[:2]).upper() or "L"
    f = _load_ttf(_resolve("display", "bold"), int(size * 0.38), weight="bold")
    d.text((size / 2, size / 2), initials, font=f, fill=(*pal.ink, 255), anchor="mm")
    return img


def _round_mask(w: int, h: int, corner: float, ss: int = 3) -> Image.Image | None:
    if corner <= 0.001:
        return None
    r = min(corner, 0.5) * min(w, h)
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
    return m.resize((w, h), Image.Resampling.BOX)


def _border_mask(w: int, h: int, corner: float, bw: int, ss: int = 3) -> Image.Image:
    r = min(max(corner, 0.0), 0.5) * min(w, h)
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r * ss, outline=255, width=max(1, bw * ss))
    return m.resize((w, h), Image.Resampling.BOX)


def _shadowed(tile: Image.Image, color: RGB, blur: float, dx: float, dy: float, opacity: float,
              passes: int = 1) -> Image.Image:
    """Adds a blurred drop shadow / glow under an RGBA tile (tile must already carry padding)."""
    a = tile.getchannel("A").point(lambda v: int(v * _clamp(opacity, 0, 1)))
    sh = Image.new("RGBA", tile.size, (*color, 0))
    sh.putalpha(a)
    if blur > 0.2:
        sh = sh.filter(ImageFilter.GaussianBlur(blur))
    moved = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    moved.paste(sh, (int(dx), int(dy)))
    out = moved
    for _ in range(max(0, passes - 1)):
        out = Image.alpha_composite(out, moved)
    return Image.alpha_composite(out, tile)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TextStyle:
    family: str = "display"
    size: int = 44
    color: RGB = (255, 255, 255)
    bold: bool = True
    italic: bool = False
    underline: bool = False
    highlight: bool = False
    highlight_color: RGB = (255, 230, 120)
    align: str = "left"
    leading: float = 1.22
    # --- v2 ---
    color_role: str = "custom"          # custom | ink | muted | accent | on_bg  (follows template colours)
    uppercase: bool = False
    letter_spacing: float = 0.0
    opacity: float = 1.0
    rotation: float = 0.0
    gradient: bool = False
    gradient_color: RGB = (255, 196, 56)
    stroke_width: int = 0
    stroke_color: RGB = (20, 20, 20)
    shadow: bool = False
    shadow_color: RGB = (0, 0, 0)
    shadow_blur: float = 8.0
    shadow_dx: float = 4.0
    shadow_dy: float = 4.0
    shadow_opacity: float = 0.5


@dataclass
class TextLayer:
    text: str
    x: float = 0.08
    y: float = 0.10
    w: float = 0.60
    style: TextStyle = field(default_factory=TextStyle)
    visible: bool = True
    kind: str = "custom"                # headline | body | cta take part in auto-layout
    id: str = field(default_factory=_new_id)
    label: str = "Text"
    h: float = 0.0                      # box height (fraction) used by autofit
    autofit: bool = False


@dataclass
class ShapeLayer:
    kind: str = "rounded"
    x: float = 0.10
    y: float = 0.10
    w: float = 0.30
    h: float = 0.10
    color: RGB = (255, 255, 255)
    color_role: str = "peek"            # custom | peek | card | cta | ink | bg
    gradient: bool = False
    color2: RGB = (255, 105, 180)
    angle: float = 45.0
    alpha: float = 1.0
    rotation: float = 0.0
    filled: bool = True
    stroke: int = 6
    radius: float = 0.25
    shadow: bool = False
    visible: bool = True
    id: str = field(default_factory=_new_id)
    label: str = "Shape"


@dataclass
class PicStyle:
    visible: bool = True
    corner: float = 0.0                 # 0..0.5 (0.5 = circle / pill)
    border_w: int = 0
    border_color: RGB = (255, 255, 255)
    shadow: bool = False
    opacity: float = 1.0
    rotation: float = 0.0
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    blur: float = 0.0
    grayscale: bool = False
    tint: RGB | None = None
    tint_alpha: float = 0.35
    src: str = ""


@dataclass
class ImageLayer(PicStyle):
    image: Image.Image | None = None
    x: float = 0.055
    y: float = 0.055
    w: float = 0.14
    auto: bool = True                   # use the template's logo slot
    name: str = "Studio Brand"          # initials for the placeholder logo


@dataclass
class BackgroundLayer(PicStyle):
    image: Image.Image | None = None
    box: Box = (0.0, 0.0, 1.0, 1.0)
    focus_x: float = 0.5
    focus_y: float = 0.5
    zoom: float = 1.0
    placement: str = "auto"             # auto | full | custom (box)  -- background only
    id: str = field(default_factory=_new_id)
    label: str = "Picture"


@dataclass
class Gradient:
    enabled: bool = False
    color_a: RGB = (20, 30, 80)
    color_b: RGB = (94, 234, 212)
    angle: float = 60.0
    kind: str = "linear"                # linear | radial


@dataclass
class Effects:
    look: str = "none"
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    vignette: float = 0.0
    grain: float = 0.0
    fade_bottom: float = 0.0
    fade_top: float = 0.0
    fade_color: RGB = (0, 0, 0)


@dataclass
class ContactInfo:
    phone: str = "+123-456-789"
    email: str = "hello@studio.com"
    website: str = "www.studio.com"
    show_phone: bool = True
    show_email: bool = False
    show_website: bool = True
    color: RGB = (255, 255, 255)
    enabled: bool = False
    align: str = "center"
    y: float | None = None
    size: int = 20
    auto_color: bool = True


@dataclass
class PostSpec:
    name: str = ""
    category: str = "Protection"
    template: str = "offset_card"
    theme: str = "periwinkle"
    custom_palette: Palette | None = None
    fmt: str = "square"
    background: BackgroundLayer = field(default_factory=BackgroundLayer)
    pictures: list[BackgroundLayer] = field(default_factory=list)
    logo: ImageLayer = field(default_factory=ImageLayer)
    text_layers: list[TextLayer] = field(default_factory=list)
    shapes: list[ShapeLayer] = field(default_factory=list)
    contact: ContactInfo = field(default_factory=ContactInfo)
    gradient: Gradient = field(default_factory=Gradient)
    effects: Effects = field(default_factory=Effects)
    scale: float = 1.0
    duration: float = 0.0               # reel seconds for this slide (0 = automatic)
    id: str = field(default_factory=_new_id)
    sel_text: str = ""
    sel_shape: str = ""
    sel_pic: str = ""

    def get_palette(self) -> Palette:
        if self.theme == "custom" and self.custom_palette:
            return self.custom_palette
        return THEMES.get(self.theme, THEMES["periwinkle"])

    def layer(self, layer_id: str) -> TextLayer | None:
        return next((t for t in self.text_layers if t.id == layer_id), None)


@dataclass
class CarouselSpec:
    slides: list[PostSpec] = field(default_factory=list)
    active_index: int = 0
    indicator: str = "none"             # none | dots | bar | numbers
    swipe: bool = False


# ---------------------------------------------------------------------------
# Canvas: anti-aliased vector primitives
# ---------------------------------------------------------------------------

class Canvas:
    def __init__(self, size: tuple[int, int], pal: Palette):
        self.size = size
        self.w, self.h = size
        self.pal = pal
        self.u = self.w / 1080.0
        self.img = Image.new("RGB", size, pal.bg)
        self.draw = ImageDraw.Draw(self.img, "RGBA")
        self.bg_locked = False

    def s(self, v: float) -> float:
        return v * self.u

    def fx(self, t: float) -> float:
        return self.w * t

    def fy(self, t: float) -> float:
        return self.h * t

    def fill_bg(self, color: RGB | None = None) -> None:
        if not self.bg_locked:
            self.draw.rectangle((0, 0, self.w, self.h), fill=(*(color or self.pal.bg), 255))

    def shape(self, painter: Callable, bbox: Box, fill: RGB, alpha: int = 255, ss: int = 3) -> None:
        """Generic anti-aliased fill. painter(draw, T, ss) draws white on a mask; T maps canvas->mask coords."""
        x0, y0 = max(0, int(math.floor(bbox[0]))), max(0, int(math.floor(bbox[1])))
        x1, y1 = min(self.w, int(math.ceil(bbox[2]))), min(self.h, int(math.ceil(bbox[3])))
        if x1 <= x0 or y1 <= y0:
            return
        if (x1 - x0) * (y1 - y0) > 1_500_000:
            ss = min(ss, 2)
        m = Image.new("L", ((x1 - x0) * ss, (y1 - y0) * ss), 0)
        painter(ImageDraw.Draw(m), lambda x, y: ((x - x0) * ss, (y - y0) * ss), ss)
        m = m.resize((x1 - x0, y1 - y0), Image.Resampling.BOX)
        if alpha < 255:
            m = m.point(lambda v: v * alpha // 255)
        self.img.paste(fill, (x0, y0, x1, y1), m)

    def rect(self, box: Box, fill: RGB, radius: float = 0, alpha: int = 255, outline: float = 0) -> None:
        if not radius and not outline:
            self.draw.rectangle(box, fill=(*fill, alpha))
            return

        def p(d, T, ss):
            bb = [*T(box[0], box[1]), *T(box[2], box[3])]
            if outline:
                d.rounded_rectangle(bb, radius=radius * ss, outline=255, width=max(1, int(outline * ss)))
            else:
                d.rounded_rectangle(bb, radius=radius * ss, fill=255)
        pad = outline + 1
        self.shape(p, (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), fill, alpha)

    def ellipse(self, box: Box, fill: RGB, alpha: int = 255, outline: float = 0) -> None:
        def p(d, T, ss):
            bb = [*T(box[0], box[1]), *T(box[2], box[3])]
            if outline:
                d.ellipse(bb, outline=255, width=max(1, int(outline * ss)))
            else:
                d.ellipse(bb, fill=255)
        pad = outline + 1
        self.shape(p, (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), fill, alpha)

    def polygon(self, pts: list[tuple[float, float]], fill: RGB, alpha: int = 255) -> None:
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]

        def p(d, T, ss):
            d.polygon([T(*q) for q in pts], fill=255)
        self.shape(p, (min(xs), min(ys), max(xs) + 1, max(ys) + 1), fill, alpha)

    def blob(self, box: Box, color: RGB, alpha: int = 255, blur: float = 0) -> None:
        """Soft-edged ellipse - used for glows and spotlights."""
        pad = int(blur * 3) + 2
        x0, y0 = max(0, int(box[0]) - pad), max(0, int(box[1]) - pad)
        x1, y1 = min(self.w, int(box[2]) + pad), min(self.h, int(box[3]) + pad)
        if x1 <= x0 or y1 <= y0:
            return
        m = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(m).ellipse((box[0] - x0, box[1] - y0, box[2] - x0, box[3] - y0), fill=255)
        if blur:
            m = m.filter(ImageFilter.GaussianBlur(blur))
        if alpha < 255:
            m = m.point(lambda v: v * alpha // 255)
        self.img.paste(color, (x0, y0, x1, y1), m)

    def result(self) -> Image.Image:
        return self.img


# ---------------------------------------------------------------------------
# Template painters
# ---------------------------------------------------------------------------

@dataclass
class Geometry:
    text_area: Box                      # x0, y0, x1, y1 as fractions of the canvas
    photo_box: Box | None
    ink: RGB
    ink_muted: RGB
    accent: RGB
    contact_ink: RGB
    contact_y: float
    logo_xy: tuple[float, float]


def _geo(area: Box, surface: RGB, p: Palette, contact_y: float, logo_xy: tuple[float, float],
         photo: Box | None = None, contact_on: RGB | None = None, ink: RGB | None = None,
         muted: RGB | None = None) -> Geometry:
    ink = ink or (p.ink if _lum(surface) == _lum(p.card) else _contrast_ink(surface))
    muted = muted or _mix(ink, surface, 0.28)
    return Geometry(area, photo, ink, muted, _accent_for(surface, p),
                    contact_on or p.on_bg, contact_y, logo_xy)


def _paint_offset_card(c: Canvas, p: Palette) -> Geometry:
    c.rect((c.fx(0.195), c.fy(0.080), c.fx(0.835), c.fy(0.118)), p.peek)
    c.rect((c.fx(0.098), c.fy(0.760), c.fx(0.760), c.fy(0.869)), p.peek)
    c.rect((0, c.fy(0.098), c.fx(0.923), c.fy(0.776)), p.card)
    return Geometry((0.09, 0.20, 0.80, 0.72), None, p.ink, p.ink_muted, _accent_for(p.card, p),
                    p.on_bg, 0.925, (0.055, 0.775))


def _paint_card_photo(c: Canvas, p: Palette) -> Geometry:
    c.rect((0, c.fy(0.75), c.w, c.h), p.peek)  # placeholder under the photo band
    c.rect((c.fx(0.08), c.fy(0.08), c.fx(0.92), c.fy(0.75)), p.card)
    return _geo((0.12, 0.13, 0.88, 0.70), p.card, p, 0.025, (0.05, 0.78), photo=(0.0, 0.75, 1.0, 1.0))


def _paint_photo_side(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.rect((c.fx(0.68), 0, c.w, c.h), p.peek)
    c.rect((c.fx(0.06), c.fy(0.10), c.fx(0.68), c.fy(0.88)), p.card)
    return _geo((0.10, 0.15, 0.64, 0.84), p.card, p, 0.925, (0.76, 0.06), photo=(0.68, 0.0, 1.0, 1.0))


def _paint_arc(c: Canvas, p: Palette) -> Geometry:
    r = max(c.w, c.h) * 0.62
    cx, cy = c.fx(0.30), c.fy(0.26)
    c.fill_bg()
    c.ellipse((cx - r, cy - r, cx + r, cy + r), p.pale)
    return Geometry((0.10, 0.12, 0.62, 0.60), None, p.pale_ink, _mix(p.pale_ink, p.pale, 0.3),
                    _accent_for(p.pale, p), p.on_bg, 0.94, (0.055, 0.70))


def _paint_banner(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.rect((0, c.fy(0.15), c.w, c.fy(0.60)), p.card, alpha=236)
    return _geo((0.08, 0.18, 0.92, 0.57), p.card, p, 0.935, (0.055, 0.70))


def _paint_glassmorphism(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.blob((c.fx(0.02), c.fy(0.02), c.fx(0.58), c.fy(0.58)), p.peek, 235, c.s(70))
    c.blob((c.fx(0.42), c.fy(0.42), c.fx(0.98), c.fy(0.98)), p.cta_bg, 210, c.s(70))
    box = (c.fx(0.10), c.fy(0.12), c.fx(0.90), c.fy(0.88))
    c.rect(box, p.card, radius=c.s(40), alpha=205)
    c.rect(box, (255, 255, 255), radius=c.s(40), alpha=90, outline=c.s(3))
    return _geo((0.15, 0.28, 0.85, 0.78), p.card, p, 0.83, (0.14, 0.15), contact_on=p.ink)


def _paint_geometric_diag(c: Canvas, p: Palette) -> Geometry:
    c.polygon([(0, 0), (c.w, 0), (c.w, c.fy(0.45)), (0, c.fy(0.70))], p.peek)
    c.polygon([(0, c.fy(0.20)), (c.w, c.fy(0.05)), (c.w, c.h), (0, c.h)], p.card)
    return _geo((0.08, 0.27, 0.88, 0.82), p.card, p, 0.93, (0.05, 0.015), contact_on=p.ink_muted)


def _paint_minimal_frame(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.rect((c.fx(0.06), c.fy(0.06), c.fx(0.94), c.fy(0.94)), p.peek, outline=c.s(8))
    ink = p.on_bg
    return Geometry((0.12, 0.13, 0.88, 0.72), None, ink, _mix(ink, p.bg, 0.3), _accent_for(p.bg, p),
                    ink, 0.895, (0.10, 0.75))


def _paint_split_diagonal(c: Canvas, p: Palette) -> Geometry:
    c.polygon([(0, 0), (c.w, 0), (0, c.h)], p.bg)
    c.polygon([(c.w, 0), (c.w, c.h), (0, c.h)], p.card)
    ink = p.on_bg
    return Geometry((0.07, 0.09, 0.55, 0.42), None, ink, _mix(ink, p.bg, 0.3), _accent_for(p.bg, p),
                    p.ink, 0.93, (0.05, 0.60))


def _paint_floating_card(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.rect((c.fx(0.12), c.fy(0.18), c.fx(0.92), c.fy(0.86)), p.peek, radius=c.s(30))
    c.rect((c.fx(0.08), c.fy(0.14), c.fx(0.88), c.fy(0.82)), p.card, radius=c.s(30))
    return _geo((0.13, 0.19, 0.83, 0.76), p.card, p, 0.91, (0.08, 0.015))


def _paint_stripes(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    step, wid = c.s(96), c.s(48)

    def stripes(d, T, ss):
        x = -c.h
        while x < c.w:
            d.polygon([T(x, 0), T(x + wid, 0), T(x + wid + c.h, c.h), T(x + c.h, c.h)], fill=255)
            x += step
    c.shape(stripes, (0, 0, c.w, c.h), p.peek, alpha=95, ss=2)
    c.rect((c.fx(0.07), c.fy(0.12), c.fx(0.93), c.fy(0.86)), p.card, radius=c.s(28), alpha=244)
    return _geo((0.12, 0.17, 0.88, 0.70), p.card, p, 0.925, (0.10, 0.725))


def _wave_pts(c: Canvas, base: float, amp: float, freq: float, phase: float) -> list[tuple[float, float]]:
    pts = [(x, c.fy(base) + amp * math.sin(freq * x / c.w * 2 * math.pi + phase)) for x in range(0, c.w + 1, 8)]
    return pts + [(c.w, c.h), (0, c.h)]


def _paint_wave(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.polygon(_wave_pts(c, 0.62, c.s(34), 1.3, 0.6), p.peek)
    c.polygon(_wave_pts(c, 0.68, c.s(30), 1.1, 2.4), p.card)
    ink = p.on_bg
    return Geometry((0.08, 0.09, 0.92, 0.52), None, ink, _mix(ink, p.bg, 0.3), _accent_for(p.bg, p),
                    p.ink, 0.93, (0.06, 0.74))


def _paint_dots(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    step = c.s(46)
    maxr = step * 0.44

    def dots(d, T, ss):
        yy = 0.0
        while yy < c.h + step:
            xx = 0.0
            while xx < c.w + step:
                r = maxr * _clamp(1 - math.hypot(c.w - xx, yy) / (c.w * 0.95), 0, 1)
                if r > 1:
                    d.ellipse([*T(xx - r, yy - r), *T(xx + r, yy + r)], fill=255)
                xx += step
            yy += step
    c.shape(dots, (0, 0, c.w, c.h), p.peek, alpha=235, ss=2)
    c.rect((c.fx(0.06), c.fy(0.16), c.fx(0.94), c.fy(0.90)), p.card, radius=c.s(36))
    return _geo((0.11, 0.22, 0.89, 0.80), p.card, p, 0.935, (0.06, 0.015))


def _paint_burst(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    cx, cy, R, n = c.fx(0.5), c.fy(0.5), math.hypot(c.w, c.h), 28

    def rays(d, T, ss):
        for i in range(0, n, 2):
            a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            d.polygon([T(cx, cy), T(cx + R * math.cos(a0), cy + R * math.sin(a0)),
                       T(cx + R * math.cos(a1), cy + R * math.sin(a1))], fill=255)
    c.shape(rays, (0, 0, c.w, c.h), p.peek, alpha=120, ss=2)
    c.rect((c.fx(0.10), c.fy(0.22), c.fx(0.90), c.fy(0.78)), p.card, radius=c.s(34))
    return _geo((0.15, 0.27, 0.85, 0.73), p.card, p, 0.90, (0.43, 0.04))


def _paint_polaroid(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.rect((c.fx(0.12), c.fy(0.10), c.fx(0.88), c.fy(0.86)), p.card, radius=c.s(10))
    c.rect((c.fx(0.16), c.fy(0.14), c.fx(0.84), c.fy(0.62)), p.peek)
    return _geo((0.16, 0.65, 0.84, 0.83), p.card, p, 0.92, (0.18, 0.16), photo=(0.16, 0.14, 0.84, 0.62))


def _paint_cut_corner(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()

    def pts(dx: float, dy: float):
        return [(c.fx(0.08) + dx, c.fy(0.12) + dy), (c.fx(0.72) + dx, c.fy(0.12) + dy),
                (c.fx(0.92) + dx, c.fy(0.30) + dy), (c.fx(0.92) + dx, c.fy(0.88) + dy),
                (c.fx(0.08) + dx, c.fy(0.88) + dy)]
    c.polygon(pts(c.s(20), c.s(20)), p.peek)
    c.polygon(pts(0, 0), p.card)
    return _geo((0.13, 0.28, 0.85, 0.81), p.card, p, 0.935, (0.12, 0.15))


def _paint_spotlight(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.blob((c.fx(0.0), c.fy(0.08), c.fx(1.0), c.fy(0.92)), p.card, 255, c.s(150))
    c.rect((c.fx(0.10), c.fy(0.175), c.fx(0.26), c.fy(0.185)), p.peek)
    return _geo((0.10, 0.22, 0.90, 0.78), p.card, p, 0.92, (0.43, 0.04))


def _paint_blocks(c: Canvas, p: Palette) -> Geometry:
    c.fill_bg()
    c.rect((0, 0, c.fx(0.78), c.fy(0.72)), p.card)
    c.rect((c.fx(0.78), 0, c.w, c.fy(0.50)), p.peek)
    c.rect((c.fx(0.78), c.fy(0.50), c.w, c.fy(0.72)), p.cta_bg)
    c.rect((0, c.fy(0.72), c.fx(0.45), c.h), p.peek)
    return _geo((0.07, 0.10, 0.72, 0.66), p.card, p, 0.91, (0.805, 0.06))


_PAINTERS: dict[str, Callable[[Canvas, Palette], Geometry]] = {
    "offset_card": _paint_offset_card, "card_photo": _paint_card_photo, "photo_side": _paint_photo_side,
    "arc": _paint_arc, "banner": _paint_banner, "glassmorphism": _paint_glassmorphism,
    "geometric_diag": _paint_geometric_diag, "minimal_frame": _paint_minimal_frame,
    "split_diagonal": _paint_split_diagonal, "floating_card": _paint_floating_card,
    "stripes": _paint_stripes, "wave": _paint_wave, "dots": _paint_dots, "burst": _paint_burst,
    "polaroid": _paint_polaroid, "cut_corner": _paint_cut_corner, "spotlight": _paint_spotlight,
    "blocks": _paint_blocks,
}
_PHOTO_TEMPLATES = {"card_photo", "photo_side", "polaroid"}

TEMPLATES = _PAINTERS
TEMPLATE_LABELS = {
    "offset_card": "Offset Card - layered cards with peek accents",
    "card_photo": "Card & Photo - headline card with photo base",
    "photo_side": "Split Photo Side - text card beside a photo",
    "arc": "Arc Vector - big circular cut",
    "banner": "Translucent Banner - text band over a picture",
    "glassmorphism": "Glassmorphism - frosted card with glowing orbs",
    "geometric_diag": "Geometric Diagonal - sharp dynamic panels",
    "minimal_frame": "Minimal Frame - bordered elegant layout",
    "split_diagonal": "Split Diagonal - two-tone diagonal split",
    "floating_card": "Floating Card - rounded stacked cards",
    "stripes": "Candy Stripes - diagonal stripes behind a card",
    "wave": "Wave - flowing layered waves",
    "dots": "Halftone Dots - fading dot pattern",
    "burst": "Sunburst - radiating rays behind a card",
    "polaroid": "Polaroid - photo frame with caption",
    "cut_corner": "Cut Corner - angled corner card",
    "spotlight": "Spotlight - soft glow focus",
    "blocks": "Colour Blocks - bold mosaic panels",
}


@lru_cache(maxsize=64)
def _template_geometry_cached(template: str, fmt: str) -> Geometry:
    W, H = FORMATS.get(fmt, FORMATS["square"])
    c = Canvas((max(40, W // 10), max(40, H // 10)), THEMES["periwinkle"])
    return _PAINTERS.get(template, _paint_offset_card)(c, c.pal)


def template_geometry(template: str, fmt: str = "square") -> Geometry:
    """Layout slots of a template (text area, photo box, logo slot) - independent of colours."""
    return copy.copy(_template_geometry_cached(template, fmt))


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------

def _role_color(st: TextStyle, geo: Geometry | None, pal: Palette) -> RGB:
    role = st.color_role
    if role == "ink":
        return geo.ink if geo else pal.ink
    if role == "muted":
        return geo.ink_muted if geo else pal.ink_muted
    if role == "accent":
        return geo.accent if geo else pal.cta_bg
    if role == "on_bg":
        return pal.on_bg
    return st.color


def _prepare_text(layer: TextLayer, cw: int, ch: int, u: float):
    st = layer.style
    text = layer.text.upper() if st.uppercase else layer.text
    path = _resolve_weight(st.family, st.bold, st.italic)
    weight = "bold" if st.bold else "regular"
    max_w = max(40 * u, cw * layer.w)
    sp = st.letter_spacing * u

    def wrap(size_px: int):
        f = _load_ttf(path, size_px, weight)

        def tw(s: str) -> float:
            return f.getlength(s) + sp * max(0, len(s) - 1)
        lines: list[str] = []
        for para in text.split("\n"):
            curr = ""
            for word in para.split():
                trial = f"{curr} {word}".strip()
                if tw(trial) <= max_w or not curr:
                    curr = trial
                else:
                    lines.append(curr)
                    curr = word
            lines.append(curr)
        while lines and not lines[-1]:
            lines.pop()
        return f, lines, tw

    size_px = max(8, int(st.size * u))
    f, lines, tw = wrap(size_px)
    if layer.autofit and layer.h > 0:
        while len(lines) * size_px * st.leading > ch * layer.h and size_px > 10:
            size_px = int(size_px * 0.94)
            f, lines, tw = wrap(size_px)
    return f, lines, tw, size_px, max_w, size_px * st.leading, sp


def measure_text_layer(layer: TextLayer, cw: int, ch: int) -> float:
    """Height in pixels the layer's text block occupies on a cw x ch canvas."""
    _f, lines, _tw, _sz, _mw, line_h, _sp = _prepare_text(layer, cw, ch, cw / 1080.0)
    return len(lines) * line_h


def draw_text_layer(c: Canvas, layer: TextLayer, geo: Geometry | None = None) -> None:
    if not layer.visible or not layer.text.strip():
        return
    st = layer.style
    f, lines, tw, size_px, max_w, line_h, sp = _prepare_text(layer, c.w, c.h, c.u)
    if not lines:
        return
    color = _role_color(st, geo, c.pal)
    stroke_px = int(c.s(st.stroke_width))
    glow = (st.shadow_blur * 3 + abs(st.shadow_dx) + abs(st.shadow_dy)) * c.u if st.shadow else 0
    pad = int(size_px * 0.5 + glow + stroke_px * 2) + 6
    tile_w, tile_h = int(max_w + 2 * pad), int(len(lines) * line_h + 2 * pad + size_px * 0.3)

    def line_x(line: str) -> float:
        wl = tw(line)
        if st.align == "center":
            return pad + (max_w - wl) / 2
        if st.align == "right":
            return pad + max_w - wl
        return pad

    def paint(fill: RGB, stroke: int, extras: bool) -> Image.Image:
        tile = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        y = pad
        for line in lines:
            lx = line_x(line)
            wl = tw(line)
            if extras and st.highlight and line:
                d.rectangle((lx - 6 * c.u, y - 2 * c.u, lx + wl + 6 * c.u, y + size_px + 4 * c.u), fill=(*st.highlight_color, 255))
            if line:
                if sp:
                    x = lx
                    for ch_ in line:
                        d.text((x, y), ch_, font=f, fill=(*fill, 255), stroke_width=stroke, stroke_fill=(*fill, 255))
                        x += f.getlength(ch_) + sp
                else:
                    d.text((lx, y), line, font=f, fill=(*fill, 255), stroke_width=stroke, stroke_fill=(*fill, 255))
            if extras and st.underline and line:
                uy = y + size_px * 0.98
                d.line((lx, uy, lx + wl, uy), fill=(*fill, 255), width=max(1, int(size_px * 0.055)))
            y += line_h
        return tile

    tile = paint(color, 0, True)
    if st.gradient:
        a = tile.getchannel("A")
        tile = make_gradient(tile.size, color, st.gradient_color, 90).convert("RGBA")
        tile.putalpha(a)
    if stroke_px > 0:
        tile = Image.alpha_composite(paint(st.stroke_color, stroke_px, False), tile)
    if st.shadow:
        tile = _shadowed(tile, st.shadow_color, st.shadow_blur * c.u, st.shadow_dx * c.u,
                         st.shadow_dy * c.u, st.shadow_opacity,
                         passes=2 if st.shadow_dx == 0 and st.shadow_dy == 0 else 1)
    cx = c.fx(layer.x) - pad + tile.width / 2
    cy = c.fy(layer.y) - pad + tile.height / 2
    _place(c, tile, cx, cy, st.rotation, st.opacity)


def _place(c: Canvas, tile: Image.Image, cx: float, cy: float, rotation: float = 0.0, opacity: float = 1.0) -> None:
    if rotation:
        tile = tile.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    if opacity < 0.999:
        tile.putalpha(tile.getchannel("A").point(lambda v: int(v * _clamp(opacity, 0, 1))))
    c.img.paste(tile, (int(round(cx - tile.width / 2)), int(round(cy - tile.height / 2))), tile)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

SHAPE_KINDS = ["rect", "rounded", "pill", "ellipse", "ring", "triangle", "diamond", "hexagon", "star", "burst", "arrow"]
_SHAPE_ROLES = ("custom", "peek", "card", "cta", "ink", "bg")


def _star_pts(x0: float, y0: float, x1: float, y1: float, n: int, inner: float) -> list[tuple[float, float]]:
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    pts = []
    for i in range(n * 2):
        r = 1.0 if i % 2 == 0 else inner
        a = -math.pi / 2 + i * math.pi / n
        pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
    return pts


def _draw_kind(d: ImageDraw.ImageDraw, sh: ShapeLayer, box: tuple[float, float, float, float], lw: int) -> None:
    x0, y0, x1, y1 = box
    w, h, cx, cy = x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2
    k = sh.kind
    kw = dict(outline=255, width=lw) if lw else dict(fill=255)
    if k == "rect":
        d.rectangle(box, **kw)
    elif k == "rounded":
        d.rounded_rectangle(box, radius=min(w, h) * _clamp(sh.radius, 0, 0.5), **kw)
    elif k == "pill":
        d.rounded_rectangle(box, radius=min(w, h) / 2, **kw)
    elif k == "ellipse":
        d.ellipse(box, **kw)
    elif k == "ring":
        d.ellipse(box, outline=255, width=max(lw, int(min(w, h) * 0.12)))
    else:
        if k == "triangle":
            pts = [(cx, y0), (x1, y1), (x0, y1)]
        elif k == "diamond":
            pts = [(cx, y0), (x1, cy), (cx, y1), (x0, cy)]
        elif k == "hexagon":
            pts = [(x0 + w * .25, y0), (x0 + w * .75, y0), (x1, cy), (x0 + w * .75, y1), (x0 + w * .25, y1), (x0, cy)]
        elif k == "star":
            pts = _star_pts(x0, y0, x1, y1, 5, 0.45)
        elif k == "burst":
            pts = _star_pts(x0, y0, x1, y1, 14, 0.78)
        else:  # arrow
            pts = [(x0, y0 + h * .3), (x0 + w * .6, y0 + h * .3), (x0 + w * .6, y0), (x1, cy),
                   (x0 + w * .6, y1), (x0 + w * .6, y0 + h * .7), (x0, y0 + h * .7)]
        if lw:
            d.line(pts + [pts[0]], fill=255, width=lw, joint="curve")
        else:
            d.polygon(pts, fill=255)


def _shape_color(role: str, custom: RGB, pal: Palette) -> RGB:
    return {"peek": pal.peek, "card": pal.card, "cta": pal.cta_bg, "ink": pal.ink, "bg": pal.bg}.get(role, custom)


def draw_shape(c: Canvas, sh: ShapeLayer) -> None:
    if not sh.visible:
        return
    W, H = max(2, int(c.fx(sh.w))), max(2, int(c.fy(sh.h)))
    lw = 0 if sh.filled else max(1, int(c.s(sh.stroke)))
    pad = lw + 4 + (int(c.s(48)) if sh.shadow else 0)
    ss = 3 if W * H < 1_000_000 else 2
    tw_, th_ = W + 2 * pad, H + 2 * pad
    m = Image.new("L", (tw_ * ss, th_ * ss), 0)
    _draw_kind(ImageDraw.Draw(m), sh, (pad * ss, pad * ss, (pad + W) * ss - 1, (pad + H) * ss - 1), lw * ss)
    m = m.resize((tw_, th_), Image.Resampling.BOX)
    col = _shape_color(sh.color_role, sh.color, c.pal)
    fill = make_gradient((tw_, th_), col, sh.color2, sh.angle) if sh.gradient else Image.new("RGB", (tw_, th_), col)
    tile = fill.convert("RGBA")
    tile.putalpha(m)
    if sh.shadow:
        tile = _shadowed(tile, (0, 0, 0), c.s(16), 0, c.s(12), 0.45)
    _place(c, tile, c.fx(sh.x) + W / 2, c.fy(sh.y) + H / 2, sh.rotation, sh.alpha)


# ---------------------------------------------------------------------------
# Pictures, logo, background
# ---------------------------------------------------------------------------

def _adjust(img: Image.Image, o: PicStyle, u: float = 1.0) -> Image.Image:
    if (o.brightness == 1 and o.contrast == 1 and o.saturation == 1 and o.blur <= 0 and not o.grayscale):
        return img
    a = img.getchannel("A")
    rgb = img.convert("RGB")
    if o.grayscale:
        rgb = ImageOps.grayscale(rgb).convert("RGB")
    if o.brightness != 1:
        rgb = ImageEnhance.Brightness(rgb).enhance(o.brightness)
    if o.contrast != 1:
        rgb = ImageEnhance.Contrast(rgb).enhance(o.contrast)
    if o.saturation != 1:
        rgb = ImageEnhance.Color(rgb).enhance(o.saturation)
    if o.blur > 0:
        rgb = rgb.filter(ImageFilter.GaussianBlur(o.blur * u))
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def _style_core(core: Image.Image, ps: PicStyle, u: float) -> Image.Image:
    core = _adjust(core.convert("RGBA"), ps, u)
    if ps.tint is not None and ps.tint_alpha > 0:
        a = core.getchannel("A")
        ov = Image.new("RGBA", core.size, (*ps.tint, int(255 * _clamp(ps.tint_alpha, 0, 1))))
        core = Image.alpha_composite(core, ov)
        core.putalpha(a)
    w, h = core.size
    mask = _round_mask(w, h, ps.corner)
    if mask is not None:
        core.putalpha(ImageChops.multiply(core.getchannel("A"), mask))
    if ps.border_w > 0:
        core.paste((*ps.border_color, 255), (0, 0, w, h), _border_mask(w, h, ps.corner, max(1, int(ps.border_w * u))))
    return core


def _picture_tile(core: Image.Image, ps: PicStyle, u: float) -> Image.Image:
    pad = int(60 * u) + 4 if ps.shadow else 2
    tile = Image.new("RGBA", (core.width + 2 * pad, core.height + 2 * pad), (0, 0, 0, 0))
    tile.paste(core, (pad, pad))
    if ps.shadow:
        tile = _shadowed(tile, (0, 0, 0), 14 * u, 0, 10 * u, 0.5)
    return tile


def _box_px(c: Canvas, b: Box) -> tuple[int, int, int, int]:
    return (int(c.fx(b[0])), int(c.fy(b[1])), int(c.fx(b[2])), int(c.fy(b[3])))


def _draw_picture(c: Canvas, ps: BackgroundLayer, box: tuple[int, int, int, int]) -> None:
    if ps.image is None or not ps.visible:
        return
    x0, y0, x1, y1 = box
    w, h = max(2, x1 - x0), max(2, y1 - y0)
    core = _style_core(cover_crop(ps.image, (w, h), (ps.focus_x, ps.focus_y), ps.zoom), ps, c.u)
    _place(c, _picture_tile(core, ps, c.u), (x0 + x1) / 2, (y0 + y1) / 2, ps.rotation, ps.opacity)


def _paint_full_background(c: Canvas, bg: BackgroundLayer) -> None:
    core = _style_core(cover_crop(bg.image, c.size, (bg.focus_x, bg.focus_y), bg.zoom), bg, c.u)
    if bg.opacity < 0.999:
        core.putalpha(core.getchannel("A").point(lambda v: int(v * bg.opacity)))
    c.img.paste(core, (0, 0), core)


def _draw_logo(c: Canvas, spec: PostSpec, geo: Geometry) -> None:
    lg = spec.logo
    if not lg.visible:
        return
    img = lg.image or default_logo(lg.name, c.pal)
    lw = max(8, int(c.fx(lg.w)))
    lh = max(8, int(img.height * lw / max(1, img.width)))
    x, y = geo.logo_xy if lg.auto else (lg.x, lg.y)
    px, py = c.fx(x), c.fy(y)
    if lg.auto:
        m = c.s(16)
        px, py = _clamp(px, m, max(m, c.w - lw - m)), _clamp(py, m, max(m, c.h - lh - m))
    core = _style_core(img.convert("RGBA").resize((lw, lh), Image.Resampling.LANCZOS), lg, c.u)
    _place(c, _picture_tile(core, lg, c.u), px + lw / 2, py + lh / 2, lg.rotation, lg.opacity)


def _draw_contact(c: Canvas, spec: PostSpec, geo: Geometry) -> None:
    ct = spec.contact
    if not ct.enabled:
        return
    parts = [p for p, on in ((ct.phone, ct.show_phone), (ct.email, ct.show_email), (ct.website, ct.show_website)) if on and p]
    if not parts:
        return
    y = ct.y if ct.y is not None else geo.contact_y
    y = min(y, 1 - (ct.size * 1.7 * c.u) / c.h)
    color = geo.contact_ink if ct.auto_color else ct.color
    draw_text_layer(c, TextLayer(text="   •   ".join(parts), x=0.05, y=y, w=0.90, label="Contact", kind="contact",
                                 style=TextStyle(family="body", size=ct.size, color=color, bold=False, align=ct.align)), geo)


def _draw_indicator(c: Canvas, page: tuple[int, int], style: str, swipe: bool) -> None:
    i, n = page
    if n <= 1 or (style == "none" and not swipe):
        return
    y = c.h * 0.962
    ink = _contrast_ink(c.img.getpixel((int(c.w / 2), int(y))))
    if style == "dots":
        r, gap = c.s(7), c.s(28)
        x0 = c.w / 2 - (n - 1) * gap / 2
        for k in range(n):
            cx = x0 + k * gap
            c.ellipse((cx - r, y - r, cx + r, y + r), ink, alpha=255 if k == i else 90)
    elif style == "bar":
        total, seg = c.w * 0.5, c.w * 0.5 / n
        x0 = c.w * 0.25
        for k in range(n):
            c.rect((x0 + k * seg + c.s(3), y - c.s(4), x0 + (k + 1) * seg - c.s(3), y + c.s(4)), ink, radius=c.s(4), alpha=255 if k == i else 80)
    elif style == "numbers":
        draw_text_layer(c, TextLayer(text=f"{i + 1} / {n}", x=0.0, y=(y - c.s(14)) / c.h, w=1.0,
                                     style=TextStyle(family="display", size=24, color=ink, align="center", bold=True)))
    if swipe and i < n - 1:
        x, a = c.w * 0.945, c.s(14)
        c.shape(lambda d, T, ss: d.line([T(x - a, y - a), T(x, y), T(x - a, y + a)], fill=255, width=int(c.s(6) * ss), joint="curve"),
                (x - a * 2, y - a * 2, x + a * 2, y + a * 2), ink)


# ---------------------------------------------------------------------------
# Effects / looks / guides
# ---------------------------------------------------------------------------

LOOKS = ["none", "vivid", "pop", "warm", "cool", "noir", "fade", "dreamy"]


def _apply_look(img: Image.Image, look: str) -> Image.Image:
    if look == "vivid":
        return ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.35)).enhance(1.08)
    if look == "pop":
        return ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.65)).enhance(1.15)
    if look in ("warm", "cool"):
        r, g, b = img.split()
        k = (1.07, 0.93) if look == "warm" else (0.93, 1.07)
        r = r.point(lambda v: min(255, int(v * k[0])))
        b = b.point(lambda v: min(255, int(v * k[1])))
        return Image.merge("RGB", (r, g, b))
    if look == "noir":
        return ImageEnhance.Contrast(ImageOps.grayscale(img).convert("RGB")).enhance(1.25)
    if look == "fade":
        return Image.blend(ImageEnhance.Contrast(img).enhance(0.88), Image.new("RGB", img.size, (236, 232, 228)), 0.12)
    if look == "dreamy":
        soft = img.filter(ImageFilter.GaussianBlur(max(2, img.width / 140)))
        return ImageEnhance.Brightness(Image.blend(img, soft, 0.38)).enhance(1.04)
    return img


def apply_effects(img: Image.Image, fx: Effects) -> Image.Image:
    img = _apply_look(img, fx.look)
    if fx.brightness != 1:
        img = ImageEnhance.Brightness(img).enhance(fx.brightness)
    if fx.contrast != 1:
        img = ImageEnhance.Contrast(img).enhance(fx.contrast)
    if fx.saturation != 1:
        img = ImageEnhance.Color(img).enhance(fx.saturation)
    if fx.vignette <= 0 and fx.grain <= 0 and fx.fade_bottom <= 0 and fx.fade_top <= 0:
        return img
    a = np.asarray(img, dtype=np.float32)
    h, w = a.shape[:2]
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    if fx.vignette > 0:
        d = np.sqrt(((xx - .5) * 2) ** 2 + ((yy - .5) * 2) ** 2) / 1.4142
        a *= (1 - fx.vignette * np.clip((d - 0.35) / 0.65, 0, 1) ** 1.6)[..., None]
    col = np.asarray(fx.fade_color, np.float32)
    for strength, ramp in ((fx.fade_bottom, np.clip((yy - 0.45) / 0.55, 0, 1)),
                           (fx.fade_top, np.clip((0.55 - yy) / 0.55, 0, 1))):
        if strength > 0:
            r = (ramp * strength)[..., None] * np.ones((1, w, 1), np.float32)
            a = a * (1 - r) + col * r
    if fx.grain > 0:
        a += np.random.default_rng(7).normal(0, fx.grain * 28, (h, w, 1)).astype(np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def _draw_guides(img: Image.Image, fmt: str) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out, "RGBA")
    w, h = out.size
    for t in (1 / 3, 2 / 3):
        d.line((w * t, 0, w * t, h), fill=(255, 255, 255, 70), width=1)
        d.line((0, h * t, w, h * t), fill=(255, 255, 255, 70), width=1)
    m = w * 0.05
    step = max(8, w // 60)
    for x in range(int(m), int(w - m), step * 2):
        d.line((x, m, x + step, m), fill=(0, 220, 255, 230), width=2)
        d.line((x, h - m, x + step, h - m), fill=(0, 220, 255, 230), width=2)
    for y in range(int(m), int(h - m), step * 2):
        d.line((m, y, m, y + step), fill=(0, 220, 255, 230), width=2)
        d.line((w - m, y, w - m, y + step), fill=(0, 220, 255, 230), width=2)
    if h / w > 1.6:  # story / reel UI-overlay zones
        d.rectangle((0, 0, w, h * 0.14), fill=(255, 60, 60, 55))
        d.rectangle((0, h * 0.80, w, h), fill=(255, 60, 60, 55))
    return out


# ---------------------------------------------------------------------------
# Auto layout
# ---------------------------------------------------------------------------

def auto_layout(spec: PostSpec, gap: float = 0.022, fit: bool = True) -> None:
    """Stacks headline/body/cta inside the template's text area, shrinking fonts until they fit."""
    geo = template_geometry(spec.template, spec.fmt)
    W, H = FORMATS.get(spec.fmt, FORMATS["square"])
    x0, y0, x1, y1 = geo.text_area
    layers = [t for t in spec.text_layers if t.kind in ("headline", "body", "cta") and t.visible and t.text.strip()]
    if not layers:
        return
    for t in layers:
        t.x, t.w = x0, x1 - x0
    for _ in range(16):
        total = sum(measure_text_layer(t, W, H) / H for t in layers) + gap * (len(layers) - 1)
        if not fit or total <= (y1 - y0):
            break
        for t in layers:
            t.style.size = max(12, int(t.style.size * 0.93))
    y = y0
    for t in layers:
        hh = measure_text_layer(t, W, H) / H
        t.y = max(y, y1 - hh) if t.kind == "cta" else y
        y = t.y + hh + gap


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(spec: PostSpec, scale: float | None = None, guides: bool = False,
           page: tuple[int, int] | None = None, indicator: str = "none", swipe: bool = False,
           include_text: bool = True) -> Image.Image:
    base_w, base_h = FORMATS.get(spec.fmt, FORMATS["square"])
    s = spec.scale if scale is None else scale
    size = (max(64, int(base_w * s)), max(64, int(base_h * s)))
    pal = spec.get_palette()
    c = Canvas(size, pal)

    bg = spec.background
    use_full = bg.image is not None and (bg.placement == "full" or (bg.placement == "auto" and spec.template not in _PHOTO_TEMPLATES))
    if spec.gradient.enabled:
        c.img.paste(make_gradient(size, spec.gradient.color_a, spec.gradient.color_b, spec.gradient.angle, spec.gradient.kind))
        c.bg_locked = True
    if use_full:
        _paint_full_background(c, bg)
        c.bg_locked = True

    geo = _PAINTERS.get(spec.template, _paint_offset_card)(c, pal)

    if bg.image is not None and not use_full:
        box = bg.box if bg.placement == "custom" else (geo.photo_box or bg.box)
        _draw_picture(c, bg, _box_px(c, box))
    for pic in spec.pictures:
        _draw_picture(c, pic, _box_px(c, pic.box))
    for sh in spec.shapes:
        draw_shape(c, sh)
    if include_text:
        for tl in spec.text_layers:
            draw_text_layer(c, tl, geo)
        _draw_contact(c, spec, geo)
    _draw_logo(c, spec, geo)
    if page:
        _draw_indicator(c, page, indicator, swipe)

    out = apply_effects(c.result(), spec.effects)
    return _draw_guides(out, spec.fmt) if guides else out


# ---------------------------------------------------------------------------
# Spec factories
# ---------------------------------------------------------------------------

TEXT_KINDS = ["Headline", "Body", "Badge", "Quote", "Big number", "Caption"]


def make_text_layer(kind: str, pal: Palette) -> TextLayer:
    if kind == "Headline":
        return TextLayer("New headline", 0.1, 0.2, 0.8, TextStyle("display", 64, pal.ink, True, color_role="ink"), kind="custom", label="Headline")
    if kind == "Body":
        return TextLayer("Supporting copy goes here.", 0.1, 0.4, 0.8, TextStyle("body", 30, pal.ink_muted, False, color_role="muted"), label="Body")
    if kind == "Badge":
        return TextLayer("NEW", 0.1, 0.1, 0.3, TextStyle("display", 24, pal.cta_ink, True, highlight=True,
                                                         highlight_color=pal.cta_bg, uppercase=True, letter_spacing=3), label="Badge")
    if kind == "Quote":
        return TextLayer("“A memorable quote.”", 0.1, 0.3, 0.8, TextStyle("serif", 46, pal.ink, False, True, color_role="ink"), label="Quote")
    if kind == "Big number":
        return TextLayer("01", 0.1, 0.1, 0.5, TextStyle("display", 150, pal.peek, True, gradient=True, gradient_color=pal.cta_bg), label="Big number")
    return TextLayer("Caption", 0.1, 0.8, 0.6, TextStyle("body", 20, pal.ink_muted, False, color_role="muted"), label="Caption")


TEXT_PRESETS: dict[str, dict] = {
    "Clean": dict(shadow=False, stroke_width=0, gradient=False, highlight=False, uppercase=False, letter_spacing=0.0, opacity=1.0),
    "Bold Pop": dict(bold=True, shadow=True, shadow_dx=7, shadow_dy=7, shadow_blur=1, shadow_color=(0, 0, 0), shadow_opacity=0.85),
    "Neon Glow": dict(bold=True, shadow=True, shadow_dx=0, shadow_dy=0, shadow_blur=14, shadow_opacity=1.0, shadow_color="@color"),
    "Gradient Hero": dict(bold=True, gradient=True, uppercase=True, color=(255, 95, 150), gradient_color=(255, 196, 56), color_role="custom"),
    "Outlined": dict(bold=True, stroke_width=3, stroke_color=(20, 20, 20), color=(255, 255, 255), color_role="custom"),
    "Soft Shadow": dict(shadow=True, shadow_dx=0, shadow_dy=6, shadow_blur=12, shadow_opacity=0.35, shadow_color=(0, 0, 0)),
    "Highlighter": dict(highlight=True, highlight_color=(255, 230, 90), color=(20, 20, 20), color_role="custom", bold=True),
    "Spaced Caps": dict(uppercase=True, letter_spacing=7.0, bold=False),
}


def new_post(headline: str, body: str = "", cta: str = "", category: str = "Protection",
             fmt: str = "square", template: str = "offset_card", theme: str = "periwinkle") -> PostSpec:
    pal = THEMES.get(theme, THEMES["periwinkle"])
    layers = [
        TextLayer(text=headline, label="Headline", kind="headline",
                  style=TextStyle(family="display", size=64, color=pal.ink, bold=True, color_role="ink")),
        TextLayer(text=body, label="Body Copy", kind="body",
                  style=TextStyle(family="body", size=30, color=pal.ink_muted, bold=False, color_role="muted")),
    ]
    if cta:
        layers.append(TextLayer(text=cta, label="CTA", kind="cta",
                                style=TextStyle(family="display", size=28, color=pal.cta_bg, bold=True, color_role="accent")))
    spec = PostSpec(category=category, template=template, theme=theme, fmt=fmt, text_layers=layers)
    spec.sel_text = layers[0].id
    auto_layout(spec)
    return spec


def _apply_generated_copy(spec: PostSpec, text: str) -> TextLayer:
    headline = next((l for l in spec.text_layers if l.label == "Headline"), None)
    body = next((l for l in spec.text_layers if l.label == "Body Copy"), None)
    if body is None:
        pal = spec.get_palette()
        body = TextLayer(text="", label="Body Copy", kind="body",
                         style=TextStyle(family="body", size=30, color=pal.ink_muted, bold=False, color_role="muted"))
        spec.text_layers.append(body)
    if headline:
        headline.text = ""
    body.text = text
    auto_layout(spec)
    return body


def build_post_canvas(headline: str, body: str, category: str = "Lifestyle", accent: str = "gold",
                      logo_text: str = "FCA Advisory",
                      contact_text: str = "hello@adviser.co.uk • 020 0000 0000 • adviser.co.uk",
                      background_path: str | None = None) -> Image.Image:
    """Original one-call API, preserved for existing callers."""
    theme = {"gold": "mustard", "navy": "royal", "green": "emerald", "purple": "periwinkle", "teal": "emerald"}.get(accent.lower(), "periwinkle")
    category = category if category in CATEGORY_PRESETS else "Protection"
    spec = new_post(headline, body, category=category, template="photo_side" if background_path else "offset_card", theme=theme)
    spec.logo.image = default_logo(logo_text, spec.get_palette())
    if background_path:
        try:
            with Image.open(background_path) as background:
                spec.background.image = background.convert("RGB")
        except (OSError, ValueError):
            pass
    if contact_text:
        spec.text_layers.append(TextLayer(text=contact_text, x=0.08, y=0.93, w=0.84, label="Contact",
                                          style=TextStyle(family="body", size=16, color=spec.get_palette().on_bg, bold=False)))
    return render(spec)


# ---------------------------------------------------------------------------
# Carousel, exports, reel
# ---------------------------------------------------------------------------

def render_carousel(carousel: CarouselSpec, scale: float | None = None) -> list[Image.Image]:
    n = len(carousel.slides)
    return [render(s, scale=scale, page=(i, n), indicator=carousel.indicator, swipe=carousel.swipe)
            for i, s in enumerate(carousel.slides)]


def export_image(img: Image.Image, fmt: str = "PNG", quality: int = 92) -> tuple[bytes, str, str]:
    """Returns (bytes, mime, extension)."""
    fmt = fmt.upper()
    buf = io.BytesIO()
    if fmt in ("JPEG", "JPG"):
        img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, subsampling=0)
        return buf.getvalue(), "image/jpeg", "jpg"
    if fmt == "WEBP":
        img.save(buf, "WEBP", quality=quality)
        return buf.getvalue(), "image/webp", "webp"
    img.save(buf, "PNG")
    return buf.getvalue(), "image/png", "png"


def export_carousel_zip(carousel: CarouselSpec, fmt: str = "PNG", quality: int = 92, scale: float | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, im in enumerate(render_carousel(carousel, scale)):
            data, _mime, ext = export_image(im, fmt, quality)
            zf.writestr(f"slide_{i + 1:02d}.{ext}", data)
    return buf.getvalue()


def export_carousel_pdf(carousel: CarouselSpec, scale: float | None = None) -> bytes:
    imgs = [im.convert("RGB") for im in render_carousel(carousel, scale)]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:], resolution=150)
    return buf.getvalue()


def _even(v: float) -> int:
    v = max(2, int(round(v)))
    return v - (v % 2)


def _kb(img: Image.Image, t: float, mode: str, mag: float = 0.10) -> Image.Image:
    """Ken Burns pan/zoom: crops a moving window and scales it back to full size."""
    if mode == "none" or mag <= 0:
        return img
    w, h = img.size
    t = _ease(t)
    px = 0.5
    if mode == "zoom_in":
        z = 1 + mag * t
    elif mode == "zoom_out":
        z = 1 + mag * (1 - t)
    elif mode == "pan_left":
        z, px = 1 + mag, 1 - t
    elif mode == "pan_right":
        z, px = 1 + mag, t
    else:
        return img
    cw, ch = w / z, h / z
    x0, y0 = (w - cw) * px, (h - ch) * 0.5
    return img.resize((w, h), Image.Resampling.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))


def _transition(a: Image.Image, b: Image.Image, p: float, kind: str) -> Image.Image:
    w, h = a.size
    if kind == "slide_left":
        off = int(w * p)
        out = Image.new("RGB", a.size)
        out.paste(a, (-off, 0))
        out.paste(b, (w - off, 0))
        return out
    if kind == "slide_up":
        off = int(h * p)
        out = Image.new("RGB", a.size)
        out.paste(a, (0, -off))
        out.paste(b, (0, h - off))
        return out
    if kind == "wipe":
        x = int(w * p)
        out = a.copy()
        if x > 0:
            out.paste(b.crop((0, 0, x, h)), (0, 0))
        return out
    if kind == "zoom":
        return Image.blend(_kb(a, p, "zoom_in", 0.18), _kb(b, 1 - p, "zoom_out", 0.18), p)
    return Image.blend(a, b, p)  # crossfade


TRANSITIONS = ["crossfade", "slide_left", "slide_up", "wipe", "zoom", "none"]
KENBURNS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "none"]


def iter_reel_frames(slides: list[PostSpec], duration: float = 18.0, fps: int = 15, animate: bool = True,
                     transition: str = "crossfade", kenburns: str = "zoom_in", reveal_text: bool = False,
                     out_scale: float = 1.0, progress: Callable[[float], None] | None = None) -> Iterator[Image.Image]:
    """Yields reel frames one at a time (constant memory), so long reels never blow up RAM."""
    if not slides:
        raise ValueError("A reel needs at least one slide.")
    if not animate:
        transition, kenburns, reveal_text = "none", "none", False
    n = len(slides)
    duration, fps = max(1.0, float(duration)), max(1, int(fps))
    bw, bh = FORMATS.get(slides[0].fmt, FORMATS["story"])
    size = (_even(bw * out_scale), _even(bh * out_scale))

    def prep(spec: PostSpec, with_text: bool) -> Image.Image:
        im = render(spec, scale=out_scale, include_text=with_text).convert("RGB")
        return im if im.size == size else ImageOps.fit(im, size, Image.Resampling.LANCZOS)

    full = [prep(s, True) for s in slides]
    base = [prep(s, False) for s in slides] if reveal_text else None

    fixed = sum(s.duration for s in slides if s.duration > 0)
    autos = [s for s in slides if s.duration <= 0]
    share = max(0.5, (duration - fixed) / len(autos)) if autos else 0.0
    counts = [max(2, int(round((s.duration if s.duration > 0 else share) * fps))) for s in slides]
    total = sum(counts)
    tf = int(fps * 0.6) if transition != "none" and n > 1 else 0
    done = 0

    for i in range(n):
        tr = min(tf, counts[i] // 3) if i < n - 1 else 0
        hold = counts[i] - tr
        rev = min(int(fps * 0.7), hold // 2) if reveal_text else 0
        for k in range(hold):
            t = k / max(1, hold - 1)
            src = full[i]
            if rev and k < rev:
                src = Image.blend(base[i], full[i], _ease((k + 1) / rev))  # type: ignore[index]
            yield _kb(src, t, kenburns)
            done += 1
        if tr:
            a = _kb(full[i], 1.0, kenburns)
            b = _kb(base[i + 1] if base else full[i + 1], 0.0, kenburns)  # type: ignore[index]
            for k in range(1, tr + 1):
                yield _transition(a, b, _ease(k / (tr + 1)), transition)
                done += 1
        if progress:
            progress(min(1.0, done / total))


def export_reel_video(slides: list[PostSpec], duration: float = 18.0, fps: int = 15, animate: bool = True,
                      transition: str = "crossfade", kenburns: str = "zoom_in", reveal_text: bool = False,
                      out_scale: float = 1.0, progress: Callable[[float], None] | None = None) -> tuple[bytes, str]:
    """Renders slides into one reel. Returns (bytes, ext) where ext is "mp4" or "gif".

    Frames are streamed to the encoder. If MP4 encoding fails for any reason (no
    imageio-ffmpeg, no codec, ...) an animated GIF (<=480px wide) is produced instead,
    so a broken video backend only lowers output quality - it never breaks the app."""
    if not slides:
        raise ValueError("export_reel_video needs at least one slide.")
    kw = dict(duration=duration, fps=fps, animate=animate, transition=transition, kenburns=kenburns, reveal_text=reveal_text)
    if HAS_IMAGEIO:
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
            writer = imageio.get_writer(tmp, format="FFMPEG", mode="I", fps=fps, codec="libx264", quality=8,
                                        macro_block_size=1, pixelformat="yuv420p")
            try:
                for fr in iter_reel_frames(slides, out_scale=out_scale, progress=progress, **kw):
                    writer.append_data(np.asarray(fr))
            finally:
                writer.close()
            with open(tmp, "rb") as fh:
                return fh.read(), "mp4"
        except Exception:
            pass
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
    bw = FORMATS.get(slides[0].fmt, FORMATS["story"])[0]
    gscale = min(out_scale, 480 / bw)
    gfps = min(int(fps), 10)
    kw["fps"] = gfps
    frames = [f.copy() for f in iter_reel_frames(slides, out_scale=gscale, **kw)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=int(1000 / gfps), loop=0)
    return buf.getvalue(), "gif"


def font_preview(style: TextStyle, text: str = "Aa Sample text 123", height: int = 64) -> Image.Image:
    path = _resolve_weight(style.family, style.bold, style.italic)
    f = _load_ttf(path, 38, weight="bold" if style.bold else "regular")
    img = Image.new("RGB", (460, height), (245, 246, 248))
    d = ImageDraw.Draw(img)
    col = style.color if _lum(style.color) < 0.85 else (60, 60, 70)
    d.text((12, 10), text.upper() if style.uppercase else text, font=f, fill=col)
    return img


# ---------------------------------------------------------------------------
# Project save / load (JSON; images embedded)
# ---------------------------------------------------------------------------

_CLASSES = {cls.__name__: cls for cls in (Palette, TextStyle, TextLayer, ShapeLayer, ImageLayer, BackgroundLayer,
                                          Gradient, Effects, ContactInfo, PostSpec, CarouselSpec)}


def _encode_image(im: Image.Image) -> str:
    im = im.copy()
    im.thumbnail((2400, 2400))
    buf = io.BytesIO()
    has_alpha = im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 255
    if has_alpha:
        im.save(buf, "PNG")
    else:
        im.convert("RGB").save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def _to_jsonable(o, images: bool = True, skip: tuple[str, ...] = ()):
    if isinstance(o, Image.Image):
        return {"__img": _encode_image(o) if images else str(o.size)}
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        d = {"__t": type(o).__name__}
        for f in dataclasses.fields(o):
            if f.name not in skip:
                d[f.name] = _to_jsonable(getattr(o, f.name), images, skip)
        return d
    if isinstance(o, (list, tuple)):
        return [_to_jsonable(x, images, skip) for x in o]
    return o


def _from_jsonable(o):
    if isinstance(o, dict):
        if "__img" in o:
            im = Image.open(io.BytesIO(base64.b64decode(o["__img"])))
            im.load()
            return im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        if "__t" in o and o["__t"] in _CLASSES:
            cls = _CLASSES[o["__t"]]
            names = {f.name for f in dataclasses.fields(cls)}
            return cls(**{k: _from_jsonable(v) for k, v in o.items() if k in names})
        return {k: _from_jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        items = [_from_jsonable(x) for x in o]
        if items and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in items):
            return tuple(items)
        return items
    return o


def project_to_bytes(carousel: CarouselSpec) -> bytes:
    return json.dumps({"studio": 2, "carousel": _to_jsonable(carousel)}).encode("utf-8")


def project_from_bytes(data: bytes) -> CarouselSpec:
    obj = json.loads(data.decode("utf-8"))
    car = _from_jsonable(obj["carousel"])
    if not isinstance(car, CarouselSpec) or not car.slides:
        raise ValueError("Not a valid Post Studio project file.")
    car.active_index = min(max(0, car.active_index), len(car.slides) - 1)
    return car


_SIG_SKIP = ("sel_text", "sel_shape", "sel_pic", "active_index")


def _signature(car: CarouselSpec) -> str:
    return json.dumps(_to_jsonable(car, images=False, skip=_SIG_SKIP), sort_keys=True, default=str)


def _collect_images(o, out: list) -> None:
    if isinstance(o, Image.Image):
        out.append(o)
    elif dataclasses.is_dataclass(o) and not isinstance(o, type):
        for f in dataclasses.fields(o):
            _collect_images(getattr(o, f.name), out)
    elif isinstance(o, (list, tuple)):
        for x in o:
            _collect_images(x, out)


def _clone(o):
    """Deep copy that shares (immutable-by-convention) PIL images instead of duplicating pixels."""
    imgs: list = []
    _collect_images(o, imgs)
    return copy.deepcopy(o, {id(i): i for i in imgs})


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

_CSS = """
<style>
.block-container{padding-top:1.1rem;max-width:1650px}
h1.studio-title{background:linear-gradient(90deg,#ff5f6d,#ffc371 30%,#47c9e5 65%,#8a63ff);
 -webkit-background-clip:text;background-clip:text;color:transparent;font-weight:800;font-size:2.2rem;margin:0 0 .2rem 0}
.stButton>button,.stDownloadButton>button{border-radius:10px;font-weight:600}
.stTabs [data-baseweb="tab"]{font-weight:600}
.pal-strip{display:flex;border-radius:10px;overflow:hidden;height:26px;margin:2px 0 8px 0;border:1px solid rgba(128,128,128,.3)}
.pal-strip div{flex:1}
</style>
"""


def _img(st, im: Image.Image, **kw) -> None:
    for extra in ({"width": "stretch"}, {"use_container_width": True}, {}):
        try:
            st.image(im, **extra, **kw)
            return
        except Exception:
            continue


def _sl(st, label, lo, hi, val, step, key, **kw):
    isint = all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi, step))
    v = _clamp(val, lo, hi)
    return st.slider(label, lo, hi, int(v) if isint else float(v), step, key=key, **kw)


def _sb(st, label, options, value, key, fmt=None, **kw):
    opts = list(options)
    return st.selectbox(label, opts, index=opts.index(value) if value in opts else 0, key=key,
                        format_func=fmt or str, **kw)


def _cp(st, label, rgb, key) -> RGB:
    return _from_hex(st.color_picker(label, _hex(rgb), key=key), fallback=tuple(rgb))  # type: ignore[arg-type]


def _bump(st) -> None:
    st.session_state["gen"] = st.session_state.get("gen", 0) + 1


def _commit(st) -> None:
    """Pushes the previous state onto the undo stack when anything changed."""
    ss = st.session_state
    car = ss["carousel"]
    sig = _signature(car)
    if ss.get("last_sig") is None:
        ss["last_sig"], ss["prev_snap"] = sig, _clone(car)
    elif sig != ss["last_sig"]:
        ss["hist"].append(ss["prev_snap"])
        del ss["hist"][:-40]
        ss["redo"] = []
        ss["prev_snap"], ss["last_sig"] = _clone(car), sig


def _restore(st, car: CarouselSpec) -> None:
    ss = st.session_state
    car.active_index = min(ss["carousel"].active_index, len(car.slides) - 1)
    ss["carousel"] = car
    ss["prev_snap"], ss["last_sig"] = _clone(car), _signature(car)
    _bump(st)


def _cb_undo(st) -> None:
    ss = st.session_state
    _commit(st)
    if ss["hist"]:
        ss["redo"].append(_clone(ss["carousel"]))
        _restore(st, ss["hist"].pop())


def _cb_redo(st) -> None:
    ss = st.session_state
    _commit(st)
    if ss["redo"]:
        ss["hist"].append(_clone(ss["carousel"]))
        _restore(st, ss["redo"].pop())


def _ingest(file) -> Image.Image | None:
    try:
        im = Image.open(file)
        im = ImageOps.exif_transpose(im)
        im.thumbnail((2400, 2400))
        has_alpha = im.mode in ("RGBA", "LA", "P")
        return im.convert("RGBA") if has_alpha else im.convert("RGB")
    except Exception:
        return None


def _tag(file) -> str:
    return f"{getattr(file, 'name', '')}:{getattr(file, 'size', 0)}"


def _slide_like(src: PostSpec, headline: str, body: str) -> PostSpec:
    """New slide that inherits the branding (theme, template, fonts, shapes, logo, effects) of `src`."""
    ns = new_post(headline, body, category=src.category, fmt=src.fmt, template=src.template, theme=src.theme)
    ns.custom_palette = _clone(src.custom_palette)
    ns.gradient, ns.effects, ns.contact, ns.logo = _clone(src.gradient), _clone(src.effects), _clone(src.contact), _clone(src.logo)
    ns.shapes = _clone(src.shapes)
    for t in ns.text_layers:
        m = next((x for x in src.text_layers if x.kind == t.kind), None)
        if m:
            t.style = _clone(m.style)
    auto_layout(ns)
    return ns


# ---- callbacks (all run before the script reruns, so they may touch widget state) ----

def _cb_select(st, i: int) -> None:
    st.session_state["carousel"].active_index = i


def _cb_move(st, car, d: int) -> None:
    _commit(st)
    i = car.active_index
    car.slides[i], car.slides[i + d] = car.slides[i + d], car.slides[i]
    car.active_index = i + d
    _bump(st)


def _cb_dup(st, car) -> None:
    _commit(st)
    i = car.active_index
    ns = _clone(car.slides[i])
    ns.id, ns.name = _new_id(), (car.slides[i].name or f"Slide {i + 1}") + " copy"
    car.slides.insert(i + 1, ns)
    car.active_index = i + 1
    _bump(st)


def _cb_add(st, car) -> None:
    _commit(st)
    n = len(car.slides)
    car.slides.append(_slide_like(car.slides[car.active_index], f"Slide {n + 1} title", "Add your slide content here."))
    car.active_index = n
    _bump(st)


def _cb_del(st, car) -> None:
    _commit(st)
    car.slides.pop(car.active_index)
    car.active_index = max(0, car.active_index - 1)
    _bump(st)


def _cb_bulk(st, car, key: str) -> None:
    _commit(st)
    text = st.session_state.get(key, "")
    blocks = [b.strip() for b in text.replace("\r", "").split("\n\n") if b.strip()]
    src = car.slides[car.active_index]
    for b in blocks:
        head, _, rest = b.partition("\n")
        car.slides.append(_slide_like(src, head.strip(), rest.strip()))
    if blocks:
        car.active_index = len(car.slides) - 1
    _bump(st)


def _cb_template(st, spec: PostSpec, tpl: str) -> None:
    _commit(st)
    spec.template = tpl
    if st.session_state.get("auto_fit", True):
        auto_layout(spec)
    _bump(st)


def _cb_category(st, spec: PostSpec) -> None:
    _commit(st)
    pre = CATEGORY_PRESETS.get(spec.category, {})
    spec.theme, spec.template = pre.get("theme", spec.theme), pre.get("template", spec.template)
    if st.session_state.get("auto_fit", True):
        auto_layout(spec)
    _bump(st)


def _cb_palette(st, spec: PostSpec, pal: Palette) -> None:
    _commit(st)
    spec.custom_palette, spec.theme = pal, "custom"
    _bump(st)


def _cb_random_palette(st, spec) -> None:
    _cb_palette(st, spec, random_palette())


def _cb_harmony(st, spec, kh: str, km: str, kd: str) -> None:
    ss = st.session_state
    _cb_palette(st, spec, harmony_palette(_from_hex(ss[kh], (120, 80, 220)), ss[km], bool(ss[kd])))


def _cb_palette_from_image(st, spec) -> None:
    src = spec.background.image or spec.logo.image or next((p.image for p in spec.pictures if p.image is not None), None)
    if src is not None:
        _cb_palette(st, spec, palette_from_image(src))


def _cb_branding_all(st, car, spec) -> None:
    _commit(st)
    for s in car.slides:
        s.theme, s.template = spec.theme, spec.template
        s.custom_palette, s.gradient, s.effects = _clone(spec.custom_palette), _clone(spec.gradient), _clone(spec.effects)
        s.logo, s.contact = _clone(spec.logo), _clone(spec.contact)
    _bump(st)


def _cb_format_all(st, car, spec) -> None:
    _commit(st)
    for s in car.slides:
        s.fmt = spec.fmt
        if st.session_state.get("auto_fit", True):
            auto_layout(s)
    _bump(st)


def _cb_gradient_preset(st, spec, key: str) -> None:
    name = st.session_state.get(key)
    if name in GRADIENT_PRESETS:
        _commit(st)
        a, b, ang = GRADIENT_PRESETS[name]
        spec.gradient.color_a, spec.gradient.color_b, spec.gradient.angle, spec.gradient.enabled = a, b, ang, True
        _bump(st)


def _cb_add_text(st, spec, key: str) -> None:
    _commit(st)
    tl = make_text_layer(st.session_state.get(key, "Headline"), spec.get_palette())
    spec.text_layers.append(tl)
    spec.sel_text = tl.id
    _bump(st)


def _cb_autolayout(st, spec) -> None:
    _commit(st)
    auto_layout(spec)
    _bump(st)


def _cb_layer_op(st, spec, layer, op: str) -> None:
    _commit(st)
    lst = spec.text_layers
    i = lst.index(layer)
    if op == "up" and i < len(lst) - 1:
        lst[i], lst[i + 1] = lst[i + 1], lst[i]
    elif op == "down" and i > 0:
        lst[i], lst[i - 1] = lst[i - 1], lst[i]
    elif op == "dup":
        n = _clone(layer)
        n.id, n.label, n.x, n.y = _new_id(), layer.label + " copy", min(0.9, layer.x + 0.03), min(0.9, layer.y + 0.03)
        lst.insert(i + 1, n)
        spec.sel_text = n.id
    elif op == "del":
        lst.pop(i)
        spec.sel_text = lst[max(0, i - 1)].id if lst else ""
    _bump(st)


def _cb_text_preset(st, spec, layer, key: str) -> None:
    name = st.session_state.get(key)
    if name not in TEXT_PRESETS:
        return
    _commit(st)
    for k, v in TEXT_PRESETS[name].items():
        setattr(layer.style, k, layer.style.color if v == "@color" else v)
    _bump(st)


def _cb_quick_color(st, layer, key: str, table: dict) -> None:
    val = st.session_state.get(key)
    if val in table:
        _commit(st)
        layer.style.color, layer.style.color_role = table[val], "custom"
        _bump(st)


def _cb_nudge(st, obj, dx: float, dy: float) -> None:
    obj.x, obj.y = round(obj.x + dx, 3), round(obj.y + dy, 3)
    _bump(st)


def _cb_center_x(st, obj) -> None:
    obj.x = round((1 - obj.w) / 2, 3)
    _bump(st)


def _cb_add_shape(st, spec, key: str) -> None:
    _commit(st)
    kind = st.session_state.get(key, "rounded")
    sh = ShapeLayer(kind=kind, x=0.1, y=0.1, w=0.25 if kind not in ("pill", "rect", "rounded") else 0.35,
                    h=0.25 if kind not in ("pill", "rect", "rounded") else 0.08, label=kind.title())
    spec.shapes.append(sh)
    spec.sel_shape = sh.id
    _bump(st)


def _cb_shape_op(st, spec, sh, op: str) -> None:
    _commit(st)
    lst = spec.shapes
    i = lst.index(sh)
    if op == "dup":
        n = _clone(sh)
        n.id, n.x, n.y = _new_id(), min(0.9, sh.x + 0.04), min(0.9, sh.y + 0.04)
        lst.insert(i + 1, n)
        spec.sel_shape = n.id
    elif op == "del":
        lst.pop(i)
        spec.sel_shape = lst[max(0, i - 1)].id if lst else ""
    elif op == "up" and i < len(lst) - 1:
        lst[i], lst[i + 1] = lst[i + 1], lst[i]
    elif op == "down" and i > 0:
        lst[i], lst[i - 1] = lst[i - 1], lst[i]
    _bump(st)


def _cb_add_picture(st, spec, key: str, counter: str) -> None:
    f = st.session_state.get(key)
    if f is None:
        return
    im = _ingest(f)
    if im is None:
        return
    _commit(st)
    pic = BackgroundLayer(image=im, box=(0.52, 0.18, 0.92, 0.50), placement="custom", src=_tag(f), corner=0.08, label=f.name[:18])
    spec.pictures.append(pic)
    spec.sel_pic = pic.id
    st.session_state[counter] = st.session_state.get(counter, 0) + 1
    _bump(st)


def _cb_pic_op(st, spec, pic, op: str) -> None:
    _commit(st)
    i = spec.pictures.index(pic)
    if op == "del":
        spec.pictures.pop(i)
        spec.sel_pic = spec.pictures[max(0, i - 1)].id if spec.pictures else ""
    elif op == "dup":
        n = _clone(pic)
        n.id, n.box = _new_id(), tuple(min(1.0, v + 0.03) for v in pic.box)  # type: ignore[assignment]
        spec.pictures.insert(i + 1, n)
        spec.sel_pic = n.id
    _bump(st)


def _cb_remove_bg(st, spec) -> None:
    _commit(st)
    spec.background = BackgroundLayer()
    _bump(st)


def _cb_remove_logo(st, spec) -> None:
    _commit(st)
    spec.logo.image, spec.logo.src = None, ""
    _bump(st)


def _cb_effects_all(st, car, spec) -> None:
    _commit(st)
    for s in car.slides:
        s.effects = _clone(spec.effects)
    _bump(st)


def _cb_load_project(st, key: str) -> None:
    f = st.session_state.get(key)
    if f is None:
        return
    try:
        car = project_from_bytes(f.getvalue())
    except Exception as exc:
        st.session_state["flash"] = ("error", f"Couldn't open project: {exc}")
        return
    _commit(st)
    st.session_state["hist"].append(st.session_state["prev_snap"])
    st.session_state["carousel"] = car
    st.session_state["prev_snap"], st.session_state["last_sig"] = _clone(car), _signature(car)
    st.session_state["redo"] = []
    st.session_state["exports"] = {}
    st.session_state["flash"] = ("success", f"Opened project with {len(car.slides)} slide(s).")
    _bump(st)


# ---- sections ----

def _ui_sidebar(st, car: CarouselSpec) -> None:
    ss = st.session_state
    sb = st.sidebar
    sb.header("Studio")
    c1, c2 = sb.columns(2)
    c1.button("↶ Undo", key="undo", on_click=_cb_undo, args=(st,), disabled=not ss["hist"])
    c2.button("↷ Redo", key="redo_btn", on_click=_cb_redo, args=(st,), disabled=not ss["redo"])
    sb.caption(f"{len(ss['hist'])} undo step(s) available")
    ss["auto_fit"] = sb.checkbox("Auto-fit text when template / format changes", value=ss.get("auto_fit", True))
    ss["guides"] = sb.checkbox("Show guides (thirds, safe area, story zones)", value=ss.get("guides", False))
    ss["preview_scale"] = sb.select_slider("Preview quality", options=[0.4, 0.55, 0.7, 0.85, 1.0],
                                           value=ss.get("preview_scale", 0.55), help="Lower = faster editing")
    sb.divider()
    sb.subheader("Carousel")
    car.indicator = sb.selectbox("Page indicator", ["none", "dots", "bar", "numbers"],
                                 index=["none", "dots", "bar", "numbers"].index(car.indicator),
                                 key=f"ind_{ss['gen']}")
    car.swipe = sb.checkbox("Swipe arrow (all but last slide)", value=car.swipe, key=f"swipe_{ss['gen']}")
    sb.divider()
    sb.subheader("Project")
    if sb.button("Prepare project file", key="prep_proj"):
        ss["exports"]["project"] = (project_to_bytes(car), "post_studio_project.json", "application/json", _signature(car))
    ex = ss["exports"].get("project")
    if ex:
        sb.download_button("Download project", ex[0], file_name=ex[1], mime=ex[2], key="dl_proj")
    up = sb.file_uploader("Open a project (.json)", type=["json"], key=f"open_proj_{ss['gen']}")
    sb.button("Load project", key="load_proj", on_click=_cb_load_project, args=(st, f"open_proj_{ss['gen']}"), disabled=up is None)


def _ui_strip(st, car: CarouselSpec) -> None:
    ss = st.session_state
    n, per = len(car.slides), 6
    st.subheader("Carousel")
    for start in range(0, n, per):
        cols = st.columns(per)
        for col, i in zip(cols, range(start, min(n, start + per))):
            slide = car.slides[i]
            with col:
                _img(st, render(slide, scale=0.18, page=(i, n), indicator=car.indicator, swipe=car.swipe))
                active = i == car.active_index
                st.button(("▶ " if active else "") + (slide.name or f"Slide {i + 1}"), key=f"sel_{slide.id}_{ss['gen']}",
                          on_click=_cb_select, args=(st, i), type="primary" if active else "secondary")


def _ui_toolbar(st, car: CarouselSpec, spec: PostSpec, K) -> None:
    ss = st.session_state
    i = car.active_index
    a = st.columns([3, 1, 1, 1, 1, 1])
    spec.name = a[0].text_input("Slide name", spec.name, placeholder=f"Slide {i + 1}", key=K("name"))
    a[1].button("⬅", key=K("mv_l"), help="Move earlier", on_click=_cb_move, args=(st, car, -1), disabled=i == 0)
    a[2].button("➡", key=K("mv_r"), help="Move later", on_click=_cb_move, args=(st, car, 1), disabled=i == len(car.slides) - 1)
    a[3].button("📋", key=K("dup"), help="Duplicate slide", on_click=_cb_dup, args=(st, car))
    a[4].button("➕", key=K("add"), help="Add slide with the same branding", on_click=_cb_add, args=(st, car))
    a[5].button("🗑", key=K("del"), help="Delete slide", on_click=_cb_del, args=(st, car), disabled=len(car.slides) <= 1)
    b = st.columns([2, 2, 2])
    new_fmt = _sb(st, "Canvas format", FORMATS.keys(), spec.fmt, K("fmt"), fmt=lambda k: f"{k}  ({FORMATS[k][0]}×{FORMATS[k][1]})")
    if new_fmt != spec.fmt:
        spec.fmt = new_fmt
        if ss.get("auto_fit", True):
            auto_layout(spec)
    b[2].button("Apply format to all slides", key=K("fmt_all"), on_click=_cb_format_all, args=(st, car, spec))
    spec.duration = b[1].slider("Reel seconds (0 = auto)", 0.0, 15.0, float(_clamp(spec.duration, 0, 15)), 0.5, key=K("dur"))
    with st.expander("➕ Bulk-create slides from text"):
        st.caption("One slide per paragraph (blank line between). First line = headline, remaining lines = body. "
                   "New slides inherit this slide's branding.")
        st.text_area("Slides text", key=K("bulk"), height=150, placeholder="5 ways to protect your family\nShort supporting line.\n\n"
                                                                          "Tip 1: Review your cover\nWhy it matters.")
        st.button("Create slides", key=K("bulk_go"), on_click=_cb_bulk, args=(st, car, K("bulk")))


def _ui_design(st, car, spec, K) -> None:
    ss = st.session_state
    st.markdown("**Template**")
    c1, c2 = st.columns([3, 2])
    spec.category = _sb(st, "Category preset", CATEGORY_PRESETS.keys(), spec.category, K("cat"))
    c2.write("")
    c2.button("Apply preset", key=K("apply_cat"), on_click=_cb_category, args=(st, spec))
    tpl_keys = list(TEMPLATE_LABELS)
    st.selectbox("Vector template", tpl_keys, index=tpl_keys.index(spec.template) if spec.template in tpl_keys else 0,
                 format_func=lambda k: TEMPLATE_LABELS[k], key=K("tpl"),
                 on_change=lambda: _cb_template(st, spec, ss[K("tpl")]))
    if st.checkbox("Template gallery", key=K("gal")):
        per = 4
        for start in range(0, len(tpl_keys), per):
            cols = st.columns(per)
            for col, t in zip(cols, tpl_keys[start:start + per]):
                with col:
                    _img(st, render(dataclasses.replace(spec, template=t), scale=0.16))
                    col.button("Use" if t != spec.template else "✔ Active", key=K(f"gal_{t}"), on_click=_cb_template, args=(st, spec, t))

    st.divider()
    st.markdown("**Colours**")
    pal = spec.get_palette()
    keys = list(THEMES)
    sel = st.selectbox("Palette preset", keys, index=keys.index(spec.theme) if spec.theme in keys else 0,
                       format_func=lambda k: THEMES[k].label, key=K("theme"))
    if sel != spec.theme:
        spec.theme = sel
        pal = spec.get_palette()
    st.markdown('<div class="pal-strip">' + "".join(f'<div style="background:{_hex(x)}"></div>' for x in
                (pal.bg, pal.card, pal.peek, pal.cta_bg, pal.ink)) + "</div>", unsafe_allow_html=True)
    g1, g2, g3 = st.columns(3)
    g1.button("Random palette", key=K("rand"), on_click=_cb_random_palette, args=(st, spec))
    g2.button("From image", key=K("pimg"), on_click=_cb_palette_from_image, args=(st, spec),
              help="Uses the background, logo or first picture")
    g3.button("Apply branding to all", key=K("brand_all"), on_click=_cb_branding_all, args=(st, car, spec),
              help="Copies theme, template, gradient, effects, logo and contact bar to every slide")
    with st.expander("Colour harmony generator"):
        h1, h2, h3 = st.columns([1, 2, 1])
        h1.color_picker("Base", "#7a50dc", key=K("hbase"))
        h2.selectbox("Harmony", ["complementary", "analogous", "triadic", "split", "mono"], key=K("hmode"))
        h3.checkbox("Dark", value=True, key=K("hdark"))
        st.button("Generate palette", key=K("hgo"), on_click=_cb_harmony, args=(st, spec, K("hbase"), K("hmode"), K("hdark")))
    with st.expander("Fine-tune colours (turns any preset into a custom palette)"):
        tag = "custom" if spec.theme == "custom" else spec.theme
        r1 = st.columns(3)
        r2 = st.columns(3)
        bg = _cp(st, "Background", pal.bg, K(f"cbg_{tag}"))
        card = _cp(st, "Card panel", pal.card, K(f"ccard_{tag}"))
        peek = _cp(st, "Accent / peek", pal.peek, K(f"cpeek_{tag}"))
        ink = _cp(st, "Primary text", pal.ink, K(f"cink_{tag}"))
        muted = _cp(st, "Muted text", pal.ink_muted, K(f"cmut_{tag}"))
        cta = _cp(st, "Button / highlight", pal.cta_bg, K(f"ccta_{tag}"))
        if (bg, card, peek, ink, muted, cta) != (pal.bg, pal.card, pal.peek, pal.ink, pal.ink_muted, pal.cta_bg):
            pale = _mix(bg, (255, 255, 255), 0.86)
            spec.custom_palette = Palette("custom", "Custom Palette Bar", bg, card, peek, ink, muted, _contrast_ink(bg),
                                          cta, _contrast_ink(cta), pale, _contrast_ink(pale))
            spec.theme = "custom"

    st.divider()
    st.markdown("**Gradient background**")
    gr = spec.gradient
    gr.enabled = st.checkbox("Enable gradient background", gr.enabled, key=K("gr_on"))
    if gr.enabled:
        pk = K("gr_preset")
        st.selectbox("Preset", ["(custom)"] + list(GRADIENT_PRESETS), key=pk, on_change=_cb_gradient_preset, args=(st, spec, pk))
        q1, q2 = st.columns(2)
        gr.color_a = _cp(st, "Colour A", gr.color_a, K("gr_a"))
        gr.color_b = _cp(st, "Colour B", gr.color_b, K("gr_b"))
        gr.kind = _sb(st, "Type", ["linear", "radial"], gr.kind, K("gr_kind"))
        if gr.kind == "linear":
            gr.angle = _sl(st, "Angle", 0.0, 360.0, gr.angle, 5.0, K("gr_ang"))
        st.caption("Tip: templates with solid backgrounds let the gradient show through - try Stripes, Wave, Dots, Burst.")


def _ui_text(st, car, spec, K) -> None:
    ss = st.session_state
    pal = spec.get_palette()
    a = st.columns([2, 1, 1])
    a[0].selectbox("Add text", TEXT_KINDS, key=K("addkind"))
    a[1].write("")
    a[1].button("➕ Add", key=K("addtxt"), on_click=_cb_add_text, args=(st, spec, K("addkind")))
    a[2].write("")
    a[2].button("🪄 Auto-layout", key=K("autolay"), on_click=_cb_autolayout, args=(st, spec),
                help="Stacks headline / body / CTA inside the template's text area and shrinks fonts to fit")
    if not spec.text_layers:
        st.info("No text yet - add some above.")
        return
    ids = [t.id for t in spec.text_layers]
    if spec.sel_text not in ids:
        spec.sel_text = ids[0]
    lookup = {t.id: t for t in spec.text_layers}
    spec.sel_text = st.selectbox("Edit layer", ids, index=ids.index(spec.sel_text), key=K("sel_text"),
                                 format_func=lambda i: f"{'👁' if lookup[i].visible else '🚫'}  {lookup[i].label}: {lookup[i].text[:26]}")
    layer = lookup[spec.sel_text]
    L = lambda n: K(f"{layer.id}_{n}")

    o = st.columns(5)
    layer.visible = o[0].checkbox("Visible", layer.visible, key=L("vis"))
    o[1].button("⬆", key=L("up"), help="Bring forward", on_click=_cb_layer_op, args=(st, spec, layer, "up"))
    o[2].button("⬇", key=L("dn"), help="Send backward", on_click=_cb_layer_op, args=(st, spec, layer, "down"))
    o[3].button("📋", key=L("dup"), help="Duplicate", on_click=_cb_layer_op, args=(st, spec, layer, "dup"))
    o[4].button("🗑", key=L("del"), help="Delete", on_click=_cb_layer_op, args=(st, spec, layer, "del"))

    layer.label = st.text_input("Layer name", layer.label, key=L("label"))
    layer.text = st.text_area("Content", layer.text, key=L("text"), height=110)
    layer.kind = _sb(st, "Auto-layout role", ["headline", "body", "cta", "custom"], layer.kind, L("kind"),
                     help="headline / body / cta layers are stacked automatically by 🪄 Auto-layout")
    st.selectbox("✨ Style preset", list(TEXT_PRESETS), index=None, placeholder="Apply a ready-made style...", key=L("preset"),
                 on_change=_cb_text_preset, args=(st, spec, layer, L("preset")))
    s = layer.style

    with st.expander("Typography", expanded=True):
        f1, f2 = st.columns([2, 1])
        fams = list(FONT_FAMILIES)
        s.family = f1.selectbox("Font", fams, index=fams.index(s.family) if s.family in fams else 0,
                                format_func=lambda k: FONT_FAMILIES[k], key=L("fam"))
        s.align = _sb(f2, "Align", ["left", "center", "right"], s.align, L("al"))
        t = st.columns(5)
        s.bold = t[0].checkbox("Bold", s.bold, key=L("b"))
        s.italic = t[1].checkbox("Italic", s.italic, key=L("i"))
        s.underline = t[2].checkbox("Underline", s.underline, key=L("u"))
        s.uppercase = t[3].checkbox("CAPS", s.uppercase, key=L("caps"))
        s.highlight = t[4].checkbox("Highlight", s.highlight, key=L("h"))
        if s.highlight:
            s.highlight_color = _cp(st, "Highlight colour", s.highlight_color, L("hc"))
        s.size = _sl(st, "Size", 10, 300, int(s.size), 1, L("size"))
        z1, z2 = st.columns(2)
        s.leading = _sl(z1, "Line height", 0.8, 2.2, s.leading, 0.02, L("lead"))
        s.letter_spacing = _sl(z2, "Letter spacing", -4.0, 40.0, s.letter_spacing, 0.5, L("ls"))
        _img(st, font_preview(s), caption="Font preview")

    with st.expander("Colour & effects"):
        roles = {"custom": "Custom colour", "ink": "Template text", "muted": "Template muted", "accent": "Template accent", "on_bg": "On background"}
        s.color_role = st.selectbox("Colour source", list(roles), index=list(roles).index(s.color_role) if s.color_role in roles else 0,
                                    format_func=lambda k: roles[k], key=L("role"))
        if s.color_role == "custom":
            s.color = _cp(st, "Text colour", s.color, L("col"))
            quick = {"Accent": pal.peek, "Button": pal.cta_bg, "Card": pal.card, "Background": pal.bg, "White": (255, 255, 255),
                     "Black": (20, 20, 20), "Gold": (240, 190, 60), "Red": (230, 60, 70), "Blue": (50, 110, 230), "Green": (50, 190, 120)}
            st.selectbox("Quick colour", list(quick), index=None, placeholder="Pick from theme / basics...", key=L("quick"),
                         on_change=_cb_quick_color, args=(st, layer, L("quick"), quick))
        s.opacity = _sl(st, "Opacity", 0.05, 1.0, s.opacity, 0.05, L("op"))
        s.gradient = st.checkbox("Gradient fill", s.gradient, key=L("gr"))
        if s.gradient:
            s.gradient_color = _cp(st, "Gradient to", s.gradient_color, L("grc"))
        w1, w2 = st.columns(2)
        s.stroke_width = _sl(w1, "Outline width", 0, 16, int(s.stroke_width), 1, L("sw"))
        if s.stroke_width:
            s.stroke_color = _cp(w2, "Outline colour", s.stroke_color, L("sc"))
        s.shadow = st.checkbox("Shadow / glow", s.shadow, key=L("sh"))
        if s.shadow:
            s.shadow_color = _cp(st, "Shadow colour", s.shadow_color, L("shc"))
            y1, y2, y3, y4 = st.columns(4)
            s.shadow_blur = _sl(y1, "Blur", 0.0, 40.0, s.shadow_blur, 1.0, L("shb"))
            s.shadow_dx = _sl(y2, "X", -30.0, 30.0, s.shadow_dx, 1.0, L("shx"))
            s.shadow_dy = _sl(y3, "Y", -30.0, 30.0, s.shadow_dy, 1.0, L("shy"))
            s.shadow_opacity = _sl(y4, "Strength", 0.05, 1.0, s.shadow_opacity, 0.05, L("sho"))

    with st.expander("Position & box"):
        p1, p2 = st.columns(2)
        layer.x = _sl(p1, "X", -0.3, 1.2, layer.x, 0.005, L("x"))
        layer.y = _sl(p2, "Y", -0.3, 1.2, layer.y, 0.005, L("y"))
        p3, p4 = st.columns(2)
        layer.w = _sl(p3, "Box width", 0.1, 1.2, layer.w, 0.01, L("w"))
        s.rotation = _sl(p4, "Rotation", -180.0, 180.0, s.rotation, 1.0, L("rot"))
        layer.autofit = st.checkbox("Auto-shrink to fit box height", layer.autofit, key=L("af"))
        if layer.autofit:
            layer.h = _sl(st, "Box height", 0.05, 1.0, layer.h or 0.2, 0.01, L("bh"))
        n = st.columns(5)
        n[0].button("◀", key=L("nl"), on_click=_cb_nudge, args=(st, layer, -0.01, 0))
        n[1].button("▲", key=L("nu"), on_click=_cb_nudge, args=(st, layer, 0, -0.01))
        n[2].button("▼", key=L("nd"), on_click=_cb_nudge, args=(st, layer, 0, 0.01))
        n[3].button("▶", key=L("nr"), on_click=_cb_nudge, args=(st, layer, 0.01, 0))
        n[4].button("↔ Centre", key=L("cx"), on_click=_cb_center_x, args=(st, layer))


def _ui_elements(st, car, spec, K) -> None:
    st.markdown("**Vector shapes**")
    a = st.columns([2, 1])
    a[0].selectbox("Add shape", SHAPE_KINDS, key=K("addshape"))
    a[1].write("")
    a[1].button("➕ Add shape", key=K("addsh"), on_click=_cb_add_shape, args=(st, spec, K("addshape")))
    if spec.shapes:
        ids = [x.id for x in spec.shapes]
        if spec.sel_shape not in ids:
            spec.sel_shape = ids[0]
        lookup = {x.id: x for x in spec.shapes}
        spec.sel_shape = st.selectbox("Edit shape", ids, index=ids.index(spec.sel_shape), key=K("sel_shape"),
                                      format_func=lambda i: f"{'👁' if lookup[i].visible else '🚫'}  {lookup[i].label} ({lookup[i].kind})")
        sh = lookup[spec.sel_shape]
        L = lambda n: K(f"{sh.id}_{n}")
        o = st.columns(5)
        sh.visible = o[0].checkbox("Visible", sh.visible, key=L("vis"))
        o[1].button("⬆", key=L("up"), on_click=_cb_shape_op, args=(st, spec, sh, "up"))
        o[2].button("⬇", key=L("dn"), on_click=_cb_shape_op, args=(st, spec, sh, "down"))
        o[3].button("📋", key=L("dup"), on_click=_cb_shape_op, args=(st, spec, sh, "dup"))
        o[4].button("🗑", key=L("del"), on_click=_cb_shape_op, args=(st, spec, sh, "del"))
        sh.kind = _sb(st, "Shape", SHAPE_KINDS, sh.kind, L("kind"))
        roles = {"peek": "Theme accent", "card": "Theme card", "cta": "Theme button", "ink": "Theme text", "bg": "Theme background", "custom": "Custom"}
        sh.color_role = st.selectbox("Colour source", list(roles), index=list(roles).index(sh.color_role) if sh.color_role in roles else 0,
                                     format_func=lambda k: roles[k], key=L("role"))
        if sh.color_role == "custom":
            sh.color = _cp(st, "Colour", sh.color, L("col"))
        sh.gradient = st.checkbox("Gradient fill", sh.gradient, key=L("gr"))
        if sh.gradient:
            g1, g2 = st.columns(2)
            sh.color2 = _cp(g1, "Gradient to", sh.color2, L("c2"))
            sh.angle = _sl(g2, "Angle", 0.0, 360.0, sh.angle, 5.0, L("ang"))
        sh.filled = st.checkbox("Filled (off = outline)", sh.filled, key=L("fill"))
        if not sh.filled:
            sh.stroke = _sl(st, "Outline width", 1, 40, int(sh.stroke), 1, L("stk"))
        if sh.kind == "rounded":
            sh.radius = _sl(st, "Corner radius", 0.0, 0.5, sh.radius, 0.01, L("rad"))
        p = st.columns(2)
        sh.x = _sl(p[0], "X", -0.3, 1.2, sh.x, 0.005, L("x"))
        sh.y = _sl(p[1], "Y", -0.3, 1.2, sh.y, 0.005, L("y"))
        p = st.columns(2)
        sh.w = _sl(p[0], "Width", 0.01, 1.5, sh.w, 0.005, L("w"))
        sh.h = _sl(p[1], "Height", 0.01, 1.5, sh.h, 0.005, L("h"))
        p = st.columns(2)
        sh.rotation = _sl(p[0], "Rotation", -180.0, 180.0, sh.rotation, 1.0, L("rot"))
        sh.alpha = _sl(p[1], "Opacity", 0.05, 1.0, sh.alpha, 0.05, L("alpha"))
        sh.shadow = st.checkbox("Drop shadow", sh.shadow, key=L("shd"))
        n = st.columns(4)
        n[0].button("◀", key=L("nl"), on_click=_cb_nudge, args=(st, sh, -0.01, 0))
        n[1].button("▲", key=L("nu"), on_click=_cb_nudge, args=(st, sh, 0, -0.01))
        n[2].button("▼", key=L("nd"), on_click=_cb_nudge, args=(st, sh, 0, 0.01))
        n[3].button("▶", key=L("nr"), on_click=_cb_nudge, args=(st, sh, 0.01, 0))
    else:
        st.caption("No shapes yet. Add badges, underlines, frames, stars, arrows and more.")

    st.divider()
    st.markdown("**Contact bar**")
    ct = spec.contact
    ct.enabled = st.checkbox("Show contact bar", ct.enabled, key=K("ct_on"))
    if ct.enabled:
        c1, c2, c3 = st.columns(3)
        ct.phone = c1.text_input("Phone", ct.phone, key=K("ct_ph"))
        ct.email = c2.text_input("Email", ct.email, key=K("ct_em"))
        ct.website = c3.text_input("Website", ct.website, key=K("ct_web"))
        f1, f2, f3 = st.columns(3)
        ct.show_phone = f1.checkbox("Show phone", ct.show_phone, key=K("ct_sp"))
        ct.show_email = f2.checkbox("Show email", ct.show_email, key=K("ct_se"))
        ct.show_website = f3.checkbox("Show website", ct.show_website, key=K("ct_sw"))
        m1, m2, m3 = st.columns(3)
        ct.align = _sb(m1, "Align", ["left", "center", "right"], ct.align, K("ct_al"))
        ct.size = _sl(m2, "Size", 12, 40, int(ct.size), 1, K("ct_sz"))
        ct.auto_color = m3.checkbox("Auto colour", ct.auto_color, key=K("ct_ac"))
        if not ct.auto_color:
            ct.color = _cp(st, "Colour", ct.color, K("ct_col"))
        custom_y = st.checkbox("Custom vertical position", ct.y is not None, key=K("ct_cy"))
        ct.y = _sl(st, "Y", 0.0, 1.0, ct.y if ct.y is not None else 0.92, 0.005, K("ct_y")) if custom_y else None


def _picture_controls(st, p: BackgroundLayer, L, with_box: bool = True) -> None:
    if with_box:
        b = list(p.box)
        c1, c2 = st.columns(2)
        b[0] = _sl(c1, "Left", -0.2, 1.0, b[0], 0.005, L("bx0"))
        b[1] = _sl(c2, "Top", -0.2, 1.0, b[1], 0.005, L("by0"))
        c3, c4 = st.columns(2)
        b[2] = _sl(c3, "Right", 0.0, 1.2, b[2], 0.005, L("bx1"))
        b[3] = _sl(c4, "Bottom", 0.0, 1.2, b[3], 0.005, L("by1"))
        p.box = (b[0], b[1], max(b[2], b[0] + 0.02), max(b[3], b[1] + 0.02))
    f1, f2, f3 = st.columns(3)
    p.focus_x = _sl(f1, "Focus X", 0.0, 1.0, p.focus_x, 0.01, L("fx"))
    p.focus_y = _sl(f2, "Focus Y", 0.0, 1.0, p.focus_y, 0.01, L("fy"))
    p.zoom = _sl(f3, "Zoom", 1.0, 4.0, p.zoom, 0.05, L("zm"))
    _style_controls(st, p, L)


def _style_controls(st, p: PicStyle, L) -> None:
    s1, s2, s3 = st.columns(3)
    p.corner = _sl(s1, "Corner", 0.0, 0.5, p.corner, 0.01, L("cr"), help="0.5 = circle / pill")
    p.opacity = _sl(s2, "Opacity", 0.05, 1.0, p.opacity, 0.05, L("op"))
    p.rotation = _sl(s3, "Rotate", -180.0, 180.0, p.rotation, 1.0, L("rot"))
    b1, b2, b3 = st.columns(3)
    p.border_w = _sl(b1, "Border", 0, 30, int(p.border_w), 1, L("bw"))
    if p.border_w:
        p.border_color = _cp(b2, "Border colour", p.border_color, L("bc"))
    p.shadow = b3.checkbox("Shadow", p.shadow, key=L("shd"))
    with st.expander("Adjust image"):
        a1, a2, a3 = st.columns(3)
        p.brightness = _sl(a1, "Brightness", 0.3, 1.8, p.brightness, 0.02, L("br"))
        p.contrast = _sl(a2, "Contrast", 0.3, 1.8, p.contrast, 0.02, L("ct"))
        p.saturation = _sl(a3, "Saturation", 0.0, 2.0, p.saturation, 0.02, L("sa"))
        z1, z2 = st.columns(2)
        p.blur = _sl(z1, "Blur", 0.0, 30.0, p.blur, 0.5, L("bl"))
        p.grayscale = z2.checkbox("Black & white", p.grayscale, key=L("gs"))
        t_on = st.checkbox("Colour tint", p.tint is not None, key=L("tint_on"))
        if t_on:
            t1, t2 = st.columns(2)
            p.tint = _cp(t1, "Tint colour", p.tint or (20, 20, 60), L("tc"))
            p.tint_alpha = _sl(t2, "Tint strength", 0.0, 0.9, p.tint_alpha, 0.01, L("ta"))
        else:
            p.tint = None


def _ui_images(st, car, spec, K) -> None:
    ss = st.session_state
    with st.expander("🏷️ Logo", expanded=True):
        lg = spec.logo
        lg.visible = st.checkbox("Show logo", lg.visible, key=K("lg_vis"))
        up = st.file_uploader("Upload logo (PNG with transparency works best)", type=["png", "jpg", "jpeg", "webp"], key=K("lg_up"))
        if up is not None and lg.src != _tag(up):
            im = _ingest(up)
            if im is not None:
                lg.image, lg.src = im, _tag(up)
        if lg.image is None:
            lg.name = st.text_input("Placeholder logo name", lg.name, key=K("lg_name"))
        else:
            st.button("Remove logo", key=K("lg_rm"), on_click=_cb_remove_logo, args=(st, spec))
        lg.auto = st.checkbox("Auto-position (use the template's logo slot)", lg.auto, key=K("lg_auto"))
        if not lg.auto:
            c1, c2 = st.columns(2)
            lg.x = _sl(c1, "X", 0.0, 1.0, lg.x, 0.005, K("lg_x"))
            lg.y = _sl(c2, "Y", 0.0, 1.0, lg.y, 0.005, K("lg_y"))
        lg.w = _sl(st, "Width", 0.04, 0.6, lg.w, 0.005, K("lg_w"))
        _style_controls(st, lg, lambda n: K(f"lg_{n}"))

    with st.expander("Background picture"):
        bg = spec.background
        up = st.file_uploader("Upload background", type=["png", "jpg", "jpeg", "webp"], key=K("bg_up"))
        if up is not None and bg.src != _tag(up):
            im = _ingest(up)
            if im is not None:
                bg.image, bg.src = im, _tag(up)
        if bg.image is None:
            st.caption("Photo templates (Card & Photo, Split Photo, Polaroid) fill their photo slot; other templates use it full-bleed.")
        else:
            st.button("Remove background", key=K("bg_rm"), on_click=_cb_remove_bg, args=(st, spec))
            labels = {"auto": "Auto (photo slot or full-bleed)", "full": "Full-bleed behind the template", "custom": "Custom box"}
            bg.placement = st.selectbox("Placement", list(labels), index=list(labels).index(bg.placement) if bg.placement in labels else 0,
                                        format_func=lambda k: labels[k], key=K("bg_pl"))
            _picture_controls(st, bg, lambda n: K(f"bg_{n}"), with_box=bg.placement == "custom")

    with st.expander("Overlay pictures", expanded=True):
        counter = f"upn_{spec.id}"
        key = f"addpic_{spec.id}_{ss.get(counter, 0)}"
        st.file_uploader("Add a picture", type=["png", "jpg", "jpeg", "webp"], key=key,
                         on_change=_cb_add_picture, args=(st, spec, key, counter))
        if spec.pictures:
            ids = [p.id for p in spec.pictures]
            if spec.sel_pic not in ids:
                spec.sel_pic = ids[0]
            lookup = {p.id: p for p in spec.pictures}
            spec.sel_pic = st.selectbox("Edit picture", ids, index=ids.index(spec.sel_pic), key=K("sel_pic"),
                                        format_func=lambda i: f"{'👁' if lookup[i].visible else '🚫'}  {lookup[i].label}")
            pic = lookup[spec.sel_pic]
            L = lambda n: K(f"{pic.id}_{n}")
            o = st.columns(3)
            pic.visible = o[0].checkbox("Visible", pic.visible, key=L("vis"))
            o[1].button("Duplicate", key=L("dup"), on_click=_cb_pic_op, args=(st, spec, pic, "dup"))
            o[2].button("🗑 Delete", key=L("del"), on_click=_cb_pic_op, args=(st, spec, pic, "del"))
            _picture_controls(st, pic, L)


def _ui_effects(st, car, spec, K) -> None:
    fx = spec.effects
    fx.look = st.selectbox("Look / filter", LOOKS, index=LOOKS.index(fx.look) if fx.look in LOOKS else 0, key=K("look"))
    c1, c2, c3 = st.columns(3)
    fx.brightness = _sl(c1, "Brightness", 0.5, 1.6, fx.brightness, 0.02, K("fb"))
    fx.contrast = _sl(c2, "Contrast", 0.5, 1.6, fx.contrast, 0.02, K("fc"))
    fx.saturation = _sl(c3, "Saturation", 0.0, 2.0, fx.saturation, 0.02, K("fs"))
    d1, d2 = st.columns(2)
    fx.vignette = _sl(d1, "Vignette", 0.0, 1.0, fx.vignette, 0.02, K("fv"))
    fx.grain = _sl(d2, "Film grain", 0.0, 1.0, fx.grain, 0.02, K("fg"))
    e1, e2 = st.columns(2)
    fx.fade_bottom = _sl(e1, "Fade to colour - bottom", 0.0, 1.0, fx.fade_bottom, 0.02, K("ffb"))
    fx.fade_top = _sl(e2, "Fade to colour - top", 0.0, 1.0, fx.fade_top, 0.02, K("fft"))
    if fx.fade_bottom or fx.fade_top:
        fx.fade_color = _cp(st, "Fade colour", fx.fade_color, K("ffc"))
    st.button("Apply these effects to all slides", key=K("fx_all"), on_click=_cb_effects_all, args=(st, car, spec))


def _store(st, key: str, data: bytes, name: str, mime: str, car: CarouselSpec) -> None:
    st.session_state["exports"][key] = (data, name, mime, _signature(car))


def _offer(st, key: str, label: str, car: CarouselSpec) -> None:
    ex = st.session_state["exports"].get(key)
    if not ex:
        return
    if ex[3] != _signature(car):
        st.warning("You've edited the design since this file was built - rebuild it to include the latest changes.")
    st.download_button(label, ex[0], file_name=ex[1], mime=ex[2], key=f"dl_{key}")


def _ui_preview(st, car, spec, K) -> None:
    ss = st.session_state
    st.subheader("Live canvas")
    flash = ss.pop("flash", None)
    if flash:
        getattr(st, flash[0])(flash[1])
    img = render(spec, scale=ss.get("preview_scale", 0.55), guides=ss.get("guides", False),
                 page=(car.active_index, len(car.slides)), indicator=car.indicator, swipe=car.swipe)
    _img(st, img)
    w, h = FORMATS.get(spec.fmt, FORMATS["square"])
    st.caption(f"{spec.fmt} · {w}×{h}px · template: {spec.template}")


def _ui_export(st, car, spec, K) -> None:
    ss = st.session_state
    st.subheader("Export")
    t_img, t_car, t_reel = st.tabs(["This slide", "Carousel", "Reel"])
    with t_img:
        c1, c2, c3 = st.columns(3)
        fmt = c1.selectbox("Format", ["PNG", "JPEG", "WEBP"], key="ex_fmt")
        q = c2.slider("Quality", 60, 100, 92, key="ex_q", disabled=fmt == "PNG")
        sc = c3.select_slider("Size", options=[0.5, 1.0, 1.5, 2.0], value=1.0, key="ex_sc", format_func=lambda v: f"{v}×")
        if st.button("Build image", key="b_img", type="primary"):
            with st.spinner("Rendering..."):
                im = render(spec, scale=sc, page=(car.active_index, len(car.slides)), indicator=car.indicator, swipe=car.swipe)
                data, mime, ext = export_image(im, fmt, q)
            _store(st, "img", data, f"slide_{car.active_index + 1}.{ext}", mime, car)
        _offer(st, "img", "Download slide", car)
    with t_car:
        c1, c2, c3 = st.columns(3)
        cfmt = c1.selectbox("Image format", ["PNG", "JPEG", "WEBP"], key="cx_fmt")
        cq = c2.slider("Quality", 60, 100, 92, key="cx_q", disabled=cfmt == "PNG")
        csc = c3.select_slider("Size", options=[0.5, 1.0, 1.5, 2.0], value=1.0, key="cx_sc", format_func=lambda v: f"{v}×")
        b1, b2 = st.columns(2)
        if b1.button("Build ZIP", key="b_zip"):
            with st.spinner("Rendering slides..."):
                _store(st, "zip", export_carousel_zip(car, cfmt, cq, csc), "carousel.zip", "application/zip", car)
        if b2.button("Build PDF", key="b_pdf"):
            with st.spinner("Rendering slides..."):
                _store(st, "pdf", export_carousel_pdf(car, csc), "carousel.pdf", "application/pdf", car)
        _offer(st, "zip", "Download carousel (ZIP)", car)
        _offer(st, "pdf", "Download carousel (PDF)", car)
    with t_reel:
        r1, r2 = st.columns([2, 1])
        dur = r1.slider("Total length (seconds)", 5, 60, 18, key="rl_dur", help="Slides with their own 'Reel seconds' keep that length.")
        fps = r2.select_slider("FPS", options=[12, 15, 24, 30], value=15, key="rl_fps")
        a1, a2 = st.columns(2)
        trans = a1.selectbox("Transition", TRANSITIONS, key="rl_tr")
        kb = a2.selectbox("Camera motion", KENBURNS, key="rl_kb")
        b1, b2, b3 = st.columns(3)
        reveal = b1.checkbox("Text reveal", True, key="rl_rev", help="Slides start without text, then the text fades in.")
        anim = b2.checkbox("Animate", True, key="rl_an")
        res = b3.selectbox("Resolution", ["720p", "Full (1080p)"], key="rl_res")
        if st.button("🎥 Build reel", type="primary", key="b_reel"):
            bar = st.progress(0.0, text="Rendering reel...")
            try:
                data, ext = export_reel_video(car.slides, duration=dur, fps=fps, animate=anim, transition=trans, kenburns=kb,
                                              reveal_text=reveal, out_scale=0.667 if res == "720p" else 1.0,
                                              progress=lambda p: bar.progress(p, text="Rendering reel..."))
                bar.empty()
                _store(st, "reel", data, f"reel.{ext}", f"video/{ext}" if ext == "mp4" else "image/gif", car)
                if ext == "gif":
                    st.warning("Saved as an animated GIF (480px) because MP4 encoding isn't available. "
                               "Run `pip install imageio imageio-ffmpeg` for real MP4 output.")
                else:
                    st.success("Reel ready!")
            except Exception as exc:
                bar.empty()
                st.error(f"Couldn't build the reel: {exc}")
        ex = ss["exports"].get("reel")
        if ex:
            if ex[1].endswith("mp4"):
                st.video(ex[0])
            else:
                st.image(ex[0])
            _offer(st, "reel", f"Download reel ({ex[1].split('.')[-1].upper()})", car)
        if not HAS_IMAGEIO:
            st.caption("⚠️ `imageio` isn't installed, so reels save as animated GIF. `pip install imageio imageio-ffmpeg` enables MP4.")


def post_studio_ui() -> None:
    import streamlit as st

    try:
        st.set_page_config(layout="wide", page_title="Post, Carousel & Reel Studio", page_icon="🎨")
    except Exception:
        pass
    ss = st.session_state
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown('<h1 class="studio-title">Post, Carousel &amp; Reel Studio</h1>', unsafe_allow_html=True)

    ss.setdefault("gen", 0)
    ss.setdefault("hist", [])
    ss.setdefault("redo", [])
    ss.setdefault("exports", {})
    if "carousel" not in ss:
        ss["carousel"] = CarouselSpec(slides=[new_post(
            "Design stunning posts, carousels & reels",
            "Vector templates, gradients, shapes, pictures, effects and animated exports - all in one studio.",
            "Get started ➔", template="stripes", theme="aurora")])
    car: CarouselSpec = ss["carousel"]

    generated = ss.pop("generated_post_text", None)  # hook: other parts of an app can inject copy
    if generated:
        _apply_generated_copy(car.slides[car.active_index], generated)
        _bump(st)

    car.active_index = int(_clamp(car.active_index, 0, len(car.slides) - 1))
    _commit(st)

    gaps = missing_fonts()
    if gaps:
        st.warning(f"⚠️ {len(gaps)} of {len(FONT_FAMILIES)} fonts weren't found, so choosing them has no visible effect: "
                   f"**{', '.join(gaps)}**. Deploy the `assets/fonts/` folder next to post_studio.py and restart.")

    _ui_sidebar(st, car)
    _ui_strip(st, car)
    spec = car.slides[car.active_index]

    def K(name: str) -> str:
        return f"{name}_{spec.id}_{ss['gen']}"

    st.divider()
    _ui_toolbar(st, car, spec, K)
    left, right = st.columns([5, 6], gap="medium")
    with left:
        tabs = st.tabs(["Design", "Text", "Elements", "Images", "Effects"])
        with tabs[0]:
            _ui_design(st, car, spec, K)
        with tabs[1]:
            _ui_text(st, car, spec, K)
        with tabs[2]:
            _ui_elements(st, car, spec, K)
        with tabs[3]:
            _ui_images(st, car, spec, K)
        with tabs[4]:
            _ui_effects(st, car, spec, K)
    with right:
        _ui_preview(st, car, spec, K)
        _ui_export(st, car, spec, K)


if __name__ == "__main__":
    post_studio_ui()