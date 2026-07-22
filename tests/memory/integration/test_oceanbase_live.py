"""Live OceanBase Memory backend verification."""

from __future__ import annotations

# Dynamic identifiers below come only from the adapter's validated ``table_names``.
# ruff: noqa: S608
import asyncio
import os
from dataclasses import dataclass
from uuid import uuid4

import pymysql
import pytest

from powercontext import RevisionConflictError
from powercontext.errors import MemoryBackendConfigurationError
from powercontext.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    EmbeddingVector,
    MemoryCapabilities,
    MemoryEntryInput,
    MemoryService,
)
from powercontext.memory.backends.oceanbase import OceanBaseMemoryBackend
from tests.memory import current_entry
from tests.memory.backends.contract import CaptureIds, ContractIds, exercise_memory_backend, prepare_memory_commit

TEST_PROFILE = EmbeddingProfile(
    profile_id="keyword-v1",
    model="keyword",
    dimension=3,
    distance="l2",
    normalization="none",
)


@dataclass(frozen=True, slots=True)
class LiveConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


class KeywordEmbeddingProvider:
    @property
    def profile(self) -> EmbeddingProfile:
        return TEST_PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> tuple[EmbeddingVector, ...]:
        return tuple(self._embed(text) for text in texts)

    @staticmethod
    def _embed(text: str) -> EmbeddingVector:
        normalized = text.casefold()
        if "alpha" in normalized:
            return (1.0, 0.0, 0.0)
        if "beta" in normalized:
            return (0.0, 1.0, 0.0)
        return (0.0, 0.0, 1.0)


def live_config() -> LiveConfig:
    names = (
        "POWERCONTEXT_OCEANBASE_HOST",
        "POWERCONTEXT_OCEANBASE_PORT",
        "POWERCONTEXT_OCEANBASE_USER",
        "POWERCONTEXT_OCEANBASE_PASSWORD",
        "POWERCONTEXT_OCEANBASE_DATABASE",
    )
    values = {name: os.environ.get(name) for name in names}
    if any(value is None for value in values.values()):
        pytest.skip("OceanBase integration environment is not configured")
    return LiveConfig(
        host=str(values[names[0]]),
        port=int(str(values[names[1]])),
        user=str(values[names[2]]),
        password=str(values[names[3]]),
        database=str(values[names[4]]),
    )


def backend_for(config: LiveConfig, prefix: str) -> OceanBaseMemoryBackend:
    return OceanBaseMemoryBackend(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        embedding_profile=TEST_PROFILE,
        table_prefix=prefix,
    )


def remaining_tables(config: LiveConfig, names: tuple[str, ...]) -> tuple[str, ...]:
    connection = live_connection(config)
    try:
        with connection.cursor() as cursor:
            found: list[str] = []
            for name in names:
                cursor.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (config.database, name),
                )
                found.extend(str(row[0]) for row in cursor.fetchall())
            return tuple(found)
    finally:
        connection.close()


def live_connection(config: LiveConfig) -> pymysql.Connection:
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=True,
    )


def database_scalar(config: LiveConfig, statement: str, bindings: tuple[object, ...] = ()) -> object:
    connection = live_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, bindings)
            row = cursor.fetchone()
            assert row is not None
            return row[0]
    finally:
        connection.close()


def test_live_schema_and_capabilities() -> None:
    async def scenario() -> None:
        config = live_config()
        prefix = f"pc_rfc3_{uuid4().hex[:8]}_"
        backend = backend_for(config, prefix)
        try:
            await backend.initialize()
            assert await backend.capabilities() == MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            )
            assert remaining_tables(config, backend.table_names) == backend.table_names
        finally:
            await backend.drop_schema()
            await backend.close()
        assert remaining_tables(config, backend.table_names) == ()

    asyncio.run(scenario())


def test_live_backend_conformance_and_stale_cas() -> None:
    async def scenario() -> None:
        config = live_config()
        prefix = f"pc_rfc3_{uuid4().hex[:8]}_"
        first_backend = backend_for(config, prefix)
        second_backend = backend_for(config, prefix)
        try:
            await first_backend.initialize()
            await second_backend.initialize()
            head = await exercise_memory_backend(first_backend)
            service = MemoryService(backend=first_backend)
            entry_id = head.content.manifest.entries[0].entry_id
            winning = await prepare_memory_commit(
                first_backend,
                head,
                MemoryEntryInput(
                    entry=await current_entry(service, head, entry_id),
                    kind="constraint",
                    text="Winning update.",
                ),
                namespace="winning",
            )
            stale = await prepare_memory_commit(
                second_backend,
                head,
                MemoryEntryInput(
                    entry=await current_entry(service, head, entry_id),
                    kind="constraint",
                    text="Stale update.",
                ),
                namespace="stale",
            )
            async with first_backend.begin() as transaction:
                await transaction.commit(winning)
            with pytest.raises(RevisionConflictError):
                async with second_backend.begin() as transaction:
                    await transaction.commit(stale)
            assert (
                database_scalar(
                    config,
                    f"SELECT count(*) FROM {first_backend.table_names[1]}",
                )
                == 5
            )
            assert (
                database_scalar(
                    config,
                    f"SELECT count(*) FROM {first_backend.table_names[3]} WHERE entry_version_id = %s",
                    (stale.entry_versions[0].entry_version_id,),
                )
                == 0
            )
        finally:
            await first_backend.drop_schema()
            await first_backend.close()
            await second_backend.close()
        assert remaining_tables(config, first_backend.table_names) == ()

    asyncio.run(scenario())


