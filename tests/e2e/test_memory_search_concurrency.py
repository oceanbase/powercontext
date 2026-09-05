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
import os
from contextlib import suppress
from pathlib import Path
from types import MethodType
from typing import Any, Literal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
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
    MemoryRerankDecision,
    MemorySearchMode,
)
from powercontext.builtin.artifacts.memory.canonical import entry_content_hash, memory_content_hash
from powercontext.builtin.artifacts.memory.errors import MemoryBackendConfigurationError
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError

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
OCEANBASE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")


class _KeywordEmbeddingModel:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        vectors = tuple((1.0, 0.0, 0.0) if "stable" in text.casefold() else (0.0, 1.0, 0.0) for text in texts)
        return EmbeddingResult(vectors=vectors)


class _PausingReranker:
    policy_id = "test.concurrent-memory-search.v1"

    def __init__(self) -> None:
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()

    async def rerank(
        self,
        _query: str,
        candidates: tuple[MemoryHit, ...],
        _limit: int,
        /,
    ) -> MemoryRerankDecision:
        self.paused.set()
        await self.resume.wait()
        return MemoryRerankDecision(
            selected_ranks=(1,),
            usage=InferenceUsage(requests=1),
        )


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
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Concurrent Memory search",
                    summary="Index snapshot consistency",
                    idempotency_key=f"concurrent-memory-search-{uuid4()}",
                )
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            initial = await memory.remember(
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

            assert result.memory_ref in (initial.memory_ref, new_head.memory_ref)
            assert result.mode == mode
            assert tuple(hit.text for hit in result.hits) == ("Stable searchable fact.",)
            assert result.hits[0].memory_ref == result.memory_ref
            assert result.hits[0].matched_by == EXPECTED_CHANNELS[mode]

    asyncio.run(scenario())


def test_memory_search_keeps_the_completed_revision_snapshot_when_head_advances_during_reranking(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        reranker = _PausingReranker()
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rerank-snapshot.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database), memory_reranker=reranker) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Rerank snapshot", summary="Stable revision snapshot", idempotency_key="rerank-snapshot"
                )
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            initial = await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Stable searchable fact."),))
            )
            pending = asyncio.create_task(
                memory.search(SearchMemoryRequest(query="stable searchable", mode="fts", limit=1))
            )
            try:
                await asyncio.wait_for(reranker.paused.wait(), timeout=TIMEOUT_SECONDS)
                new_head = await memory.remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Unrelated appended fact."),))
                )
                reranker.resume.set()
                result = await asyncio.wait_for(pending, timeout=TIMEOUT_SECONDS)
            finally:
                reranker.resume.set()
                if not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending

            assert new_head.memory_ref.revision == initial.memory_ref.revision + 1
            assert result.memory_ref == initial.memory_ref
            assert tuple(hit.text for hit in result.hits) == ("Stable searchable fact.",)
            assert result.hits[0].memory_ref == initial.memory_ref
            assert result.rerank is not None

    asyncio.run(scenario())


def test_memory_search_reports_revision_conflict_when_every_attempt_starts_from_a_stale_head() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Perpetually stale search",
                    summary="Revision conflict retry limit",
                    idempotency_key="perpetually-stale-search",
                )
            )
            scope_id = scope.scope_id
            memory = runtime.memory.for_scope(scope_id)
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Stable searchable fact."),))
            )

            provider: Any = runtime._provider
            context = await provider.get(scope_id)
            service = context.artifacts.memory
            original_search = service.search
            update_number = 0

            async def advance_head_before_search(_self: Any, *args: Any, **kwargs: Any) -> Any:
                nonlocal update_number
                update_number += 1
                await memory.remember(
                    RememberMemoryRequest(
                        entries=(MemoryEntryInput(kind="fact", text=f"Concurrent update {update_number}."),)
                    )
                )
                return await original_search(*args, **kwargs)

            service.search = MethodType(advance_head_before_search, service)
            try:
                with pytest.raises(RevisionConflictError):
                    await asyncio.wait_for(
                        memory.search(SearchMemoryRequest(query="stable searchable", mode="fts")),
                        timeout=TIMEOUT_SECONDS,
                    )
            finally:
                service.search = original_search

    asyncio.run(scenario())


