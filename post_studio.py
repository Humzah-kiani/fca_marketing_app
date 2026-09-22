"""
post_studio.py
==============

A social-post generator AND editor, built entirely on Pillow + Streamlit —
no JS canvas, no external components. It works because the whole "editor"
is just: hold the post as a list of independent layers, let Streamlit
widgets edit whichever layer is selected, and re-render the flat PNG with
Pillow on every rerun (which is Streamlit's normal behaviour anyway).

Layers
------
  TextLayer       one block of text: content, font family, size, colour,
                   bold, italic, underline, highlight, alignment, position
  ImageLayer      the logo: replaceable, position + size. A default logo
                   is generated procedurally if none is supplied.
  BackgroundLayer the post photo: replaceable, with focus/zoom controls
  ContactInfo     phone, email, website — each its own visible/editable line

A PostSpec bundles these plus a *template* (from the client's sample set)
that paints the non-text scaffolding — the flat colour field, the solid
card, the offset "peek" slabs — behind the layers. Choosing a template only
sets sensible starting positions/colours for the layers it creates; every
layer is independently editable afterwards, including moving it onto a
completely different part of the canvas.

Public API
----------
    new_post(...)      -> PostSpec   (build a first draft from a brief)
    render(spec)        -> PIL.Image  (flatten all layers to one image)
    default_logo(...)   -> PIL.Image  (procedural placeholder logo)
    build_post_canvas(...) -> PIL.Image   (old call signature, still works)
    post_studio_ui()    -> a complete Streamlit editor page
"""

from __future__ import annotations

import math
import os
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
Box = tuple[float, float, float, float]

__all__ = [
    "Palette", "TextStyle", "TextLayer", "ImageLayer", "BackgroundLayer",
    "ContactInfo", "PostSpec",
    "THEMES", "FORMATS", "TEMPLATES", "TEMPLATE_LABELS", "CATEGORY_PRESETS",
    "FONT_FAMILIES",
    "new_post", "render", "default_logo", "build_post_canvas",
    "missing_fonts", "post_studio_ui",
]


# ---------------------------------------------------------------------------
# Palettes (lifted from the client's sample posts) + formats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
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


THEMES: dict[str, Palette] = {
    "periwinkle": Palette("periwinkle", "Periwinkle / charcoal",
        bg=(107, 111, 196), card=(62, 59, 74), peek=(198, 194, 250),
        ink=(255, 255, 255), ink_muted=(216, 214, 224), on_bg=(255, 255, 255),
        cta_bg=(198, 194, 250), cta_ink=(48, 45, 60),
        pale=(238, 236, 252), pale_ink=(46, 44, 58)),
    "royal": Palette("royal", "Royal blue / mint",
        bg=(16, 70, 139), card=(126, 227, 152), peek=(138, 224, 246),
        ink=(12, 26, 20), ink_muted=(38, 66, 50), on_bg=(255, 255, 255),
        cta_bg=(12, 38, 72), cta_ink=(255, 255, 255),
        pale=(240, 250, 243), pale_ink=(14, 40, 26)),
    "plum": Palette("plum", "Plum / coral",
        bg=(122, 87, 118), card=(232, 131, 107), peek=(214, 210, 208),
        ink=(255, 255, 255), ink_muted=(252, 232, 226), on_bg=(255, 255, 255),
        cta_bg=(255, 255, 255), cta_ink=(154, 72, 52),
        pale=(250, 236, 230), pale_ink=(96, 46, 34)),
    "maroon": Palette("maroon", "Maroon / forest",
        bg=(126, 26, 26), card=(31, 107, 87), peek=(247, 235, 200),
        ink=(255, 255, 255), ink_muted=(216, 232, 226), on_bg=(255, 255, 255),
        cta_bg=(247, 235, 200), cta_ink=(31, 70, 58),
        pale=(247, 235, 200), pale_ink=(58, 26, 22)),
    "mustard": Palette("mustard", "Charcoal / mustard",
        bg=(34, 34, 40), card=(242, 194, 48), peek=(88, 58, 138),
        ink=(28, 26, 22), ink_muted=(74, 66, 40), on_bg=(255, 255, 255),
        cta_bg=(28, 26, 22), cta_ink=(242, 194, 48),
        pale=(250, 240, 210), pale_ink=(34, 32, 28)),
    "ivory": Palette("ivory", "Navy / ivory",
        bg=(20, 40, 72), card=(248, 243, 233), peek=(196, 140, 92),
        ink=(22, 28, 38), ink_muted=(84, 92, 104), on_bg=(255, 255, 255),
        cta_bg=(20, 40, 72), cta_ink=(255, 255, 255),
        pale=(248, 243, 233), pale_ink=(22, 28, 38)),
    "brick": Palette("brick", "Brick / deep navy",
        bg=(178, 58, 46), card=(30, 58, 95), peek=(246, 219, 210),
        ink=(255, 255, 255), ink_muted=(212, 222, 236), on_bg=(255, 255, 255),
        cta_bg=(246, 219, 210), cta_ink=(30, 58, 95),
        pale=(246, 219, 210), pale_ink=(64, 26, 20)),
    "sage": Palette("sage", "Slate / sage",
        bg=(74, 90, 92), card=(150, 182, 165), peek=(238, 240, 232),
        ink=(20, 32, 30), ink_muted=(52, 70, 64), on_bg=(255, 255, 255),
        cta_bg=(28, 44, 42), cta_ink=(255, 255, 255),
        pale=(238, 240, 232), pale_ink=(24, 38, 34)),
}

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


# ---------------------------------------------------------------------------
# Fonts — three families, each with real regular/bold/italic/bold-italic
# files where installed, and a guaranteed fallback (synthetic bold via
# stroke, synthetic italic via shear) so styling never silently no-ops.
# ---------------------------------------------------------------------------


