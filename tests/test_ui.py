"""Web UI: form validation feedback, create/view/edit/delete flow, history and downloads."""

import re

from tests.test_vcard_qr import PNG_SIGNATURE, decode_qr


def form(valid_contact, **changes):
    return {k: ("" if v is None else v) for k, v in {**valid_contact, **changes}.items()}


def created_id(response) -> str:
    match = re.fullmatch(r"/contacts/([0-9a-f-]{36})\?flash=created", response.headers["location"])
    assert match, response.headers["location"]
    return match.group(1)


def test_form_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="first_name"' in response.text and 'action="/contacts"' in response.text


def test_invalid_form_shows_errors_and_keeps_input(client, valid_contact):
    response = client.post(
        "/contacts", data=form(valid_contact, email="bad-email", first_name="", city="Kaunas")
    )
    assert response.status_code == 422
    assert "Enter a valid email address." in response.text
    assert "This field is required." in response.text
    assert 'value="Kaunas"' in response.text  # user input is preserved
    assert 'aria-invalid="true"' in response.text


def test_missing_contact_method_shows_form_level_error(client, valid_contact):
    response = client.post("/contacts", data=form(valid_contact, email="", phone=""))
    assert response.status_code == 422
    assert "Provide at least an email address or a phone number." in response.text


def test_create_redirects_to_detail_with_qr(client, valid_contact):
    response = client.post("/contacts", data=form(valid_contact), follow_redirects=False)
    assert response.status_code == 303
    contact_id = created_id(response)

    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "Contact saved and QR code generated." in detail.text
    assert f'src="/contacts/{contact_id}/qr.png"' in detail.text
    assert "jonas@example.com" in detail.text

    qr = client.get(f"/contacts/{contact_id}/qr.png")
    assert qr.content.startswith(PNG_SIGNATURE)
    assert "FN:Jonas Jonaitis" in decode_qr(qr.content)
    download = client.get(f"/contacts/{contact_id}/qr.png?download=1")
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.get(f"/contacts/{contact_id}/contact.vcf").text.startswith("BEGIN:VCARD")


def test_history_lists_and_searches_contacts(client, valid_contact):
    client.post("/contacts", data=form(valid_contact))
    client.post("/contacts", data=form(valid_contact, first_name="Ona", email="ona@x.lt"))
    page = client.get("/contacts")
    assert page.status_code == 200
    assert "Jonas Jonaitis" in page.text and "Ona Jonaitis" in page.text
    searched = client.get("/contacts", params={"q": "ona@x"})
    assert "Ona Jonaitis" in searched.text and "Jonas Jonaitis" not in searched.text


def test_edit_and_delete_flow(client, valid_contact):
    contact_id = created_id(
        client.post("/contacts", data=form(valid_contact), follow_redirects=False)
    )
    edit = client.get(f"/contacts/{contact_id}/edit")
    assert edit.status_code == 200 and 'value="Jonas"' in edit.text

    bad = client.post(f"/contacts/{contact_id}/edit", data=form(valid_contact, phone="123"))
    assert bad.status_code == 422 and "Enter a valid phone number" in bad.text

    ok = client.post(
        f"/contacts/{contact_id}/edit",
        data=form(valid_contact, first_name="Petras"),
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert "Petras Jonaitis" in client.get(f"/contacts/{contact_id}").text

    deleted = client.post(f"/contacts/{contact_id}/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert client.get(f"/contacts/{contact_id}").status_code == 404


def test_ui_errors_are_html_pages(client):
    response = client.get("/contacts/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Contact not found." in response.text
    assert client.get("/contacts/not-a-uuid").status_code == 404


def test_user_input_is_html_escaped(client, valid_contact):
    contact_id = created_id(
        client.post(
            "/contacts",
            data=form(valid_contact, note="<script>alert(1)</script>"),
            follow_redirects=False,
        )
    )
    page = client.get(f"/contacts/{contact_id}").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_security_headers(client):
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "content-security-policy" not in client.get("/api/docs").headers
