"""initial schema: contacts and request_logs

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(length=50), nullable=False),
        sa.Column("last_name", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("street", sa.String(length=100), nullable=True),
        sa.Column("city", sa.String(length=60), nullable=True),
        sa.Column("postal_code", sa.String(length=12), nullable=True),
        sa.Column("country", sa.String(length=60), nullable=True),
        sa.Column("organization", sa.String(length=100), nullable=True),
        sa.Column("job_title", sa.String(length=60), nullable=True),
        sa.Column("website", sa.String(length=200), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("vcard", sa.Text(), nullable=False),
        sa.Column("qr_png", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contacts_created_at", "contacts", ["created_at"])

    op.create_table(
        "request_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("query_string", sa.String(length=2048), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_logs_timestamp", "request_logs", ["timestamp"])
    op.create_index("ix_request_logs_source", "request_logs", ["source"])


def downgrade() -> None:
    op.drop_table("request_logs")
    op.drop_table("contacts")