FONT_FAMILIES: dict[str, str] = {
    "display": "Poppins (sans)",
    "serif": "Lora (serif)",
    "body": "System sans",
}

_FONT_STACKS: dict[tuple[str, str], Sequence[str]] = {
    ("display", "regular"): ("Poppins-Regular", "Inter-Regular", "Carlito-Regular",
                              "LiberationSans-Regular", "DejaVuSans", "arial"),
    ("display", "bold"): ("Poppins-Bold", "Poppins-SemiBold", "Inter-Bold",
                           "Carlito-Bold", "LiberationSans-Bold", "DejaVuSans-Bold", "arialbd"),
    ("display", "italic"): ("Poppins-Italic", "Inter-Italic", "Carlito-Italic",
                             "LiberationSans-Italic", "DejaVuSans-Oblique", "ariali"),
    ("display", "bold_italic"): ("Poppins-BoldItalic", "Inter-BoldItalic", "Carlito-BoldItalic",
                                  "LiberationSans-BoldItalic", "DejaVuSans-BoldOblique", "arialbi"),
    ("serif", "regular"): ("Lora-Regular", "Lora-Variable", "PlayfairDisplay-Regular",
                            "DejaVuSerif", "LiberationSerif-Regular", "georgia"),
    ("serif", "bold"): ("Lora-Bold", "Lora-Variable", "PlayfairDisplay-Bold",
                         "DejaVuSerif-Bold", "LiberationSerif-Bold", "georgiab"),
    ("serif", "italic"): ("Lora-Italic", "Lora-Italic-Variable", "PlayfairDisplay-Italic",
                           "DejaVuSerif-Italic", "LiberationSerif-Italic", "georgiai"),
    ("serif", "bold_italic"): ("Lora-BoldItalic", "Lora-Italic-Variable", "PlayfairDisplay-BoldItalic",
                                "DejaVuSerif-BoldItalic", "LiberationSerif-BoldItalic", "georgiaz"),
    ("body", "regular"): ("Inter-Regular", "Carlito-Regular", "LiberationSans-Regular",
                           "DejaVuSans", "FreeSans", "arial"),
    ("body", "bold"): ("Inter-Bold", "Carlito-Bold", "LiberationSans-Bold",
                        "DejaVuSans-Bold", "FreeSansBold", "arialbd"),
    ("body", "italic"): ("Inter-Italic", "Carlito-Italic", "LiberationSans-Italic",
                          "DejaVuSans-Oblique", "ariali"),
    ("body", "bold_italic"): ("Inter-BoldItalic", "Carlito-BoldItalic", "LiberationSans-BoldItalic",
                               "DejaVuSans-BoldOblique", "arialbi"),
}

FONT_DIRS = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts"),
    "/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
    "C:/Windows/Fonts",
)


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
def _resolve(family: str, weight: str) -> str | None:
    index = _font_index()
    for cand in _FONT_STACKS.get((family, weight), ()):
        hit = index.get(cand.lower())
        if hit:
            return hit
    for cand in _FONT_STACKS.get((family, weight), ()):
        needle = cand.lower().replace("-", "")
        for stem, path in index.items():
            if needle in stem.replace("-", ""):
                return path
    return None


@dataclass(frozen=True)
class _Resolved:
    path: str | None
    synthetic_bold: bool
    synthetic_italic: bool


def _resolve_style(family: str, bold: bool, italic: bool) -> _Resolved:
    """Real file for this exact weight/slant if one exists; otherwise fall
    back to the nearest real file and flag what still needs to be faked."""
    if bold and italic:
        path = _resolve(family, "bold_italic")
        if path:
            return _Resolved(path, False, False)
        path = _resolve(family, "bold")
        if path:
            return _Resolved(path, False, True)   # have bold, fake the slant
        path = _resolve(family, "italic")
        if path:
            return _Resolved(path, True, False)    # have italic, fake the weight
        return _Resolved(_resolve(family, "regular"), True, True)
    if bold:
        path = _resolve(family, "bold")
        return _Resolved(path, False, False) if path else _Resolved(_resolve(family, "regular"), True, False)
    if italic:
        path = _resolve(family, "italic")
        return _Resolved(path, False, False) if path else _Resolved(_resolve(family, "regular"), False, True)
    return _Resolved(_resolve(family, "regular"), False, False)


@lru_cache(maxsize=1024)
def _load_ttf(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    size = max(6, int(size))
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def missing_fonts() -> list[str]:
    """Families with no real file for any weight — editor will still work,
    just falls back to the system default rather than the intended look."""
    out = []
    for fam in ("display", "serif", "body"):
        if _resolve(fam, "regular") is None:
            out.append(fam)
    return out


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def _luminance(c: RGB) -> float:
    lin = []
    for v in (c[0] / 255, c[1] / 255, c[2] / 255):
        lin.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


def _readable_on(bg: RGB) -> RGB:
    return (20, 24, 28) if _luminance(bg) > 0.45 else (255, 255, 255)


def _hex(c: RGB) -> str:
    return "#%02x%02x%02x" % c


def _from_hex(s: str, fallback: RGB = (0, 0, 0)) -> RGB:
    s = s.lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback


def cover_crop(img: Image.Image, size: tuple[int, int], focus: tuple[float, float] = (0.5, 0.5),
               zoom: float = 1.0) -> Image.Image:
    """Fill the box without distorting; `zoom` > 1 lets the editor push in."""
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    fitted = ImageOps.fit(img.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS, centering=focus)
    if zoom and zoom > 1.001:
        zw, zh = int(w * zoom), int(h * zoom)
        big = ImageOps.fit(img.convert("RGB"), (zw, zh), method=Image.Resampling.LANCZOS, centering=focus)
        x0 = (zw - w) // 2
        y0 = (zh - h) // 2
        fitted = big.crop((x0, y0, x0 + w, y0 + h))
    return fitted


def circle_mask(size: tuple[int, int]) -> Image.Image:
    ss = 4
    big = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size[0] * ss - 1, size[1] * ss - 1), fill=255)
    return big.resize(size, Image.Resampling.LANCZOS)


