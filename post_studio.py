"""
post_studio.py
==============

Post generator rebuilt against the client's supplied sample set
(Sample_Posts_and_Reels: Lifestyle, Retirement, Protection, Mortgage,
Pension, Investment & Estate Planning).

The samples share one visual system, and this module encodes it rather than
inventing a new one:

  * flat two-tone fields — a saturated background with a solid content card
    laid over it, hard 90-degree corners, no gradients or drop shadows
  * offset "peek" slabs behind the card in a third tint, top/bottom/side
  * photography as a full-bleed band or side panel butted against the card,
    never floating and never squashed
  * headline in heavy sans, left / centre / right aligned per layout, with a
    short rule under it on some layouts
  * chrome as a single line: tracked "L O G O" one side, handset glyph and
    phone number the other, top or bottom of the canvas

Public API
----------
    render(spec)                 -> PIL.Image
    variants(spec, n=4)          -> list[PIL.Image]
    build_post_canvas(...)       -> PIL.Image   (legacy call site)
    post_studio_ui()             -> Streamlit panel

Every dimension is a fraction of the canvas or a design unit (1 unit = 1px at
1080 wide), so one layout definition serves square, portrait, story and
landscape at any export scale.
"""

from __future__ import annotations

import colorsys
import hashlib
import math
import os
import random
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
Box = tuple[float, float, float, float]

__all__ = [
    "Palette", "Brand", "PostSpec",
    "THEMES", "FORMATS", "TEMPLATES", "TEMPLATE_LABELS", "CATEGORY_PRESETS",
    "render", "variants", "build_post_canvas",
    "palette_from_image", "missing_fonts", "post_studio_ui",
]


# ---------------------------------------------------------------------------
# Tokens — palettes lifted from the client's sample posts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Palette:
    """Four flat colours per theme: field, card, peek slab, and the ink on each."""

    key: str
    label: str
    bg: RGB          # the saturated field behind everything
    card: RGB        # the solid content card
    peek: RGB        # offset slabs / blobs that sit between bg and card
    ink: RGB         # text on the card
    ink_muted: RGB   # secondary text on the card
    on_bg: RGB       # chrome text sitting directly on the field
    cta_bg: RGB      # CTA pill fill
    cta_ink: RGB     # CTA pill text
    pale: RGB        # pale card, for layouts that need dark text
    pale_ink: RGB    # text on the pale card


THEMES: dict[str, Palette] = {
    "periwinkle": Palette(
        key="periwinkle", label="Periwinkle / charcoal",
        bg=(107, 111, 196), card=(62, 59, 74), peek=(198, 194, 250),
        ink=(255, 255, 255), ink_muted=(216, 214, 224), on_bg=(255, 255, 255),
        cta_bg=(198, 194, 250), cta_ink=(48, 45, 60),
        pale=(238, 236, 252), pale_ink=(46, 44, 58),
    ),
    "royal": Palette(
        key="royal", label="Royal blue / mint",
        bg=(16, 70, 139), card=(126, 227, 152), peek=(138, 224, 246),
        ink=(12, 26, 20), ink_muted=(38, 66, 50), on_bg=(255, 255, 255),
        cta_bg=(12, 38, 72), cta_ink=(255, 255, 255),
        pale=(240, 250, 243), pale_ink=(14, 40, 26),
    ),
    "plum": Palette(
        key="plum", label="Plum / coral",
        bg=(122, 87, 118), card=(232, 131, 107), peek=(214, 210, 208),
        ink=(255, 255, 255), ink_muted=(252, 232, 226), on_bg=(255, 255, 255),
        cta_bg=(255, 255, 255), cta_ink=(154, 72, 52),
        pale=(250, 236, 230), pale_ink=(96, 46, 34),
    ),
    "maroon": Palette(
        key="maroon", label="Maroon / forest",
        bg=(126, 26, 26), card=(31, 107, 87), peek=(247, 235, 200),
        ink=(255, 255, 255), ink_muted=(216, 232, 226), on_bg=(255, 255, 255),
        cta_bg=(247, 235, 200), cta_ink=(31, 70, 58),
        pale=(247, 235, 200), pale_ink=(58, 26, 22),
    ),
    "mustard": Palette(
        key="mustard", label="Charcoal / mustard",
        bg=(34, 34, 40), card=(242, 194, 48), peek=(88, 58, 138),
        ink=(28, 26, 22), ink_muted=(74, 66, 40), on_bg=(255, 255, 255),
        cta_bg=(28, 26, 22), cta_ink=(242, 194, 48),
        pale=(250, 240, 210), pale_ink=(34, 32, 28),
    ),
    "ivory": Palette(
        key="ivory", label="Navy / ivory",
        bg=(20, 40, 72), card=(248, 243, 233), peek=(196, 140, 92),
        ink=(22, 28, 38), ink_muted=(84, 92, 104), on_bg=(255, 255, 255),
        cta_bg=(20, 40, 72), cta_ink=(255, 255, 255),
        pale=(248, 243, 233), pale_ink=(22, 28, 38),
    ),
    "brick": Palette(
        key="brick", label="Brick / deep navy",
        bg=(178, 58, 46), card=(30, 58, 95), peek=(246, 219, 210),
        ink=(255, 255, 255), ink_muted=(212, 222, 236), on_bg=(255, 255, 255),
        cta_bg=(246, 219, 210), cta_ink=(30, 58, 95),
        pale=(246, 219, 210), pale_ink=(64, 26, 20),
    ),
    "sage": Palette(
        key="sage", label="Slate / sage",
        bg=(74, 90, 92), card=(150, 182, 165), peek=(238, 240, 232),
        ink=(20, 32, 30), ink_muted=(52, 70, 64), on_bg=(255, 255, 255),
        cta_bg=(28, 44, 42), cta_ink=(255, 255, 255),
        pale=(238, 240, 232), pale_ink=(24, 38, 34),
    ),
}

