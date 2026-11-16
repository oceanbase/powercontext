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
import rfc8785
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


def _working_note(name: str, *, expires_at: str | int | None = None) -> ContentSource:
    metadata = {"kind": "working_note", "session_id": "session-1", "note_kind": "validation"}
    if expires_at is not None:
        metadata["expires_at"] = expires_at
    return ContentSource(
        name=name,
        materialization=SourceMaterialization.CAPTURED,
        content=name,
        metadata=metadata,
        working_note=True,
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


def test_legacy_metadata_only_working_note_marker_remains_ordinary_source() -> None:
    async def scenario() -> None:
        repository = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        access = GenerationSourceAccess(repository)
        legacy = ContentSource(
            name="legacy",
            materialization=SourceMaterialization.CAPTURED,
            content="Historical ordinary content.",
            metadata={"kind": "working_note"},
        )
        async with repository_profile() as (profile, _repositories):
            async with profile.database.transaction() as connection:
                stored = await repository.add(connection, "scope-a", legacy)
                legacy_value = legacy.model_dump(mode="json", by_alias=True)
                legacy_value.pop("working_note")
                await connection.execute(
                    update(SOURCES_TABLE)
                    .where(
                        SOURCES_TABLE.c.scope_id == "scope-a",
                        SOURCES_TABLE.c.source_type == stored.ref.source_type,
                        SOURCES_TABLE.c.source_id == stored.ref.source_id,
                    )
                    .values(
                        payload=rfc8785.dumps({
                            "encoding": "powercontext-source-v1",
                            "representation": "native",
                            "value": legacy_value,
                        })
                    )
                )
                window = await access.list_window_for_generation(
                    connection,
                    "scope-a",
                    after=0,
                    through=stored.journal_position,
                )

            # Historical envelopes do not record the server save time.
            assert window == (stored.model_copy(update={"saved_at": None}),)

    asyncio.run(scenario())


@pytest.mark.parametrize("expires_at", ["2000-01-01T00:00:00+00:00", "not-a-timestamp", 1])
def test_expired_or_malformed_working_notes_are_not_explicit_generation_evidence(
    expires_at: str | int,
) -> None:
    async def scenario() -> None:
        repository = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        access = GenerationSourceAccess(repository)
        async with repository_profile() as (profile, _repositories):
            async with profile.database.transaction() as connection:
                note = await repository.add(
                    connection,
                    "scope-a",
                    _working_note("unavailable-note", expires_at=expires_at),
                )

                with pytest.raises(SourceNotEligibleError) as error:
                    await access.require_for_generation(
                        connection,
                        "scope-a",
                        (note.ref,),
                        allow_working_notes=True,
                    )

            assert error.value.source == note.ref

    asyncio.run(scenario())


def test_unexpired_working_note_remains_explicit_generation_evidence() -> None:
    async def scenario() -> None:
        repository = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        access = GenerationSourceAccess(repository)
        async with repository_profile() as (profile, _repositories):
            async with profile.database.transaction() as connection:
                note = await repository.add(
                    connection,
                    "scope-a",
                    _working_note("active-note", expires_at="9999-01-01T00:00:00+00:00"),
                )

                resolved = await access.require_for_generation(
                    connection,
                    "scope-a",
                    (note.ref,),
                    allow_working_notes=True,
                )

            assert resolved == (note,)

    asyncio.run(scenario())


def test_working_notes_are_skipped_by_automatic_generation_but_remain_explicit_evidence() -> None:
    async def scenario() -> None:
        repository = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        access = GenerationSourceAccess(repository)
        async with repository_profile() as (profile, _repositories):
            async with profile.database.transaction() as connection:
                note = await repository.add(connection, "scope-a", _working_note("note"))

                window = await access.list_window_for_generation(
                    connection,
                    "scope-a",
                    after=0,
                    through=note.journal_position,
                )
                with pytest.raises(SourceNotEligibleError):
                    await access.require_for_generation(connection, "scope-a", (note.ref,))
                explicit = await access.require_for_generation(
                    connection,
                    "scope-a",
                    (note.ref,),
                    allow_working_notes=True,
                )

            assert window == ()
            assert explicit == (note,)

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