def test_live_fts_vector_hybrid_and_null_vector_fallback() -> None:
    async def scenario() -> None:
        config = live_config()
        prefix = f"pc_rfc3_{uuid4().hex[:8]}_"
        backend = backend_for(config, prefix)
        try:
            await backend.initialize()
            provider = KeywordEmbeddingProvider()
            service = MemoryService(backend=backend, embedding_provider=provider, id_factory=ContractIds())
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha lexical record."),
                    MemoryEntryInput(kind="fact", text="Beta lexical record."),
                    MemoryEntryInput(kind="constraint", text="修改文档前运行检查。"),
                ),
                mode="append",
            )
            assert memory is not None
            assert (await service.search("alpha", memories=(memory,), mode="fts")).hits[0].text.startswith("Alpha")
            assert (await service.search("alpha", memories=(memory,), mode="vector")).hits[0].text.startswith("Alpha")
            assert (await service.search("文档 检查", memories=(memory,), mode="fts")).hits[
                0
            ].text == "修改文档前运行检查。"
            hybrid = await service.search("alpha", memories=(memory,), mode="hybrid")
            assert hybrid.hits[0].matched_by == ("fts", "vector")
            beta_id = memory.content.manifest.entries[1].entry_id
            forgotten = await service.forget(memory, entries=(await current_entry(service, memory, beta_id),))
            assert (await service.search("beta", memories=(forgotten,), mode="fts")).hits == ()
            assert await backend.vector_complete((forgotten.ref,), TEST_PROFILE)

            fallback_service = MemoryService(backend=backend, id_factory=CaptureIds("fallback"))
            incomplete = await fallback_service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Gamma text-only record."),),
                mode="append",
            )
            assert incomplete is not None
            assert (await fallback_service.search("gamma", memories=(incomplete,), mode="auto")).mode == "fts"
            with pytest.raises(CapabilityNotSupportedError):
                await service.search("gamma", memories=(incomplete,), mode="vector")
            assert (
                database_scalar(
                    config,
                    f"""
                SELECT count(*) FROM {backend.table_names[4]}
                WHERE memory_artifact_id = %s
                  AND embedding IS NULL
                  AND embedding_content_hash IS NULL
                """,
                    (incomplete.artifact_id,),
                )
                == 1
            )

            connection = live_connection(config)
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {backend.table_names[4]}")
            connection.close()
            assert (await service.search("alpha", memories=(forgotten,), mode="fts")).hits == ()

            await backend.rebuild_projections(provider)

            assert await backend.vector_complete((forgotten.ref, incomplete.ref), TEST_PROFILE)
            rebuilt = await service.search("alpha", memories=(forgotten,), mode="hybrid")
            assert rebuilt.hits[0].text.startswith("Alpha")
            assert rebuilt.hits[0].matched_by == ("fts", "vector")
        finally:
            await backend.drop_schema()
            await backend.close()
        assert remaining_tables(config, backend.table_names) == ()

    asyncio.run(scenario())


def test_live_projection_failure_rolls_back_and_authoritative_tampering_is_detected() -> None:
    async def scenario() -> None:
        config = live_config()
        prefix = f"pc_rfc3_{uuid4().hex[:8]}_"
        backend = backend_for(config, prefix)
        try:
            await backend.initialize()
            service = MemoryService(backend=backend, id_factory=ContractIds())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Immutable OceanBase body."),),
                mode="append",
            )
            assert first is not None
            entry_id = first.content.manifest.entries[0].entry_id
            pending = await prepare_memory_commit(
                backend,
                first,
                MemoryEntryInput(
                    entry=await current_entry(service, first, entry_id),
                    kind="fact",
                    text="A pending update.",
                ),
                namespace="rollback",
            )

            connection = live_connection(config)
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE {backend.table_names[4]}")
            connection.close()
            with pytest.raises(pymysql.MySQLError):
                async with backend.begin() as transaction:
                    await transaction.commit(pending)
            assert database_scalar(config, f"SELECT count(*) FROM {backend.table_names[1]}") == 1
            assert database_scalar(config, f"SELECT count(*) FROM {backend.table_names[3]}") == 1

            connection = live_connection(config)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {backend.table_names[3]} SET text = 'tampered' WHERE memory_artifact_id = %s",
                    (first.artifact_id,),
                )
            connection.close()
            with pytest.raises(MemoryBackendConfigurationError, match="hash"):
                await backend.entries(first.ref)
        finally:
            await backend.drop_schema()
            await backend.close()
        assert remaining_tables(config, backend.table_names) == ()

    asyncio.run(scenario())
