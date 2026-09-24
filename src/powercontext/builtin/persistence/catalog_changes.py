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

"""Tag proposal codec over the shared Candidate tables."""

import json

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.catalog_changes.models import CatalogChangeCandidate
from powercontext.builtin.persistence.tables import CANDIDATE_HEADS_TABLE, CANDIDATE_VERSIONS_TABLE
from powercontext.builtin.review.errors import CandidateConflictError, CandidateNotFoundError, CandidateTerminalError
from powercontext.builtin.tags import ArtifactTagSet


def decode_tag_candidate(row) -> CatalogChangeCandidate:
    snapshot = CatalogChangeCandidate.model_validate_json(row["proposal"])
    result = None if row["result_payload"] is None else ArtifactTagSet.model_validate_json(row["result_payload"])
    return CatalogChangeCandidate.model_validate({
        **snapshot.model_dump(),
        "status": row["status"],
        "result": result,
        "decision_reason": row["decision_reason"],
    })


class CatalogCandidateRepository:
    async def get(
        self, connection: AsyncConnection, scope_id: str, candidate_id: str, *, current: bool = False
    ) -> CatalogChangeCandidate:
        heads, versions = CANDIDATE_HEADS_TABLE, CANDIDATE_VERSIONS_TABLE
        statement = (
            select(heads, versions)
            .join(
                versions,
                (heads.c.scope_id == versions.c.scope_id)
                & (heads.c.candidate_id == versions.c.candidate_id)
                & (heads.c.version == versions.c.version),
            )
            .where(heads.c.scope_id == scope_id, heads.c.candidate_id == candidate_id, heads.c.candidate_kind == "tag")
        )
        if current:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            raise CandidateNotFoundError(candidate_id)
        return decode_tag_candidate(row)

    async def lock_pending(
        self, connection: AsyncConnection, scope_id: str, candidate_id: str, expected_version: int
    ) -> CatalogChangeCandidate:
        table = CANDIDATE_HEADS_TABLE
        await connection.execute(
            update(table)
            .where(table.c.scope_id == scope_id, table.c.candidate_id == candidate_id, table.c.candidate_kind == "tag")
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
            insert(CANDIDATE_HEADS_TABLE).values(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                family=candidate.proposal.target.family,
                candidate_kind="tag",
                version=candidate.version,
                status=candidate.status,
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
        table = CANDIDATE_HEADS_TABLE
        changed = await connection.execute(
            update(table)
            .where(
                table.c.scope_id == scope_id,
                table.c.candidate_id == current.candidate_id,
                table.c.candidate_kind == "tag",
                table.c.version == current.version,
                table.c.status == "pending",
            )
            .values(
                version=candidate.version,
                status=candidate.status,
                decision_reason=candidate.decision_reason,
                result_payload=None if candidate.result is None else candidate.result.model_dump_json().encode(),
            )
        )
        if changed.rowcount != 1:
            raise CandidateConflictError(current.candidate_id, current.version, current.version)
        return candidate

    async def _version(self, connection: AsyncConnection, scope_id: str, candidate: CatalogChangeCandidate) -> None:
        from powercontext.builtin.persistence.citation_codec import dump_memory_citations

        await connection.execute(
            insert(CANDIDATE_VERSIONS_TABLE).values(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                version=candidate.version,
                family=candidate.proposal.target.family,
                proposal=candidate.model_dump_json().encode(),
                source_refs=json.dumps([ref.model_dump(mode="json") for ref in candidate.sources]).encode(),
                artifact_refs=json.dumps([ref.model_dump(mode="json") for ref in candidate.artifacts]).encode(),
                memory_citations=dump_memory_citations(candidate.memory_citations),
                reason=candidate.reason,
            )
        )

    async def history(
        self, connection: AsyncConnection, scope_id: str, candidate_id: str
    ) -> tuple[CatalogChangeCandidate, ...]:
        await self.get(connection, scope_id, candidate_id, current=True)
        table = CANDIDATE_VERSIONS_TABLE
        payloads = await connection.scalars(
            select(table.c.proposal)
            .where(table.c.scope_id == scope_id, table.c.candidate_id == candidate_id)
            .order_by(table.c.version)
        )
        return tuple(CatalogChangeCandidate.model_validate_json(payload) for payload in payloads)

    async def list(
        self, connection: AsyncConnection, scope_id: str, *, status: str | None, after: str, limit: int
    ) -> tuple[CatalogChangeCandidate, ...]:
        table = CANDIDATE_HEADS_TABLE
        stmt = select(table.c.candidate_id).where(
            table.c.scope_id == scope_id, table.c.candidate_kind == "tag", table.c.candidate_id > after
        )
        if status is not None:
            stmt = stmt.where(table.c.status == status)
        ids = await connection.scalars(stmt.order_by(table.c.candidate_id).limit(limit))
        return tuple([await self.get(connection, scope_id, identity) for identity in ids])
