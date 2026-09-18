import textwrap

from PIL import Image, ImageDraw, ImageFont

ACCENT_COLORS = {
    "gold": (202, 164, 82),
    "navy": (18, 34, 50),
    "green": (26, 118, 95),
    "purple": (83, 92, 154),
    "teal": (24, 132, 143),
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
    """Render a simple Canva-like social post template for quick adviser marketing mockups."""
    width, height = 1080, 1080
    accent_rgb = ACCENT_COLORS.get(accent.lower(), ACCENT_COLORS["gold"])

    base = Image.new("RGB", (width, height), color=(16, 28, 39))
    draw = ImageDraw.Draw(base)

    if background_path:
        try:
            with Image.open(background_path) as cover:
                cover = cover.convert("RGB")
                cover = cover.resize((width, height))
                base = cover.copy()
        except Exception:
            pass

    overlay = Image.new("RGBA", (width, height), (12, 19, 28, 165))
    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)

    # simple top accent bar
    accent_bar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent_bar)
    accent_draw.rounded_rectangle((60, 70, 1020, 130), radius=18, fill=(*accent_rgb, 220))
    base = Image.alpha_composite(base.convert("RGBA"), accent_bar).convert("RGB")
    draw = ImageDraw.Draw(base)

    # main text card
    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card)
    card_draw.rounded_rectangle((100, 180, 560, 820), radius=28, fill=(13, 22, 31, 150))
    base = Image.alpha_composite(base.convert("RGBA"), card).convert("RGB")
    draw = ImageDraw.Draw(base)

    # logo + category
    logo_font = _load_font(22, bold=True)
    draw.text((130, 92), logo_text.upper(), fill=(*accent_rgb, 255), font=logo_font)

    badge_font = _load_font(22, bold=True)
    badge_text = category.upper()
    badge_box = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_x = 130
    badge_y = 235
    badge_w = badge_box[2] + 26
    badge_h = badge_box[3] + 18
    draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_w, badge_y + badge_h), radius=12, fill=(*accent_rgb, 255))
    draw.text((badge_x + 12, badge_y + 8), badge_text, fill=(18, 26, 35), font=badge_font)

    # headline
    headline_font = _load_font(64, bold=True)
    headline_lines = _wrap_text(draw, headline, 330, headline_font)
    headline_y = 310
    for line in headline_lines[:3]:
        draw.text((130, headline_y), line, fill=(255, 255, 255), font=headline_font)
        headline_y += 66

    # body text
    body_font = _load_font(28, bold=False)
    body_lines = _wrap_text(draw, body, 350, body_font)
    body_y = headline_y + 16
    for line in body_lines[:4]:
        draw.text((130, body_y), line, fill=(228, 233, 238), font=body_font)
        body_y += 38

    # basic CTA
    cta_font = _load_font(23, bold=True)
    cta_text = "Book a consultation"
    cta_box = draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_x0 = 130
    cta_y0 = 720
    cta_x1 = cta_x0 + cta_box[2] + 34
    cta_y1 = cta_y0 + cta_box[3] + 16
    draw.rounded_rectangle((cta_x0, cta_y0, cta_x1, cta_y1), radius=16, fill=(*accent_rgb, 255))
    draw.text((cta_x0 + 16, cta_y0 + 7), cta_text, fill=(21, 29, 38), font=cta_font)

    # footer contact strip
    draw.rounded_rectangle((130, 790, 980, 870), radius=16, fill=(255, 255, 255, 25))
    footer_font = _load_font(18, bold=False)
    draw.text((150, 812), contact_text, fill=(242, 245, 247), font=footer_font)

    # simple right-side photo block
    visual = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    visual_draw = ImageDraw.Draw(visual)
    visual_draw.rounded_rectangle((600, 180, 940, 820), radius=32, fill=(38, 52, 67, 180))
    visual_draw.ellipse((650, 230, 900, 440), fill=(*accent_rgb, 150))
    visual_draw.rounded_rectangle((670, 500, 895, 720), radius=28, fill=(12, 22, 31, 140))
    visual_draw.line((690, 220, 900, 760), fill=(*accent_rgb, 200), width=7)
    base = Image.alpha_composite(base.convert("RGBA"), visual).convert("RGB")
    draw = ImageDraw.Draw(base)

    return base