def default_logo(name: str = "LOGO", palette: Palette | None = None, size: int = 320) -> Image.Image:
    """A clean placeholder mark so a post never ships with no logo at all —
    a circular monogram badge. Meant to be replaced via the logo uploader."""
    pal = palette or THEMES["periwinkle"]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=(*pal.card, 255))
    d.ellipse((size * 0.045, size * 0.045, size * 0.955, size * 0.955),
              outline=(*pal.peek, 255), width=max(2, size // 45))
    initials = "".join(w[0] for w in name.strip().split()[:2]).upper() or "L"
    path = _resolve("display", "bold")
    f = _load_ttf(path, int(size * 0.38))
    d.text((size / 2, size / 2), initials, font=f, fill=(*pal.ink, 255), anchor="mm")
    return img


# ---------------------------------------------------------------------------
# Text engine — wrapping, balancing, and styled-line rendering
# ---------------------------------------------------------------------------


def _text_width(f: ImageFont.FreeTypeFont, s: str) -> float:
    return f.getlength(s) if s else 0.0


def _wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words, current = para.split(), ""
        if not words:
            lines.append("")
            continue
        for word in words:
            trial = f"{current} {word}".strip()
            if _text_width(f, trial) <= max_w or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _balanced(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    base = _wrap(text, f, max_w)
    if len(base) < 2:
        return base
    best, best_spread = base, _spread(base, f)
    for pct in (0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72):
        trial = _wrap(text, f, max_w * pct)
        if len(trial) != len(base):
            continue
        spread = _spread(trial, f)
        if spread < best_spread:
            best, best_spread = trial, spread
    return best


def _spread(lines: list[str], f: ImageFont.FreeTypeFont) -> float:
    widths = [_text_width(f, ln) for ln in lines]
    return max(widths) - min(widths) if widths else 0.0


def _fit_size(text: str, family: str, bold: bool, italic: bool, max_w: float, max_h: float,
              hi: int, lo: int, leading: float, max_lines: int | None) -> tuple[int, list[str]]:
    """Largest size at which the text fits its box — used only when building
    the first draft; once a layer exists its size is whatever the user set."""
    res = _resolve_style(family, bold, italic)
    size = max(int(hi), int(lo))
    lo = max(6, int(lo))
    while size > lo:
        f = _load_ttf(res.path, size)
        lines = _balanced(text, f, max_w)
        if len(lines) * size * leading <= max_h and (max_lines is None or len(lines) <= max_lines):
            return size, lines
        size -= max(1, round(size * 0.04))
    f = _load_ttf(res.path, lo)
    return lo, _wrap(text, f, max_w)


def _render_line_tile(line: str, f: ImageFont.FreeTypeFont, colour: RGB,
                      synthetic_bold: bool, synthetic_italic: bool) -> tuple[Image.Image, float]:
    """One line of text as an RGBA tile, with synthetic bold/italic applied
    when no real bold/italic font file was available. Returns (tile, width)
    where width is the *unsheared* text width, used for layout math."""
    if not line:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0.0
    bbox = f.getbbox(line)
    pad = max(2, int(f.size * 0.18))
    slant_pad = int(f.size * 0.45) if synthetic_italic else 0
    w = int(bbox[2] - bbox[0]) + pad * 2 + slant_pad
    h = int(f.size * 1.5)
    tile = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    ox = pad - bbox[0]
    stroke = max(1, round(f.size * 0.04)) if synthetic_bold else 0
    d.text((ox, pad), line, font=f, fill=(*colour, 255),
           stroke_width=stroke, stroke_fill=(*colour, 255))
    if synthetic_italic:
        shear = -0.22
        tile = tile.transform(
            (tile.width + int(abs(shear) * tile.height), tile.height),
            Image.AFFINE, (1, shear, max(0, -shear) * tile.height, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
    text_w = _text_width(f, line)
    return tile, text_w


# ---------------------------------------------------------------------------
# Layer model
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class TextStyle:
    family: str = "display"        # "display" | "serif" | "body"
    size: int = 44                 # design units at 1080 canvas width
    color: RGB = (255, 255, 255)
    bold: bool = True
    italic: bool = False
    underline: bool = False
    highlight: bool = False
    highlight_color: RGB = (255, 230, 120)
    align: str = "left"            # "left" | "center" | "right"
    leading: float = 1.22


@dataclass
class TextLayer:
    text: str
    x: float = 0.08                # fraction of canvas width, box left
    y: float = 0.10                # fraction of canvas height, box top
    w: float = 0.60                # fraction of canvas width, box width
    style: TextStyle = field(default_factory=TextStyle)
    visible: bool = True
    kind: str = "custom"           # "headline" | "body" | "bullet" | "cta" | "contact" | "custom"
    id: str = field(default_factory=_new_id)
    label: str = "Text"


@dataclass
class ImageLayer:
    image: Image.Image | None = None   # None => default_logo() is drawn
    x: float = 0.055
    y: float = 0.055
    w: float = 0.14                    # fraction of canvas width; height keeps aspect
    visible: bool = True
    is_default: bool = True


@dataclass
class BackgroundLayer:
    image: Image.Image | None = None
    box: Box = (0.0, 0.0, 1.0, 1.0)    # fraction box the photo fills
    focus_x: float = 0.5
    focus_y: float = 0.42
    zoom: float = 1.0
    tint: RGB | None = None            # optional flat colour if no photo


@dataclass
class ContactInfo:
    phone: str = "+123-456-789"
    email: str = "hello@yourfirm.co.uk"
    website: str = "www.yourfirm.co.uk"
    show_phone: bool = True
    show_email: bool = False
    show_website: bool = True
    color: RGB = (255, 255, 255)


@dataclass
class PostSpec:
    category: str = "Protection"
    template: str = "offset_card"
    theme: str = "periwinkle"
    fmt: str = "square"
    background: BackgroundLayer = field(default_factory=BackgroundLayer)
    logo: ImageLayer = field(default_factory=ImageLayer)
    text_layers: list[TextLayer] = field(default_factory=list)
    contact: ContactInfo = field(default_factory=ContactInfo)
    scale: float = 1.0

    def layer(self, layer_id: str) -> TextLayer | None:
        return next((t for t in self.text_layers if t.id == layer_id), None)


# ---------------------------------------------------------------------------
# Canvas — drawing primitives shared by templates and layers
# ---------------------------------------------------------------------------


class Canvas:
    def __init__(self, size: tuple[int, int], pal: Palette):
        self.size = size
        self.w, self.h = size
        self.pal = pal
        self.u = self.w / 1080.0
        self.img = Image.new("RGBA", size, (*pal.bg, 255))
        self.draw = ImageDraw.Draw(self.img)

    def s(self, v: float) -> float:
        return v * self.u

    def fx(self, t: float) -> float:
        return self.w * t

    def fy(self, t: float) -> float:
        return self.h * t

    def rect(self, box: Box, fill: RGB, radius: float = 0) -> None:
        if radius:
            self.draw.rounded_rectangle(box, radius=radius, fill=(*fill, 255))
        else:
            self.draw.rectangle(box, fill=(*fill, 255))

    def layer_img(self) -> Image.Image:
        return Image.new("RGBA", self.size, (0, 0, 0, 0))

    def merge(self, layer: Image.Image) -> None:
        self.img = Image.alpha_composite(self.img, layer)
        self.draw = ImageDraw.Draw(self.img)

    def paste(self, img: Image.Image, xy: tuple[int, int], mask: Image.Image | None = None) -> None:
        self.img.paste(img, xy, mask if mask is not None else (img if img.mode == "RGBA" else None))
        self.draw = ImageDraw.Draw(self.img)

    def photo(self, img: Image.Image, box: Box, focus: tuple[float, float], zoom: float = 1.0,
              mask: Image.Image | None = None) -> None:
        x0, y0, x1, y1 = (int(v) for v in box)
        panel = cover_crop(img, (x1 - x0, y1 - y0), focus, zoom)
        if mask is not None and mask.size != panel.size:
            mask = mask.resize(panel.size, Image.Resampling.LANCZOS)
        self.img.paste(panel, (x0, y0), mask)
        self.draw = ImageDraw.Draw(self.img)

    def result(self) -> Image.Image:
        return self.img.convert("RGB")


# ---------------------------------------------------------------------------
# Templates — paint only the decorative scaffolding (field / card / peek
# slabs / photo panel). They return the geometry used to seed default
# layer positions; they never draw text or the logo themselves.
# ---------------------------------------------------------------------------


@dataclass
class Geometry:
    card_box: Box           # fraction box: where headline/body/CTA start out
    photo_box: Box | None   # fraction box: where the background photo sits
    ink: RGB
    ink_muted: RGB
    contact_ink: RGB
    contact_y: float        # fraction, baseline for the contact row
    logo_xy: tuple[float, float]


def _paint_offset_card(c: Canvas, pal: Palette) -> Geometry:
    card: Box = (0, c.fy(0.098), c.fx(0.923), c.fy(0.776))
    c.rect((c.fx(0.195), c.fy(0.080), c.fx(0.835), c.fy(0.118)), pal.peek)
    c.rect((c.fx(0.098), c.fy(0.760), c.fx(0.760), c.fy(0.869)), pal.peek)
    c.rect((c.fx(0.955), c.fy(0.170), c.fx(1.0), c.fy(0.790)), pal.peek)
    c.rect(card, pal.card)
    return Geometry((0.09, 0.20, 0.80, 0.72), None, pal.ink, pal.ink_muted, pal.on_bg, 0.938, (0.055, 0.90))


def _paint_card_photo(c: Canvas, pal: Palette) -> Geometry:
    band_top = c.fy(0.775)
    card: Box = (c.fx(0.10), c.fy(0.098), c.w, band_top)
    c.rect((c.fx(0.055), c.fy(0.18), c.fx(0.105), c.fy(0.71)), pal.peek)
    c.rect(card, pal.card)
    c.rect((c.fx(0.79), band_top, c.fx(0.875), band_top + c.fy(0.055)), pal.peek)
    return Geometry((0.14, 0.16, 0.80, 0.56), (0.0, 0.775, 0.79, 1.0), pal.ink, pal.ink_muted,
                    pal.on_bg, 0.052, (0.055, 0.052))


def _paint_photo_side(c: Canvas, pal: Palette) -> Geometry:
    tall = c.h / c.w > 1.15
    if tall:
        photo_box = (0.0, 0.0, 1.0, 0.40)
        card: Box = (c.fx(0.07), c.fy(0.34), c.fx(0.97), c.fy(0.86))
        peek: Box = (c.fx(0.11), c.fy(0.86), c.fx(0.93), c.fy(0.895))
    else:
        photo_box = (0.56, 0.0, 1.0, 1.0)
        card = (c.fx(0.06), c.fy(0.13), c.fx(0.70), c.fy(0.87))
        peek = (c.fx(0.02), c.fy(0.20), c.fx(0.06), c.fy(0.80))
    c.rect((0, 0, c.w, c.h), _mix(pal.card, pal.bg, 0.5))
    c.rect(peek, pal.peek)
    c.rect(card, pal.card)
    return Geometry((0.12, 0.40, 0.78, 0.42), photo_box, pal.ink, pal.ink_muted, pal.on_bg,
                    0.912, (0.055, 0.90))


def _paint_arc(c: Canvas, pal: Palette) -> Geometry:
    r = max(c.w, c.h) * 0.62
    cx, cy = c.fx(0.30), c.fy(0.26)
    c.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*pal.pale, 255))
    r2 = max(c.w, c.h) * 0.16
    c.draw.ellipse((c.w - r2 * 0.55, c.h - r2 * 0.75, c.w + r2, c.h + r2), fill=(*pal.peek, 255))
    c.rect((c.fx(0.90), c.fy(0.14), c.fx(0.945), c.fy(0.52)), pal.peek)
    return Geometry((0.13, 0.13, 0.58, 0.42), None, pal.pale_ink, _mix(pal.pale_ink, pal.pale, 0.3),
                    pal.on_bg, 0.938, (0.055, 0.90))


def _paint_circle_photo(c: Canvas, pal: Palette) -> Geometry:
    c.rect((c.fx(0.09), c.fy(0.09), c.fx(0.99), c.fy(0.90)), pal.card)
    c.rect((c.fx(0.05), c.fy(0.05), c.fx(0.95), c.fy(0.86)), pal.pale)
    d = min(c.fx(0.40), c.fy(0.40))
    return Geometry((0.09, 0.16, 0.46, 0.55), (0.60, 0.10, 0.60 + d / c.w, 0.10 + d / c.h),
                    pal.pale_ink, _mix(pal.pale_ink, pal.pale, 0.28), pal.on_bg, 0.938, (0.055, 0.90))


def _paint_banner(c: Canvas, pal: Palette) -> Geometry:
    band: Box = (0, c.fy(0.10), c.w, c.fy(0.52))
    layer = c.layer_img()
    ImageDraw.Draw(layer).rectangle(band, fill=(*pal.card, 209))
    c.merge(layer)
    ink = _readable_on(pal.card)
    return Geometry((0.10, 0.13, 0.80, 0.34), (0.0, 0.0, 1.0, 1.0), ink, _mix(ink, pal.card, 0.25),
                    pal.on_bg, 0.938, (0.055, 0.90))


_PAINTERS: dict[str, Callable[[Canvas, Palette], Geometry]] = {
    "offset_card": _paint_offset_card,
    "card_photo": _paint_card_photo,
    "photo_side": _paint_photo_side,
    "arc": _paint_arc,
    "circle_photo": _paint_circle_photo,
    "banner": _paint_banner,
}

TEMPLATES = _PAINTERS  # exported name expected elsewhere
TEMPLATE_LABELS = {
    "offset_card": "Offset card — solid panel, peek slabs, no photo needed",
    "card_photo": "Card and photo band — card above, photo across the base",
    "photo_side": "Photo side panel — card overlapping a full-height photo",
    "arc": "Arc — oversized circle bleeding off the corner",
    "circle_photo": "Circle photo — pale card with a round portrait",
    "banner": "Banner — full-bleed photo with a translucent copy band",
}


# ---------------------------------------------------------------------------
# Drawing a single text layer with full styling
# ---------------------------------------------------------------------------


def draw_text_layer(c: Canvas, layer: TextLayer) -> None:
    if not layer.visible or not layer.text.strip():
        return
    st = layer.style
    res = _resolve_style(st.family, st.bold, st.italic)
    size_px = max(8, int(c.s(st.size)))
    f = _load_ttf(res.path, size_px)

    max_w = max(c.s(40), c.fx(layer.w))
    lines = _balanced(layer.text, f, max_w)
    x0 = c.fx(layer.x)
    y = c.fy(layer.y)
    line_h = size_px * st.leading

    for line in lines:
        tile, text_w = _render_line_tile(line, f, st.color, res.synthetic_bold, res.synthetic_italic)
        if st.align == "center":
            lx = x0 + (max_w - text_w) / 2
        elif st.align == "right":
            lx = x0 + (max_w - text_w)
        else:
            lx = x0

        if st.highlight and line.strip():
            pad_x, pad_y = c.s(7), size_px * 0.12
            c.rect((lx - pad_x, y - pad_y, lx + text_w + pad_x, y + size_px * 1.06 + pad_y),
                   st.highlight_color)

        # tile has its own left padding baked in; correct for it on paste
        tile_pad = max(2, int(size_px * 0.18))
        c.paste(tile, (int(lx - tile_pad), int(y - tile_pad)))

        if st.underline and line.strip():
            uy = y + size_px * 0.98
            c.draw.line((lx, uy, lx + text_w, uy), fill=(*st.color, 255),
                       width=max(2, int(c.s(st.size * 0.045))))
        y += line_h


def _cta_box(c: Canvas, layer: TextLayer, pal: Palette) -> None:
    """CTA layers render as a pill instead of plain text."""
    st = layer.style
    res = _resolve_style(st.family, True, st.italic)
    size_px = max(8, int(c.s(st.size)))
    f = _load_ttf(res.path, size_px)
    label = layer.text
    pad_x, pad_y = c.s(28), c.s(15)
    w = f.getlength(label) + pad_x * 2
    h = size_px * 1.25 + pad_y * 2
    x0 = c.fx(layer.x)
    max_w = c.fx(layer.w)
    if st.align == "center":
        x0 = x0 + (max_w - w) / 2
    elif st.align == "right":
        x0 = x0 + (max_w - w)
    y0 = c.fy(layer.y)
    c.draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=h / 2, fill=(*pal.cta_bg, 255))
    c.draw.text((x0 + w / 2, y0 + h / 2), label, font=f, fill=(*pal.cta_ink, 255), anchor="mm")


# ---------------------------------------------------------------------------
# Logo + contact chrome
# ---------------------------------------------------------------------------


def draw_logo(c: Canvas, logo: ImageLayer, brand_name: str, pal: Palette) -> None:
    if not logo.visible:
        return
    img = logo.image if logo.image is not None else default_logo(brand_name, pal)
    target_w = max(8, c.fx(logo.w))
    ratio = target_w / img.width
    target_h = max(8, int(img.height * ratio))
    resized = img.resize((int(target_w), target_h), Image.Resampling.LANCZOS)
    x, y = int(c.fx(logo.x)), int(c.fy(logo.y))
    layer = c.layer_img()
    layer.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)
    c.merge(layer)


def _phone_glyph(c: Canvas, cx: float, cy: float, size: float, colour: RGB) -> None:
    n = max(48, int(size * 6))
    tile = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    fill = (*colour, 255)
    pad, stroke = n * 0.12, n * 0.25
    d.arc((pad, pad, n - pad, n - pad), start=200, end=340, fill=fill, width=int(stroke))
    r = (n - 2 * pad) / 2 - stroke / 2
    for ang in (200, 340):
        rad = math.radians(ang)
        x, y = n / 2 + r * math.cos(rad), n / 2 + r * math.sin(rad)
        d.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=fill)
    tile = tile.rotate(-45, resample=Image.Resampling.BICUBIC)
    side = max(1, int(size))
    tile = tile.resize((side, side), Image.Resampling.LANCZOS)
    layer = c.layer_img()
    layer.paste(tile, (int(cx - side / 2), int(cy - side / 2)), tile)
    c.merge(layer)


