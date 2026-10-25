"""vCard serialization and QR code generation, including a round trip that decodes the
generated PNG the way a phone camera would and checks the contact survives intact."""

from datetime import date

import cv2
import numpy as np
import pytest

from app.qr import QRTooLargeError, render_qr_png
from app.schemas import MAX_LENGTHS, ContactIn
from app.vcard import VCardData, build_vcard, escape
from tests.conftest import VALID_CONTACT

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decode_qr(png: bytes) -> str:
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    # The ArUco-based detector is OpenCV's more robust QR reader (closer to a phone camera).
    text, points, _ = cv2.QRCodeDetectorAruco().detectAndDecode(image)
    assert points is not None and text, "QR code could not be decoded"
    return text


def parse_vcard(payload: str) -> dict[str, str]:
    """Minimal vCard reader for assertions: {property-with-params: raw value}."""
    lines = payload.split("\r\n")
    assert lines[0] == "BEGIN:VCARD" and lines[-2] == "END:VCARD" and lines[-1] == ""
    return dict(line.split(":", 1) for line in lines[1:-2])


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append("\n" if value[i + 1] in "nN" else value[i + 1])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def vcard_for(**changes) -> str:
    data = ContactIn.model_validate({**VALID_CONTACT, **changes})
    return build_vcard(VCardData(**data.model_dump()))


# --- vCard -----------------------------------------------------------------------------


def test_vcard_contains_all_fields_in_3_0_format():
    props = parse_vcard(vcard_for())
    assert props["VERSION"] == "3.0"
    assert props["N"] == "Jonaitis;Jonas;;;"
    assert props["FN"] == "Jonas Jonaitis"
    assert props["TEL;TYPE=CELL"] == "+37061234567"
    assert props["EMAIL;TYPE=INTERNET"] == "jonas@example.com"
    assert props["BDAY"] == "1990-05-17"
    assert props["ADR;TYPE=HOME"] == ";;Gedimino pr. 1;Vilnius;;01103;Lithuania"
    assert props["ORG"] == "UAB Pavyzdys"
    assert props["TITLE"] == "Engineer"
    assert props["URL"] == "https://example.com"
    assert props["NOTE"] == "Met at the conference"


def test_vcard_uses_crlf_line_endings():
    payload = vcard_for()
    assert payload.endswith("END:VCARD\r\n")
    assert "\n" not in payload.replace("\r\n", "")


def test_optional_properties_are_omitted_when_empty():
    payload = build_vcard(VCardData(first_name="A", last_name="B", email="a@b.lt"))
    assert parse_vcard(payload).keys() == {"VERSION", "N", "FN", "EMAIL;TYPE=INTERNET"}


def test_partial_address_keeps_component_positions():
    payload = build_vcard(VCardData(first_name="A", last_name="B", city="Kaunas"))
    assert parse_vcard(payload)["ADR;TYPE=HOME"] == ";;;Kaunas;;;"


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("a,b", "a\\,b"),
        ("a;b", "a\\;b"),
        ("a\\b", "a\\\\b"),
        ("a\nb", "a\\nb"),
        ("a\r\nb", "a\\nb"),
        ("plain", "plain"),
    ],
)
def test_escaping(raw, escaped):
    assert escape(raw) == escaped


def test_special_characters_cannot_inject_properties():
    payload = vcard_for(note="x\nEMAIL:attacker@evil.com", organization="Acme; Inc, Ltd")
    props = parse_vcard(payload)
    assert props["EMAIL;TYPE=INTERNET"] == "jonas@example.com"
    assert unescape(props["NOTE"]) == "x\nEMAIL:attacker@evil.com"
    assert unescape(props["ORG"]) == "Acme; Inc, Ltd"


# --- QR code -----------------------------------------------------------------------------


def test_qr_is_a_png():
    assert render_qr_png("hello").startswith(PNG_SIGNATURE)


def test_qr_round_trip_preserves_contact():
    payload = vcard_for()
    decoded = decode_qr(render_qr_png(payload))
    assert decoded == payload
    props = parse_vcard(decoded)
    assert props["FN"] == "Jonas Jonaitis"
    assert props["TEL;TYPE=CELL"] == "+37061234567"


def test_qr_round_trip_with_lithuanian_characters():
    payload = vcard_for(first_name="Šarūnas", last_name="Žemaitė", city="Šiauliai")
    decoded = decode_qr(render_qr_png(payload))
    assert "FN:Šarūnas Žemaitė" in decoded
    assert "Šiauliai" in decoded


def test_largest_possible_contact_still_fits_in_a_qr_code():
    """Every field at its maximum length, using 2-byte UTF-8 characters and characters
    that need escaping, must still encode (the length limits were chosen for this)."""
    worst = {name: ("ą," * limit)[:limit] for name, limit in MAX_LENGTHS.items()}
    payload = build_vcard(
        VCardData(
            first_name=worst["first_name"],
            last_name=worst["last_name"],
            email="a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 60,
            phone="+37061234567",
            birthday=date(1990, 1, 1),
            street=worst["street"],
            city=worst["city"],
            postal_code=worst["postal_code"],
            country=worst["country"],
            organization=worst["organization"],
            job_title=worst["job_title"],
            website="https://" + "e," * 96,
            note=worst["note"],
        )
    )
    assert render_qr_png(payload).startswith(PNG_SIGNATURE)


def test_payload_beyond_qr_capacity_raises_clear_error():
    with pytest.raises(QRTooLargeError):
        render_qr_png("x" * 5000)
