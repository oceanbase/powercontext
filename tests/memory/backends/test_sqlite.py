from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import apsw
import pytest

from powercontext import RevisionConflictError
from powercontext.errors import MemoryBackendConfigurationError
from powercontext.memory import MemoryCapabilities, MemoryEntryInput, MemoryService
from powercontext.memory.backends import SQLiteMemoryBackend
from powercontext.memory.canonical import memory_content_hash
from tests.memory import current_entry
from tests.memory.backends.contract import ContractIds, exercise_memory_backend, prepare_memory_commit


def scalar(connection: apsw.Connection, statement: str, bindings: tuple[object, ...] = ()) -> object:
    row = connection.execute(statement, bindings).fetchone()
    assert row is not None
    return row[0]


def test_sqlite_backend_passes_memory_domain_and_fts_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = SQLiteMemoryBackend(tmp_path / "memory.db")
        await backend.initialize()
        try:
            assert await backend.capabilities() == MemoryCapabilities(fts=True)
            assert await backend.foreign_keys_enabled()
            await exercise_memory_backend(backend)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_rejects_unsupported_runtime_and_schema_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def old_runtime_scenario() -> None:
        monkeypatch.setattr(apsw, "sqlitelibversion", lambda: "3.37.2")
        backend = SQLiteMemoryBackend(tmp_path / "old-runtime.db")
        with pytest.raises(MemoryBackendConfigurationError, match=r"3\.38\.0"):
            await backend.initialize()

    asyncio.run(old_runtime_scenario())

    database = tmp_path / "future-schema.db"
    connection = apsw.Connection(str(database))
    connection.execute("CREATE TABLE powercontext_schema (version INTEGER NOT NULL PRIMARY KEY)")
    connection.execute("INSERT INTO powercontext_schema (version) VALUES (2)")
    connection.close()

    async def future_schema_scenario() -> None:
        monkeypatch.undo()
        backend = SQLiteMemoryBackend(database)
        with pytest.raises(MemoryBackendConfigurationError, match="schema version"):
            await backend.initialize()

    asyncio.run(future_schema_scenario())


