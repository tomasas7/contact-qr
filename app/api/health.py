import logging

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db import ping
from app.schemas import HealthOut

router = APIRouter(tags=["System"])
logger = logging.getLogger(__name__)


@router.head("/health", include_in_schema=False)
@router.get(
    "/health",
    response_model=HealthOut,
    summary="Health check (verifies the database too)",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "checks": {"database": "ok"},
                        "version": "1.0.0+3f1c2a9",
                    }
                }
            }
        },
        503: {
            "model": HealthOut,
            "description": "A dependency is unavailable.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "error",
                        "checks": {"database": "unavailable"},
                        "version": "1.0.0+3f1c2a9",
                    }
                }
            },
        },
    },
)
async def health(request: Request) -> JSONResponse:
    """The app is only useful with its database, so the check includes a `SELECT 1`. A failing
    check returns 503, which makes the container unhealthy and fails the deploy gate."""
    checks = {"database": "ok"}
    try:
        with anyio.fail_after(3):
            await anyio.to_thread.run_sync(ping, request.app.state.engine)
    except Exception:
        logger.warning("health check: database unavailable", exc_info=True)
        checks["database"] = "unavailable"
    healthy = all(v == "ok" for v in checks.values())
    body = HealthOut(
        status="ok" if healthy else "error",
        checks=checks,
        version=request.app.state.settings.app_version,
    )
    return JSONResponse(body.model_dump(), status_code=200 if healthy else 503)