@pytest.mark.skipif(
    not OCEANBASE_URL,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database",
)
def test_oceanbase_memory_version_identity_race_is_database_enforced() -> None:
    async def scenario() -> None:
        assert OCEANBASE_URL is not None
        scope_id = f"concurrent-version-identity-{uuid4()}"
        async with open_builtin_contexts(
            BuiltinConfig(database=OceanBaseConfig(url=SecretStr(OCEANBASE_URL)))
        ) as contexts:
            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id=scope_id,
                artifacts=contexts.repositories.artifacts,
                index=contexts.index,
            )
            mutable_backend: Any = backend
            original = mutable_backend._commit_versions
            both_checked = asyncio.Event()
            release = asyncio.Event()
            arrivals = 0
            arrival_lock = asyncio.Lock()

            async def pause_after_identity_read(
                _self: RelationalMemoryBackend,
                connection: Any,
                value: MemoryCommit,
            ) -> dict[str, MemoryEntryVersion]:
                nonlocal arrivals
                versions = await original(connection, value)
                async with arrival_lock:
                    arrivals += 1
                    if arrivals == 2:
                        both_checked.set()
                await release.wait()
                return versions

            mutable_backend._commit_versions = MethodType(pause_after_identity_read, backend)
            commits = (
                _colliding_initial_commit("memory-a", "entry-a"),
                _colliding_initial_commit("memory-b", "entry-b"),
            )
            pending = tuple(asyncio.create_task(_commit_memory(backend, commit)) for commit in commits)
            results: list[Memory | BaseException] = []
            try:
                await asyncio.wait_for(both_checked.wait(), timeout=TIMEOUT_SECONDS)
                release.set()
                results = await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=TIMEOUT_SECONDS,
                )
            finally:
                release.set()
                for task in pending:
                    if not task.done():
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task

            assert len(tuple(result for result in results if isinstance(result, Memory))) == 1
            failures = tuple(result for result in results if isinstance(result, BaseException))
            assert len(failures) == 1
            assert isinstance(failures[0], MemoryBackendConfigurationError)
            stored = []
            for artifact_id in ("memory-a", "memory-b"):
                with suppress(ArtifactNotFoundError):
                    stored.append(await backend.latest(artifact_id))
            assert len(stored) == 1

    asyncio.run(scenario())


def _colliding_initial_commit(memory_id: str, entry_id: str) -> MemoryCommit:
    text = f"Concurrent identity for {memory_id}."
    content_hash = entry_content_hash(kind="fact", text=text, source_refs=(), artifact_refs=())
    version = MemoryEntryVersion(
        memory_artifact_id=memory_id,
        entry_id=entry_id,
        entry_version_id="shared-concurrent-version",
        version=1,
        previous_version_id=None,
        kind="fact",
        text=text,
        entry_content_hash=content_hash,
        created_in_revision=1,
    )
    content = MemoryContent(
        manifest=MemoryManifest(
            entries=(
                MemoryManifestEntry(
                    entry_id=entry_id,
                    entry_version_id=version.entry_version_id,
                    entry_content_hash=content_hash,
                    state="active",
                ),
            )
        ),
        changes=(
            MemoryChange(
                op="add",
                entry_id=entry_id,
                from_entry_version_id=None,
                to_entry_version_id=version.entry_version_id,
            ),
        ),
    )
    memory = Memory(artifact_id=memory_id, revision=1, content=content)
    return MemoryCommit(
        base=None,
        memory=memory,
        content_hash=memory_content_hash(content),
        entry_versions=(version,),
        projections=(MemoryProjection(entry_version=version, searchable_text=analyze_text(text)),),
    )


async def _commit_memory(backend: RelationalMemoryBackend, commit: MemoryCommit) -> Memory:
    async with backend.begin() as unit_of_work:
        return await unit_of_work.commit(commit)


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

    return SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'memory-search-{mode}.db'}")
