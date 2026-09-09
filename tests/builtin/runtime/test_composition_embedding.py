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
from contextlib import AsyncExitStack
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic_ai import Embedder

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryCapabilityError,
    TopicMemoryContent,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.composition import _embedding_models
from powercontext.builtin.runtime.config import InferenceConfig


class _SpyEmbedder(Embedder):
    settings_seen: ClassVar[list[dict[str, object] | None]] = []

    def __init__(self, model, *, settings=None, defer_model_check=True, instrument=None):
        super().__init__(model, settings=settings, defer_model_check=defer_model_check, instrument=instrument)
        type(self).settings_seen.append(settings)


def test_embedding_models_send_the_configured_dimension_to_the_provider(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("pydantic_ai.Embedder", _SpyEmbedder)
    _SpyEmbedder.settings_seen = []

    async def scenario() -> None:
        config = InferenceConfig(
            embedding_model="openai:text-embedding-3-small",
            embedding_model_settings={"dimensions": 512, "extra_body": {"route": "embedding"}},
            embedding_profile_id="bailian-1536-v1",
            embedding_dimension=1536,
        )
        async with AsyncExitStack() as resources:
            operational, readiness = await _embedding_models(config, resources, None)
        assert operational is not None
        assert readiness is not None
        expected_settings = {"dimensions": 1536, "extra_body": {"route": "embedding"}}
        assert _SpyEmbedder.settings_seen == [expected_settings, expected_settings]

    asyncio.run(scenario())


def test_embedding_models_without_configuration_return_no_models() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            operational, readiness = await _embedding_models(InferenceConfig(), resources, None)
        assert operational is None
        assert readiness is None

    asyncio.run(scenario())


@pytest.mark.parametrize("existing_memory", [False, True])
def test_empty_topic_database_allows_embedding_configuration_changes(tmp_path: Path, existing_memory: bool) -> None:
    class Embedding:
        def __init__(self, profile_id: str) -> None:
            self.profile = EmbeddingProfile(
                profile_id=profile_id, model="test", dimension=2, distance="l2", normalization="unit"
            )

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'configuration.db'}"))
        memory = None
        async with open_builtin_contexts(config) as contexts:
            if existing_memory:
                context = await contexts.get("project")
                memory = await context.artifacts.memory.remember(
                    memory=None,
                    entries=(MemoryEntryInput(kind="decision", text="Preserve ordinary Memory."),),
                    mode="append",
                )

        # Reopening an unused Topic store must not lock out ordinary Memory
        # embeddings, disabling embeddings, or changing a compatible profile.
        for embedding in (Embedding("first"), Embedding("second"), None, Embedding("third")):
            async with open_builtin_contexts(config, embedding_model=embedding) as contexts:
                assert contexts.index.capabilities.vector is (embedding is not None)
                assert contexts.topic_memory_index.capabilities.vector is (embedding is not None)
                if existing_memory:
                    assert memory is not None
                    context = await contexts.get("project")
                    result = await context.artifacts.memory.search("ordinary", memories=(memory,), mode="fts")
                    assert [hit.text for hit in result.hits] == ["Preserve ordinary Memory."]

    asyncio.run(scenario())


@pytest.mark.parametrize("transition", ["profile-change", "hybrid-to-fts", "fts-to-hybrid"])
def test_stale_topic_runtime_rejects_reads_after_empty_store_reconfiguration(tmp_path: Path, transition: str) -> None:
    class Embedding:
        def __init__(self, profile_id: str) -> None:
            self.profile = EmbeddingProfile(
                profile_id=profile_id, model="test", dimension=2, distance="l2", normalization="unit"
            )

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'stale-runtime.db'}"))
        old_embedding = None if transition == "fts-to-hybrid" else Embedding("old")
        new_embedding = None if transition == "hybrid-to-fts" else Embedding("new")
        vector = (1.0, 0.0)
        # Keep both independently composed runtimes/engines open. Matching
        # dimensions alone must not allow queries across embedding spaces.
        async with (
            open_builtin_contexts(config, embedding_model=old_embedding) as old,
            open_builtin_contexts(config, embedding_model=new_embedding) as current,
        ):
            with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                await old.search_topic_memories("scope-a", "evidence", limit=2)
            content = TopicMemoryContent(
                title="Evidence", summary="Current profile", detail="Evidence from a new space."
            )
            chunks = prepare_topic_memory_projection(content).chunks
            projection = prepare_topic_memory_projection(
                content,
                topic_embedding=None if new_embedding is None else vector,
                chunk_embeddings=() if new_embedding is None else (vector,) * len(chunks),
                embedding_profile=None if new_embedding is None else new_embedding.profile,
            )
            async with current.database.transaction() as connection:
                published = await current.repositories.topic_memories.publish_create(
                    connection, "scope-a", "current", TopicMemoryDraft(content=content), projection
                )
            modes = ("fts", "auto") if old_embedding is None else ("fts", "auto", "vector", "hybrid")
            for mode in modes:
                with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                    await old.search_topic_memories(
                        "scope-a",
                        "evidence",
                        limit=2,
                        mode=mode,
                        query_vector=None if old_embedding is None else vector,
                        embedding_profile=None if old_embedding is None else old_embedding.profile,
                    )
            # Even the zero-Analyzer-term fast path must not silently succeed.
            with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                await old.search_topic_memories("scope-a", "!!!", limit=2, mode="fts")
            with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                await old.get_topic_memory("scope-a", published.topic.as_ref())
            with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                await old.browse_topic_memories("scope-a", limit=2)

            result = await current.search_topic_memories(
                "scope-a",
                "evidence",
                limit=2,
                query_vector=None if new_embedding is None else vector,
                embedding_profile=None if new_embedding is None else new_embedding.profile,
            )
            assert result.mode == ("fts" if new_embedding is None else "hybrid")
            assert [hit.artifact_ref for hit in result.hits] == [published.topic.as_ref()]
            assert (await current.get_topic_memory("scope-a", published.topic.as_ref())).topic == published.topic
            assert [item.artifact_ref for item in await current.browse_topic_memories("scope-a", limit=2)] == [
                published.topic.as_ref()
            ]

    asyncio.run(scenario())