# The client's folders map to sensible defaults, so a topic picks its own look.
CATEGORY_PRESETS: dict[str, dict[str, str]] = {
    "Protection": {"theme": "periwinkle", "template": "offset_card"},
    "Retirement": {"theme": "royal", "template": "card_photo"},
    "Pension": {"theme": "sage", "template": "photo_side"},
    "Mortgage": {"theme": "brick", "template": "banner"},
    "Investment and Estate Planning": {"theme": "ivory", "template": "circle_photo"},
    "Lifestyle": {"theme": "mustard", "template": "arc"},
}

FORMATS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "landscape": (1200, 628),
}

FONT_STACKS: dict[str, Sequence[str]] = {
    "display": (
        "Poppins-Bold", "Poppins-SemiBold", "Montserrat-Bold", "Inter-Bold",
        "Archivo-Bold", "Carlito-Bold", "LiberationSans-Bold", "DejaVuSans-Bold",
        "FreeSansBold", "arialbd",
    ),
    "serif": (
        "Lora-Bold", "Lora-Variable", "PlayfairDisplay-Bold",
        "DejaVuSerif-Bold", "LiberationSerif-Bold", "georgiab",
    ),
    "body": (
        "Poppins-Regular", "Inter-Regular", "Carlito-Regular",
        "LiberationSans-Regular", "DejaVuSans", "FreeSans", "arial",
    ),
    "body-medium": (
        "Poppins-Medium", "Poppins-SemiBold", "Inter-Medium", "Carlito-Bold",
        "LiberationSans-Bold", "DejaVuSans-Bold", "arialbd",
    ),
}

FONT_DIRS = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts"),
    "/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
    "C:/Windows/Fonts",
)


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


@dataclass
class Brand:
    name: str = "LOGO"
    phone: str = "+123-456-789"
    website: str = "www.reallygreatsite.com"
    logo_path: str | Image.Image | None = None
    disclaimer: str = ""

    def wordmark(self) -> str:
        return self.name.upper()


@dataclass
class PostSpec:
    headline: str
    body: str = ""
    bullets: Sequence[str] = field(default_factory=tuple)
    cta: str = ""
    category: str = "Protection"
    brand: Brand = field(default_factory=Brand)
    image: str | Image.Image | None = None
    image_focus: tuple[float, float] = (0.5, 0.4)
    template: str = "offset_card"
    theme: str = "periwinkle"
    fmt: str = "square"
    align: str = "auto"            # auto | left | center | right
    headline_face: str = "display"  # display | serif
    divider: bool = False           # short rule under the headline
    chrome: str = "auto"            # auto | bottom | top | none
    show_website: bool = False
    scale: float = 1.0
    seed: int | None = None


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for root in FONT_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                stem, ext = os.path.splitext(name)
                if ext.lower() in (".ttf", ".otf"):
                    index.setdefault(stem.lower(), os.path.join(dirpath, name))
    return index


@lru_cache(maxsize=64)
def _resolve(role: str) -> str | None:
    index = _font_index()
    for cand in FONT_STACKS.get(role, ()):
        hit = index.get(cand.lower())
        if hit:
            return hit
    for cand in FONT_STACKS.get(role, ()):
        needle = cand.lower().replace("-", "")
        for stem, path in index.items():
            if needle in stem.replace("-", ""):
                return path
    return None


@lru_cache(maxsize=512)
def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(6, int(size))
    path = _resolve(role)
    if path:
        try:
            f = ImageFont.truetype(path, size=size)
            if role in ("display", "serif", "body-medium"):
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
            return f
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def missing_fonts() -> list[str]:
    return [role for role in FONT_STACKS if _resolve(role) is None]


# ---------------------------------------------------------------------------
# Colour + paint
# ---------------------------------------------------------------------------


def _luminance(c: RGB) -> float:
    lin = []
    for v in (c[0] / 255, c[1] / 255, c[2] / 255):
        lin.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (round(a[0] + (b[0] - a[0]) * t),
            round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t))


