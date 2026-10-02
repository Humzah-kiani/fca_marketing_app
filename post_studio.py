"""
post_studio.py
==============

A social-post, carousel, and short reel generator/editor built on Pillow, Streamlit, and ImageIO.
All template graphics are fully customizable vector shapes—no static non-editable PNG templates.
"""

from __future__ import annotations

import io
import math
import os
import uuid
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import imageio
    HAS_IMAGEIO = True
except ImportError:
    HAS_IMAGEIO = False

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
Box = tuple[float, float, float, float]

__all__ = [
    "Palette", "TextStyle", "TextLayer", "ImageLayer", "BackgroundLayer",
    "ContactInfo", "PostSpec", "SlideSpec", "CarouselSpec",
    "THEMES", "FORMATS", "TEMPLATES", "TEMPLATE_LABELS", "CATEGORY_PRESETS",
    "FONT_FAMILIES", "new_post", "render", "default_logo", "build_post_canvas", "post_studio_ui",
    "render_carousel", "export_reel_video"
]

# ---------------------------------------------------------------------------
# Dynamic Custom Color Palettes & Custom Schemes
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
    "custom": Palette("custom", "Custom Palette",
        bg=(25, 25, 30), card=(45, 45, 55), peek=(255, 105, 180),
        ink=(255, 255, 255), ink_muted=(200, 200, 210), on_bg=(255, 255, 255),
        cta_bg=(255, 105, 180), cta_ink=(255, 255, 255),
        pale=(240, 240, 245), pale_ink=(20, 20, 30))
}

CATEGORY_PRESETS: dict[str, dict[str, str]] = {
    "Protection": {"theme": "periwinkle", "template": "offset_card"},
    "Retirement": {"theme": "royal", "template": "card_photo"},
    "Pension": {"theme": "emerald", "template": "photo_side"},
    "Mortgage": {"theme": "plum", "template": "banner"},
    "Lifestyle": {"theme": "mustard", "template": "arc"},
}

FORMATS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "portrait": (1080, 1350),
    "story": (1080, 1920),
    "reel": (1080, 1920),
    "landscape": (1200, 628),
}

FONT_FAMILIES: dict[str, str] = {
    "display": "Poppins (sans)",
    "serif": "Lora (serif)",
    "body": "System sans",
}

_FONT_STACKS: dict[tuple[str, str], Sequence[str]] = {
    ("display", "regular"): ("Poppins-Regular", "Inter-Regular", "LiberationSans-Regular", "DejaVuSans", "arial"),
    ("display", "bold"): ("Poppins-Bold", "Inter-Bold", "LiberationSans-Bold", "DejaVuSans-Bold", "arialbd"),
    ("display", "italic"): ("Poppins-Italic", "Inter-Italic", "LiberationSans-Italic", "DejaVuSans-Oblique", "ariali"),
    ("serif", "regular"): ("Lora-Regular", "PlayfairDisplay-Regular", "DejaVuSerif", "georgia"),
    ("serif", "bold"): ("Lora-Bold", "PlayfairDisplay-Bold", "DejaVuSerif-Bold", "georgiab"),
    ("body", "regular"): ("Inter-Regular", "LiberationSans-Regular", "DejaVuSans", "arial"),
    ("body", "bold"): ("Inter-Bold", "LiberationSans-Bold", "DejaVuSans-Bold", "arialbd"),
}

FONT_DIRS = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts", "C:/Windows/Fonts"
)

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
                    index.setdefault(stem.lower(), os.path.join(dirpath, name))
    return index

@lru_cache(maxsize=64)
def _resolve(family: str, weight: str) -> str | None:
    index = _font_index()
    for cand in _FONT_STACKS.get((family, weight), ()):
        hit = index.get(cand.lower())
        if hit:
            return hit
    return None

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

def _from_hex(s: str, fallback: RGB = (0, 0, 0)) -> RGB:
    s = s.lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback

def _hex(c: RGB) -> str:
    return "#%02x%02x%02x" % c