def draw_contact(c: Canvas, contact: ContactInfo, y_frac: float, margin_frac: float = 0.061) -> None:
    parts = []
    if contact.show_email and contact.email:
        parts.append(("mail", contact.email))
    if contact.show_website and contact.website:
        parts.append(("text", contact.website))
    y = c.fy(y_frac)
    right = c.w - c.fx(margin_frac)
    f = _load_ttf(_resolve("display", "bold"), int(c.s(24)))
    fbody = _load_ttf(_resolve("body", "regular"), int(c.s(20)))

    if contact.show_phone and contact.phone:
        num_w = f.getlength(contact.phone)
        c.draw.text((right, y), contact.phone, font=f, fill=(*contact.color, 255), anchor="rm")
        _phone_glyph(c, right - num_w - c.s(32), y, c.s(32), contact.color)
        right -= num_w + c.s(64)

    for kind, text in reversed(parts):
        w = fbody.getlength(text)
        c.draw.text((right, y), text, font=fbody, fill=(*contact.color, 235), anchor="rm")
        right -= w + c.s(36)


# ---------------------------------------------------------------------------
# Building a first draft
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


def new_post(headline: str, body: str = "", bullets: Sequence[str] = (), cta: str = "",
            category: str = "Protection", brand_name: str = "LOGO",
            logo_image=None, background_image=None,
            contact: ContactInfo | None = None,
            template: str | None = None, theme: str | None = None,
            fmt: str = "square") -> PostSpec:
    """Build a first-draft PostSpec: sensible layers, positioned by the
    chosen template, fully editable afterwards."""
    preset = CATEGORY_PRESETS.get(category, {"theme": "periwinkle", "template": "offset_card"})
    template = template or preset["template"]
    theme_key = theme or preset["theme"]
    pal = THEMES.get(theme_key, THEMES["periwinkle"])

    probe = Canvas(FORMATS.get(fmt, FORMATS["square"]), pal)
    geo = _PAINTERS.get(template, _paint_offset_card)(probe, pal)

    cx0, cy0, cw, chh = geo.card_box
    layers: list[TextLayer] = []

    body_or_bullets = bool(body) or bool(bullets)
    hsize, hlines = _fit_size(headline, "display", True, False, probe.fx(cw),
                              probe.fy(chh * (0.55 if body_or_bullets else 0.9)),
                              hi=90, lo=32, leading=1.14, max_lines=4)
    layers.append(TextLayer(
        text=headline, x=cx0, y=cy0, w=cw, kind="headline", label="Headline",
        style=TextStyle(family="display", size=hsize, color=geo.ink, bold=True, align="left", leading=1.14),
    ))
    y_cursor = cy0 + (hsize * 1.14 * len(hlines)) / probe.h + 0.035

    bullets = [b for b in bullets if b.strip()]
    if bullets:
        bsize = max(18, min(30, int(hsize * 0.42)))
        step = (bsize * 1.55) / 1080 + 0.028
        for i, b in enumerate(bullets[:6]):
            layers.append(TextLayer(
                text=f"•  {b}", x=cx0, y=y_cursor + step * i, w=cw, kind="bullet", label=f"Bullet {i+1}",
                style=TextStyle(family="body", size=bsize, color=geo.ink_muted, bold=False, align="left", leading=1.34),
            ))
        y_cursor += step * len(bullets[:6]) + 0.04
    elif body:
        bsize = max(19, min(30, int(hsize * 0.40)))
        layers.append(TextLayer(
            text=body, x=cx0, y=y_cursor + 0.03, w=cw, kind="body", label="Body",
            style=TextStyle(family="body", size=bsize, color=geo.ink_muted, bold=False, align="left", leading=1.42),
        ))
        _, blines = _fit_size(body, "body", False, False, probe.fx(cw), probe.fy(0.5), hi=bsize, lo=bsize,
                              leading=1.42, max_lines=None)
        y_cursor += 0.03 + (bsize * 1.42 / 1080) * len(blines) + 0.045

    if cta:
        layers.append(TextLayer(
            text=cta, x=cx0, y=min(y_cursor, cy0 + chh - 0.10), w=cw, kind="cta", label="Call to action",
            style=TextStyle(family="display", size=23, color=pal.cta_ink, bold=True, align="left"),
        ))

    contact = contact or ContactInfo(color=geo.contact_ink)
    contact.color = geo.contact_ink if contact.color == (255, 255, 255) else contact.color

    bg = BackgroundLayer(
        image=_open_image(background_image),
        box=geo.photo_box or (0.0, 0.0, 1.0, 1.0),
        focus_x=0.5, focus_y=0.42, zoom=1.0,
    )
    logo = ImageLayer(
        image=_open_image(logo_image), x=geo.logo_xy[0], y=geo.logo_xy[1] - 0.045, w=0.15,
        is_default=logo_image is None,
    )

    return PostSpec(category=category, template=template, theme=theme_key, fmt=fmt,
                    background=bg, logo=logo, text_layers=layers, contact=contact)


