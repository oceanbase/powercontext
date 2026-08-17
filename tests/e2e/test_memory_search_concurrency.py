from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
from types import MethodType
from typing import Any, Literal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput, MemorySearchMode
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)

DatabaseKind = Literal["sqlite", "oceanbase"]
TIMEOUT_SECONDS = 15
PROFILE = EmbeddingProfile(
    profile_id="concurrent-search-test-v1",
    model="test",
    dimension=3,
    distance="l2",
    normalization="unit",
)
EXPECTED_CHANNELS = {
    "fts": ("fts",),
    "vector": ("vector",),
    "hybrid": ("fts", "vector"),
}


class _KeywordEmbeddingModel:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        vectors = tuple((1.0, 0.0, 0.0) if "stable" in text.casefold() else (0.0, 1.0, 0.0) for text in texts)
        return EmbeddingResult(vectors=vectors)


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
@pytest.mark.parametrize("mode", ["fts", "vector", "hybrid"])
def test_memory_search_stays_consistent_when_append_advances_head_before_index_query(
    database_kind: DatabaseKind,
    mode: MemorySearchMode,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = _database_config(database_kind, mode, tmp_path)
        embedding_model = None if mode == "fts" else _KeywordEmbeddingModel()
        async with open_builtin_runtime(
            BuiltinConfig(database=database),
            embedding_model=embedding_model,
        ) as runtime:
            memory = runtime.memory.for_scope(f"concurrent-memory-search-{uuid4()}")
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Stable searchable fact."),))
            )

            provider: Any = runtime._provider
            index = provider.index
            original_search = index.search
            paused = asyncio.Event()
            resume = asyncio.Event()
            pending: asyncio.Task[Any] | None = None

            async def pause_first_search(
                _self: Any,
                connection: Any,
                scope_id: str,
                request: Any,
            ) -> Any:
                if not paused.is_set():
                    paused.set()
                    await resume.wait()
                return await original_search(connection, scope_id, request)

            index.search = MethodType(pause_first_search, index)
            try:
                pending = asyncio.create_task(memory.search(SearchMemoryRequest(query="stable searchable", mode=mode)))
                await asyncio.wait_for(paused.wait(), timeout=TIMEOUT_SECONDS)
                new_head = await memory.remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Unrelated appended fact."),))
                )
                resume.set()
                result = await asyncio.wait_for(pending, timeout=TIMEOUT_SECONDS)
            finally:
                resume.set()
                index.search = original_search
                if pending is not None and not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending

            assert result.memory_ref == new_head.memory_ref
            assert result.mode == mode
            assert tuple(hit.text for hit in result.hits) == ("Stable searchable fact.",)
            assert result.hits[0].matched_by == EXPECTED_CHANNELS[mode]

    asyncio.run(scenario())


def _database_config(
    database_kind: DatabaseKind,
    mode: MemorySearchMode,
    tmp_path: Path,
) -> SQLiteConfig | OceanBaseConfig:
    if database_kind == "oceanbase":
        url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
        if url is None:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database")
        return OceanBaseConfig(url=SecretStr(url))

    extension = os.environ.get("POWERCONTEXT_VEC1_EXTENSION")
    if mode in {"vector", "hybrid"} and (extension is None or not Path(extension).is_file()):
        pytest.skip("set POWERCONTEXT_VEC1_EXTENSION to a Vec1 extension file")
    return SQLiteConfig(
        url=f"sqlite+aiosqlite:///{tmp_path / f'memory-search-{mode}.db'}",
        vec1_extension=None if extension is None else Path(extension),
    )
