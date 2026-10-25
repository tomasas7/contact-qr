import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db import Base


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(16))  # E.164
    birthday: Mapped[date | None] = mapped_column(Date)
    street: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(60))
    postal_code: Mapped[str | None] = mapped_column(String(12))
    country: Mapped[str | None] = mapped_column(String(60))
    organization: Mapped[str | None] = mapped_column(String(100))
    job_title: Mapped[str | None] = mapped_column(String(60))
    website: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(200))

    # The exact payload encoded in the QR code, and the rendered PNG. The image is
    # persisted (not re-rendered on demand) so what was issued is what is served.
    vcard: Mapped[str] = mapped_column(Text)
    qr_png: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_contacts_created_at", "created_at"),)


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    client_ip: Mapped[str | None] = mapped_column(String(45))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(2048))
    query_string: Mapped[str | None] = mapped_column(String(2048))
    status_code: Mapped[int] = mapped_column(Integer)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(10))  # UI | API | SYSTEM
    duration_ms: Mapped[float] = mapped_column(Float)

    __table_args__ = (
        Index("ix_request_logs_timestamp", "timestamp"),
        Index("ix_request_logs_source", "source"),
    )
