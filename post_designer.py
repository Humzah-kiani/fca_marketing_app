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
    """Render a square social-media post mockup in a finance-style template."""
    width, height = 1080, 1080
    accent_rgb = ACCENT_COLORS.get(accent.lower(), ACCENT_COLORS["gold"])

    base = Image.new("RGB", (width, height), color=(12, 26, 39))
    draw = ImageDraw.Draw(base)

    if background_path:
        try:
            with Image.open(background_path) as cover:
                cover = cover.convert("RGB")
                cover = cover.resize((width, height))
                base = cover.copy()
        except Exception:
            pass

    overlay = Image.new("RGBA", (width, height), (10, 16, 24, 180))
    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)

    # Brand accent strip
    accent_bar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent_bar)
    accent_draw.rounded_rectangle((60, 60, 1020, 170), radius=22, fill=(*accent_rgb, 220))
    base = Image.alpha_composite(base.convert("RGBA"), accent_bar).convert("RGB")
    draw = ImageDraw.Draw(base)

    # Content panel
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((80, 200, 980, 930), radius=36, fill=(17, 25, 35, 150))
    base = Image.alpha_composite(base.convert("RGBA"), panel).convert("RGB")
    draw = ImageDraw.Draw(base)

    # Category badge
    badge_font = _load_font(28, bold=True)
    badge_box = draw.textbbox((0, 0), category.upper(), font=badge_font)
    badge_x0 = 150
    badge_y0 = 245
    badge_x1 = badge_x0 + badge_box[2] + 36
    badge_y1 = badge_y0 + badge_box[3] + 28
    draw.rounded_rectangle((badge_x0, badge_y0, badge_x1, badge_y1), radius=18, fill=(*accent_rgb, 255))
    draw.text((badge_x0 + 18, badge_y0 + 12), category.upper(), fill=(14, 20, 28), font=badge_font)

    # Headline
    headline_font = _load_font(72, bold=True)
    headline_lines = _wrap_text(draw, headline, 620, headline_font)
    headline_y = 340
    for line in headline_lines[:3]:
        line_box = draw.textbbox((0, 0), line, font=headline_font)
        draw.text((150, headline_y), line, fill=(255, 255, 255), font=headline_font)
        headline_y += line_box[3] + 12

    # Body text
    body_font = _load_font(34, bold=False)
    body_lines = _wrap_text(draw, body, 630, body_font)
    body_y = headline_y + 12
    for line in body_lines[:5]:
        draw.text((150, body_y), line, fill=(226, 232, 238), font=body_font)
        body_y += 42

    # Bottom contact bar
    draw.rounded_rectangle((150, 815, 930, 890), radius=18, fill=(255, 255, 255, 40))
    logo_font = _load_font(26, bold=True)
    draw.text((170, 840), logo_text.upper(), fill=(*accent_rgb, 255), font=logo_font)
    contact_font = _load_font(22, bold=False)
    draw.text((550, 842), contact_text, fill=(240, 242, 245), font=contact_font)

    # Simple decorative circle accent
    draw.ellipse((825, 230, 980, 385), fill=(*accent_rgb, 170))

    return base
