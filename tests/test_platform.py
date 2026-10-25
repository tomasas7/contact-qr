"""Health check, rate limiting, persistence across restarts and migration consistency."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient

from app.db import Base
from app.main import create_app
from app.ratelimit import RateLimitExceededError, SlidingWindowRateLimiter
from tests.conftest import _settings


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}, "version": "test"}
    assert client.head("/health").status_code == 200


def test_health_reports_database_outage_as_503(engine):
    # Point the app at a port where no database listens.
    app = create_app(_settings(postgres_port=1))
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert response.json()["checks"] == {"database": "unavailable"}


def test_rate_limiter_unit():
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
    limiter.hit("1.1.1.1")
    limiter.hit("1.1.1.1")
    limiter.hit("2.2.2.2")  # other clients are unaffected
    with pytest.raises(RateLimitExceededError) as exc:
        limiter.hit("1.1.1.1")
    assert 1 <= exc.value.retry_after <= 60


def test_rate_limit_applies_to_writes_only(make_client, valid_contact):
    client = make_client(rate_limit_writes_per_minute=2)
    assert client.post("/api/v1/contacts", json=valid_contact).status_code == 201
    assert client.post("/api/v1/contacts", json=valid_contact).status_code == 201
    response = client.post("/api/v1/contacts", json=valid_contact)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1
    # The UI shares the same per-IP budget, and reads are never limited.
    assert client.post("/contacts", data=valid_contact).status_code == 429
    assert client.get("/api/v1/contacts").status_code == 200


def test_data_survives_application_restart(make_client, valid_contact):
    first = make_client()
    created = first.post("/api/v1/contacts", json=valid_contact).json()
    first.__exit__(None, None, None)  # shut the app down (engine disposed)

    second = make_client()
    assert second.get(f"/api/v1/contacts/{created['id']}").json() == created
    assert second.get(created["qr_url"]).status_code == 200
    assert second.get("/api/v1/logs", params={"path": "/api/v1/contacts"}).json()["total"] >= 2


def test_migrations_match_models(engine):
    """Guards against model changes that were not captured in an Alembic migration."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
