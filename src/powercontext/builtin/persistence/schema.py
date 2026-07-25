"""Relational table creation over caller-selected SQLAlchemy metadata."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import MetaData, Table
from sqlalchemy.ext.asyncio import AsyncConnection


async def create_tables(connection: AsyncConnection, tables: Sequence[Table], /) -> None:
    """Create missing tables and indexes using SQLAlchemy's native metadata support."""

    groups: dict[MetaData, list[Table]] = {}
    for table in tables:
        groups.setdefault(table.metadata, []).append(table)

    for metadata, selected in groups.items():
        await connection.run_sync(
            lambda sync_connection, current=metadata, owned=selected: current.create_all(
                sync_connection,
                tables=owned,
                checkfirst=True,
            )
        )
