"""REST API: contact CRUD, QR/vCard downloads, error handling and status codes."""

import uuid

from tests.test_vcard_qr import PNG_SIGNATURE, decode_qr, parse_vcard

API = "/api/v1/contacts"


def create(client, data):
    response = client.post(API, json=data)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_contact_returns_201_with_location_and_links(client, valid_contact):
    response = client.post(API, json=valid_contact)
    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"{API}/{body['id']}"
    assert body["first_name"] == "Jonas"
    assert body["phone"] == "+37061234567"
    assert body["qr_url"] == f"{API}/{body['id']}/qr"
    assert body["vcard_url"] == f"{API}/{body['id']}/vcard"
    assert body["vcard"].startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")


def test_create_normalizes_input(client, valid_contact):
    body = create(client, {**valid_contact, "phone": "8 612 34567", "first_name": "  Jonas "})
    assert body["phone"] == "+37061234567"
    assert body["first_name"] == "Jonas"


def test_get_contact(client, valid_contact):
    created = create(client, valid_contact)
    response = client.get(f"{API}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_qr_endpoint_returns_scannable_png_matching_contact(client, valid_contact):
    created = create(client, valid_contact)
    response = client.get(created["qr_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'inline; filename="jonas-jonaitis.png"'
    assert response.content.startswith(PNG_SIGNATURE)
    assert decode_qr(response.content) == created["vcard"]


def test_qr_download_is_an_attachment(client, valid_contact):
    created = create(client, {**valid_contact, "first_name": "Šarūnas"})
    response = client.get(created["qr_url"], params={"download": "true"})
    assert response.headers["content-disposition"] == (
        'attachment; filename="sarunas-jonaitis.png"'
    )


def test_vcard_endpoint(client, valid_contact):
    created = create(client, valid_contact)
    response = client.get(created["vcard_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/vcard; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    assert parse_vcard(response.text)["FN"] == "Jonas Jonaitis"


def test_list_contacts_newest_first_with_pagination(client, valid_contact):
    ids = [create(client, {**valid_contact, "first_name": f"P{i}"})["id"] for i in range(3)]
    page = client.get(API, params={"limit": 2}).json()
    assert page["total"] == 3 and page["limit"] == 2 and page["offset"] == 0
    assert [c["id"] for c in page["items"]] == ids[::-1][:2]
    page2 = client.get(API, params={"limit": 2, "offset": 2}).json()
    assert [c["id"] for c in page2["items"]] == [ids[0]]


def test_list_contacts_search(client, valid_contact):
    create(client, valid_contact)
    create(
        client,
        {
            **valid_contact,
            "first_name": "Ona",
            "last_name": "Onaitė",
            "email": "ona@test.lt",
            "city": "Kaunas",
        },
    )
    assert [c["first_name"] for c in client.get(API, params={"q": "kaunas"}).json()["items"]] == [
        "Ona"
    ]
    assert client.get(API, params={"q": "jonas jonaitis"}).json()["total"] == 1
    assert client.get(API, params={"q": "%"}).json()["total"] == 0  # wildcards are literal


def test_list_validates_query_parameters(client):
    response = client.get(API, params={"limit": 0})
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "limit"


def test_update_replaces_contact_and_regenerates_qr(client, valid_contact):
    created = create(client, valid_contact)
    old_qr = client.get(created["qr_url"]).content
    response = client.put(
        f"{API}/{created['id']}", json={**valid_contact, "first_name": "Petras", "email": None}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Petras" and body["email"] is None
    assert "FN:Petras Jonaitis" in body["vcard"]
    new_qr = client.get(created["qr_url"]).content
    assert new_qr != old_qr
    assert decode_qr(new_qr) == body["vcard"]


def test_delete_contact(client, valid_contact):
    created = create(client, valid_contact)
    response = client.delete(f"{API}/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"{API}/{created['id']}").status_code == 404
    assert client.get(created["qr_url"]).status_code == 404


# --- Errors -------------------------------------------------------------------------------


def test_validation_error_envelope(client, valid_contact):
    response = client.post(
        API, json={**valid_contact, "email": "nope", "birthday": "2999-01-01", "first_name": ""}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "The request contains invalid data."
    by_field = {d["field"]: d for d in error["details"]}
    assert by_field["email"] == {
        "field": "email",
        "message": "Enter a valid email address.",
        "code": "invalid_email",
    }
    assert by_field["birthday"]["code"] == "birthday_in_future"
    assert by_field["first_name"]["message"] == "This field is required."


def test_missing_contact_method_is_a_form_level_error(client, valid_contact):
    response = client.post(API, json={**valid_contact, "email": None, "phone": None})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {
            "field": None,
            "code": "contact_method_required",
            "message": "Provide at least an email address or a phone number.",
        }
    ]


def test_malformed_json_is_400(client):
    response = client.post(API, content="{not json", headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"


def test_unknown_contact_is_404(client):
    for path in ("", "/qr", "/vcard"):
        response = client.get(f"{API}/{uuid.uuid4()}{path}")
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "not_found", "message": "Contact not found."}}


def test_invalid_uuid_is_422(client):
    response = client.get(f"{API}/not-a-uuid")
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "contact_id"


def test_unknown_api_route_is_json_404(client):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_wrong_method_is_405(client):
    response = client.patch(API)
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_openapi_documents_all_endpoints(client):
    spec = client.get("/api/openapi.json").json()
    paths = spec["paths"]
    assert {"post", "get"} <= paths[API].keys()
    assert {"get", "put", "delete"} <= paths[API + "/{contact_id}"].keys()
    assert "image/png" in paths[API + "/{contact_id}/qr"]["get"]["responses"]["200"]["content"]
    assert "/api/v1/logs" in paths and "/health" in paths
    assert client.get("/api/docs").status_code == 200
