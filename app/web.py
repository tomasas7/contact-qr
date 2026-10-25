"""Server-rendered web UI. Uses the same service layer and the same ContactIn validation
as the REST API, so both interfaces behave identically."""

import math
import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.downloads import content_disposition, download_filename
from app.errors import format_validation_errors
from app.ratelimit import limit_writes
from app.schemas import MAX_LENGTHS, ContactIn
from app.services import contacts as service
from app.services.request_logs import LogFilters, list_logs

router = APIRouter(include_in_schema=False)

CONTACT_FIELDS = list(ContactIn.model_fields)
CONTACTS_PER_PAGE = 20
LOGS_PER_PAGE = 50
FLASH_MESSAGES = {
    "created": "Contact saved and QR code generated.",
    "updated": "Contact updated and QR code regenerated.",
    "deleted": "Contact deleted.",
}


def _render(request: Request, template: str, context: dict, status_code: int = 200):
    return request.app.state.templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


def _page_number(request: Request) -> int:
    raw = request.query_params.get("page", "1")
    return max(1, int(raw)) if raw.isdigit() else 1


def _pagination(page: int, per_page: int, total: int) -> dict:
    return {"page": page, "pages": max(1, math.ceil(total / per_page)), "total": total}


async def _validated_form(request: Request) -> tuple[dict, ContactIn | None, dict, list]:
    """Returns (raw values, validated data or None, field errors, form-level errors)."""
    form = await request.form()
    values = {f: str(form.get(f, "")) for f in CONTACT_FIELDS}
    try:
        return values, ContactIn.model_validate(values), {}, []
    except ValidationError as exc:
        field_errors, form_errors = {}, []
        for detail in format_validation_errors(exc.errors()):
            if detail.field:
                field_errors.setdefault(detail.field, detail.message)
            else:
                form_errors.append(detail.message)
        return values, None, field_errors, form_errors


def _form_context(mode: str, values: dict, field_errors=None, form_errors=None, contact=None):
    return {
        "mode": mode,
        "values": values,
        "errors": field_errors or {},
        "form_errors": form_errors or [],
        "contact": contact,
        "max_lengths": MAX_LENGTHS,
        "today": datetime.now(UTC).date().isoformat(),
    }


@router.get("/", response_class=HTMLResponse)
def new_contact_form(request: Request):
    return _render(request, "contact_form.html", _form_context("create", {}))


@router.post("/contacts", dependencies=[Depends(limit_writes)])
async def create_contact(request: Request, session: Session = Depends(get_session)):
    values, data, field_errors, form_errors = await _validated_form(request)
    if data is None:
        return _render(
            request,
            "contact_form.html",
            _form_context("create", values, field_errors, form_errors),
            status_code=422,
        )
    contact = await run_in_threadpool(service.create_contact, session, data)
    return RedirectResponse(f"/contacts/{contact.id}?flash=created", status_code=303)


@router.get("/contacts", response_class=HTMLResponse)
def contact_history(request: Request, session: Session = Depends(get_session)):
    q = request.query_params.get("q", "").strip()[:100]
    page = _page_number(request)
    items, total = service.list_contacts(
        session, q=q or None, limit=CONTACTS_PER_PAGE, offset=(page - 1) * CONTACTS_PER_PAGE
    )
    return _render(
        request,
        "contacts.html",
        {
            "contacts": items,
            "q": q,
            **_pagination(page, CONTACTS_PER_PAGE, total),
            "flash": FLASH_MESSAGES.get(request.query_params.get("flash", "")),
        },
    )


@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contact_detail(
    request: Request, contact_id: uuid.UUID, session: Session = Depends(get_session)
):
    contact = service.get_contact(session, contact_id)
    return _render(
        request,
        "contact_detail.html",
        {
            "contact": contact,
            "flash": FLASH_MESSAGES.get(request.query_params.get("flash", "")),
        },
    )


@router.get("/contacts/{contact_id}/edit", response_class=HTMLResponse)
def edit_contact_form(
    request: Request, contact_id: uuid.UUID, session: Session = Depends(get_session)
):
    contact = service.get_contact(session, contact_id)
    values = {f: getattr(contact, f) or "" for f in CONTACT_FIELDS}
    values["birthday"] = contact.birthday.isoformat() if contact.birthday else ""
    return _render(request, "contact_form.html", _form_context("edit", values, contact=contact))


@router.post("/contacts/{contact_id}/edit", dependencies=[Depends(limit_writes)])
async def update_contact(
    request: Request, contact_id: uuid.UUID, session: Session = Depends(get_session)
):
    contact = await run_in_threadpool(service.get_contact, session, contact_id)
    values, data, field_errors, form_errors = await _validated_form(request)
    if data is None:
        return _render(
            request,
            "contact_form.html",
            _form_context("edit", values, field_errors, form_errors, contact),
            status_code=422,
        )
    await run_in_threadpool(service.update_contact, session, contact_id, data)
    return RedirectResponse(f"/contacts/{contact_id}?flash=updated", status_code=303)


@router.post("/contacts/{contact_id}/delete", dependencies=[Depends(limit_writes)])
def delete_contact(contact_id: uuid.UUID, session: Session = Depends(get_session)):
    service.delete_contact(session, contact_id)
    return RedirectResponse("/contacts?flash=deleted", status_code=303)


@router.get("/contacts/{contact_id}/qr.png")
def contact_qr(request: Request, contact_id: uuid.UUID, session: Session = Depends(get_session)):
    contact = service.get_contact_with_qr(session, contact_id)
    attachment = request.query_params.get("download") == "1"
    return Response(
        contact.qr_png,
        media_type="image/png",
        headers={
            "Content-Disposition": content_disposition(
                download_filename(contact, "png"), attachment=attachment
            ),
            "Cache-Control": "no-cache",
        },
    )


@router.get("/contacts/{contact_id}/contact.vcf")
def contact_vcard(contact_id: uuid.UUID, session: Session = Depends(get_session)):
    contact = service.get_contact(session, contact_id)
    return Response(
        contact.vcard,
        media_type="text/vcard; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(
                download_filename(contact, "vcf"), attachment=True
            ),
        },
    )


def _parse_dt(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)  # form inputs are UTC


@router.get("/logs", response_class=HTMLResponse)
def request_logs(request: Request, session: Session = Depends(get_session)):
    qp = request.query_params
    form = {
        k: qp.get(k, "").strip()[:200]
        for k in ("source", "method", "status_code", "path", "client_ip", "since", "until")
    }
    filters = LogFilters(
        source=form["source"] if form["source"] in ("UI", "API", "SYSTEM") else None,
        method=form["method"] or None,
        status_code=int(form["status_code"]) if form["status_code"].isdigit() else None,
        path=form["path"] or None,
        client_ip=form["client_ip"] or None,
        since=_parse_dt(form["since"]) if form["since"] else None,
        until=_parse_dt(form["until"]) if form["until"] else None,
    )
    page = _page_number(request)
    items, total = list_logs(
        session, filters, limit=LOGS_PER_PAGE, offset=(page - 1) * LOGS_PER_PAGE
    )
    query = urlencode({k: v for k, v in form.items() if v})
    return _render(
        request,
        "logs.html",
        {
            "logs": items,
            "form": form,
            "filter_query": query,
            **_pagination(page, LOGS_PER_PAGE, total),
        },
    )
