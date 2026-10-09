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
import json
from dataclasses import replace

import pytest
from pydantic import BaseModel, JsonValue

from powercontext.builtin.artifacts.prompt import PROMPT_KEYS, Prompt, PromptError, PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.family_management import FamilyManagementWriterRegistry, PromptManagementWriter
from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.rollback import merged_source_revision
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE, BUILTIN_TABLES
from powercontext.builtin.records import (
    ArtifactRevisionActor,
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    BaseValueConflictError,
    InvalidBaseAccessRequestError,
    InvalidCursorError,
)
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER
from powercontext.limits import MAX_ARTIFACT_REVISION_ACTOR_LENGTH


def _records(profile: SQLiteProfile, registry: PromptRegistry | None = None) -> RelationalRecordService:
    sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
    artifacts = ArtifactRepository((Prompt,), sources=sources)
    selected = registry or PromptRegistry(builtin_prompt_definitions(), supported=frozenset(PROMPT_KEYS))
    return RelationalRecordService(
        profile.database,
        sources,
        artifacts,
        FamilyManagementWriterRegistry((PromptManagementWriter(artifacts, selected),)),
        cursor_secret=b"prompt-test-cursor-secret",
    )


def _content(instructions: str = "Keep release preferences.") -> dict[str, JsonValue]:
    return {
        "schema_version": "powercontext.prompt.v1",
        "mode": "custom",
        "instructions": instructions,
        "demonstrations": [
            {
                "input": {"evidence": [], "current_entries": []},
                "expected_output": {"candidates": []},
            }
        ],
    }


def test_prompt_scope_isolation_and_immutable_rollback() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records = _records(profile)
            first = await records.create_artifact(
                "scope-a", "prompt", ArtifactWrite(content=_content(), prompt_key="memory.extract")
            )
            await records.create_artifact(
                "scope-b",
                "prompt",
                ArtifactWrite(content=_content("Keep personal preferences."), prompt_key="memory.extract"),
            )
            assert (first.artifact_id, first.revision) == ("memory.extract", 1)
            with pytest.raises(BaseValueConflictError):
                await records.create_artifact(
                    "scope-a", "prompt", ArtifactWrite(content=_content(), prompt_key="memory.extract")
                )
            original = await records.get_artifact_revision("scope-a", "prompt", "memory.extract", 1)
            updated = await records.replace_artifact(
                "scope-a",
                "prompt",
                "memory.extract",
                '"revision:1"',
                ArtifactWrite(content=_content("Keep decisions.")),
            )
            with pytest.raises(ArtifactRevisionPreconditionError):
                await records.replace_artifact(
                    "scope-a", "prompt", "memory.extract", '"revision:1"', ArtifactWrite(content=original.content)
                )
            restored = await records.replace_artifact(
                "scope-a", "prompt", "memory.extract", '"revision:2"', ArtifactWrite(content=original.content)
            )
            assert restored.revision == 3
            assert restored.content_digest == original.content_digest
            assert updated.content_digest != original.content_digest
            assert await records.get_artifact_revision("scope-a", "prompt", "memory.extract", 1) == original
            other_scope = await records.get_artifact("scope-b", "prompt", "memory.extract")
            assert other_scope.revision == 1
            assert other_scope.content["instructions"] == "Keep personal preferences."
            history = await records.list_artifact_revisions(
                "scope-a", "prompt", "memory.extract", limit=10, cursor=None
            )
            assert [item.revision for item in history.items] == [3, 2, 1]
            assert history.next_cursor is None
            assert all("content" not in item.model_dump() for item in history.items)

    asyncio.run(scenario())


def test_revision_cursor_freezes_snapshot_and_cannot_cross_resource_boundaries() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records = _records(profile)
            await records.create_artifact(
                "scope-a", "prompt", ArtifactWrite(content=_content(), prompt_key="memory.extract")
            )
            for revision in range(1, 4):
                await records.replace_artifact(
                    "scope-a",
                    "prompt",
                    "memory.extract",
                    f'"revision:{revision}"',
                    ArtifactWrite(content=_content(f"Release preference {revision + 1}.")),
                )
            first = await records.list_artifact_revisions("scope-a", "prompt", "memory.extract", limit=2, cursor=None)
            assert [item.revision for item in first.items] == [4, 3]
            assert first.next_cursor is not None
            await records.replace_artifact(
                "scope-a",
                "prompt",
                "memory.extract",
                '"revision:4"',
                ArtifactWrite(content=_content("Newest revision.")),
            )
            second = await records.list_artifact_revisions(
                "scope-a", "prompt", "memory.extract", limit=2, cursor=first.next_cursor
            )
            assert [item.revision for item in second.items] == [2, 1]
            assert second.next_cursor is None
            for scope, key in (("scope-b", "memory.extract"), ("scope-a", "skill.generate")):
                with pytest.raises(InvalidCursorError):
                    await records.list_artifact_revisions(scope, "prompt", key, limit=2, cursor=first.next_cursor)
            with pytest.raises(InvalidCursorError):
                await records.list_artifact_revisions(
                    "scope-a", "prompt", "memory.extract", limit=2, cursor=first.next_cursor + "tampered"
                )

    asyncio.run(scenario())