def _alpha(c: RGB, a: float) -> RGBA:
    return (c[0], c[1], c[2], max(0, min(255, round(a * 255))))


def _readable_on(bg: RGB) -> RGB:
    return (20, 24, 28) if _luminance(bg) > 0.45 else (255, 255, 255)


def cover_crop(img: Image.Image, size: tuple[int, int],
               focus: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    """Fill the panel without distorting — the original code stretched photos."""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    return ImageOps.fit(img.convert("RGB"), (w, h),
                        method=Image.Resampling.LANCZOS, centering=focus)


def circle_mask(size: tuple[int, int]) -> Image.Image:
    ss = 4
    big = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size[0] * ss - 1, size[1] * ss - 1), fill=255)
    return big.resize(size, Image.Resampling.LANCZOS)


def palette_from_image(img: Image.Image, base: Palette) -> Palette:
    """Pull the card colour out of an uploaded photo when asked."""
    small = img.convert("RGB").resize((96, 96))
    reduced = small.quantize(colors=8, method=Image.Quantize.FASTOCTREE).convert("RGB")
    best, best_score = None, -1.0
    for count, colour in sorted(reduced.getcolors(9216) or [], key=lambda c: -c[0])[:8]:
        mx, mn = max(colour), min(colour)
        sat = 0.0 if mx == 0 else (mx - mn) / mx
        lum = _luminance(colour)
        if lum < 0.10 or lum > 0.92:
            continue
        score = sat * 2.2 + (count / 9216) * 0.8
        if score > best_score:
            best, best_score = colour, score
    if best is None:
        return base
    h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in best))
    card = tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, 0.58, min(1.0, max(0.5, s))))
    bg = tuple(round(v * 255) for v in colorsys.hls_to_rgb((h + 0.5) % 1.0, 0.26, min(1.0, max(0.4, s))))
    peek = tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, 0.86, min(1.0, s * 0.8)))
    ink = _readable_on(card)  # type: ignore[arg-type]
    pale = tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, 0.93, min(1.0, s * 0.55)))
    return replace(base, key=base.key + "-auto", bg=bg, card=card, peek=peek,  # type: ignore[arg-type]
                   ink=ink, ink_muted=_mix(ink, card, 0.28), cta_bg=bg,
                   cta_ink=_readable_on(bg), pale=pale, pale_ink=_readable_on(pale))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Text engine
# ---------------------------------------------------------------------------


def text_width(f: ImageFont.FreeTypeFont, s: str, tracking: float = 0.0) -> float:
    if not s:
        return 0.0
    return f.getlength(s) + tracking * (len(s) - 1)


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float, tracking: float = 0.0) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words, current = para.split(), ""
        if not words:
            lines.append("")
            continue
        for word in words:
            trial = f"{current} {word}".strip()
            if text_width(f, trial, tracking) <= max_w or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _balanced(text: str, f: ImageFont.FreeTypeFont, max_w: float, tracking: float = 0.0) -> list[str]:
    """Even line lengths at the same line count — the samples never orphan a word."""
    base = wrap(text, f, max_w, tracking)
    if len(base) < 2:
        return base
    best = base
    best_spread = _spread(best, f, tracking)
    for pct in (0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72):
        trial = wrap(text, f, max_w * pct, tracking)
        if len(trial) != len(base):
            continue
        spread = _spread(trial, f, tracking)
        if spread < best_spread:
            best, best_spread = trial, spread
    return best


def _spread(lines: list[str], f: ImageFont.FreeTypeFont, tracking: float) -> float:
    widths = [text_width(f, ln, tracking) for ln in lines]
    return max(widths) - min(widths)


def fit_text(text: str, role: str, max_w: float, max_h: float, hi: int, lo: int,
             leading: float = 1.08, max_lines: int | None = None,
             tracking: float = 0.0, balance: bool = True):
    """Biggest size that fits the box. Copy is never silently clipped."""
    size = max(int(hi), int(lo))
    lo = max(6, int(lo))
    while size > lo:
        f = font(role, size)
        lines = _balanced(text, f, max_w, tracking) if balance else wrap(text, f, max_w, tracking)
        if len(lines) * size * leading <= max_h and (max_lines is None or len(lines) <= max_lines):
            return f, lines, size
        size -= max(1, round(size * 0.04))
    f = font(role, lo)
    return f, wrap(text, f, max_w, tracking), lo


def draw_lines(c: "Canvas", lines: Sequence[str], f: ImageFont.FreeTypeFont, size: float,
               box: Box, y: float, fill: RGB, align: str, leading: float) -> float:
    x0, _, x1, _ = box
    for line in lines:
        if align == "center":
            c.draw.text(((x0 + x1) / 2, y), line, font=f, fill=(*fill, 255), anchor="ma")
        elif align == "right":
            c.draw.text((x1, y), line, font=f, fill=(*fill, 255), anchor="ra")
        else:
            c.draw.text((x0, y), line, font=f, fill=(*fill, 255), anchor="la")
        y += size * leading
    return y


