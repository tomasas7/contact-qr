"""vCard 3.0 (RFC 2426) serialization.

vCard 3.0 is used rather than 4.0 because it is the version both the iOS and Android
camera apps reliably recognise and offer to "Add to contacts".
"""

from dataclasses import dataclass
from datetime import date

CRLF = "\r\n"


@dataclass(frozen=True)
class VCardData:
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    birthday: date | None = None
    street: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country: str | None = None
    organization: str | None = None
    job_title: str | None = None
    website: str | None = None
    note: str | None = None


def escape(value: str) -> str:
    """Escape a text value per RFC 2426 §5: backslash, comma, semicolon and newlines."""
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def build_vcard(c: VCardData) -> str:
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{escape(c.last_name)};{escape(c.first_name)};;;",
        f"FN:{escape(f'{c.first_name} {c.last_name}')}",
    ]
    if c.organization:
        lines.append(f"ORG:{escape(c.organization)}")
    if c.job_title:
        lines.append(f"TITLE:{escape(c.job_title)}")
    if c.phone:
        lines.append(f"TEL;TYPE=CELL:{escape(c.phone)}")
    if c.email:
        lines.append(f"EMAIL;TYPE=INTERNET:{escape(c.email)}")
    if any((c.street, c.city, c.postal_code, c.country)):
        # ADR components: PO box; extended; street; locality; region; postal code; country
        parts = ["", "", c.street, c.city, "", c.postal_code, c.country]
        lines.append("ADR;TYPE=HOME:" + ";".join(escape(p or "") for p in parts))
    if c.birthday:
        lines.append(f"BDAY:{c.birthday.isoformat()}")
    if c.website:
        lines.append(f"URL:{escape(c.website)}")
    if c.note:
        lines.append(f"NOTE:{escape(c.note)}")
    lines.append("END:VCARD")
    # Lines are intentionally not folded at 75 octets: phone scanners do not need it,
    # and folding only adds bytes to the QR payload.
    return CRLF.join(lines) + CRLF
