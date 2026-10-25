from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import RequestLog


@dataclass(frozen=True)
class LogFilters:
    source: str | None = None
    method: str | None = None
    status_code: int | None = None
    path: str | None = None  # substring match
    client_ip: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def list_logs(
    session: Session, filters: LogFilters, *, limit: int = 50, offset: int = 0
) -> tuple[list[RequestLog], int]:
    stmt = select(RequestLog)
    if filters.source:
        stmt = stmt.where(RequestLog.source == filters.source.upper())
    if filters.method:
        stmt = stmt.where(RequestLog.method == filters.method.upper())
    if filters.status_code is not None:
        stmt = stmt.where(RequestLog.status_code == filters.status_code)
    if filters.path:
        escaped = filters.path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(RequestLog.path.ilike(f"%{escaped}%", escape="\\"))
    if filters.client_ip:
        stmt = stmt.where(RequestLog.client_ip == filters.client_ip)
    if filters.since:
        stmt = stmt.where(RequestLog.timestamp >= filters.since)
    if filters.until:
        stmt = stmt.where(RequestLog.timestamp <= filters.until)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(
        stmt.order_by(RequestLog.timestamp.desc(), RequestLog.id.desc()).limit(limit).offset(offset)
    ).all()
    return list(items), total


def prune_logs(session: Session, older_than_days: int) -> int:
    """Retention policy: delete request logs older than `older_than_days`. Returns rows deleted."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    result = session.execute(delete(RequestLog).where(RequestLog.timestamp < cutoff))
    session.commit()
    return result.rowcount or 0
