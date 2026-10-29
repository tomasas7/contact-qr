"""QR code rendering. A contact's code carries a small badge with their initials in the
centre; error correction H (about 30% of the symbol may be damaged) recovers the modules
the badge hides."""

import functools
import io
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont
from segno import DataOverflowError

FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "Inter-Bold.ttf"  # SIL OFL 1.1
BADGE_COLOR = "#2f5bea"  # the app's primary colour
BADGE_TEXT_COLOR = "#ffffff"
# Width of the badge's clear zone relative to the symbol: about 6% of its area, well
# inside what level H can recover.
BADGE_WIDTH_RATIO = 0.24


class QRTooLargeError(ValueError):
    pass


def initials(first_name: str, last_name: str) -> str:
    """'Šarūnas', 'Žemaitė' -> 'ŠŽ'. Takes the first letter or digit of each name and
    skips characters the badge font cannot draw (e.g. CJK), so the result may be empty."""
    letters = []
    for name in (first_name, last_name):
        char = next((c for c in name if c.isalnum()), None)
        if char and _font_has_glyph(char.upper()[0]):
            letters.append(char.upper()[0])
    return "".join(letters)


def render_qr_png(payload: str, *, label: str = "", scale: int = 8, border: int = 4) -> bytes:
    """Render `payload` as a PNG QR code, with `label` (initials) in a centre badge.

    With a label the code is made at error correction H. If the payload doesn't fit at H,
    the badge is dropped: the plain code targets M (~15% damage tolerance; segno raises it
    when there is spare capacity) and falls back to L so very long contacts still encode.
    """
    if label:
        try:
            qr = segno.make_qr(payload, error="h", encoding="utf-8")
        except DataOverflowError:
            pass
        else:
            return _with_badge(qr, label, scale=scale, border=border)

    for error in ("m", "l"):
        try:
            qr = segno.make_qr(payload, error=error, boost_error=True, encoding="utf-8")
            break
        except DataOverflowError:
            continue
    else:
        raise QRTooLargeError("Contact data is too large to fit in a QR code.")

    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border)
    return buffer.getvalue()


def _with_badge(qr: segno.QRCode, label: str, *, scale: int, border: int) -> bytes:
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border)
    image = Image.open(buffer).convert("RGB")
    draw = ImageDraw.Draw(image)

    # A white clear zone aligned to whole modules, centred on the symbol (whose width is
    # always odd, so an odd zone width centres exactly).
    modules = qr.symbol_size(border=0)[0]
    side = round(modules * BADGE_WIDTH_RATIO)
    side += (modules - side) % 2
    start = (border + (modules - side) // 2) * scale
    end = start + side * scale - 1
    draw.rectangle((start, start, end, end), fill="white")

    # The badge sits one module inside the zone, so it never touches a dark module.
    inner = (start + scale, start + scale, end - scale, end - scale)
    width = inner[2] - inner[0]
    draw.rounded_rectangle(inner, radius=width // 5, fill=BADGE_COLOR)

    font = _fit_font(label, max_width=width * 0.72, max_height=width * 0.5)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    x = inner[0] + (width - (right - left)) / 2 - left
    y = inner[1] + (width - (bottom - top)) / 2 - top
    draw.text((x, y), label, font=font, fill=BADGE_TEXT_COLOR)

    # A 16-colour palette keeps black and white exact (the anti-aliased text edges are
    # the only other colours) and halves the file size compared with RGB.
    out = io.BytesIO()
    image.quantize(colors=16, method=Image.Quantize.MEDIANCUT).save(
        out, format="PNG", optimize=True
    )
    return out.getvalue()


@functools.cache
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def _fit_font(label: str, *, max_width: float, max_height: float) -> ImageFont.FreeTypeFont:
    size = max(int(max_height), 1)
    left, top, right, bottom = _font(size).getbbox(label)
    shrink = min(1.0, max_width / max(right - left, 1), max_height / max(bottom - top, 1))
    return _font(max(int(size * shrink), 1))


def _glyph_pixels(char: str) -> bytes:
    image = Image.new("L", (48, 48))
    ImageDraw.Draw(image).text((4, 4), char, font=_font(32), fill=255)
    return image.tobytes()


@functools.cache
def _font_has_glyph(char: str) -> bool:
    # A missing glyph renders as the font's .notdef box, the same as an unassigned
    # private-use code point.
    return _glyph_pixels(char) != _glyph_pixels("\U000f0000")
