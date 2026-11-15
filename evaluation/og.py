"""The website's link-preview images (Open Graph), one per language.

A link pasted in WhatsApp or Telegram shows og:image as the card; without one, the site looked
like a bare URL (16-10-2026). Regenerate after changing the wording: `uv run python -m
evaluation.og`. Roboto is a development dependency (Pillow's default font has no ¿ or accents).
"""

from __future__ import annotations

from pathlib import Path

import font_roboto
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "web" / "estimo"
W, H = 1200, 630
BG, FG, MUTED, ACCENT = (31, 35, 40), (232, 230, 227), (163, 171, 181), (45, 212, 191)
LIGHTS = [(249, 112, 102), (253, 176, 34), (71, 205, 137)]

TEXT = {
    "es": (
        "Comprueba un alquiler",
        "antes de pagar",
        "Anuncio y conversación: verde, ámbar o rojo, y por qué. Gratis.",
        "og.png",
    ),
    "en": (
        "Check a rental",
        "before you pay",
        "Listing and chat: green, amber or red, and why. Free.",
        "og-en.png",
    ),
}


def render(lang: str) -> Path:
    line1, line2, lead, name = TEXT[lang]
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # The favicon's traffic light, large, on the left.
    x, r = 150, 62
    for i, colour in enumerate(LIGHTS):
        cy = H // 2 + (i - 1) * (2 * r + 30)
        d.ellipse((x - r, cy - r, x + r, cy + r), fill=colour)
    left = 300
    d.text((left, 120), "¿Es timo?", font=ImageFont.truetype(font_roboto.RobotoBold, 96), fill=FG)
    big = ImageFont.truetype(font_roboto.RobotoMedium, 58)
    d.text((left, 260), line1, font=big, fill=FG)
    d.text((left, 330), line2, font=big, fill=FG)
    d.text((left, 440), lead, font=ImageFont.truetype(font_roboto.Roboto, 30), fill=MUTED)
    d.text(
        (left, 510),
        "estimo.patriciollorens.com · @EsTimoBot",
        font=ImageFont.truetype(font_roboto.RobotoMedium, 30),
        fill=ACCENT,
    )
    path = OUT / name
    img.save(path, optimize=True)
    return path


def main() -> None:
    for lang in TEXT:
        print(render(lang))


if __name__ == "__main__":
    main()
