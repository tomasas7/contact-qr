"""Request/response models. ContactIn is the single source of truth for input validation:
the REST API and the HTML form both validate through it.

Mandatory fields: first name, last name, and at least one way to reach the person
(email or phone). Everything else is optional. See NOTES.md for the reasoning.
"""

import unicodedata
import uuid
from datetime import date, datetime
from typing import Annotated, Generic, Literal, TypeVar
from urllib.parse import urlsplit

import phonenumbers
from email_validator import EmailNotValidError, validate_email
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.config import get_settings

MAX_LENGTHS = {
    "first_name": 50,
    "last_name": 50,
    "email": 254,
    "street": 100,
    "city": 60,
    "postal_code": 12,
    "country": 60,
    "organization": 100,
    "job_title": 60,
    "website": 200,
    "note": 200,
}


def _check_text(value: str, max_len: int, multiline: bool) -> str:
    if len(value) > max_len:
        raise PydanticCustomError(
            "too_long", "Must be at most {max_len} characters.", {"max_len": max_len}
        )
    for ch in value:
        if unicodedata.category(ch) in ("Cc", "Cf") and not (multiline and ch == "\n"):
            raise PydanticCustomError("invalid_characters", "Contains invalid control characters.")
    return value


def _text(max_len: int, *, required: bool, multiline: bool = False) -> BeforeValidator:
    def validate(value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                raise PydanticCustomError("required", "This field is required.")
            return None
        if not isinstance(value, str):
            raise PydanticCustomError("string_type", "Must be a string.")
        value = value.strip()
        if multiline:
            value = value.replace("\r\n", "\n").replace("\r", "\n")
        return _check_text(value, max_len, multiline)

    return BeforeValidator(validate)


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value.strip() if isinstance(value, str) else value


def _validate_email(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        # Syntax only: no DNS lookups, so validation is fast, deterministic and offline.
        result = validate_email(value, check_deliverability=False)
    except EmailNotValidError as exc:
        raise PydanticCustomError("invalid_email", "Enter a valid email address.") from exc
    return result.normalized


def _validate_phone(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 30:
        raise PydanticCustomError("invalid_phone", "Enter a valid phone number.")
    region = get_settings().default_phone_region
    try:
        parsed = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException as exc:
        raise PydanticCustomError("invalid_phone", "Enter a valid phone number.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise PydanticCustomError(
            "invalid_phone",
            "Enter a valid phone number, e.g. +370 612 34567 or 8 612 34567.",
        )
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _validate_birthday(value: date | None) -> date | None:
    if value is None:
        return None
    if value > date.today():
        raise PydanticCustomError("birthday_in_future", "Birthday cannot be in the future.")
    if value.year < 1900:
        raise PydanticCustomError("birthday_too_old", "Birthday must be in 1900 or later.")
    return value


def _validate_website(value: str | None) -> str | None:
    if value is None:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in value):
        raise PydanticCustomError(
            "invalid_url", "Enter a full web address starting with http:// or https://."
        )
    return value


OptionalStr = str | None
Email = Annotated[
    OptionalStr, _text(MAX_LENGTHS["email"], required=False), AfterValidator(_validate_email)
]
Phone = Annotated[OptionalStr, _text(30, required=False), AfterValidator(_validate_phone)]
Birthday = Annotated[
    date | None, BeforeValidator(_blank_to_none), AfterValidator(_validate_birthday)
]
Website = Annotated[
    OptionalStr, _text(MAX_LENGTHS["website"], required=False), AfterValidator(_validate_website)
]


FirstName = Annotated[str, _text(MAX_LENGTHS["first_name"], required=True)]
LastName = Annotated[str, _text(MAX_LENGTHS["last_name"], required=True)]
Street = Annotated[OptionalStr, _text(MAX_LENGTHS["street"], required=False)]
City = Annotated[OptionalStr, _text(MAX_LENGTHS["city"], required=False)]
PostalCode = Annotated[OptionalStr, _text(MAX_LENGTHS["postal_code"], required=False)]
Country = Annotated[OptionalStr, _text(MAX_LENGTHS["country"], required=False)]
Organization = Annotated[OptionalStr, _text(MAX_LENGTHS["organization"], required=False)]
JobTitle = Annotated[OptionalStr, _text(MAX_LENGTHS["job_title"], required=False)]
Note = Annotated[OptionalStr, _text(MAX_LENGTHS["note"], required=False, multiline=True)]


class ContactIn(BaseModel):
    """Contact data accepted on create and (full) update."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
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
                }
            ]
        },
    )

    first_name: FirstName = Field(description="Required. 1–50 characters.")
    last_name: LastName = Field(description="Required. 1–50 characters.")
    email: Email = Field(None, description="Email or phone is required. Syntax-checked.")
    phone: Phone = Field(
        None,
        description="Email or phone is required. Any common format; numbers without a "
        "+country prefix are read in the default region (LT). Stored as E.164.",
    )
    birthday: Birthday = Field(None, description="YYYY-MM-DD, not in the future, year ≥ 1900.")
    street: Street = Field(None, description="Street address, ≤100 chars.")
    city: City = Field(None, description="≤60 chars.")
    postal_code: PostalCode = Field(None, description="≤12 chars.")
    country: Country = Field(None, description="≤60 chars.")
    organization: Organization = Field(None, description="≤100 chars.")
    job_title: JobTitle = Field(None, description="≤60 chars.")
    website: Website = Field(None, description="http(s) URL, ≤200 chars.")
    note: Note = Field(None, description="Free text, ≤200 chars, may contain line breaks.")

    @model_validator(mode="after")
    def _need_contact_method(self) -> "ContactIn":
        if not self.email and not self.phone:
            raise PydanticCustomError(
                "contact_method_required", "Provide at least an email address or a phone number."
            )
        return self


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    birthday: date | None
    street: str | None
    city: str | None
    postal_code: str | None
    country: str | None
    organization: str | None
    job_title: str | None
    website: str | None
    note: str | None
    vcard: str = Field(description="The exact vCard 3.0 payload encoded in the QR code.")
    qr_url: str = Field(description="URL of the QR code PNG.")
    vcard_url: str = Field(description="URL of the .vcf file.")
    created_at: datetime
    updated_at: datetime


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(description="Total number of matching items.")
    limit: int
    offset: int


class RequestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    client_ip: str | None
    method: str
    path: str
    query_string: str | None
    status_code: int
    user_agent: str | None
    source: Literal["UI", "API", "SYSTEM"]
    duration_ms: float


class ErrorDetail(BaseModel):
    field: str | None = Field(description="Offending field, or null for whole-request errors.")
    message: str
    code: str


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable summary.")
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthOut(BaseModel):
    status: Literal["ok", "error"]
    checks: dict[str, str]
    version: str