# ---------------------------------------------------------------------------
# Render — flatten scaffolding + background photo + every layer
# ---------------------------------------------------------------------------


def render(spec: PostSpec) -> Image.Image:
    base_w, base_h = FORMATS.get(spec.fmt, FORMATS["square"])
    scale = max(0.25, min(4.0, spec.scale))
    size = (int(base_w * scale), int(base_h * scale))
    pal = THEMES.get(spec.theme, THEMES["periwinkle"])

    c = Canvas(size, pal)
    geo = _PAINTERS.get(spec.template, _paint_offset_card)(c, pal)

    if spec.background.image is not None and spec.background.box:
        box: Box = (c.fx(spec.background.box[0]), c.fy(spec.background.box[1]),
                    c.fx(spec.background.box[2]), c.fy(spec.background.box[3]))
        mask = None
        if spec.template == "circle_photo":
            mask = circle_mask((int(box[2] - box[0]), int(box[3] - box[1])))
        c.photo(spec.background.image, box, (spec.background.focus_x, spec.background.focus_y),
               spec.background.zoom, mask)
    elif spec.background.image is not None and spec.template == "banner":
        c.photo(spec.background.image, (0, 0, c.w, c.h),
               (spec.background.focus_x, spec.background.focus_y), spec.background.zoom)
        # repaint the translucent band on top since the photo just covered it
        _PAINTERS["banner"](c, pal)

    for tl in spec.text_layers:
        if tl.kind == "cta":
            if tl.visible and tl.text.strip():
                _cta_box(c, tl, pal)
        else:
            draw_text_layer(c, tl)

    draw_logo(c, spec.logo, "Firm", pal)
    draw_contact(c, spec.contact, geo.contact_y)

    return c.result()


