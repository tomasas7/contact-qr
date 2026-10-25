"""Consistent error handling.

API requests (/api/*, /health) get a JSON envelope:
    {"error": {"code": "<machine-readable>", "message": "<human-readable>", "details": [...]}}
Web UI requests get a rendered HTML error page with the same status code.
"""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.middleware import classify_source
from app.qr import QRTooLargeError
from app.ratelimit import RateLimitExceededError
from app.schemas import ErrorBody, ErrorDetail, ErrorResponse
from app.services.contacts import ContactNotFoundError

logger = logging.getLogger(__name__)

_FRIENDLY = {
    "missing": "This field is required.",
    "extra_forbidden": "Unknown field.",
    "date_parsing": "Enter a valid date (YYYY-MM-DD).",
    "date_from_datetime_parsing": "Enter a valid date (YYYY-MM-DD).",
    "date_type": "Enter a valid date (YYYY-MM-DD).",
    "uuid_parsing": "Must be a valid UUID.",
    "int_parsing": "Must be a whole number.",
}

_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def format_validation_errors(errors) -> list[ErrorDetail]:
    details = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        details.append(
            ErrorDetail(
                field=".".join(loc) or None,
                message=_FRIENDLY.get(err["type"], err["msg"]),
                code=err["type"],
            )
        )
    return details


def _wants_json(request: Request) -> bool:
    return classify_source(request.url.path) != "UI"


def error_response(
    request: Request,
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    code = code or _CODES.get(status_code, "error")
    if _wants_json(request):
        body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
        payload = body.model_dump()
        if details is None:
            del payload["error"]["details"]
        return JSONResponse(payload, status_code=status_code, headers=headers)
    return request.app.state.templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": status_code, "title": HTTPStatus(status_code).phrase, "message": message},
        status_code=status_code,
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        errors = exc.errors()
        if any(e["type"] == "json_invalid" for e in errors):
            return error_response(
                request, 400, "Request body is not valid JSON.", code="invalid_json"
            )
        if not _wants_json(request):  # only path/query params can fail on UI routes
            return error_response(request, 404, "The page you requested does not exist.")
        return error_response(
            request,
            422,
            "The request contains invalid data.",
            details=format_validation_errors(errors),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
        if exc.status_code == 404 and message == "Not Found":
            message = "The requested resource does not exist."
        return error_response(request, exc.status_code, message, headers=exc.headers)

    @app.exception_handler(ContactNotFoundError)
    async def _not_found(request: Request, exc: ContactNotFoundError) -> Response:
        return error_response(request, 404, "Contact not found.")

    @app.exception_handler(RateLimitExceededError)
    async def _rate_limited(request: Request, exc: RateLimitExceededError) -> Response:
        return error_response(
            request,
            429,
            f"Too many write requests. Try again in {exc.retry_after} seconds.",
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(QRTooLargeError)
    async def _qr_too_large(request: Request, exc: QRTooLargeError) -> Response:
        return error_response(request, 422, str(exc), code="qr_too_large")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return error_response(request, 500, "An unexpected error occurred.")