def draw_tracked(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
                 f: ImageFont.FreeTypeFont, fill: RGBA, tracking: float) -> float:
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill, anchor="lm")
        x += f.getlength(ch) + tracking
    return x - tracking - xy[0]


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------


class Canvas:
    def __init__(self, size: tuple[int, int], pal: Palette, seed: int = 0):
        self.size = size
        self.w, self.h = size
        self.pal = pal
        self.rng = random.Random(seed)
        self.u = self.w / 1080.0
        self.img = Image.new("RGBA", size, (*pal.bg, 255))
        self.draw = ImageDraw.Draw(self.img)

    def s(self, v: float) -> float:
        """Design unit -> pixels."""
        return v * self.u

    def fx(self, t: float) -> float:
        return self.w * t

    def fy(self, t: float) -> float:
        return self.h * t

    @property
    def margin(self) -> float:
        return self.s(66)

    def rect(self, box: Box, fill: RGB, radius: float = 0) -> None:
        if radius:
            self.draw.rounded_rectangle(box, radius=radius, fill=(*fill, 255))
        else:
            self.draw.rectangle(box, fill=(*fill, 255))

    def layer(self) -> Image.Image:
        return Image.new("RGBA", self.size, (0, 0, 0, 0))

    def merge(self, layer: Image.Image) -> None:
        self.img = Image.alpha_composite(self.img, layer)
        self.draw = ImageDraw.Draw(self.img)

    def photo(self, img: Image.Image, box: Box, focus: tuple[float, float],
              mask: Image.Image | None = None) -> None:
        x0, y0, x1, y1 = (int(v) for v in box)
        panel = cover_crop(img, (x1 - x0, y1 - y0), focus)
        if mask is not None and mask.size != panel.size:
            mask = mask.resize(panel.size, Image.Resampling.LANCZOS)
        self.img.paste(panel, (x0, y0), mask)
        self.draw = ImageDraw.Draw(self.img)

    def result(self) -> Image.Image:
        return self.img.convert("RGB")


# ---------------------------------------------------------------------------
# Chrome: logo wordmark + handset glyph + phone number
# ---------------------------------------------------------------------------


def _handset(c: Canvas, cx: float, cy: float, size: float, colour: RGB) -> None:
    """Vector handset hook, drawn rather than relying on an icon font."""
    n = max(48, int(size * 6))
    tile = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    fill = (*colour, 255)
    pad, stroke = n * 0.12, n * 0.25
    d.arc((pad, pad, n - pad, n - pad), start=200, end=340, fill=fill, width=int(stroke))
    r = (n - 2 * pad) / 2 - stroke / 2
    for ang in (200, 340):                      # round off both ends of the hook
        rad = math.radians(ang)
        x, y = n / 2 + r * math.cos(rad), n / 2 + r * math.sin(rad)
        d.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=fill)
    tile = tile.rotate(-45, resample=Image.Resampling.BICUBIC)
    side = max(1, int(size))
    tile = tile.resize((side, side), Image.Resampling.LANCZOS)
    layer = c.layer()
    layer.paste(tile, (int(cx - side / 2), int(cy - side / 2)), tile)
    c.merge(layer)


def _chrome(c: Canvas, spec: PostSpec, position: str, ink: RGB,
            strip: RGB | None = None) -> None:
    """One line: wordmark left, phone right. Matches the sample set exactly."""
    if position == "none":
        return
    y = c.fy(0.062) if position == "top" else c.h - c.fy(0.062)
    if strip is not None:
        # Keep the line off any photography underneath it.
        band = (0, y - c.fy(0.062), c.w, y + c.fy(0.062)) if position == "top" \
            else (0, y - c.fy(0.062), c.w, c.h)
        c.rect(band, strip)
    x = c.margin

    logo = _open_image(spec.brand.logo_path)
    if logo is not None:
        target = c.s(44)
        ratio = target / logo.height
        logo = logo.resize((max(1, int(logo.width * ratio)), int(target)), Image.Resampling.LANCZOS)
        layer = c.layer()
        layer.paste(logo, (int(x), int(y - target / 2)), logo if logo.mode == "RGBA" else None)
        c.merge(layer)
    else:
        f = font("body-medium", c.s(25))
        draw_tracked(c.draw, (x, y), spec.brand.wordmark(), f, (*ink, 255), c.s(6))

    right = c.w - c.margin
    if spec.brand.phone:
        f = font("display", c.s(26))
        num_w = f.getlength(spec.brand.phone)
        c.draw.text((right, y), spec.brand.phone, font=f, fill=(*ink, 255), anchor="rm")
        _handset(c, right - num_w - c.s(32), y, c.s(34), ink)
        right = right - num_w - c.s(64)

    if spec.show_website and spec.brand.website:
        f = font("body", c.s(20))
        c.draw.text((right - c.s(16), y), spec.brand.website, font=f,
                    fill=(*ink, 230), anchor="rm")


