"""Test fixtures. Integration tests run against a real PostgreSQL (the same engine as
production); the schema is created by running the Alembic migrations, so migrations are
tested too. Connection settings come from POSTGRES_* environment variables."""

import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db import make_engine
from app.main import create_app

os.environ.setdefault("POSTGRES_DB", "contactqr_test")


def _settings(**overrides) -> Settings:
    base = {
        "app_env": "test",
        "app_version": "test",
        "log_retention_days": 0,  # no background task during tests
        "rate_limit_writes_per_minute": 0,
    }
    return Settings(**{**base, **overrides})


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = _settings()
    if not s.postgres_db.endswith("_test"):
        pytest.exit(f"Refusing to run tests against non-test database {s.postgres_db!r}")
    return s


@pytest.fixture(scope="session")
def engine(settings):
    engine = make_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config("alembic.ini")
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(request):
    if "engine" not in request.fixturenames and "client" not in request.fixturenames:
        yield
        return
    engine = request.getfixturevalue("engine")
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE contacts, request_logs RESTART IDENTITY"))
    yield


@pytest.fixture
def make_client(engine):
    """Factory for a TestClient with custom settings (e.g. a low rate limit)."""
    clients = []

    def factory(**overrides) -> TestClient:
        client = TestClient(create_app(_settings(**overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
def session(engine):
    from app.db import make_session_factory

    with make_session_factory(engine)() as s:
        yield s


VALID_CONTACT = {
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


@pytest.fixture
def valid_contact() -> dict:
    return dict(VALID_CONTACT)
