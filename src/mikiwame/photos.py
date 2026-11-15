"""Perceptual hashes of property photos, to recognise the same photo across cases.

pHash (DCT of a 32x32 greyscale thumbnail, top-left 8x8 coefficients against their median):
survives recompression, resizing and small crops, unlike a byte hash. Implemented with Pillow
only; the 64-bit hash of a photo of a flat is not personal data, so it is stored as is.
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageOps

SIZE = 32
LOW = 8
# Hamming distance (of 64 bits) at or below which two photos count as the same picture.
# Measured 2026-10-01 on photo-like images: resize+recompress <= 2, 3 % crop <= 10, +15 %
# brightness <= 8, a text watermark 0; different photos >= 18. An 8 % crop reaches 24, so
# screenshots of a listing (portal UI around the photo) match worse than forwarded photos.
SAME_PHOTO = 10

_COS = [[math.cos(math.pi * (2 * n + 1) * k / (2 * SIZE)) for n in range(SIZE)] for k in range(LOW)]


def phash(data: bytes) -> str | None:
    """16 hex chars, or None if the image cannot be read."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("L").resize((SIZE, SIZE), Image.LANCZOS)
            px = list(im.tobytes())  # mode "L": one byte per pixel
    except Exception:
        return None
    rows = [px[r * SIZE : (r + 1) * SIZE] for r in range(SIZE)]
    # DCT-II, only the LOW lowest frequencies in each direction.
    row_dct = [
        [sum(c * v for c, v in zip(_COS[k], row, strict=True)) for k in range(LOW)] for row in rows
    ]
    coeffs = [
        sum(_COS[k][n] * row_dct[n][j] for n in range(SIZE)) for k in range(LOW) for j in range(LOW)
    ]
    body = coeffs[1:]  # the DC term only measures overall brightness
    median = sorted(body)[len(body) // 2]
    bits = 0
    for c in coeffs:
        bits = (bits << 1) | (1 if c > median else 0)
    return f"{bits:016x}"


def distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def same_photo(a: str, b: str) -> bool:
    return distance(a, b) <= SAME_PHOTO
