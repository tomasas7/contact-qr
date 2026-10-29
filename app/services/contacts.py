"""Contact use cases. The web UI and the REST API both call these functions, so the two
interfaces cannot drift apart in behaviour."""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, undefer

from app.models import Contact
from app.qr import initials, render_qr_png
from app.schemas import ContactIn
from app.vcard import VCardData, build_vcard


class ContactNotFoundError(LookupError):
    pass


def _apply(contact: Contact, data: ContactIn) -> None:
    for field, value in data.model_dump().items():
        setattr(contact, field, value)
    contact.vcard = build_vcard(VCardData(**data.model_dump()))
    contact.qr_png = render_qr_png(contact.vcard, label=initials(data.first_name, data.last_name))


def create_contact(session: Session, data: ContactIn) -> Contact:
    contact = Contact(id=uuid.uuid4())
    _apply(contact, data)
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def get_contact(session: Session, contact_id: uuid.UUID) -> Contact:
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise ContactNotFoundError(contact_id)
    return contact


def update_contact(session: Session, contact_id: uuid.UUID, data: ContactIn) -> Contact:
    contact = get_contact(session, contact_id)
    _apply(contact, data)  # the QR code is regenerated so it always matches the data
    session.commit()
    session.refresh(contact)
    return contact


def delete_contact(session: Session, contact_id: uuid.UUID) -> None:
    contact = get_contact(session, contact_id)
    session.delete(contact)
    session.commit()


def get_contact_with_qr(session: Session, contact_id: uuid.UUID) -> Contact:
    """Like get_contact, but also loads the (deferred) QR image column."""
    contact = session.scalar(
        select(Contact).options(undefer(Contact.qr_png)).where(Contact.id == contact_id)
    )
    if contact is None:
        raise ContactNotFoundError(contact_id)
    return contact


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_contacts(
    session: Session, *, q: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[Contact], int]:
    stmt = select(Contact)
    if q and q.strip():
        pattern = _like(q.strip())
        full_name = Contact.first_name + " " + Contact.last_name
        stmt = stmt.where(
            or_(
                full_name.ilike(pattern, escape="\\"),
                Contact.email.ilike(pattern, escape="\\"),
                Contact.phone.ilike(pattern, escape="\\"),
                Contact.organization.ilike(pattern, escape="\\"),
                Contact.city.ilike(pattern, escape="\\"),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(
        stmt.order_by(Contact.created_at.desc(), Contact.id).limit(limit).offset(offset)
    ).all()
    return list(items), total