# ---------------------------------------------------------------------------
# The card: the block every layout is built around
# ---------------------------------------------------------------------------


def _cta(c: Canvas, box: Box, y: float, label: str, align: str,
         bg: RGB, ink: RGB) -> float:
    f = font("body-medium", c.s(23))
    pad_x, pad_y = c.s(30), c.s(16)
    w = f.getlength(label) + pad_x * 2
    h = c.s(23) * 1.3 + pad_y * 2
    x0, _, x1, _ = box
    if align == "center":
        x = (x0 + x1) / 2 - w / 2
    elif align == "right":
        x = x1 - w
    else:
        x = x0
    c.draw.rounded_rectangle((x, y, x + w, y + h), radius=h / 2, fill=(*bg, 255))
    c.draw.text((x + w / 2, y + h / 2), label, font=f, fill=(*ink, 255), anchor="mm")
    return y + h


def _card_content(c: Canvas, spec: PostSpec, box: Box, align: str,
                  ink: RGB, ink_muted: RGB, pad: float | None = None,
                  headline_cap: float = 100.0) -> None:
    """Headline, optional rule, body or bullets, optional CTA — vertically centred."""
    pad = c.s(54) if pad is None else pad
    x0, y0, x1, y1 = box[0] + pad, box[1] + pad, box[2] - pad, box[3] - pad
    col_w, col_h = max(c.s(80), x1 - x0), max(c.s(80), y1 - y0)
    inner: Box = (x0, y0, x1, y1)

    bullets = [b for b in spec.bullets if b.strip()]
    hf, hlines, hsize = fit_text(
        spec.headline, spec.headline_face, col_w, col_h * (0.52 if (spec.body or bullets) else 0.86),
        hi=int(c.s(headline_cap)), lo=int(c.s(34)), leading=1.12, max_lines=4,
    )
    block = len(hlines) * hsize * 1.12

    rule_gap = c.s(40) if spec.divider else 0.0
    block += rule_gap * 2 if spec.divider else 0.0

    bf = blines = bsize = None
    if bullets:
        remaining = col_h - block - c.s(46)
        bsize = int(min(c.s(32), max(c.s(18), remaining / max(1, len(bullets)) * 0.46)))
        bf = font("body", bsize)
        wrapped = [_balanced(b, bf, col_w - c.s(40))[:2] for b in bullets[:6]]
        block += c.s(46) + sum(len(w) * bsize * 1.34 for w in wrapped) + c.s(14) * (len(wrapped) - 1)
    elif spec.body:
        bf, blines, bsize = fit_text(
            spec.body, "body", col_w, col_h - block - c.s(46),
            hi=int(c.s(36)), lo=int(c.s(19)), leading=1.42, max_lines=6,
        )
        block += c.s(46) + len(blines) * bsize * 1.42

    cta_h = (c.s(23) * 1.3 + c.s(16) * 2 + c.s(44)) if spec.cta else 0.0
    block += cta_h

    y = y0 + max(0.0, (col_h - block) / 2)
    y = draw_lines(c, hlines, hf, hsize, inner, y, ink, align, 1.12)

    if spec.divider:
        y += rule_gap
        rw = c.s(150)
        rx = {"center": (x0 + x1) / 2 - rw / 2, "right": x1 - rw}.get(align, x0)
        c.draw.line((rx, y, rx + rw, y), fill=(*ink, 255), width=max(2, int(c.s(3))))
        y += rule_gap

    if bullets and bf and bsize:
        y += c.s(46)
        for item in bullets[:6]:
            lines = _balanced(item, bf, col_w - c.s(40))[:2]
            dot = c.s(7)
            dot_y = y + bsize * 0.62
            c.draw.ellipse((x0, dot_y - dot, x0 + dot * 2, dot_y + dot), fill=(*ink, 255))
            for line in lines:
                c.draw.text((x0 + c.s(32), y), line, font=bf, fill=(*ink, 255), anchor="la")
                y += bsize * 1.34
            y += c.s(14)
    elif blines and bf and bsize:
        y += c.s(46)
        y = draw_lines(c, blines, bf, bsize, inner, y, ink_muted, align, 1.42)

    if spec.cta:
        _cta(c, inner, y + c.s(44), spec.cta, align, c.pal.cta_bg, c.pal.cta_ink)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _open_image(src) -> Image.Image | None:
    if src is None:
        return None
    if isinstance(src, Image.Image):
        return src.copy()
    try:
        with Image.open(src) as im:
            return im.convert("RGB")
    except Exception:
        return None