def cover_crop(img: Image.Image, size: tuple[int, int], focus: tuple[float, float] = (0.5, 0.5), zoom: float = 1.0) -> Image.Image:
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    fitted = ImageOps.fit(img.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS, centering=focus)
    if zoom > 1.001:
        zw, zh = int(w * zoom), int(h * zoom)
        big = ImageOps.fit(img.convert("RGB"), (zw, zh), method=Image.Resampling.LANCZOS, centering=focus)
        x0 = (zw - w) // 2
        y0 = (zh - h) // 2
        fitted = big.crop((x0, y0, x0 + w, y0 + h))
    return fitted

def default_logo(name: str = "LOGO", palette: Palette | None = None, size: int = 320) -> Image.Image:
    pal = palette or THEMES["periwinkle"]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=(*pal.card, 255))
    d.ellipse((size * 0.045, size * 0.045, size * 0.955, size * 0.955), outline=(*pal.peek, 255), width=max(2, size // 45))
    initials = "".join(w[0] for w in name.strip().split()[:2]).upper() or "L"
    path = _resolve("display", "bold")
    f = _load_ttf(path, int(size * 0.38))
    d.text((size / 2, size / 2), initials, font=f, fill=(*pal.ink, 255), anchor="mm")
    return img

# ---------------------------------------------------------------------------
# Data Models: Layers, Post Specs, Slides, and Carousels
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return uuid.uuid4().hex[:8]

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

@dataclass
class TextLayer:
    text: str
    x: float = 0.08
    y: float = 0.10
    w: float = 0.60
    style: TextStyle = field(default_factory=TextStyle)
    visible: bool = True
    kind: str = "custom"
    id: str = field(default_factory=_new_id)
    label: str = "Text"

@dataclass
class ImageLayer:
    image: Image.Image | None = None
    x: float = 0.055
    y: float = 0.055
    w: float = 0.14
    visible: bool = True
    is_default: bool = True

@dataclass
class BackgroundLayer:
    image: Image.Image | None = None
    box: Box = (0.0, 0.0, 1.0, 1.0)
    focus_x: float = 0.5
    focus_y: float = 0.42
    zoom: float = 1.0
    tint: RGB | None = None

@dataclass
class ContactInfo:
    phone: str = "+123-456-789"
    email: str = "hello@studio.com"
    website: str = "www.studio.com"
    show_phone: bool = True
    show_email: bool = False
    show_website: bool = True
    color: RGB = (255, 255, 255)

@dataclass
class PostSpec:
    category: str = "Protection"
    template: str = "offset_card"
    theme: str = "periwinkle"
    custom_palette: Palette | None = None
    fmt: str = "square"
    background: BackgroundLayer = field(default_factory=BackgroundLayer)
    logo: ImageLayer = field(default_factory=ImageLayer)
    text_layers: list[TextLayer] = field(default_factory=list)
    contact: ContactInfo = field(default_factory=ContactInfo)
    scale: float = 1.0

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

# ---------------------------------------------------------------------------
# Vector Canvas & Rendering Engine
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

    def result(self) -> Image.Image:
        return self.img.convert("RGB")

@dataclass
class Geometry:
    card_box: Box
    photo_box: Box | None
    ink: RGB
    ink_muted: RGB
    contact_ink: RGB
    contact_y: float
    logo_xy: tuple[float, float]

def _paint_offset_card(c: Canvas, pal: Palette) -> Geometry:
    card: Box = (0, c.fy(0.098), c.fx(0.923), c.fy(0.776))
    c.rect((c.fx(0.195), c.fy(0.080), c.fx(0.835), c.fy(0.118)), pal.peek)
    c.rect((c.fx(0.098), c.fy(0.760), c.fx(0.760), c.fy(0.869)), pal.peek)
    c.rect(card, pal.card)
    return Geometry((0.09, 0.20, 0.80, 0.72), None, pal.ink, pal.ink_muted, pal.on_bg, 0.938, (0.055, 0.90))

def _paint_card_photo(c: Canvas, pal: Palette) -> Geometry:
    band_top = c.fy(0.75)
    card: Box = (c.fx(0.08), c.fy(0.08), c.fx(0.92), band_top)
    c.rect(card, pal.card)
    return Geometry((0.12, 0.14, 0.76, 0.55), (0.0, 0.75, 1.0, 1.0), pal.ink, pal.ink_muted, pal.on_bg, 0.052, (0.055, 0.052))

def _paint_photo_side(c: Canvas, pal: Palette) -> Geometry:
    card: Box = (c.fx(0.06), c.fy(0.10), c.fx(0.68), c.fy(0.88))
    c.rect((0, 0, c.w, c.h), pal.bg)
    c.rect(card, pal.card)
    return Geometry((0.10, 0.15, 0.60, 0.65), (0.55, 0.0, 1.0, 1.0), pal.ink, pal.ink_muted, pal.on_bg, 0.92, (0.055, 0.90))

def _paint_arc(c: Canvas, pal: Palette) -> Geometry:
    r = max(c.w, c.h) * 0.62
    cx, cy = c.fx(0.30), c.fy(0.26)
    c.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*pal.pale, 255))
    return Geometry((0.10, 0.12, 0.60, 0.45), None, pal.pale_ink, pal.ink_muted, pal.on_bg, 0.938, (0.055, 0.90))

