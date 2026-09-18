import textwrap

from PIL import Image, ImageDraw, ImageFont

ACCENT_COLORS = {
    "gold": (214, 176, 90),
    "navy": (17, 39, 56),
    "green": (23, 119, 89),
    "purple": (95, 93, 162),
    "teal": (28, 146, 155),
}


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "arial.ttf",
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            if bold:
                return ImageFont.truetype(candidate, size=size)
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, font: ImageFont.ImageFont) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_post_canvas(
    headline: str,
    body: str,
    category: str = "Lifestyle",
    accent: str = "gold",
    logo_text: str = "FCA Advisory",
    contact_text: str = "hello@adviser.co.uk • 020 0000 0000 • adviser.co.uk",
    background_path: str | None = None,
) -> Image.Image:
    """Render a premium square social-media post mockup matching adviser-style sample references."""
    width, height = 1080, 1080
    accent_rgb = ACCENT_COLORS.get(accent.lower(), ACCENT_COLORS["gold"])

    base = Image.new("RGB", (width, height), color=(11, 21, 32))
    draw = ImageDraw.Draw(base)

    if background_path:
        try:
            with Image.open(background_path) as cover:
                cover = cover.convert("RGB")
                cover = cover.resize((width, height))
                base = cover.copy()
        except Exception:
            pass

    overlay = Image.new("RGBA", (width, height), (10, 16, 24, 185))
    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)

    # soft premium accent panels
    accent_bar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent_bar)
    accent_draw.rounded_rectangle((50, 60, 1030, 150), radius=26, fill=(*accent_rgb, 230))
    accent_draw.rounded_rectangle((610, 180, 980, 930), radius=38, fill=(255, 255, 255, 20))
    base = Image.alpha_composite(base.convert("RGBA"), accent_bar).convert("RGB")
    draw = ImageDraw.Draw(base)

    # left-side text card
    left_panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    left_draw = ImageDraw.Draw(left_panel)
    left_draw.rounded_rectangle((85, 190, 565, 845), radius=34, fill=(15, 23, 34, 150))
    base = Image.alpha_composite(base.convert("RGBA"), left_panel).convert("RGB")
    draw = ImageDraw.Draw(base)

    # top left logo block
    logo_font = _load_font(26, bold=True)
    logo_box = draw.textbbox((0, 0), logo_text.upper(), font=logo_font)
    draw.rounded_rectangle((110, 90, 110 + logo_box[2] + 26, 90 + logo_box[3] + 20), radius=12, fill=(255, 255, 255, 36))
    draw.text((126, 100), logo_text.upper(), fill=(*accent_rgb, 255), font=logo_font)

    # category badge
    badge_font = _load_font(26, bold=True)
    badge_text = category.upper()
    badge_box = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_x0 = 120
    badge_y0 = 240
    badge_x1 = badge_x0 + badge_box[2] + 32
    badge_y1 = badge_y0 + badge_box[3] + 18
    draw.rounded_rectangle((badge_x0, badge_y0, badge_x1, badge_y1), radius=16, fill=(*accent_rgb, 255))
    draw.text((badge_x0 + 16, badge_y0 + 8), badge_text, fill=(13, 20, 31), font=badge_font)

    # headline
    headline_font = _load_font(74, bold=True)
    headline_lines = _wrap_text(draw, headline, 430, headline_font)
    headline_y = 315
    for line in headline_lines[:3]:
        draw.text((120, headline_y), line, fill=(255, 255, 255), font=headline_font)
        headline_y += 76

    # body copy
    body_font = _load_font(30, bold=False)
    body_lines = _wrap_text(draw, body, 430, body_font)
    body_y = headline_y + 10
    for line in body_lines[:5]:
        draw.text((120, body_y), line, fill=(225, 231, 239), font=body_font)
        body_y += 42

    # CTA pill
    cta_font = _load_font(24, bold=True)
    cta_text = "Book a consultation"
    cta_box = draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_x0 = 120
    cta_y0 = 770
    cta_x1 = cta_x0 + cta_box[2] + 42
    cta_y1 = cta_y0 + cta_box[3] + 18
    draw.rounded_rectangle((cta_x0, cta_y0, cta_x1, cta_y1), radius=20, fill=(*accent_rgb, 255))
    draw.text((cta_x0 + 21, cta_y0 + 9), cta_text, fill=(15, 22, 29), font=cta_font)

    # bottom contact bar
    draw.rounded_rectangle((120, 815, 980, 900), radius=18, fill=(255, 255, 255, 32))
    footer_font = _load_font(20, bold=False)
    draw.text((145, 840), contact_text, fill=(240, 242, 245), font=footer_font)

    # decorative image-style shape on the right
    shape = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shape_draw = ImageDraw.Draw(shape)
    shape_draw.ellipse((650, 220, 930, 500), fill=(*accent_rgb, 160))
    shape_draw.rounded_rectangle((640, 520, 920, 760), radius=36, fill=(20, 38, 50, 160))
    base = Image.alpha_composite(base.convert("RGBA"), shape).convert("RGB")
    draw = ImageDraw.Draw(base)

    # diagonal accent line
    draw.line((700, 220, 930, 760), fill=(*accent_rgb, 200), width=8)

    return base
