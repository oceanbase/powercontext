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

from powercontext.builtin.persistence import (
    InvalidRepositoryArgumentError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import (
    SOURCE_ADAPTERS,
    CommitSource,
    NoteSource,
    repository_profile,
)


def test_two_source_adapters_share_one_repository_and_journal() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            commit = CommitSource(
                name="abc123",
                materialization=SourceMaterialization.REFERENCED,
                revision="abc123",
            )
            note = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="Review the repository boundary.",
            )
            async with profile.database.transaction() as connection:
                first = await repositories.sources.add(connection, "scope-a", commit)
                second = await repositories.sources.add(connection, "scope-a", note)
                repeated = await repositories.sources.add(connection, "scope-a", commit)

                assert first.journal_position == 1
                assert second.journal_position == 2
                assert repeated == first
                assert await repositories.sources.journal_position(connection, "scope-a") == 2
                assert await repositories.sources.list(connection, "scope-a", after=1) == (second,)

            async with profile.database.transaction() as connection:
                loaded = await repositories.sources.get(connection, "scope-a", first.ref)
                assert type(loaded.value) is CommitSource
                assert loaded.value == commit

    asyncio.run(scenario())


def test_reusing_a_stable_source_identity_with_different_payload_is_a_conflict() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            original = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="original",
            )
            changed = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="changed",
            )
            async with profile.database.transaction() as connection:
                await repositories.sources.add(connection, "scope-a", original)
                with pytest.raises(StoredPayloadConflictError):
                    await repositories.sources.add(connection, "scope-a", changed)

    asyncio.run(scenario())


def test_repository_rejects_a_scope_outside_the_relational_identity_baseline() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            source = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="x",
            )
            async with profile.database.transaction() as connection:
                with pytest.raises(InvalidRepositoryArgumentError) as error:
                    await repositories.sources.add(connection, "x" * 257, source)
                assert error.value.field == "scope_id"

    asyncio.run(scenario())


def test_source_journal_allocator_serializes_concurrent_scope_writes_without_idempotency_gaps(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent-sources.db'}",
            busy_timeout_ms=10_000,
        )
        repository = SourceRepository(SOURCE_ADAPTERS)
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:

            async def add(scope_id: str, source: NoteSource):
                async with profile.database.transaction() as connection:
                    return await repository.add(connection, scope_id, source)

            unique = await asyncio.gather(
                *(
                    add(
                        "scope-concurrent",
                        NoteSource(
                            name=f"note-{index}",
                            materialization=SourceMaterialization.CAPTURED,
                            body=f"body-{index}",
                        ),
                    )
                    for index in range(16)
                )
            )
            assert sorted(item.journal_position for item in unique) == list(range(1, 17))

            repeated_source = NoteSource(
                name="same-note",
                materialization=SourceMaterialization.CAPTURED,
                body="same-body",
            )
            repeated = await asyncio.gather(*(add("scope-idempotent", repeated_source) for _ in range(8)))
            assert {item.journal_position for item in repeated} == {1}
            following = await add(
                "scope-idempotent",
                NoteSource(
                    name="following-note",
                    materialization=SourceMaterialization.CAPTURED,
                    body="following-body",
                ),
            )
            assert following.journal_position == 2

    asyncio.run(scenario())
