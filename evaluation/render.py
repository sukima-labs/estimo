"""Render a fixture chat as WhatsApp-like screenshots (JPEG, as Telegram delivers photos).

Usage: uv run python -m evaluation.render [fixture-glob]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import font_roboto
from PIL import Image, ImageDraw, ImageFont

from evaluation.fixtures import IMAGES_DIR, Fixture, load_all

W = 720
MAX_H = 1560
HEADER_H = 150
BG = (239, 231, 222)
HEADER = (0, 128, 105)
BUBBLE_IN = (255, 255, 255)
BUBBLE_OUT = (217, 253, 211)
TEXT = (17, 27, 33)
META = (102, 119, 129)
PAD = 16
BUBBLE_MAX = int(W * 0.76)

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}
_NON_BMP = re.compile(r"[\U00010000-\U0010FFFF]")


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        # Pillow's bundled default font has no accents, ñ or €; Roboto (dev dependency) does.
        _font_cache[size] = ImageFont.truetype(font_roboto.Roboto, size)
    return _font_cache[size]


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            cand = f"{cur} {w}".strip()
            if f.getlength(cand) <= width:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _bubbles(fx: Fixture) -> list[tuple[str, list[str], str]]:
    f = font(26)
    out = []
    minute = 2
    for who, text in fx.chat:
        clean = _NON_BMP.sub("", text).strip()
        out.append((who, wrap(clean, f, BUBBLE_MAX - 2 * PAD), f"18:{minute:02d}"))
        minute += 1
    return out


def _header(img: Image.Image, fx: Fixture) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 40], fill=(0, 94, 77))
    d.text((PAD, 8), "18:10", font=font(20), fill=(255, 255, 255))
    d.rectangle([0, 40, W, HEADER_H], fill=HEADER)
    d.ellipse([60, 62, 126, 128], fill=(200, 200, 200))
    # Unsaved contacts show the number as title and the push name as subtitle.
    title = fx.advertiser_phone or fx.advertiser_name or "Contacto"
    sub = f"~{fx.advertiser_name}" if fx.advertiser_phone and fx.advertiser_name else "en línea"
    d.text((20, 78), "<", font=font(34), fill=(255, 255, 255))
    d.text((142, 64), title, font=font(30), fill=(255, 255, 255))
    d.text((142, 104), sub, font=font(22), fill=(220, 240, 235))


def render(fx: Fixture) -> list[Image.Image]:
    f, fm = font(26), font(19)
    line_h = 34
    pages: list[Image.Image] = []
    img = Image.new("RGB", (W, MAX_H), BG)
    _header(img, fx)
    y = HEADER_H + 20
    for who, lines, ts in _bubbles(fx):
        text_w = max((f.getlength(line) for line in lines), default=0)
        bw = int(min(BUBBLE_MAX, max(text_w + 2 * PAD, 140)))
        bh = len(lines) * line_h + 2 * PAD + 18
        if y + bh + 20 > MAX_H:
            pages.append(img.crop((0, 0, W, y + 10)))
            img = Image.new("RGB", (W, MAX_H), BG)
            _header(img, fx)
            y = HEADER_H + 20
        x = W - bw - PAD if who == "me" else PAD
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(
            [x, y, x + bw, y + bh], radius=14, fill=BUBBLE_OUT if who == "me" else BUBBLE_IN
        )
        ty = y + PAD
        for line in lines:
            d.text((x + PAD, ty), line, font=f, fill=TEXT)
            ty += line_h
        d.text((x + bw - PAD - fm.getlength(ts), y + bh - 28), ts, font=fm, fill=META)
        y += bh + 14
    pages.append(img.crop((0, 0, W, max(y + 10, 400))))
    return pages


def write(fx: Fixture, out_dir: Path = IMAGES_DIR) -> list[Path]:
    for old in out_dir.glob(f"{fx.id}_*.jpg"):
        old.unlink()
    if not fx.chat:
        return []
    paths = []
    for i, page in enumerate(render(fx), start=1):
        p = out_dir / f"{fx.id}_{i}.jpg"
        page.save(p, "JPEG", quality=85, optimize=True)
        paths.append(p)
    return paths


def main(argv: list[str]) -> int:
    pattern = argv[0] if argv else "*"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    for fx in load_all(pattern):
        paths = write(fx)
        print(fx.id, [p.name for p in paths])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
