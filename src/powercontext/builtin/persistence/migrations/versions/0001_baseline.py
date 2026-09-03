"""Create the validated PowerContext baseline schema."""

from __future__ import annotations

from collections import defaultdict

from alembic import op
from sqlalchemy import MetaData, Table

from powercontext.builtin.persistence.migration import baseline_tables

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    grouped: dict[MetaData, list[Table]] = defaultdict(list)
    for table in baseline_tables():
        grouped[table.metadata].append(table)
    for metadata, tables in grouped.items():
        metadata.create_all(bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
