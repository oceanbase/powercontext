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

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import update

from powercontext.builtin.persistence import InvalidStoredPayloadError
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import SOURCES_TABLE
from powercontext.builtin.source_eligibility import SourceNotEligibleError
from powercontext.builtin.sources import (
    CONTENT_SOURCE_ADAPTER,
    ContentSource,
    ContentSourceInternal,
    ContentSourceTarget,
)
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import repository_profile


def _content_source(name: str, *, lineage_only: bool = False) -> ContentSource:
    internal = (
        ContentSourceInternal(
            role="lineage_only",
            operation="artifact_create",
            target=ContentSourceTarget(
                scope_id="scope-a",
                family="future-family",
                artifact_id="artifact-1",
                revision=1,
            ),
        )
        if lineage_only
        else None
    )
    return ContentSource(
        name=name,
        materialization=SourceMaterialization.CAPTURED,
        content=name,
        internal=internal,
    )


def test_generation_source_access_rejects_explicit_refs_but_skips_window_entries() -> None:
    async def scenario() -> None:
        repository = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        access = GenerationSourceAccess(repository)
        async with repository_profile() as (profile, _repositories):
            async with profile.database.transaction() as connection:
                ordinary = await repository.add(connection, "scope-a", _content_source("ordinary"))
                reserved = await repository.add(
                    connection,
                    "scope-a",
                    _content_source("reserved", lineage_only=True),
                )

                with pytest.raises(SourceNotEligibleError) as error:
                    await access.require_for_generation(connection, "scope-a", (ordinary.ref, reserved.ref))
                window = await access.list_window_for_generation(
                    connection,
                    "scope-a",
                    after=0,
                    through=reserved.journal_position,
                )

            assert error.value.source == reserved.ref
            assert window == (ordinary,)

    asyncio.run(scenario())


def test_generation_window_fails_closed_on_a_malformed_source_payload() -> None:
    async def scenario() -> None:
        repository = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        access = GenerationSourceAccess(repository)
        async with (
            repository_profile() as (profile, _repositories),
            profile.database.transaction() as connection,
        ):
            stored = await repository.add(connection, "scope-a", _content_source("broken"))
            await connection.execute(
                update(SOURCES_TABLE)
                .where(
                    SOURCES_TABLE.c.scope_id == "scope-a",
                    SOURCES_TABLE.c.source_type == stored.ref.source_type,
                    SOURCES_TABLE.c.source_id == stored.ref.source_id,
                )
                .values(payload=b"not-json")
            )

            with pytest.raises(InvalidStoredPayloadError):
                await access.list_window_for_generation(
                    connection,
                    "scope-a",
                    after=0,
                    through=stored.journal_position,
                )

    asyncio.run(scenario())