def _paint_banner(c: Canvas, pal: Palette) -> Geometry:
    band: Box = (0, c.fy(0.15), c.w, c.fy(0.60))
    c.rect(band, pal.card)
    return Geometry((0.08, 0.18, 0.84, 0.38), (0.0, 0.0, 1.0, 1.0), pal.ink, pal.ink_muted, pal.on_bg, 0.938, (0.055, 0.90))

_PAINTERS: dict[str, Callable[[Canvas, Palette], Geometry]] = {
    "offset_card": _paint_offset_card,
    "card_photo": _paint_card_photo,
    "photo_side": _paint_photo_side,
    "arc": _paint_arc,
    "banner": _paint_banner,
}

TEMPLATES = _PAINTERS
TEMPLATE_LABELS = {
    "offset_card": "Offset Card — Modern layered cards with vector peek accents",
    "card_photo": "Card & Photo — Clear headline card with photo base",
    "photo_side": "Split Photo Side — Clean typography card beside image field",
    "arc": "Arc Vector — Circular geometric background vector cut",
    "banner": "Translucent Banner — Text banner standard layout"
}

def draw_text_layer(c: Canvas, layer: TextLayer) -> None:
    if not layer.visible or not layer.text.strip():
        return
    st = layer.style
    path = _resolve(st.family, "bold" if st.bold else "regular")
    size_px = max(8, int(c.s(st.size)))
    f = _load_ttf(path, size_px)
    max_w = max(c.s(40), c.fx(layer.w))

    # Custom simple text render
    words = layer.text.split()
    lines, curr = [], ""
    for w in words:
        trial = f"{curr} {w}".strip()
        if f.getlength(trial) <= max_w or not curr:
            curr = trial
        else:
            lines.append(curr)
            curr = w
    if curr:
        lines.append(curr)

    x0 = c.fx(layer.x)
    y = c.fy(layer.y)
    line_h = size_px * st.leading

    for line in lines:
        tw = f.getlength(line)
        lx = x0 + (max_w - tw) / 2 if st.align == "center" else (x0 + max_w - tw if st.align == "right" else x0)
        if st.highlight:
            c.rect((lx - 4, y - 2, lx + tw + 4, y + size_px + 2), st.highlight_color)
        c.draw.text((lx, y), line, font=f, fill=(*st.color, 255))
        y += line_h

