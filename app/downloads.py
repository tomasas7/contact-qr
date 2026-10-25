import re
import unicodedata

from app.models import Contact


def download_filename(contact: Contact, extension: str) -> str:
    """ASCII-only, filesystem-safe name such as `jonas-jonaitis.png` ("Šarūnas" -> "sarunas")."""
    raw = f"{contact.first_name} {contact.last_name}"
    ascii_name = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "contact"
    return f"{slug[:60]}.{extension}"


def content_disposition(filename: str, *, attachment: bool) -> str:
    return f'{"attachment" if attachment else "inline"}; filename="{filename}"'