def build_post_canvas(
    headline: str,
    body: str,
    category: str = "Lifestyle",
    accent: str = "gold",
    logo_text: str = "FCA Advisory",
    contact_text: str = "hello@adviser.co.uk • 020 0000 0000 • adviser.co.uk",
    background_path: str | None = None,
) -> Image.Image:
    """Backwards-compatible shim for the original call signature."""
    theme = {"gold": "mustard", "navy": "royal", "green": "sage",
             "purple": "periwinkle", "teal": "sage"}.get(accent.lower(), "periwinkle")
    parts = [p.strip() for p in contact_text.split("•")]
    email = next((p for p in parts if "@" in p), "")
    phone = next((p for p in parts if any(ch.isdigit() for ch in p) and "@" not in p), "")
    website = next((p for p in parts if p and p != email and p != phone), "")
    spec = new_post(
        headline, body, category=category if category in CATEGORY_PRESETS else "Protection",
        brand_name=logo_text, background_image=background_path, theme=theme,
        template="photo_side" if background_path else "offset_card",
        contact=ContactInfo(phone=phone, email=email, website=website,
                            show_phone=bool(phone), show_email=bool(email), show_website=bool(website)),
    )
    return render(spec)


# ---------------------------------------------------------------------------
# Streamlit editor
# ---------------------------------------------------------------------------


