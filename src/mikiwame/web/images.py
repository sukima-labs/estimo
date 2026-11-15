"""Uploaded screenshots → the kind of JPEG the bot gets from Telegram.

Re-encoding drops EXIF (location, device) and anything hidden after the image data, and the
downscale keeps vision tokens and memory the same as a Telegram photo (1280 px on its long side).
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 1600
# A phone screenshot is ~3-4 Mpx; anything near this is not one (decompression bombs).
MAX_PIXELS = 40_000_000


class BadImage(ValueError):
    """Not an image we can read. code: a key of web.app.ERRORS."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                raise BadImage("image_too_large")
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise BadImage("unreadable") from e
