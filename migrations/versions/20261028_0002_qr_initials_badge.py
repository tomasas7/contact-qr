"""re-render stored QR codes with the initials badge

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28

Data only, no schema change: re-renders every contact's QR PNG from its stored vCard text
with the application's renderer. Downgrade renders them again without the badge.
"""

import sqlalchemy as sa
from alembic import op

from app.qr import initials, render_qr_png

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

contacts = sa.table(
    "contacts",
    sa.column("id", sa.Uuid()),
    sa.column("first_name", sa.String()),
    sa.column("last_name", sa.String()),
    sa.column("vcard", sa.Text()),
    sa.column("qr_png", sa.LargeBinary()),
)


def _rerender(*, badge: bool) -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(contacts.c.id, contacts.c.first_name, contacts.c.last_name, contacts.c.vcard)
    ).all()
    for row in rows:
        label = initials(row.first_name, row.last_name) if badge else ""
        conn.execute(
            contacts.update()
            .where(contacts.c.id == row.id)
            .values(qr_png=render_qr_png(row.vcard, label=label))
        )


def upgrade() -> None:
    _rerender(badge=True)


def downgrade() -> None:
    _rerender(badge=False)