def _color_picker(st, label: str, value: RGB, key: str) -> RGB:
    return _from_hex(st.color_picker(label, _hex(value), key=key), value)


def _layer_controls(st, spec: PostSpec, layer: TextLayer) -> None:
    st.text_area("Text", layer.text, key=f"txt_{layer.id}", height=90)
    layer.text = st.session_state[f"txt_{layer.id}"]

    cols = st.columns(3)
    fam_keys = list(FONT_FAMILIES)
    layer.style.family = cols[0].selectbox(
        "Font", fam_keys, index=fam_keys.index(layer.style.family),
        format_func=lambda k: FONT_FAMILIES[k], key=f"fam_{layer.id}")
    layer.style.size = cols[1].slider("Size", 12, 140, layer.style.size, key=f"size_{layer.id}")
    layer.style.color = _color_picker(cols[2], "Colour", layer.style.color, f"col_{layer.id}")

    cols2 = st.columns(4)
    layer.style.bold = cols2[0].checkbox("Bold", layer.style.bold, key=f"b_{layer.id}")
    layer.style.italic = cols2[1].checkbox("Italic", layer.style.italic, key=f"i_{layer.id}")
    layer.style.underline = cols2[2].checkbox("Underline", layer.style.underline, key=f"u_{layer.id}")
    layer.style.highlight = cols2[3].checkbox("Highlight", layer.style.highlight, key=f"h_{layer.id}")
    if layer.style.highlight:
        layer.style.highlight_color = _color_picker(st, "Highlight colour", layer.style.highlight_color,
                                                     f"hc_{layer.id}")

    layer.style.align = st.radio("Alignment", ["left", "center", "right"],
                                 index=["left", "center", "right"].index(layer.style.align),
                                 horizontal=True, key=f"al_{layer.id}")

    st.caption("Position and box width (fractions of the canvas)")
    p = st.columns(3)
    layer.x = p[0].slider("X", 0.0, 0.95, layer.x, 0.01, key=f"x_{layer.id}")
    layer.y = p[1].slider("Y", 0.0, 0.95, layer.y, 0.01, key=f"y_{layer.id}")
    layer.w = p[2].slider("Width", 0.05, 1.0, layer.w, 0.01, key=f"w_{layer.id}")

    layer.visible = st.checkbox("Visible", layer.visible, key=f"vis_{layer.id}")
    if layer.kind == "custom":
        if st.button("Delete this text box", key=f"del_{layer.id}"):
            spec.text_layers = [t for t in spec.text_layers if t.id != layer.id]
            st.rerun()


