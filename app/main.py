"""Application factory. Run with: uvicorn --factory app.main:create_app"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import anyio
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import web
from app.api import contacts as contacts_api
from app.api import health as health_api
from app.api import logs as logs_api
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.errors import register_error_handlers
from app.middleware import RequestLoggingMiddleware, SecurityHeadersMiddleware
from app.ratelimit import SlidingWindowRateLimiter
from app.services.request_logs import prune_logs

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("app")

API_DESCRIPTION = """
Create contacts and get scannable **vCard 3.0 QR codes** for them. Scanning a code with
a standard phone camera offers to save the person as a contact.

### Conventions
* JSON in and out (`Content-Type: application/json`), UTF-8, timestamps in ISO 8601 / UTC.
* List endpoints are paginated with `limit` / `offset` and return `total`.
* **Errors** always use one envelope, with a stable `code` for programs and a `message`
  for humans; validation errors list each problem in `details`:
  ```json
  {"error": {"code": "validation_error", "message": "The request contains invalid data.",
             "details": [{"field": "email", "message": "Enter a valid email address.",
                          "code": "invalid_email"}]}}
  ```
* **Rate limiting:** write operations (POST/PUT/DELETE) are limited per client IP. Over the
  limit you get `429` with a `Retry-After` header.
* **Request logging:** every request (web UI and API) is logged and can be queried via
  `GET /api/v1/logs`.

### Contact fields
`first_name` and `last_name` are required, plus at least one of `email` / `phone`.
All other fields are optional.
"""


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC") if value else ""


async def _retention_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings

    def run() -> int:
        with app.state.session_factory() as session:
            return prune_logs(session, settings.log_retention_days)

    while True:
        try:
            deleted = await anyio.to_thread.run_sync(run)
            logger.info(
                "log retention: deleted %d entries older than %d days",
                deleted,
                settings.log_retention_days,
            )
        except Exception:
            logger.warning("log retention run failed", exc_info=True)
        await asyncio.sleep(settings.log_retention_interval_hours * 3600)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        retention = None
        if settings.log_retention_days > 0:
            retention = asyncio.create_task(_retention_loop(app))
        yield
        if retention:
            retention.cancel()
        engine.dispose()

    app = FastAPI(
        title="Contact QR API",
        version=settings.app_version,
        description=API_DESCRIPTION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        generate_unique_id_function=lambda route: route.name,  # operationId = function name
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.rate_limiter = SlidingWindowRateLimiter(settings.rate_limit_writes_per_minute)

    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.filters["dt"] = _format_dt
    templates.env.globals["app_version"] = settings.app_version
    app.state.templates = templates

    # Starlette runs the last-added middleware first, so request logging wraps everything.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    register_error_handlers(app)

    app.include_router(contacts_api.router)
    app.include_router(logs_api.router)
    app.include_router(health_api.router)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.get("/docs", include_in_schema=False)
    def docs_redirect() -> RedirectResponse:
        return RedirectResponse("/api/docs")

    return app