def test_definition_change_does_not_hide_history_or_partially_commit_a_failed_rollback() -> None:
    class IncompatibleInput(BaseModel):
        required_new_field: str

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records = _records(profile)
            await records.create_artifact(
                "scope-a", "prompt", ArtifactWrite(content=_content(), prompt_key="memory.extract")
            )
            original = await records.get_artifact("scope-a", "prompt", "memory.extract")
            definitions = builtin_prompt_definitions()
            changed = (replace(definitions[0], input_type=IncompatibleInput), *definitions[1:])
            upgraded = _records(profile, PromptRegistry(changed, supported=frozenset(PROMPT_KEYS)))
            assert await upgraded.get_artifact_revision("scope-a", "prompt", "memory.extract", 1) == original
            with pytest.raises(PromptError) as failure:
                await upgraded.replace_artifact(
                    "scope-a", "prompt", "memory.extract", '"revision:1"', ArtifactWrite(content=original.content)
                )
            assert failure.value.code == "prompt_definition_incompatible"
            assert await upgraded.get_artifact("scope-a", "prompt", "memory.extract") == original
            summary = await upgraded.list_scopes(limit=10, cursor=None)
            assert summary.items[0].source_count == 1
            recovered = await upgraded.replace_artifact(
                "scope-a",
                "prompt",
                "memory.extract",
                '"revision:1"',
                ArtifactWrite(
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "auto",
                        "instructions": "",
                        "demonstrations": [],
                    }
                ),
            )
            assert recovered.revision == 2
            assert recovered.content["mode"] == "auto"

    asyncio.run(scenario())


def test_prompt_rollback_records_its_source_and_rejects_an_incomplete_restore() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records = _records(profile)
            await records.create_artifact(
                "scope-a",
                "prompt",
                ArtifactWrite(content=_content("Keep the approved prompt."), prompt_key="memory.extract"),
            )
            first = await records.get_artifact("scope-a", "prompt", "memory.extract")
            updated = await records.replace_artifact(
                "scope-a",
                "prompt",
                "memory.extract",
                '"revision:1"',
                ArtifactWrite(content=_content("Keep a later prompt.")),
            )
            assert updated.created_at is not None
            assert updated.created_by is None
            assert updated.restored_from_revision is None
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.replace_artifact(
                    "scope-a",
                    "prompt",
                    "memory.extract",
                    '"revision:2"',
                    ArtifactWrite(content=first.content, restored_from_revision=1),
                )
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.replace_artifact(
                    "scope-a",
                    "prompt",
                    "memory.extract",
                    '"revision:2"',
                    ArtifactWrite(content=first.content, restored_from_revision=2, reason="Restore the current head"),
                )
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.replace_artifact(
                    "scope-a",
                    "prompt",
                    "memory.extract",
                    '"revision:2"',
                    ArtifactWrite(
                        content=_content("Different text."),
                        restored_from_revision=1,
                        reason="Restore the approved prompt",
                    ),
                )
            restored = await records.replace_artifact(
                "scope-a",
                "prompt",
                "memory.extract",
                '"revision:2"',
                ArtifactWrite(
                    content=first.content,
                    restored_from_revision=1,
                    reason="  Restore the approved prompt  ",
                ),
                actor_type="user",
                actor_id="operator",
            )
            assert restored.revision == 3
            assert restored.content_digest == first.content_digest
            assert restored.restored_from_revision == 1
            assert restored.reason == "Restore the approved prompt"
            assert restored.created_at is not None
            assert restored.created_by == ArtifactRevisionActor(type="user", id="operator")
            listed = await records.list_artifact_revisions("scope-a", "prompt", "memory.extract", limit=10, cursor=None)
            assert listed.items[0].reason == "Restore the approved prompt"
            assert listed.items[1].created_by is None

    asyncio.run(scenario())


def test_rollback_actor_keeps_a_maximum_length_principal_id() -> None:
    actor_id = "张" * 255
    quoted = "\\" * 255
    from sqlalchemy.dialects.mysql.base import MySQLDialect
    from sqlalchemy.schema import CreateColumn

    ddl = str(CreateColumn(ARTIFACTS_TABLE.c.created_by).compile(dialect=MySQLDialect()))
    assert "VARCHAR(2048)" in ddl
    for value in (actor_id, quoted):
        payload = json.dumps({"type": "user", "id": value}, ensure_ascii=False, separators=(",", ":"))
        assert len(payload) <= MAX_ARTIFACT_REVISION_ACTOR_LENGTH

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records = _records(profile)
            await records.create_artifact(
                "scope-a",
                "prompt",
                ArtifactWrite(content=_content("Keep the approved prompt."), prompt_key="memory.extract"),
            )
            first = await records.get_artifact("scope-a", "prompt", "memory.extract")
            await records.replace_artifact(
                "scope-a",
                "prompt",
                "memory.extract",
                '"revision:1"',
                ArtifactWrite(content=_content("Keep a later prompt.")),
            )
            restored = await records.replace_artifact(
                "scope-a",
                "prompt",
                "memory.extract",
                '"revision:2"',
                ArtifactWrite(content=first.content, restored_from_revision=1, reason="Restore the approved prompt"),
                actor_type="user",
                actor_id=actor_id,
            )
            assert restored.created_by == ArtifactRevisionActor(type="user", id=actor_id)

    asyncio.run(scenario())


class _EmptyCommand(BaseModel):
    pass


def test_memory_replace_rejects_rollback_fields() -> None:
    write = ArtifactWrite(content={"changes": []}, restored_from_revision=1, reason="Restore memory")
    with pytest.raises(InvalidBaseAccessRequestError, match="is not supported for memory"):
        merged_source_revision("memory", write, _EmptyCommand())