def post_studio_ui() -> None:
    """A complete post editor page: draft generator + per-layer controls +
    live preview + PNG download. Pure Streamlit + Pillow, no JS."""
    import io

    import streamlit as st

    st.subheader("Post studio")

    gaps = missing_fonts()
    if gaps:
        st.info("Fallback fonts in use for: " + ", ".join(gaps) +
                ". Drop .ttf files into ./assets/fonts to match the house style exactly.")

    if "spec" not in st.session_state:
        st.session_state["spec"] = new_post(
            "A Lifetime of Cover, for a Lifetime of Love",
            "Whole of Life insurance offers fixed protection, helping ensure loved ones "
            "are taken care of no matter when it's needed.",
            category="Protection",
        )

    spec: PostSpec = st.session_state["spec"]

    with st.expander("Start a new draft", expanded=False):
        d1, d2 = st.columns(2)
        category = d1.selectbox("Category", list(CATEGORY_PRESETS))
        fmt = d2.selectbox("Format", list(FORMATS),
                           format_func=lambda k: f"{k} — {FORMATS[k][0]}x{FORMATS[k][1]}")
        headline = st.text_area("Headline", "A Lifetime of Cover, for a Lifetime of Love", height=70)
        body = st.text_area("Body", "", height=70)
        bullets_raw = st.text_area("Bullets (one per line — replaces body)", "", height=70)
        cta = st.text_input("Call to action", "")
        if st.button("Generate draft", type="primary"):
            st.session_state["spec"] = new_post(
                headline, body, tuple(l for l in bullets_raw.splitlines() if l.strip()),
                cta, category=category, fmt=fmt,
                logo_image=spec.logo.image, background_image=spec.background.image,
                contact=spec.contact,
            )
            st.rerun()

    left, right = st.columns([5, 6], gap="large")

    with left:
        st.markdown("**Edit a layer**")
        options = ["— Background photo —", "— Logo —", "— Contact info —"] + \
                  [f"{t.label}" for t in spec.text_layers]
        ids = [None, "__logo__", "__contact__"] + [t.id for t in spec.text_layers]
        default_idx = 3 if spec.text_layers else 0
        choice = st.selectbox("Layer", range(len(options)), format_func=lambda i: options[i],
                              index=default_idx)
        selected = ids[choice]

        if st.button("+ Add text box"):
            new_layer = TextLayer(text="New text", x=0.1, y=0.1, w=0.5, kind="custom", label="Custom text",
                                  style=TextStyle(family="display", size=32,
                                                  color=THEMES[spec.theme].ink))
            spec.text_layers.append(new_layer)
            st.rerun()

        st.divider()

        if selected is None:
            st.markdown("**Background photo**")
            up = st.file_uploader("Replace photo", type=["jpg", "jpeg", "png", "webp"], key="bg_up")
            if up is not None:
                spec.background.image = Image.open(up).convert("RGB")
            if spec.background.image is not None:
                fc = st.columns(3)
                spec.background.focus_x = fc[0].slider("Focus X", 0.0, 1.0, spec.background.focus_x, 0.01)
                spec.background.focus_y = fc[1].slider("Focus Y", 0.0, 1.0, spec.background.focus_y, 0.01)
                spec.background.zoom = fc[2].slider("Zoom", 1.0, 2.0, spec.background.zoom, 0.05)
                if st.button("Remove photo"):
                    spec.background.image = None
                    st.rerun()
            else:
                st.caption("No photo set — the template's flat background is used.")

        elif selected == "__logo__":
            st.markdown("**Logo**")
            st.caption("A default placeholder logo is shown until you upload one.")
            up = st.file_uploader("Replace logo (PNG, transparent background works best)",
                                  type=["png", "jpg", "jpeg"], key="logo_up")
            if up is not None:
                spec.logo.image = Image.open(up).convert("RGBA")
                spec.logo.is_default = False
            if not spec.logo.is_default and st.button("Revert to default logo"):
                spec.logo.image = None
                spec.logo.is_default = True
                st.rerun()
            lc = st.columns(3)
            spec.logo.x = lc[0].slider("X", 0.0, 0.9, spec.logo.x, 0.01)
            spec.logo.y = lc[1].slider("Y", 0.0, 0.9, spec.logo.y, 0.01)
            spec.logo.w = lc[2].slider("Size", 0.04, 0.4, spec.logo.w, 0.01)
            spec.logo.visible = st.checkbox("Visible", spec.logo.visible)

        elif selected == "__contact__":
            st.markdown("**Contact info**")
            spec.contact.phone = st.text_input("Phone", spec.contact.phone)
            spec.contact.show_phone = st.checkbox("Show phone", spec.contact.show_phone)
            spec.contact.email = st.text_input("Email", spec.contact.email)
            spec.contact.show_email = st.checkbox("Show email", spec.contact.show_email)
            spec.contact.website = st.text_input("Website", spec.contact.website)
            spec.contact.show_website = st.checkbox("Show website", spec.contact.show_website)
            spec.contact.color = _color_picker(st, "Text colour", spec.contact.color, "contact_color")

        else:
            layer = spec.layer(selected)
            if layer is not None:
                st.markdown(f"**{layer.label}**")
                _layer_controls(st, spec, layer)

    with right:
        image = render(spec)
        st.image(image, use_container_width=True)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        st.download_button("Download PNG", buf.getvalue(),
                           file_name=f"{spec.category}-{spec.template}.png", mime="image/png")


if __name__ == "__main__":
    render(new_post(
        "A Lifetime of Cover, for a Lifetime of Love",
        "Whole of Life insurance offers fixed protection, helping ensure loved ones "
        "are taken care of no matter when it's needed.",
        category="Protection",
    )).save("demo.png")
    print("wrote demo.png")