def test_sqlite_backend_cas_rejects_stale_commit_without_orphans(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        first_backend = SQLiteMemoryBackend(database)
        second_backend = SQLiteMemoryBackend(database)
        await first_backend.initialize()
        await second_backend.initialize()
        try:
            service = MemoryService(backend=first_backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="decision", text="Use SQLite FTS."),),
                mode="append",
            )
            assert first is not None
            entry_id = first.content.manifest.entries[0].entry_id
            winning = await prepare_memory_commit(
                first_backend,
                first,
                MemoryEntryInput(
                    entry=await current_entry(service, first, entry_id), kind="decision", text="Use SQLite FTS5."
                ),
                namespace="winning",
            )
            stale = await prepare_memory_commit(
                second_backend,
                first,
                MemoryEntryInput(
                    entry=await current_entry(service, first, entry_id),
                    kind="decision",
                    text="Use another lexical index.",
                ),
                namespace="stale",
            )

            async with first_backend.begin() as transaction:
                await transaction.commit(winning)
            with pytest.raises(RevisionConflictError):
                async with second_backend.begin() as transaction:
                    await transaction.commit(stale)

            connection = apsw.Connection(str(database))
            try:
                assert scalar(connection, "SELECT count(*) FROM artifact_revisions") == 2
                assert scalar(connection, "SELECT count(*) FROM memory_entry_versions") == 2
                assert (
                    scalar(
                        connection,
                        "SELECT count(*) FROM memory_entry_versions WHERE entry_version_id = ?",
                        (stale.entry_versions[0].entry_version_id,),
                    )
                    == 0
                )
            finally:
                connection.close()
        finally:
            await first_backend.close()
            await second_backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_rolls_back_authoritative_rows_when_projection_write_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(database)
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="decision", text="Keep the projection atomic."),),
                mode="append",
            )
            assert first is not None
            entry_id = first.content.manifest.entries[0].entry_id
            pending = await prepare_memory_commit(
                backend,
                first,
                MemoryEntryInput(
                    entry=await current_entry(service, first, entry_id),
                    kind="decision",
                    text="Keep every projection atomic.",
                ),
            )
            connection = apsw.Connection(str(database))
            connection.execute("DROP TABLE memory_entry_search_fts")
            connection.close()

            with pytest.raises(apsw.SQLError, match="memory_entry_search_fts"):
                async with backend.begin() as transaction:
                    await transaction.commit(pending)

            connection = apsw.Connection(str(database))
            try:
                assert scalar(connection, "SELECT count(*) FROM artifact_revisions") == 1
                assert scalar(connection, "SELECT count(*) FROM memory_entry_versions") == 1
                assert (
                    scalar(
                        connection,
                        "SELECT revision FROM artifact_heads WHERE artifact_id = ?",
                        (first.artifact_id,),
                    )
                    == 1
                )
            finally:
                connection.close()
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_keeps_only_active_entries_in_head_and_fts_projections(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(database)
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Visible alpha token."),
                    MemoryEntryInput(kind="fact", text="Hidden omega token."),
                ),
                mode="append",
            )
            assert first is not None
            hidden_id = first.content.manifest.entries[1].entry_id
            forgotten = await service.forget(first, entries=(await current_entry(service, first, hidden_id),))
            result = await service.search("omega", memories=(forgotten,), mode="fts")
            assert result.hits == ()

            connection = apsw.Connection(str(database))
            try:
                assert scalar(connection, "SELECT count(*) FROM memory_entry_heads") == 1
                assert scalar(connection, "SELECT count(*) FROM memory_entry_search_fts") == 1
                assert (
                    scalar(
                        connection,
                        "SELECT count(*) FROM memory_entry_heads WHERE entry_id = ?",
                        (hidden_id,),
                    )
                    == 0
                )
            finally:
                connection.close()
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_loads_active_projections_for_exact_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = SQLiteMemoryBackend(tmp_path / "memory.db")
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha kept."),
                    MemoryEntryInput(kind="fact", text="Beta removed."),
                ),
                mode="append",
            )
            assert memory is not None
            removed_id = memory.content.manifest.entries[1].entry_id
            forgotten = await service.forget(memory, entries=(await current_entry(service, memory, removed_id),))
            loaded = await backend.projections(forgotten.ref)
            assert len(loaded) == 1
            assert loaded[0].entry_version.text == "Alpha kept."
            assert loaded[0].embedding is None
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_rebuilds_fts_projection_from_authoritative_heads(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(database)
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha projection recovery."),
                    MemoryEntryInput(kind="fact", text="Inactive projection recovery."),
                ),
                mode="append",
            )
            assert memory is not None
            inactive_id = memory.content.manifest.entries[1].entry_id
            memory = await service.forget(memory, entries=(await current_entry(service, memory, inactive_id),))

            connection = apsw.Connection(str(database))
            connection.execute("DELETE FROM memory_entry_search_fts")
            connection.execute("DELETE FROM memory_entry_heads")
            connection.close()
            assert (await service.search("alpha", memories=(memory,), mode="fts")).hits == ()

            await backend.rebuild_projections()

            result = await service.search("alpha", memories=(memory,), mode="fts")
            assert tuple(hit.text for hit in result.hits) == ("Alpha projection recovery.",)
            connection = apsw.Connection(str(database))
            try:
                assert scalar(connection, "SELECT count(*) FROM memory_entry_heads") == 1
                assert scalar(connection, "SELECT count(*) FROM memory_entry_search_fts") == 1
                assert (
                    scalar(
                        connection,
                        "SELECT count(*) FROM memory_entry_heads WHERE entry_id = ?",
                        (inactive_id,),
                    )
                    == 0
                )
            finally:
                connection.close()
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_detects_authoritative_entry_tampering(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(database)
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Immutable body."),),
                mode="append",
            )
            assert first is not None
            connection = apsw.Connection(str(database))
            connection.execute(
                "UPDATE memory_entry_versions SET text = 'tampered' WHERE memory_artifact_id = ?",
                (first.artifact_id,),
            )
            connection.close()

            with pytest.raises(MemoryBackendConfigurationError, match="hash"):
                await backend.entries(first.ref)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_rejects_malformed_stored_entry_refs(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(database)
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            memory = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Immutable body."),),
                mode="append",
            )
            assert memory is not None
            connection = apsw.Connection(str(database))
            connection.execute(
                "UPDATE memory_entry_versions SET source_refs = '{}' WHERE memory_artifact_id = ?",
                (memory.artifact_id,),
            )
            connection.close()

            with pytest.raises(MemoryBackendConfigurationError, match="array"):
                await backend.entries(memory.ref)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_rejects_malformed_commit_missing_manifest_change(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = SQLiteMemoryBackend(tmp_path / "memory.db")
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Original entry."),),
                mode="append",
            )
            assert first is not None
            pending = await prepare_memory_commit(
                backend,
                first,
                MemoryEntryInput(
                    entry=await current_entry(service, first),
                    kind="fact",
                    text="Revised entry.",
                ),
            )
            content = replace(pending.memory.content, changes=())
            malformed = replace(
                pending,
                memory=replace(pending.memory, content=content),
                content_hash=memory_content_hash(content),
            )

            with pytest.raises(MemoryBackendConfigurationError, match="changes"):
                async with backend.begin() as transaction:
                    await transaction.commit(malformed)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_rejects_malformed_commit_with_unreferenced_new_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = SQLiteMemoryBackend(tmp_path / "memory.db")
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Original entry."),),
                mode="append",
            )
            assert first is not None
            pending = await prepare_memory_commit(
                backend,
                first,
                MemoryEntryInput(
                    entry=await current_entry(service, first),
                    kind="fact",
                    text="Revised entry.",
                ),
            )
            extra_version = replace(
                pending.entry_versions[0],
                entry_id="unreferenced-entry",
                entry_version_id="unreferenced-version",
                version=1,
                previous_version_id=None,
            )
            malformed = replace(pending, entry_versions=(*pending.entry_versions, extra_version))

            with pytest.raises(MemoryBackendConfigurationError, match="entry versions"):
                async with backend.begin() as transaction:
                    await transaction.commit(malformed)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_backend_rejects_inactive_new_entry(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = SQLiteMemoryBackend(tmp_path / "memory.db")
        await backend.initialize()
        try:
            service = MemoryService(backend=backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Original entry."),),
                mode="append",
            )
            assert first is not None
            pending = await prepare_memory_commit(
                backend,
                first,
                MemoryEntryInput(kind="fact", text="Inactive new entry."),
            )
            new_entry_id = pending.entry_versions[0].entry_id
            manifest = replace(
                pending.memory.content.manifest,
                entries=tuple(
                    replace(item, state="inactive") if item.entry_id == new_entry_id else item
                    for item in pending.memory.content.manifest.entries
                ),
            )
            content = replace(pending.memory.content, manifest=manifest)
            malformed = replace(
                pending,
                memory=replace(pending.memory, content=content),
                content_hash=memory_content_hash(content),
                projections=tuple(
                    projection
                    for projection in pending.projections
                    if projection.entry_version.entry_id != new_entry_id
                ),
            )

            with pytest.raises(MemoryBackendConfigurationError, match="changes"):
                async with backend.begin() as transaction:
                    await transaction.commit(malformed)
        finally:
            await backend.close()

    asyncio.run(scenario())
