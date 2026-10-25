import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.downloads import content_disposition, download_filename
from app.models import Contact
from app.ratelimit import limit_writes
from app.schemas import ContactIn, ContactOut, ErrorResponse, Page
from app.services import contacts as service

router = APIRouter(prefix="/api/v1/contacts", tags=["Contacts"])

_EXAMPLE_ID = "3f1c2a9e-7b4d-4c1e-9a8f-2d6b5e4c3a21"
_CONTACT_EXAMPLE = {
    "id": _EXAMPLE_ID,
    "first_name": "Jonas",
    "last_name": "Jonaitis",
    "email": "jonas@example.com",
    "phone": "+37061234567",
    "birthday": "1990-05-17",
    "street": "Gedimino pr. 1",
    "city": "Vilnius",
    "postal_code": "01103",
    "country": "Lithuania",
    "organization": "UAB Pavyzdys",
    "job_title": "Engineer",
    "website": "https://example.com",
    "note": "Met at the conference",
    "vcard": "BEGIN:VCARD\r\nVERSION:3.0\r\nN:Jonaitis;Jonas;;;\r\nFN:Jonas Jonaitis\r\n"
    "...\r\nEND:VCARD\r\n",
    "qr_url": f"/api/v1/contacts/{_EXAMPLE_ID}/qr",
    "vcard_url": f"/api/v1/contacts/{_EXAMPLE_ID}/vcard",
    "created_at": "2026-10-25T12:00:00Z",
    "updated_at": "2026-10-25T12:00:00Z",
}


def _error(description: str, code: str, message: str, details: list | None = None) -> dict:
    body: dict = {"code": code, "message": message}
    if details:
        body["details"] = details
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": {"error": body}}},
    }


NOT_FOUND = _error("Contact does not exist.", "not_found", "Contact not found.")
VALIDATION = _error(
    "Input failed validation. `details` lists every problem per field.",
    "validation_error",
    "The request contains invalid data.",
    [
        {"field": "email", "message": "Enter a valid email address.", "code": "invalid_email"},
        {
            "field": "birthday",
            "message": "Birthday cannot be in the future.",
            "code": "birthday_in_future",
        },
    ],
)
BAD_JSON = _error("Body is not valid JSON.", "invalid_json", "Request body is not valid JSON.")
RATE_LIMITED = _error(
    "Too many write requests from this IP. See the `Retry-After` header.",
    "rate_limited",
    "Too many write requests. Try again in 12 seconds.",
)


def to_out(contact: Contact) -> ContactOut:
    return ContactOut.model_validate(
        {
            **{f: getattr(contact, f) for f in ContactOut.model_fields if hasattr(contact, f)},
            "qr_url": f"/api/v1/contacts/{contact.id}/qr",
            "vcard_url": f"/api/v1/contacts/{contact.id}/vcard",
        }
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ContactOut,
    summary="Create a contact and generate its QR code",
    dependencies=[Depends(limit_writes)],
    responses={
        201: {
            "description": "Created. `Location` points to the new contact.",
            "content": {"application/json": {"example": _CONTACT_EXAMPLE}},
        },
        400: BAD_JSON,
        422: VALIDATION,
        429: RATE_LIMITED,
    },
)
def create_contact(
    data: ContactIn, response: Response, session: Session = Depends(get_session)
) -> ContactOut:
    contact = service.create_contact(session, data)
    response.headers["Location"] = f"/api/v1/contacts/{contact.id}"
    return to_out(contact)


@router.get(
    "",
    response_model=Page[ContactOut],
    summary="List contacts (newest first), with optional search",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {"items": [_CONTACT_EXAMPLE], "total": 1, "limit": 20, "offset": 0}
                }
            }
        },
        422: VALIDATION,
    },
)
def list_contacts(
    q: str | None = Query(
        None,
        max_length=100,
        description="Case-insensitive search in name, email, phone, organization and city.",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> Page[ContactOut]:
    items, total = service.list_contacts(session, q=q, limit=limit, offset=offset)
    return Page[ContactOut](
        items=[to_out(c) for c in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{contact_id}",
    response_model=ContactOut,
    summary="Get a contact",
    responses={
        200: {"content": {"application/json": {"example": _CONTACT_EXAMPLE}}},
        404: NOT_FOUND,
    },
)
def get_contact(contact_id: uuid.UUID, session: Session = Depends(get_session)) -> ContactOut:
    return to_out(service.get_contact(session, contact_id))


@router.put(
    "/{contact_id}",
    response_model=ContactOut,
    summary="Replace a contact (QR code is regenerated)",
    dependencies=[Depends(limit_writes)],
    responses={404: NOT_FOUND, 400: BAD_JSON, 422: VALIDATION, 429: RATE_LIMITED},
)
def update_contact(
    contact_id: uuid.UUID, data: ContactIn, session: Session = Depends(get_session)
) -> ContactOut:
    return to_out(service.update_contact(session, contact_id, data))


@router.delete(
    "/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a contact and its QR code",
    dependencies=[Depends(limit_writes)],
    responses={404: NOT_FOUND, 429: RATE_LIMITED},
)
def delete_contact(contact_id: uuid.UUID, session: Session = Depends(get_session)) -> Response:
    service.delete_contact(session, contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{contact_id}/qr",
    summary="Get the contact's QR code as a PNG image",
    response_class=Response,
    responses={
        200: {
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
            "description": "PNG image. Scanning it offers to save the contact.",
        },
        404: NOT_FOUND,
    },
)
def get_contact_qr(
    contact_id: uuid.UUID,
    download: bool = Query(False, description="Send as an attachment (file download)."),
    session: Session = Depends(get_session),
) -> Response:
    contact = service.get_contact_with_qr(session, contact_id)
    return Response(
        contact.qr_png,
        media_type="image/png",
        headers={
            "Content-Disposition": content_disposition(
                download_filename(contact, "png"), attachment=download
            ),
            "Cache-Control": "no-cache",
        },
    )


@router.get(
    "/{contact_id}/vcard",
    summary="Download the contact as a .vcf file",
    response_class=Response,
    responses={
        200: {"content": {"text/vcard": {"example": "BEGIN:VCARD\r\nVERSION:3.0\r\n..."}}},
        404: NOT_FOUND,
    },
)
def get_contact_vcard(contact_id: uuid.UUID, session: Session = Depends(get_session)) -> Response:
    contact = service.get_contact(session, contact_id)
    return Response(
        contact.vcard,
        media_type="text/vcard; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(
                download_filename(contact, "vcf"), attachment=True
            )
        },
    )
