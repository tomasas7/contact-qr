import io

import segno
from segno import DataOverflowError


class QRTooLargeError(ValueError):
    pass


def render_qr_png(payload: str, *, scale: int = 8, border: int = 4) -> bytes:
    """Render `payload` as a PNG QR code.

    Error correction M (~15% damage tolerance) is the target. segno raises it further
    when the payload leaves spare capacity; if even the largest symbol cannot hold the
    payload at M, fall back to L so very long contacts still encode.
    """
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
