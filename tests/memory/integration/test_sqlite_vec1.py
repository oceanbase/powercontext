from __future__ import annotations

import asyncio
import os
from pathlib import Path

import apsw
import pytest

from powercontext.inference import EmbeddingResult, EmbeddingVector
from powercontext.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryEntryInput,
    MemoryService,
)
from powercontext.memory.backends import SQLiteMemoryBackend
from tests.memory import current_entry
from tests.memory.backends.contract import ContractIds

TEST_PROFILE = EmbeddingProfile(
    profile_id="keyword-v1",
    model="keyword",
    dimension=3,
    distance="l2",
    normalization="none",
)


class KeywordEmbeddingModel:
    def __init__(self, profile: EmbeddingProfile = TEST_PROFILE) -> None:
        self._profile = profile

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple(self._embed(text) for text in texts))

    @staticmethod
    def _embed(text: str) -> EmbeddingVector:
        normalized = text.casefold()
        if "alpha" in normalized:
            return (1.0, 0.0, 0.0)
        if "beta" in normalized:
            return (0.0, 1.0, 0.0)
        return (0.0, 0.0, 1.0)


def vec1_extension() -> Path:
    value = os.environ.get("POWERCONTEXT_VEC1_EXTENSION")
    if value is None:
        pytest.skip("POWERCONTEXT_VEC1_EXTENSION is not configured")
    path = Path(value)
    if not path.is_file():
        pytest.skip("POWERCONTEXT_VEC1_EXTENSION does not point to a file")
    return path


def test_sqlite_vec1_supports_vector_search_with_a_fixed_profile(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(
            database,
            embedding_profile=TEST_PROFILE,
            vec1_extension=vec1_extension(),
        )
        await backend.initialize()
        try:
            assert await backend.capabilities() == MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            )
            provider = KeywordEmbeddingModel()
            service = MemoryService(backend=backend, embedding_model=provider, id_factory=ContractIds())
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha lexical record."),
                    MemoryEntryInput(kind="fact", text="Beta lexical record."),
                ),
                mode="append",
            )
            assert memory is not None
            assert await backend.vector_complete((memory.ref,), TEST_PROFILE)

            result = await service.search("alpha", memories=(memory,), mode="vector")
            assert result.mode == "vector"
            assert result.hits[0].text == "Alpha lexical record."
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_vec1_removes_inactive_vectors_and_never_reuses_stale_embeddings(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(
            database,
            embedding_profile=TEST_PROFILE,
            vec1_extension=vec1_extension(),
        )
        await backend.initialize()
        try:
            provider = KeywordEmbeddingModel()
            service = MemoryService(backend=backend, embedding_model=provider, id_factory=ContractIds())
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha entry."),
                    MemoryEntryInput(kind="fact", text="Beta entry."),
                ),
                mode="append",
            )
            assert memory is not None
            alpha_id = memory.content.manifest.entries[0].entry_id
            forgotten = await service.forget(memory, entries=(await current_entry(service, memory, alpha_id),))
            assert await backend.vector_complete((forgotten.ref,), TEST_PROFILE)
            result = await service.search("alpha", memories=(forgotten,), mode="vector")
            assert all(hit.entry_id != alpha_id for hit in result.hits)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_vec1_incomplete_heads_fall_back_then_offline_rebuild(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(
            database,
            embedding_profile=TEST_PROFILE,
            vec1_extension=vec1_extension(),
        )
        await backend.initialize()
        try:
            fts_service = MemoryService(backend=backend, id_factory=ContractIds())
            memory = await fts_service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Alpha recovery record."),),
                mode="append",
            )
            assert memory is not None
            assert not await backend.vector_complete((memory.ref,), TEST_PROFILE)
            assert (await fts_service.search("alpha", memories=(memory,), mode="auto")).mode == "fts"
            with pytest.raises(CapabilityNotSupportedError):
                await fts_service.search("alpha", memories=(memory,), mode="vector")

            connection = apsw.Connection(str(database))
            connection.enable_load_extension(True)
            connection.load_extension(str(vec1_extension()))
            connection.enable_load_extension(False)
            connection.execute("DELETE FROM memory_entry_search_fts")
            connection.execute("DELETE FROM memory_entry_search_vector")
            connection.execute("DELETE FROM memory_entry_vector_metadata")
            connection.execute("DELETE FROM memory_entry_heads")
            connection.close()

            provider = KeywordEmbeddingModel()
            await backend.rebuild_projections(provider)
            assert await backend.vector_complete((memory.ref,), TEST_PROFILE)
            vector_service = MemoryService(backend=backend, embedding_model=provider)
            result = await vector_service.search("alpha", memories=(memory,), mode="hybrid")
            assert result.mode == "hybrid"
            assert result.hits[0].matched_by == ("fts", "vector")
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_vec1_rejects_a_different_fixed_dimension_on_reopen(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        first = SQLiteMemoryBackend(
            database,
            embedding_profile=TEST_PROFILE,
            vec1_extension=vec1_extension(),
        )
        await first.initialize()
        await first.close()

        incompatible = EmbeddingProfile(
            profile_id="keyword-v2",
            model="keyword-small",
            dimension=2,
            distance="l2",
            normalization="none",
        )
        second = SQLiteMemoryBackend(
            database,
            embedding_profile=incompatible,
            vec1_extension=vec1_extension(),
        )
        with pytest.raises(CapabilityNotSupportedError, match="probe"):
            await second.initialize()

    asyncio.run(scenario())


def test_sqlite_vec1_metadata_tampering_makes_the_head_incomplete(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "memory.db"
        backend = SQLiteMemoryBackend(
            database,
            embedding_profile=TEST_PROFILE,
            vec1_extension=vec1_extension(),
        )
        await backend.initialize()
        try:
            provider = KeywordEmbeddingModel()
            service = MemoryService(backend=backend, embedding_model=provider, id_factory=ContractIds())
            memory = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Alpha integrity record."),),
                mode="append",
            )
            assert memory is not None
            assert await backend.vector_complete((memory.ref,), TEST_PROFILE)

            connection = apsw.Connection(str(database))
            connection.enable_load_extension(True)
            connection.load_extension(str(vec1_extension()))
            connection.enable_load_extension(False)
            connection.execute(
                "UPDATE memory_entry_vector_metadata SET embedding_content_hash = ?",
                ("0" * 64,),
            )
            connection.close()

            assert not await backend.vector_complete((memory.ref,), TEST_PROFILE)
            assert (await service.search("alpha", memories=(memory,), mode="auto")).mode == "fts"
            with pytest.raises(CapabilityNotSupportedError):
                await service.search("alpha", memories=(memory,), mode="vector")
        finally:
            await backend.close()

    asyncio.run(scenario())