def tpl_offset_card(c: Canvas, spec: PostSpec, photo: Image.Image | None) -> None:
    """Solid card bleeding off the left edge with peek slabs above and below."""
    pal = c.pal
    card: Box = (0, c.fy(0.098), c.fx(0.923), c.fy(0.776))
    c.rect((c.fx(0.195), c.fy(0.080), c.fx(0.835), c.fy(0.118)), pal.peek)
    c.rect((c.fx(0.098), c.fy(0.760), c.fx(0.760), c.fy(0.869)), pal.peek)
    c.rect((c.fx(0.955), c.fy(0.170), c.fx(1.0), c.fy(0.790)), pal.peek)
    c.rect(card, pal.card)
    align = "left" if spec.align == "auto" else spec.align
    _card_content(c, spec, (card[0] + c.s(40), card[1], card[2], card[3]), align,
                  pal.ink, pal.ink_muted)
    _chrome(c, spec, "bottom" if spec.chrome in ("auto", "bottom") else spec.chrome, pal.on_bg)


def tpl_card_photo(c: Canvas, spec: PostSpec, photo: Image.Image | None) -> None:
    """Card on top, full-bleed photo band underneath. Chrome sits at the top."""
    pal = c.pal
    band_top = c.fy(0.775)
    card: Box = (c.fx(0.10), c.fy(0.098), c.w, band_top)
    c.rect((c.fx(0.055), c.fy(0.18), c.fx(0.105), c.fy(0.71)), pal.peek)
    c.rect(card, pal.card)

    if photo is not None:
        c.photo(photo, (0, band_top, c.fx(0.79), c.h), spec.image_focus)
        c.rect((c.fx(0.79), band_top, c.fx(0.875), band_top + c.fy(0.055)), pal.peek)
    else:
        c.rect((0, band_top, c.fx(0.79), c.h), _mix(pal.card, pal.bg, 0.55))

    align = "center" if spec.align == "auto" else spec.align
    _card_content(c, spec, (card[0], card[1], card[2] - c.s(30), card[3]), align,
                  pal.ink, pal.ink_muted)
    _chrome(c, spec, "top" if spec.chrome in ("auto", "top") else spec.chrome, pal.on_bg)


def tpl_photo_side(c: Canvas, spec: PostSpec, photo: Image.Image | None) -> None:
    """Photo panel down one side, card overlapping it. Portrait-friendly."""
    pal = c.pal
    tall = c.h / c.w > 1.15
    if tall:
        photo_box: Box = (0, 0, c.w, c.fy(0.40))
        card: Box = (c.fx(0.07), c.fy(0.34), c.fx(0.97), c.fy(0.86))
        peek: Box = (c.fx(0.11), c.fy(0.86), c.fx(0.93), c.fy(0.895))
    else:
        photo_box = (c.fx(0.56), 0, c.w, c.h)
        card = (c.fx(0.06), c.fy(0.13), c.fx(0.70), c.fy(0.87))
        peek = (c.fx(0.02), c.fy(0.20), c.fx(0.06), c.fy(0.80))

    if photo is not None:
        c.photo(photo, photo_box, spec.image_focus)
    else:
        c.rect(photo_box, _mix(pal.card, pal.bg, 0.5))
    c.rect(peek, pal.peek)
    c.rect(card, pal.card)

    align = "left" if spec.align == "auto" else spec.align
    _card_content(c, spec, card, align, pal.ink, pal.ink_muted, headline_cap=84)
    _chrome(c, spec, "bottom" if spec.chrome in ("auto", "bottom") else spec.chrome,
            pal.on_bg, strip=pal.bg)


def tpl_arc(c: Canvas, spec: PostSpec, photo: Image.Image | None) -> None:
    """Oversized circle bleeding off the top-left — the Lifestyle look."""
    pal = c.pal
    r = max(c.w, c.h) * 0.62
    cx, cy = c.fx(0.30), c.fy(0.26)
    c.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*pal.pale, 255))
    # a smaller counterweight circle off the opposite corner
    r2 = max(c.w, c.h) * 0.16
    c.draw.ellipse((c.w - r2 * 0.55, c.h - r2 * 0.75, c.w + r2, c.h + r2),
                   fill=(*pal.peek, 255))
    c.rect((c.fx(0.90), c.fy(0.14), c.fx(0.945), c.fy(0.52)), pal.peek)

    card: Box = (c.fx(0.11), c.fy(0.09), min(c.fx(0.80), cx + r * 0.72), c.fy(0.58))
    align = "left" if spec.align == "auto" else spec.align
    _card_content(c, spec, card, align, pal.pale_ink, _mix(pal.pale_ink, pal.pale, 0.30),
                  pad=c.s(20), headline_cap=88)
    _chrome(c, spec, "bottom" if spec.chrome in ("auto", "bottom") else spec.chrome, pal.on_bg)


