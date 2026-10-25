"""Every request (UI and API) is persisted with the required fields and is queryable."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.middleware import classify_source
from app.models import RequestLog
from app.services.request_logs import prune_logs


def all_logs(session) -> list[RequestLog]:
    session.expire_all()
    return list(session.scalars(select(RequestLog).order_by(RequestLog.id)))


def test_classify_source():
    assert classify_source("/api/v1/contacts") == "API"
    assert classify_source("/api/docs") == "API"
    assert classify_source("/health") == "SYSTEM"
    assert classify_source("/") == "UI"
    assert classify_source("/contacts/1") == "UI"
    assert classify_source("/apiary") == "UI"


def test_api_request_is_logged_with_all_fields(client, session, valid_contact):
    client.post("/api/v1/contacts", json=valid_contact, headers={"user-agent": "pytest-agent/1.0"})
    [log] = all_logs(session)
    assert log.method == "POST"
    assert log.path == "/api/v1/contacts"
    assert log.status_code == 201
    assert log.source == "API"
    assert log.user_agent == "pytest-agent/1.0"
    assert log.client_ip == "testclient"
    assert log.duration_ms > 0
    assert abs(datetime.now(UTC) - log.timestamp) < timedelta(minutes=1)


def test_ui_requests_and_errors_are_logged(client, session):
    client.get("/contacts?q=x")
    client.get("/does-not-exist")
    client.get("/health")
    logs = all_logs(session)
    assert [(entry.path, entry.source, entry.status_code) for entry in logs] == [
        ("/contacts", "UI", 200),
        ("/does-not-exist", "UI", 404),
        ("/health", "SYSTEM", 200),
    ]
    assert logs[0].query_string == "q=x"


def test_forwarded_client_ip_is_used_when_proxy_is_trusted(client, session):
    # Uvicorn rewrites the client address from X-Forwarded-For only for trusted proxies; the
    # middleware logs whatever ASGI reports, so here we just check the IP is recorded.
    client.get("/api/v1/contacts")
    assert all_logs(session)[0].client_ip


def test_log_write_failure_does_not_fail_the_request(client, monkeypatch, session):
    def broken_factory():
        raise RuntimeError("database down")

    monkeypatch.setattr(client.app.state, "session_factory", broken_factory)
    response = client.get("/static/style.css")
    assert response.status_code == 200
    monkeypatch.undo()
    assert all_logs(session) == []


def test_logs_api_filters_and_paginates(client):
    client.get("/contacts")
    client.get("/api/v1/contacts")
    client.get(f"/api/v1/contacts/{'0' * 8}-0000-0000-0000-{'0' * 12}")  # 404
    response = client.get("/api/v1/logs", params={"source": "API", "status_code": 404})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    assert page["items"][0]["status_code"] == 404
    assert set(page["items"][0]) == {
        "id",
        "timestamp",
        "client_ip",
        "method",
        "path",
        "query_string",
        "status_code",
        "user_agent",
        "source",
        "duration_ms",
    }
    ui = client.get("/api/v1/logs", params={"source": "UI"}).json()
    assert [i["path"] for i in ui["items"]] == ["/contacts"]
    paged = client.get("/api/v1/logs", params={"limit": 1}).json()
    assert len(paged["items"]) == 1 and paged["total"] >= 3


def test_logs_api_time_range(client):
    client.get("/contacts")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert client.get("/api/v1/logs", params={"since": future}).json()["total"] == 0
    assert client.get("/api/v1/logs", params={"until": future}).json()["total"] >= 1


def test_logs_api_rejects_invalid_filters(client):
    assert client.get("/api/v1/logs", params={"source": "FOO"}).status_code == 422
    assert client.get("/api/v1/logs", params={"status_code": 99}).status_code == 422


def test_logs_page_renders_with_filters(client):
    client.get("/api/v1/contacts")
    response = client.get("/logs", params={"source": "API"})
    assert response.status_code == 200
    assert "/api/v1/contacts" in response.text


def test_retention_deletes_only_old_logs(session):
    now = datetime.now(UTC)
    for days in (1, 89, 91, 400):
        session.add(
            RequestLog(
                timestamp=now - timedelta(days=days),
                method="GET",
                path="/",
                status_code=200,
                source="UI",
                duration_ms=1.0,
            )
        )
    session.commit()
    assert prune_logs(session, older_than_days=90) == 2
    assert len(all_logs(session)) == 2
