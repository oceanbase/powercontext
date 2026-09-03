"""Add durable work, scheduler coordination, membership, and rate limiting."""

from __future__ import annotations

from alembic import op

from powercontext.builtin.persistence.tables import COORDINATION_TABLES, SHARED_METADATA, WORK_TABLES

revision = "0002_work_ledger"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    SHARED_METADATA.create_all(
        op.get_bind(),
        tables=(*WORK_TABLES, *COORDINATION_TABLES),
        checkfirst=True,
    )


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
