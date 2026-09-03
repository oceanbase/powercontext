from __future__ import annotations

from alembic import context

from powercontext.builtin.persistence.migration import SCHEMA_VERSION_TABLE


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("PowerContext migrations require a caller-owned connection")  # noqa: TRY003
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=SCHEMA_VERSION_TABLE,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
