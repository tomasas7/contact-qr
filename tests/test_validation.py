"""Input validation rules (unit tests; no database needed)."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas import ContactIn
from tests.conftest import VALID_CONTACT


def errors_for(data: dict) -> dict[str, str]:
    """Validate and return {field: error_code}; '' is the key for whole-form errors."""
    try:
        ContactIn.model_validate(data)
    except ValidationError as exc:
        return {".".join(map(str, e["loc"])): e["type"] for e in exc.errors()}
    return {}


def with_(**changes) -> dict:
    return {**VALID_CONTACT, **changes}


def test_valid_contact_passes():
    assert errors_for(VALID_CONTACT) == {}


def test_minimal_contact_with_only_required_fields():
    contact = ContactIn.model_validate({"first_name": "A", "last_name": "B", "email": "a@b.lt"})
    assert contact.phone is None and contact.birthday is None and contact.street is None


@pytest.mark.parametrize("field", ["first_name", "last_name"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_names_are_required(field, value):
    assert errors_for(with_(**{field: value})) == {field: "required"}


def test_missing_name_key_is_required_error():
    data = dict(VALID_CONTACT)
    del data["first_name"]
    assert errors_for(data) == {"first_name": "missing"}


def test_email_or_phone_is_required():
    assert errors_for(with_(email=None, phone="")) == {"": "contact_method_required"}


def test_email_alone_or_phone_alone_is_enough():
    assert errors_for(with_(phone=None)) == {}
    assert errors_for(with_(email=None)) == {}


@pytest.mark.parametrize(
    "email",
    [
        "plainaddress",
        "@example.com",
        "jonas@",
        "jonas@@example.com",
        "jo nas@example.com",
        "jonas@example",
    ],
)
def test_invalid_email_formats(email):
    assert errors_for(with_(email=email)) == {"email": "invalid_email"}


def test_email_domain_is_normalized():
    assert ContactIn.model_validate(with_(email="Jonas@EXAMPLE.com")).email == "Jonas@example.com"


@pytest.mark.parametrize(
    ("raw", "e164"),
    [
        ("+37061234567", "+37061234567"),
        ("+370 612 34567", "+37061234567"),
        ("8 612 34567", "+37061234567"),  # Lithuanian national format
        ("861234567", "+37061234567"),
        ("+1 (650) 253-0000", "+16502530000"),  # foreign number with country code
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_phone_numbers_are_normalized_to_e164(raw, e164):
    assert ContactIn.model_validate(with_(phone=raw)).phone == e164


@pytest.mark.parametrize("phone", ["123", "abc", "+370 1", "+999 123 456 789", "+370 612 3456789"])
def test_invalid_phone_numbers(phone):
    assert errors_for(with_(phone=phone)) == {"phone": "invalid_phone"}


def test_overlong_phone_input_is_rejected():
    assert errors_for(with_(phone="1" * 31)) == {"phone": "too_long"}


def test_birthday_in_future_is_rejected():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert errors_for(with_(birthday=tomorrow)) == {"birthday": "birthday_in_future"}


def test_birthday_today_is_allowed():
    assert errors_for(with_(birthday=date.today().isoformat())) == {}


def test_birthday_before_1900_is_rejected():
    assert errors_for(with_(birthday="1899-12-31")) == {"birthday": "birthday_too_old"}


@pytest.mark.parametrize("value", ["2023-02-30", "17/05/1990", "1990-13-01", "yesterday"])
def test_invalid_dates_are_rejected(value):
    assert list(errors_for(with_(birthday=value))) == ["birthday"]


def test_empty_birthday_means_not_provided():
    assert ContactIn.model_validate(with_(birthday="")).birthday is None


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("first_name", 50),
        ("last_name", 50),
        ("street", 100),
        ("city", 60),
        ("postal_code", 12),
        ("country", 60),
        ("organization", 100),
        ("job_title", 60),
        ("note", 200),
    ],
)
def test_text_length_limits(field, limit):
    assert errors_for(with_(**{field: "x" * limit})) == {}
    assert errors_for(with_(**{field: "x" * (limit + 1)})) == {field: "too_long"}


def test_whitespace_is_trimmed_and_blank_optional_fields_become_none():
    contact = ContactIn.model_validate(with_(first_name="  Jonas  ", city="   "))
    assert contact.first_name == "Jonas"
    assert contact.city is None


@pytest.mark.parametrize("value", ["Jo\x00nas", "Jo\x1bnas", "Jo\nnas", "Jo​nas"])
def test_control_characters_rejected_in_single_line_fields(value):
    assert errors_for(with_(first_name=value)) == {"first_name": "invalid_characters"}


def test_note_allows_line_breaks_and_normalizes_them():
    assert ContactIn.model_validate(with_(note="line 1\r\nline 2")).note == "line 1\nline 2"


def test_unicode_names_are_accepted():
    contact = ContactIn.model_validate(with_(first_name="Šarūnas", last_name="Žemaitė-Ąžuolė"))
    assert contact.last_name == "Žemaitė-Ąžuolė"


@pytest.mark.parametrize(
    "url",
    ["example.com", "ftp://example.com", "javascript:alert(1)", "https://", "https://exa mple.com"],
)
def test_invalid_websites(url):
    assert errors_for(with_(website=url)) == {"website": "invalid_url"}


def test_unknown_fields_are_rejected():
    assert errors_for(with_(nickname="Jonukas")) == {"nickname": "extra_forbidden"}


def test_wrong_types_are_rejected():
    assert errors_for(with_(first_name=123)) == {"first_name": "string_type"}


def test_multiple_errors_are_reported_together():
    errors = errors_for(with_(first_name="", email="bad", birthday="2999-01-01"))
    assert set(errors) == {"first_name", "email", "birthday"}
