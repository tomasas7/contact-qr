"""Pure-ASGI middleware. Pure ASGI (rather than Starlette's BaseHTTPMiddleware) lets the
logger see the final status of every response, including errors, without buffering
response bodies."""

import json
import logging
import time
from datetime import UTC, datetime

import anyio
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.models import RequestLog

logger = logging.getLogger("app.request_log")


def classify_source(path: str) -> str:
    """UI vs API is decided by URL space: everything under /api is the REST API
    (including its docs), /health is infrastructure, and the rest is the web UI."""
    if path == "/api" or path.startswith("/api/"):
        return "API"
    if path == "/health":
        return "SYSTEM"
    return "UI"


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class RequestLoggingMiddleware:
    """Persists one RequestLog row per HTTP request.

    The row is written after the response has been sent, in a worker thread, so logging
    adds no latency to the response. A failed write never fails the request: the entry is
    emitted to stdout instead (visible in `docker logs`).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        timestamp = datetime.now(UTC)
        status_code = 500  # what the client receives if the app raises before responding

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            client = scope.get("client")
            path = scope.get("path", "")
            entry = {
                "timestamp": timestamp,
                "client_ip": client[0][:45] if client else None,
                "method": scope.get("method", "")[:10],
                "path": path[:2048],
                "query_string": scope.get("query_string", b"").decode("latin-1")[:2048] or None,
                "status_code": status_code,
                "user_agent": (_header(scope, b"user-agent") or "")[:512] or None,
                "source": classify_source(path),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            # Shielded so a client disconnect (task cancellation) cannot drop the log entry.
            with anyio.CancelScope(shield=True):
                await self._persist(scope, entry)

    async def _persist(self, scope: Scope, entry: dict) -> None:
        session_factory = getattr(scope["app"].state, "session_factory", None)
        try:
            await anyio.to_thread.run_sync(_write, session_factory, entry)
        except Exception:
            logger.warning(
                "request log write failed; entry=%s",
                json.dumps(entry, default=str),
                exc_info=True,
            )


def _write(session_factory, entry: dict) -> None:
    with session_factory() as session:
        session.add(RequestLog(**entry))
        session.commit()


# Strict CSP for our own pages. The Swagger/ReDoc pages load assets from a CDN, so they
# are left without CSP rather than weakening it everywhere.
_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
_DOCS_PATHS = ("/api/docs", "/api/redoc")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_docs = scope.get("path", "").startswith(_DOCS_PATHS)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                ]
                if not is_docs:
                    headers.append((b"content-security-policy", _CSP.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
