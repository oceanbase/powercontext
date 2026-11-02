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

"""OceanBase FULLTEXT projection shared by approved Experience and Skill heads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import match
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError
from powercontext.builtin.artifacts.search import AdmissionFloor
from powercontext.builtin.artifacts.skill import Skill, SkillPackageSnapshot
from powercontext.builtin.persistence.experience_index import (
    APPROVED_ARTIFACT_PROJECTION_VERSION,
    ensure_artifact_head_searchable_text,
    rebuild_experience_projections,
    rebuild_skill_projections,
    remove_experience_projection,
    replace_experience_projection,
    replace_skill_projection,
    search_approved_artifact_rows,
)
from powercontext.builtin.persistence.lexical_projection import (
    lexical_projection_is_current,
    record_lexical_projection_version,
)
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE

_OCEANBASE_FTS_INDEX_NAME = "ix_pc_artifact_heads_fts"
_OCEANBASE_CREATE_FTS_SQL = f"""
CREATE FULLTEXT INDEX {_OCEANBASE_FTS_INDEX_NAME}
ON pc_artifact_heads (searchable_text) WITH PARSER SPACE
"""
_OCEANBASE_FTS_INDEX_EXISTS_SQL = text(
    """
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'pc_artifact_heads'
      AND index_name = :index_name
    """
)


class OceanBaseExperienceFTSIndex:
    """Maintain and query approved Experience and Skill heads with OceanBase FULLTEXT."""

    families: frozenset[str] = frozenset({Experience.family, Skill.family})

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "mysql":
            raise CapabilityNotSupportedError("oceanbase-experience-fts")
        await ensure_artifact_head_searchable_text(connection)
        count = await connection.scalar(
            _OCEANBASE_FTS_INDEX_EXISTS_SQL,
            {"index_name": _OCEANBASE_FTS_INDEX_NAME},
        )
        if count == 0:
            await connection.exec_driver_sql(_OCEANBASE_CREATE_FTS_SQL)
        if not await lexical_projection_is_current(
            connection, ARTIFACT_HEADS_TABLE.name, version=APPROVED_ARTIFACT_PROJECTION_VERSION
        ):
            await self.rebuild(connection)
        probe = match(ARTIFACT_HEADS_TABLE.c.searchable_text, against="powercontext")
        await connection.execute(select(ARTIFACT_HEADS_TABLE.c.artifact_id).where(probe).limit(1))

    async def rebuild(self, connection: AsyncConnection, /) -> None:
        await rebuild_experience_projections(connection)
        await rebuild_skill_projections(connection)
        await record_lexical_projection_version(
            connection, ARTIFACT_HEADS_TABLE.name, version=APPROVED_ARTIFACT_PROJECTION_VERSION
        )

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        experience: Experience,
        /,
    ) -> None:
        await replace_experience_projection(connection, scope_id, experience)

    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str, /) -> None:
        await remove_experience_projection(connection, scope_id, artifact_id)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: Literal["experience", "skill"],
        query: str,
        limit: int,
        /,
        *,
        admission: AdmissionFloor | None = None,
    ) -> tuple[Mapping[Any, Any], ...]:
        return await search_approved_artifact_rows(connection, scope_id, family, query, limit, admission=admission)

    async def replace_skill(
        self,
        connection: AsyncConnection,
        scope_id: str,
        skill: Skill,
        package: SkillPackageSnapshot,
        /,
    ) -> None:
        await replace_skill_projection(connection, scope_id, skill, package)


__all__ = ["OceanBaseExperienceFTSIndex"]