def tpl_circle_photo(c: Canvas, spec: PostSpec, photo: Image.Image | None) -> None:
    """Pale card with a circle-cropped photo overlapping its corner."""
    pal = c.pal
    card: Box = (c.fx(0.05), c.fy(0.05), c.fx(0.95), c.fy(0.86))
    c.rect((c.fx(0.09), c.fy(0.09), c.fx(0.99), c.fy(0.90)), pal.card)
    c.rect(card, pal.pale)

    d = int(min(c.fx(0.40), c.fy(0.40)))
    cx0, cy0 = int(c.fx(0.60)), int(c.fy(0.10))
    circle: Box = (cx0, cy0, cx0 + d, cy0 + d)
    if photo is not None:
        c.photo(photo, circle, spec.image_focus, circle_mask((d, d)))
    else:
        c.draw.ellipse(circle, fill=(*_mix(pal.card, pal.bg, 0.4), 255))

    text_box: Box = (card[0], card[1] + d * 0.12, c.fx(0.585), card[3])
    align = "left" if spec.align == "auto" else spec.align
    _card_content(c, spec, text_box, align, pal.pale_ink,
                  _mix(pal.pale_ink, pal.pale, 0.28), headline_cap=72)

    strip_y1 = card[3]
    if spec.brand.website:
        f = font("body", c.s(20))
        w = f.getlength(spec.brand.website) + c.s(54)
        c.rect((card[0], strip_y1 - c.s(52), card[0] + w, strip_y1), pal.cta_bg)
        c.draw.text((card[0] + c.s(27), strip_y1 - c.s(26)), spec.brand.website,
                    font=f, fill=(*pal.cta_ink, 255), anchor="lm")
    _chrome(c, spec, "bottom" if spec.chrome in ("auto", "bottom") else spec.chrome, pal.on_bg)


def tpl_banner(c: Canvas, spec: PostSpec, photo: Image.Image | None) -> None:
    """Full-bleed photo with a translucent band carrying the copy."""
    pal = c.pal
    if photo is not None:
        c.photo(photo, (0, 0, c.w, c.h), spec.image_focus)
    else:
        c.rect((0, 0, c.w, c.h), pal.bg)

    band: Box = (0, c.fy(0.10), c.w, c.fy(0.52))
    layer = c.layer()
    ImageDraw.Draw(layer).rectangle(band, fill=_alpha(pal.card, 0.82))
    c.merge(layer)

    align = "center" if spec.align == "auto" else spec.align
    ink = _readable_on(pal.card)
    _card_content(c, spec, band, align, ink, _mix(ink, pal.card, 0.25),
                  pad=c.s(56), headline_cap=86)
    _chrome(c, spec, "bottom" if spec.chrome in ("auto", "bottom") else spec.chrome,
            pal.on_bg, strip=pal.bg)


TEMPLATES: dict[str, Callable[[Canvas, PostSpec, Image.Image | None], None]] = {
    "offset_card": tpl_offset_card,
    "card_photo": tpl_card_photo,
    "photo_side": tpl_photo_side,
    "arc": tpl_arc,
    "circle_photo": tpl_circle_photo,
    "banner": tpl_banner,
}

TEMPLATE_LABELS = {
    "offset_card": "Offset card — solid panel, peek slabs, no photo needed",
    "card_photo": "Card and photo band — card above, photo across the base",
    "photo_side": "Photo side panel — card overlapping a full-height photo",
    "arc": "Arc — oversized circle bleeding off the corner",
    "circle_photo": "Circle photo — pale card with a round portrait",
    "banner": "Banner — full-bleed photo with a copy band",
}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _seed_for(spec: PostSpec) -> int:
    if spec.seed is not None:
        return spec.seed
    return int(hashlib.sha256(
        f"{spec.headline}|{spec.template}|{spec.theme}".encode()).hexdigest()[:8], 16)


def render(spec: PostSpec) -> Image.Image:
    """Render one post. Bad input degrades gracefully rather than raising."""
    if not spec.headline.strip():
        spec = replace(spec, headline="Add a headline")

    base_w, base_h = FORMATS.get(spec.fmt, FORMATS["square"])
    scale = max(0.25, min(4.0, spec.scale))
    size = (int(base_w * scale), int(base_h * scale))

    pal = THEMES.get(spec.theme, THEMES["periwinkle"])
    photo = _open_image(spec.image)

    canvas = Canvas(size, pal, _seed_for(spec))
    TEMPLATES.get(spec.template, tpl_offset_card)(canvas, spec, photo)
    return canvas.result()


def variants(spec: PostSpec, n: int = 4) -> list[Image.Image]:
    """Same copy, different layouts and palettes — a set to choose from."""
    tpls, thms = list(TEMPLATES), list(THEMES)
    ti = tpls.index(spec.template) if spec.template in tpls else 0
    hi = thms.index(spec.theme) if spec.theme in thms else 0
    out = []
    for i in range(n):
        out.append(render(replace(
            spec,
            template=tpls[(ti + i) % len(tpls)],
            theme=thms[(hi + i * 3) % len(thms)],
            seed=(_seed_for(spec) + i * 977) % 10**6,
        )))
    return out


