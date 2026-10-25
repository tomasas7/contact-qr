from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import Page, RequestLogOut
from app.services.request_logs import LogFilters, list_logs

router = APIRouter(prefix="/api/v1/logs", tags=["Request logs"])

_LOG_EXAMPLE = {
    "id": 1024,
    "timestamp": "2026-10-25T12:00:00.123456Z",
    "client_ip": "203.0.113.7",
    "method": "POST",
    "path": "/api/v1/contacts",
    "query_string": None,
    "status_code": 201,
    "user_agent": "curl/8.9.1",
    "source": "API",
    "duration_ms": 18.42,
}


@router.get(
    "",
    response_model=Page[RequestLogOut],
    summary="Query request logs (newest first)",
    description="Every HTTP request handled by the application is logged, whether it came "
    "from the web UI (`UI`), the REST API (`API`) or infrastructure such as health checks "
    "(`SYSTEM`). All filters are optional and combine with AND.",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {"items": [_LOG_EXAMPLE], "total": 1, "limit": 50, "offset": 0}
                }
            }
        }
    },
)
def query_logs(
    source: Literal["UI", "API", "SYSTEM"] | None = Query(None),
    method: str | None = Query(None, max_length=10, examples=["POST"]),
    status_code: int | None = Query(None, ge=100, le=599),
    path: str | None = Query(None, max_length=200, description="Substring match on the path."),
    client_ip: str | None = Query(None, max_length=45),
    since: datetime | None = Query(None, description="ISO 8601, inclusive."),
    until: datetime | None = Query(None, description="ISO 8601, inclusive."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> Page[RequestLogOut]:
    filters = LogFilters(source, method, status_code, path, client_ip, since, until)
    items, total = list_logs(session, filters, limit=limit, offset=offset)
    return Page[RequestLogOut](
        items=[RequestLogOut.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
