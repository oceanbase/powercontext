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

from powercontext.builtin.artifacts.memory import (
    InvalidMemoryCitationError,
    Memory,
    MemoryChange,
    MemoryCommit,
    MemoryContent,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryHit,
    MemoryManifest,
    MemoryManifestEntry,
    MemoryProjection,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.canonical import entry_content_hash, memory_content_hash
from powercontext.builtin.artifacts.memory.errors import MemoryBackendConfigurationError
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import MEMORY_ENTRY_HEADS_TABLE, MEMORY_ENTRY_VERSIONS_TABLE
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.errors import ArtifactNotFoundError


def _entry(
    *,
    artifact_id: str = "memory",
    entry_id: str = "preference",
    entry_version_id: str = "preference-v1",
    version: int = 1,
    previous_version_id: str | None = None,
    text: str = "User prefers black tea.",
    content_hash: str | None = None,
    created_in_revision: int = 1,
) -> MemoryEntryVersion:
    return MemoryEntryVersion(
        memory_artifact_id=artifact_id,
        entry_id=entry_id,
        entry_version_id=entry_version_id,
        version=version,
        previous_version_id=previous_version_id,
        kind="preference",
        text=text,
        entry_content_hash=(
            entry_content_hash(kind="preference", text=text, source_refs=(), artifact_refs=())
            if content_hash is None
            else content_hash
        ),
        created_in_revision=created_in_revision,
    )


def _initial_commit(*entries: MemoryEntryVersion) -> MemoryCommit:
    ordered = tuple(sorted(entries, key=lambda entry: entry.entry_id.encode("utf-8")))
    content = MemoryContent(
        manifest=MemoryManifest(
            entries=tuple(
                MemoryManifestEntry(
                    entry_id=entry.entry_id,
                    entry_version_id=entry.entry_version_id,
                    entry_content_hash=entry.entry_content_hash,
                    state="active",
                )
                for entry in ordered
            )
        ),
        changes=tuple(
            MemoryChange(
                op="add",
                entry_id=entry.entry_id,
                from_entry_version_id=None,
                to_entry_version_id=entry.entry_version_id,
            )
            for entry in ordered
        ),
    )
    memory = Memory(artifact_id=ordered[0].memory_artifact_id, revision=1, content=content)
    return MemoryCommit(
        base=None,
        memory=memory,
        content_hash=memory_content_hash(content),
        entry_versions=ordered,
        projections=tuple(
            MemoryProjection(entry_version=entry, searchable_text=analyze_text(entry.text)) for entry in ordered
        ),
    )


def _revision_commit(
    base: Memory,
    current: tuple[MemoryEntryVersion, ...],
    replacement: MemoryEntryVersion,
) -> MemoryCommit:
    versions = {entry.entry_id: entry for entry in current}
    previous = versions[replacement.entry_id]
    versions[replacement.entry_id] = replacement
    manifest = {item.entry_id: item for item in base.content.manifest.entries}
    manifest[replacement.entry_id] = MemoryManifestEntry(
        entry_id=replacement.entry_id,
        entry_version_id=replacement.entry_version_id,
        entry_content_hash=replacement.entry_content_hash,
        state=manifest[replacement.entry_id].state,
    )
    content = MemoryContent(
        manifest=MemoryManifest(
            entries=tuple(sorted(manifest.values(), key=lambda item: item.entry_id.encode("utf-8")))
        ),
        changes=(
            MemoryChange(
                op="revise",
                entry_id=replacement.entry_id,
                from_entry_version_id=previous.entry_version_id,
                to_entry_version_id=replacement.entry_version_id,
            ),
        ),
    )
    memory = Memory(artifact_id=base.artifact_id, revision=base.revision + 1, content=content)
    return MemoryCommit(
        base=base,
        memory=memory,
        content_hash=memory_content_hash(content),
        entry_versions=(replacement,),
        projections=tuple(
            MemoryProjection(entry_version=entry, searchable_text=analyze_text(entry.text))
            for entry in sorted(versions.values(), key=lambda entry: entry.entry_id.encode("utf-8"))
        ),
    )


def _backend(contexts: RelationalContexts, scope_id: str) -> RelationalMemoryBackend:
    return RelationalMemoryBackend(
        database=contexts.database,
        scope_id=scope_id,
        artifacts=contexts.repositories.artifacts,
        index=contexts.index,
    )


async def _commit(backend: RelationalMemoryBackend, value: MemoryCommit) -> Memory:
    async with backend.begin() as unit_of_work:
        return await unit_of_work.commit(value)


def test_memory_commit_rejects_incomplete_or_mismatched_revision_atomically() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            valid = _initial_commit(_entry())
            original = valid.entry_versions[0]
            fake_predecessor = original.model_copy(update={"previous_version_id": "does-not-exist"})
            tampered = original.model_copy(update={"text": "tampered searchable body."})
            extra = _entry(entry_id="extra", entry_version_id="extra-v1")
            mismatched_item = valid.memory.content.manifest.entries[0].model_copy(
                update={"entry_content_hash": "0" * 64}
            )
            mismatched_content = valid.memory.content.model_copy(
                update={"manifest": MemoryManifest(entries=(mismatched_item,))}
            )
            invalid = (
                valid.model_copy(update={"entry_versions": ()}),
                valid.model_copy(update={"entry_versions": (original, original)}),
                valid.model_copy(update={"entry_versions": (original, extra)}),
                valid.model_copy(update={"projections": (valid.projections[0], valid.projections[0])}),
                valid.model_copy(
                    update={
                        "entry_versions": (fake_predecessor,),
                        "projections": (
                            MemoryProjection(
                                entry_version=fake_predecessor,
                                searchable_text=analyze_text(fake_predecessor.text),
                            ),
                        ),
                    }
                ),
                valid.model_copy(
                    update={
                        "entry_versions": (tampered,),
                        "projections": (
                            MemoryProjection(entry_version=tampered, searchable_text=analyze_text(tampered.text)),
                        ),
                    }
                ),
                valid.model_copy(
                    update={
                        "projections": (valid.projections[0].model_copy(update={"searchable_text": "not canonical"}),)
                    }
                ),
                valid.model_copy(
                    update={
                        "projections": (
                            valid.projections[0].model_copy(
                                update={"embedding": (1.0,), "embedding_content_hash": "0" * 64}
                            ),
                        )
                    }
                ),
                valid.model_copy(
                    update={
                        "memory": valid.memory.model_copy(update={"content": mismatched_content}),
                        "content_hash": memory_content_hash(mismatched_content),
                    }
                ),
            )

            for index, candidate in enumerate(invalid):
                backend = _backend(contexts, f"invalid-{index}")
                with pytest.raises(MemoryBackendConfigurationError):
                    await _commit(backend, candidate)
                with pytest.raises(ArtifactNotFoundError):
                    await backend.latest("memory")

    asyncio.run(scenario())


def test_memory_commit_requires_the_direct_same_entry_predecessor() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            first_a = _entry(entry_id="entry-a", entry_version_id="entry-a-v1")
            first_b = _entry(entry_id="entry-b", entry_version_id="entry-b-v1", text="User prefers green tea.")
            initial = _initial_commit(first_a, first_b)

            for scope_id, predecessor in (("missing", "does-not-exist"), ("cross-entry", "entry-b-v1")):
                backend = _backend(contexts, scope_id)
                base = await _commit(backend, initial)
                replacement = _entry(
                    entry_id="entry-a",
                    entry_version_id="entry-a-v2",
                    version=2,
                    previous_version_id=predecessor,
                    text="User prefers oolong tea.",
                    created_in_revision=2,
                )
                with pytest.raises(MemoryBackendConfigurationError):
                    await _commit(backend, _revision_commit(base, (first_a, first_b), replacement))
                assert await backend.latest("memory") == base

            backend = _backend(contexts, "cross-memory")
            base = await _commit(backend, initial)
            await _commit(
                backend,
                _initial_commit(
                    _entry(
                        artifact_id="other-memory",
                        entry_id="other-entry",
                        entry_version_id="other-entry-v1",
                    )
                ),
            )
            cross_memory = _entry(
                entry_id="entry-a",
                entry_version_id="entry-a-v2",
                version=2,
                previous_version_id="other-entry-v1",
                text="User prefers oolong tea.",
                created_in_revision=2,
            )
            with pytest.raises(MemoryBackendConfigurationError):
                await _commit(backend, _revision_commit(base, (first_a, first_b), cross_memory))
            assert await backend.latest("memory") == base

            backend = _backend(contexts, "duplicate-version-id")
            await _commit(backend, initial)
            collision = _initial_commit(
                _entry(
                    artifact_id="other-memory",
                    entry_id="other-entry",
                    entry_version_id="entry-a-v1",
                )
            )
            with pytest.raises(MemoryBackendConfigurationError):
                await _commit(backend, collision)
            with pytest.raises(ArtifactNotFoundError):
                await backend.latest("other-memory")

            backend = _backend(contexts, "skipped")
            base = await _commit(backend, initial)
            second_a = _entry(
                entry_id="entry-a",
                entry_version_id="entry-a-v2",
                version=2,
                previous_version_id="entry-a-v1",
                text="User prefers oolong tea.",
                created_in_revision=2,
            )
            second = await _commit(backend, _revision_commit(base, (first_a, first_b), second_a))
            skipped = _entry(
                entry_id="entry-a",
                entry_version_id="entry-a-v3",
                version=3,
                previous_version_id="entry-a-v1",
                text="User prefers white tea.",
                created_in_revision=3,
            )
            with pytest.raises(MemoryBackendConfigurationError):
                await _commit(backend, _revision_commit(second, (second_a, first_b), skipped))
            assert await backend.latest("memory") == second

    asyncio.run(scenario())


def test_memory_commit_rejects_corrupted_history_already_referenced_by_base() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            backend = _backend(contexts, "corrupted-base-history")
            original = _entry()
            base = await _commit(backend, _initial_commit(original))
            second = _entry(
                entry_version_id="preference-v2",
                version=2,
                previous_version_id=original.entry_version_id,
                text="User prefers oolong tea.",
                created_in_revision=2,
            )
            head = await _commit(backend, _revision_commit(base, (original,), second))
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    MEMORY_ENTRY_VERSIONS_TABLE
                    .update()
                    .where(
                        MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == "corrupted-base-history",
                        MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id == base.artifact_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id == "preference-v1",
                    )
                    .values(previous_version_id="does-not-exist")
                )

            service = MemoryService(backend=backend)
            with pytest.raises(MemoryBackendConfigurationError):
                await service.remember(
                    memory=head,
                    entries=(MemoryEntryInput(kind="fact", text="An unrelated fact."),),
                    mode="append",
                )
            assert await backend.latest(base.artifact_id) == head

    asyncio.run(scenario())