def for_category(headline: str, body: str = "", category: str = "Protection",
                 **kwargs) -> PostSpec:
    """Build a spec using the client's folder conventions as the default look."""
    preset = CATEGORY_PRESETS.get(category, {"theme": "periwinkle", "template": "offset_card"})
    return PostSpec(headline=headline, body=body, category=category,
                    theme=kwargs.pop("theme", preset["theme"]),
                    template=kwargs.pop("template", preset["template"]), **kwargs)


def build_post_canvas(
    headline: str,
    body: str,
    category: str = "Lifestyle",
    accent: str = "gold",
    logo_text: str = "FCA Advisory",
    contact_text: str = "hello@adviser.co.uk • 020 0000 0000 • adviser.co.uk",
    background_path: str | None = None,
) -> Image.Image:
    """Backwards-compatible shim for the original call site."""
    theme = {"gold": "mustard", "navy": "royal", "green": "sage",
             "purple": "periwinkle", "teal": "sage"}.get(accent.lower(), "periwinkle")
    spec = for_category(
        headline, body, category if category in CATEGORY_PRESETS else "Protection",
        theme=theme,
        template="photo_side" if background_path else "offset_card",
        brand=Brand(name=logo_text, phone="", website=contact_text),
        image=background_path, show_website=True,
    )
    return render(spec)


# ---------------------------------------------------------------------------
# Streamlit panel
# ---------------------------------------------------------------------------


def post_studio_ui() -> None:
    """`from post_studio import post_studio_ui` then call it inside your page."""
    import io

    import streamlit as st

    st.subheader("Post studio")

    gaps = missing_fonts()
    if gaps:
        st.info("Fallback fonts in use for: " + ", ".join(gaps)
                + ". Drop Poppins .ttf files into ./assets/fonts to match the samples.")

    left, right = st.columns([5, 6], gap="large")

    with left:
        category = st.selectbox("Category", list(CATEGORY_PRESETS))
        preset = CATEGORY_PRESETS[category]

        headline = st.text_area("Headline", "A Lifetime of Cover, for a Lifetime of Love", height=80)
        body = st.text_area(
            "Body",
            "Whole of Life insurance offers fixed protection, helping ensure loved ones "
            "are taken care of no matter when it's needed.", height=90)
        bullets_raw = st.text_area("Bullets (one per line, replaces body)", "", height=70)
        cta = st.text_input("CTA", "")

        tpl_keys = list(TEMPLATES)
        template = st.selectbox("Layout", tpl_keys, index=tpl_keys.index(preset["template"]),
                                format_func=lambda k: TEMPLATE_LABELS[k])
        thm_keys = list(THEMES)
        theme = st.selectbox("Palette", thm_keys, index=thm_keys.index(preset["theme"]),
                             format_func=lambda k: THEMES[k].label)
        fmt = st.selectbox("Format", list(FORMATS),
                           format_func=lambda k: f"{k} — {FORMATS[k][0]}x{FORMATS[k][1]}")
        align = st.radio("Alignment", ["auto", "left", "center", "right"], horizontal=True)
        face = st.radio("Headline type", ["display", "serif"], horizontal=True)
        divider = st.checkbox("Rule under the headline", value=False)
        retina = st.checkbox("Export at 2x", value=False)

        upload = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"])

        st.divider()
        name = st.text_input("Wordmark", "LOGO")
        phone = st.text_input("Phone", "+123-456-789")
        website = st.text_input("Website", "www.reallygreatsite.com")
        show_site = st.checkbox("Show website", value=False)
        logo_file = st.file_uploader("Logo (PNG)", type=["png"])

    spec = PostSpec(
        headline=headline,
        body=body,
        bullets=tuple(l for l in bullets_raw.splitlines() if l.strip()),
        cta=cta,
        category=category,
        brand=Brand(name=name, phone=phone, website=website,
                    logo_path=Image.open(logo_file) if logo_file else None),
        image=Image.open(upload) if upload else None,
        template=template,
        theme=theme,
        fmt=fmt,
        align=align,
        headline_face=face,
        divider=divider,
        show_website=show_site,
        scale=2.0 if retina else 1.0,
    )

    with right:
        image = render(spec)
        st.image(image, use_container_width=True)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        st.download_button("Download PNG", buf.getvalue(),
                           file_name=f"{category}-{template}-{fmt}.png", mime="image/png")

        if st.button("Show four alternatives"):
            cols = st.columns(2)
            for i, img in enumerate(variants(spec, 4)):
                cols[i % 2].image(img, use_container_width=True)


if __name__ == "__main__":
    render(for_category(
        "A Lifetime of Cover, for a Lifetime of Love",
        "Whole of Life insurance offers fixed protection, helping ensure loved ones "
        "are taken care of no matter when it's needed.",
        "Protection",
    )).save("demo.png")
    print("wrote demo.png")
