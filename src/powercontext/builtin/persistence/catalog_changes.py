# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Independent, versioned catalog review storage for both relational backends."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Table,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.catalog_changes.models import CatalogChangeCandidate
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.tables import SHARED_METADATA, identity_string
from powercontext.builtin.review.errors import CandidateConflictError, CandidateNotFoundError, CandidateTerminalError

CATALOG_CANDIDATE_VERSIONS = Table(
    "pc_catalog_change_candidate_versions",
    SHARED_METADATA,
    Column("scope_id", identity_string(256), primary_key=True),
    Column("candidate_id", identity_string(128), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("payload", LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False),
    ForeignKeyConstraint(("scope_id",), ("pc_scopes.scope_id",), ondelete="CASCADE"),
    CheckConstraint("version > 0", name="ck_pc_catalog_candidate_version"),
)
CATALOG_CANDIDATE_HEADS = Table(
    "pc_catalog_change_candidate_heads",
    SHARED_METADATA,
    Column("scope_id", identity_string(256), primary_key=True),
    Column("candidate_id", identity_string(128), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("status", identity_string(16), nullable=False),
    Column("payload", LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False),
    ForeignKeyConstraint(
        ("scope_id", "candidate_id", "version"),
        (
            "pc_catalog_change_candidate_versions.scope_id",
            "pc_catalog_change_candidate_versions.candidate_id",
            "pc_catalog_change_candidate_versions.version",
        ),
        ondelete="CASCADE",
    ),
    CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_pc_catalog_candidate_status"),
)


async def ensure_catalog_change_schema(connection: AsyncConnection) -> None:
    await create_tables(connection, (CATALOG_CANDIDATE_VERSIONS, CATALOG_CANDIDATE_HEADS))


class CatalogCandidateRepository:
    async def get(
        self, connection: AsyncConnection, scope_id: str, candidate_id: str, *, current: bool = False
    ) -> CatalogChangeCandidate:
        table = CATALOG_CANDIDATE_HEADS
        statement = select(table.c.payload).where(table.c.scope_id == scope_id, table.c.candidate_id == candidate_id)
        if current:
            statement = statement.with_for_update()
        payload = await connection.scalar(statement)
        if payload is None:
            raise CandidateNotFoundError(candidate_id)
        return CatalogChangeCandidate.model_validate_json(payload)

    async def lock_pending(
        self, connection: AsyncConnection, scope_id: str, candidate_id: str, expected_version: int
    ) -> CatalogChangeCandidate:
        table = CATALOG_CANDIDATE_HEADS
        await connection.execute(
            update(table)
            .where(table.c.scope_id == scope_id, table.c.candidate_id == candidate_id)
            .values(version=table.c.version)
        )
        current = await self.get(connection, scope_id, candidate_id, current=True)
        if current.version != expected_version:
            raise CandidateConflictError(candidate_id, expected_version, current.version)
        if current.status != "pending":
            raise CandidateTerminalError(candidate_id, current.status)
        return current

    async def create(
        self, connection: AsyncConnection, scope_id: str, candidate: CatalogChangeCandidate
    ) -> CatalogChangeCandidate:
        await self._version(connection, scope_id, candidate)
        await connection.execute(
            insert(CATALOG_CANDIDATE_HEADS).values(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                version=candidate.version,
                status=candidate.status,
                payload=candidate.model_dump_json().encode(),
            )
        )
        return candidate

    async def save(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: CatalogChangeCandidate,
        candidate: CatalogChangeCandidate,
    ) -> CatalogChangeCandidate:
        if candidate.version != current.version:
            await self._version(connection, scope_id, candidate)
        table = CATALOG_CANDIDATE_HEADS
        changed = await connection.execute(
            update(table)
            .where(
                table.c.scope_id == scope_id,
                table.c.candidate_id == current.candidate_id,
                table.c.version == current.version,
                table.c.status == "pending",
            )
            .values(version=candidate.version, status=candidate.status, payload=candidate.model_dump_json().encode())
        )
        if changed.rowcount != 1:
            raise CandidateConflictError(current.candidate_id, current.version, current.version)
        return candidate

    async def _version(self, connection: AsyncConnection, scope_id: str, candidate: CatalogChangeCandidate) -> None:
        await connection.execute(
            insert(CATALOG_CANDIDATE_VERSIONS).values(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                version=candidate.version,
                payload=candidate.model_dump_json().encode(),
            )
        )

    async def history(
        self, connection: AsyncConnection, scope_id: str, candidate_id: str
    ) -> tuple[CatalogChangeCandidate, ...]:
        current = await self.get(connection, scope_id, candidate_id, current=True)
        table = CATALOG_CANDIDATE_VERSIONS
        payloads = await connection.scalars(
            select(table.c.payload)
            .where(table.c.scope_id == scope_id, table.c.candidate_id == candidate_id)
            .order_by(table.c.version)
        )
        versions = tuple(CatalogChangeCandidate.model_validate_json(payload) for payload in payloads)
        return (*versions[:-1], current)

    async def list(
        self, connection: AsyncConnection, scope_id: str, *, status: str | None, after: str, limit: int
    ) -> tuple[CatalogChangeCandidate, ...]:
        table = CATALOG_CANDIDATE_HEADS
        stmt = select(table.c.payload).where(table.c.scope_id == scope_id, table.c.candidate_id > after)
        if status is not None:
            stmt = stmt.where(table.c.status == status)
        payloads = await connection.scalars(stmt.order_by(table.c.candidate_id).limit(limit))
        return tuple(CatalogChangeCandidate.model_validate_json(payload) for payload in payloads)