def test_memory_commit_rejects_semantic_revision_of_an_inactive_entry() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            backend = _backend(contexts, "inactive-revision")
            original = _entry()
            base = await _commit(backend, _initial_commit(original))
            service = MemoryService(backend=backend)
            inactive = await service.forget(base, entries=(original,), reason="paused")
            replacement = _entry(
                entry_version_id="preference-v2",
                version=2,
                previous_version_id=original.entry_version_id,
                text="User prefers green tea.",
                created_in_revision=3,
            )
            candidate = _revision_commit(inactive, (original,), replacement).model_copy(update={"projections": ()})

            with pytest.raises(MemoryBackendConfigurationError):
                await _commit(backend, candidate)
            assert await backend.latest(base.artifact_id) == inactive

    asyncio.run(scenario())


def test_corrupted_authoritative_entry_is_rejected_by_entries_search_and_expand() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            for scope_id, values in (
                ("body-corruption", {"text": "tampered searchable body."}),
                ("declared-hash-corruption", {"entry_content_hash": "0" * 64}),
            ):
                backend = _backend(contexts, scope_id)
                memory = await _commit(backend, _initial_commit(_entry()))
                async with contexts.database.transaction() as connection:
                    await connection.execute(
                        MEMORY_ENTRY_VERSIONS_TABLE
                        .update()
                        .where(
                            MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == scope_id,
                            MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id == memory.artifact_id,
                        )
                        .values(**values)
                    )
                service = MemoryService(backend=backend)
                hit = MemoryHit(
                    memory_ref=memory.as_ref(),
                    entry_id="preference",
                    entry_version_id="preference-v1",
                    text="User prefers black tea.",
                    score=1.0,
                    matched_by=("fts",),
                )

                with pytest.raises(InvalidMemoryCitationError):
                    await service.entries(memory)
                with pytest.raises(InvalidMemoryCitationError):
                    await service.search("black tea", memories=(memory,), mode="fts")
                with pytest.raises(InvalidMemoryCitationError):
                    await service.expand((hit,))

    asyncio.run(scenario())


def test_search_rejects_noncanonical_rebuildable_projection() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            backend = _backend(contexts, "projection-corruption")
            memory = await _commit(backend, _initial_commit(_entry()))
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    MEMORY_ENTRY_HEADS_TABLE
                    .update()
                    .where(
                        MEMORY_ENTRY_HEADS_TABLE.c.scope_id == "projection-corruption",
                        MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id == memory.artifact_id,
                    )
                    .values(searchable_text="tampered projection")
                )

            service = MemoryService(backend=backend)
            assert tuple(entry.text for entry in await service.entries(memory)) == ("User prefers black tea.",)
            with pytest.raises(InvalidMemoryCitationError):
                await service.search("black tea", memories=(memory,), mode="fts")

    asyncio.run(scenario())