def render(spec: PostSpec) -> Image.Image:
    base_w, base_h = FORMATS.get(spec.fmt, FORMATS["square"])
    size = (int(base_w * spec.scale), int(base_h * spec.scale))
    pal = spec.get_palette()

    c = Canvas(size, pal)
    geo = _PAINTERS.get(spec.template, _paint_offset_card)(c, pal)

    if spec.background.image is not None:
        box = (c.fx(spec.background.box[0]), c.fy(spec.background.box[1]), c.fx(spec.background.box[2]), c.fy(spec.background.box[3]))
        panel = cover_crop(spec.background.image, (int(box[2] - box[0]), int(box[3] - box[1])), (spec.background.focus_x, spec.background.focus_y), spec.background.zoom)
        c.img.paste(panel, (int(box[0]), int(box[1])))

    for tl in spec.text_layers:
        draw_text_layer(c, tl)

    # Draw logo placeholder/uploaded logo
    if spec.logo.visible:
        logo_img = spec.logo.image or default_logo("STUDIO", pal)
        lw = int(c.fx(spec.logo.w))
        lh = int(logo_img.height * (lw / logo_img.width))
        resized = logo_img.resize((lw, max(1, lh)), Image.Resampling.LANCZOS)
        c.img.paste(resized, (int(c.fx(spec.logo.x)), int(c.fy(spec.logo.y))), resized if resized.mode == "RGBA" else None)

    return c.result()

def new_post(headline: str, body: str = "", cta: str = "", category: str = "Protection",
             fmt: str = "square", template: str = "offset_card", theme: str = "periwinkle") -> PostSpec:
    pal = THEMES.get(theme, THEMES["periwinkle"])
    layers = [
        TextLayer(text=headline, x=0.1, y=0.18, w=0.75, label="Headline",
                  style=TextStyle(family="display", size=48, color=pal.ink, bold=True)),
        TextLayer(text=body, x=0.1, y=0.38, w=0.75, label="Body Copy",
                  style=TextStyle(family="body", size=24, color=pal.ink_muted, bold=False))
    ]
    if cta:
        layers.append(TextLayer(text=cta, x=0.1, y=0.68, w=0.5, label="CTA",
                                style=TextStyle(family="display", size=22, color=pal.cta_bg, bold=True)))
    return PostSpec(category=category, template=template, theme=theme, fmt=fmt, text_layers=layers)

def build_post_canvas(
    headline: str,
    body: str,
    category: str = "Lifestyle",
    accent: str = "gold",
    logo_text: str = "FCA Advisory",
    contact_text: str = "hello@adviser.co.uk • 020 0000 0000 • adviser.co.uk",
    background_path: str | None = None,
) -> Image.Image:
    """Keep the original canvas API available to the app and legacy callers."""
    theme = {"gold": "mustard", "navy": "royal", "green": "emerald",
             "purple": "periwinkle", "teal": "emerald"}.get(accent.lower(), "periwinkle")
    category = category if category in CATEGORY_PRESETS else "Protection"
    template = "photo_side" if background_path else "offset_card"
    spec = new_post(headline, body, category=category, template=template, theme=theme)
    spec.logo.image = default_logo(logo_text, spec.get_palette())
    spec.logo.is_default = False
    if background_path:
        try:
            with Image.open(background_path) as background:
                spec.background.image = background.convert("RGB")
        except (OSError, ValueError):
            pass
    if contact_text:
        spec.text_layers.append(TextLayer(
            text=contact_text, x=0.08, y=0.93, w=0.84, label="Contact",
            style=TextStyle(family="body", size=16, color=spec.get_palette().on_bg, bold=False),
        ))
    return render(spec)

def render_carousel(carousel: CarouselSpec) -> list[Image.Image]:
    return [render(slide) for slide in carousel.slides]

def export_reel_video(slides: list[PostSpec], fps: int = 2) -> bytes:
    """Renders carousel slides into an animated video or GIF reel bytes."""
    images = [render(s) for s in slides]
    buf = io.BytesIO()
    if HAS_IMAGEIO:
        import numpy as np
        frames = [np.array(img) for img in images]
        imageio.mimsave(buf, frames, format="MP4", fps=fps)
    else:
        images[0].save(buf, format="GIF", save_all=True, append_images=images[1:], duration=int(1000/fps), loop=0)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# Streamlit Interactive Studio UI
# ---------------------------------------------------------------------------

def post_studio_ui() -> None:
    import streamlit as st
    st.set_page_config(layout="wide", page_title="Post & Reel Studio")
    st.title("Customizable Post, Reel & Carousel Studio")

    if "carousel" not in st.session_state:
        st.session_state["carousel"] = CarouselSpec(slides=[new_post("Customizable Social Posts", "Design reels, carousels, and square posts effortlessly with dynamic vector themes.", "Swipe Left ➔")])

    car: CarouselSpec = st.session_state["carousel"]

    # Carousel Management Bar
    st.subheader("Slide & Format Settings")
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])

    active_idx = c1.number_input("Active Slide", min_value=1, max_value=len(car.slides), value=car.active_index + 1) - 1
    car.active_index = active_idx
    spec = car.slides[active_idx]

    spec.fmt = c2.selectbox("Canvas Format", list(FORMATS.keys()), index=list(FORMATS.keys()).index(spec.fmt))

    if c3.button("➕ Add Slide"):
        car.slides.append(new_post(f"Slide {len(car.slides)+1} Title", "Content text goes here."))
        st.rerun()

    if c4.button("Delete Slide") and len(car.slides) > 1:
        car.slides.pop(active_idx)
        car.active_index = max(0, active_idx - 1)
        st.rerun()

    left, right = st.columns([5, 6], gap="medium")

    with left:
        st.subheader("Custom Color & Theme Controls")
        theme_pick = st.selectbox("Color Palette", list(THEMES.keys()), index=list(THEMES.keys()).index(spec.theme))
        spec.theme = theme_pick

        if spec.theme == "custom":
            st.caption("Customize exact scheme colors below:")
            if not spec.custom_palette:
                spec.custom_palette = Palette("custom", "Custom", (30,30,30), (50,50,60), (255,100,100), (255,255,255), (200,200,200), (255,255,255), (255,100,100), (255,255,255), (240,240,240), (20,20,20))
            cp1, cp2, cp3 = st.columns(3)
            bg = _from_hex(cp1.color_picker("Background", _hex(spec.custom_palette.bg)))
            card = _from_hex(cp2.color_picker("Card Panel", _hex(spec.custom_palette.card)))
            ink = _from_hex(cp3.color_picker("Text Color", _hex(spec.custom_palette.ink)))
            spec.custom_palette = Palette("custom", "Custom", bg, card, spec.custom_palette.peek, ink, spec.custom_palette.ink_muted, ink, card, bg, card, ink)

        spec.template = st.selectbox("Vector Template Layout", list(TEMPLATE_LABELS.keys()), index=list(TEMPLATE_LABELS.keys()).index(spec.template))

        st.subheader("Edit Slide Text Layers")
        for i, layer in enumerate(spec.text_layers):
            with st.expander(f"Layer: {layer.label}", expanded=(i == 0)):
                layer.text = st.text_area("Content", layer.text, key=f"t_{active_idx}_{layer.id}")
                l1, l2, l3 = st.columns(3)
                layer.style.size = l1.slider("Size", 12, 100, layer.style.size, key=f"s_{active_idx}_{layer.id}")
                layer.x = l2.slider("Position X", 0.0, 0.9, layer.x, 0.01, key=f"x_{active_idx}_{layer.id}")
                layer.y = l3.slider("Position Y", 0.0, 0.9, layer.y, 0.01, key=f"y_{active_idx}_{layer.id}")

    with right:
        st.subheader("Live Canvas Output")
        img = render(spec)
        st.image(img, use_container_width=True)

        st.subheader("Export & Download Options")
        d1, d2, d3 = st.columns(3)

        # Single Image
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        d1.download_button("⬇Download Slide (PNG)", buf.getvalue(), file_name=f"slide_{active_idx+1}.png", mime="image/png")

        # Carousel ZIP
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            for idx, slide_img in enumerate(render_carousel(car)):
                s_buf = io.BytesIO()
                slide_img.save(s_buf, format="PNG")
                zf.writestr(f"slide_{idx+1}.png", s_buf.getvalue())
        d2.download_button("Download Carousel (ZIP)", zip_buf.getvalue(), file_name="carousel.zip", mime="application/zip")

        # Video Reel Export
        reel_bytes = export_reel_video(car.slides)
        ext = "mp4" if HAS_IMAGEIO else "gif"
        d3.download_button(f"🎥 Export Reel ({ext.upper()})", reel_bytes, file_name=f"reel.{ext}", mime=f"video/{ext}")

if __name__ == "__main__":
    post_studio_ui()