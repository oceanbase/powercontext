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

"""Topic fixture writes preserve the configured deployment used for Profile assembly."""

from __future__ import annotations

import asyncio
import math

import pytest
from pydantic_ai.embeddings import TestEmbeddingModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.processing_migration import ProcessingSchemaNotReadyError
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetTopicMemoryRequest,
    InferenceConfig,
    PrepareContextRequest,
    RuntimeConfig,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_context_text_assembly import _seed_topics


class _Embedding:
    profile = EmbeddingProfile(profile_id="profile-assembly", model="fixture", dimension=3, normalization="unit")

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=((1 / math.sqrt(3),) * 3,) * len(texts))


@pytest.mark.parametrize("migration_backend", ("sqlite", "oceanbase"), indirect=True)
def test_topic_seed_preserves_configured_profile_and_remains_searchable(
    migration_backend: MigrationBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedding = _Embedding()
    settings = ServerSettings(
        database=migration_backend.config,
        inference=InferenceConfig(
            generation_model="test",
            embedding_model=embedding.profile.model,
            embedding_profile_id=embedding.profile.profile_id,
            embedding_dimension=embedding.profile.dimension,
        ),
        runtime=RuntimeConfig(artifact_processing_supervisor_mode="dedicated"),
    )
    config = BuiltinConfig(
        database=settings.database,
        runtime=settings.runtime,
        inference=settings.inference,
        code=settings.code,
        handoff_report=settings.handoff_report,
        external_skills=settings.external_skills,
    )
    prepare_server_database(
        settings,
        evidence_dir=migration_backend.directory / "migration-evidence",
        embedding_profile=embedding.profile,
    )
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda *args, **kwargs: TestEmbeddingModel(dimensions=3)
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, embedding_model=embedding) as contexts:
            shared = await contexts.scopes.create(
                ScopeDraft(title="Shared", summary="Shared assembly evidence", idempotency_key="shared")
            )
            current = await contexts.scopes.create(
                ScopeDraft(
                    title="Current",
                    summary="Current assembly evidence",
                    idempotency_key="current",
                    context_references=(shared.scope_id,),
                )
            )
            for scope, content in (
                (shared, "Shared profile must not replace the current profile."),
                (current, "Current profile prefers verified OpenAPI changes."),
            ):
                await contexts.records.create_artifact(
                    scope.scope_id, "profile", ArtifactWrite(content={"content": content})
                )

        with pytest.raises(ProcessingSchemaNotReadyError, match="Processing configuration differs"):
            async with open_builtin_contexts(BuiltinConfig(database=settings.database), embedding_model=embedding):
                pytest.fail("An incompatible default processing profile must remain rejected")

        await _seed_topics(config, [shared.scope_id, current.scope_id], embedding)
        async with open_builtin_runtime(config, scheduler_path=migration_backend.directory / "scheduler.db") as runtime:
            prepared = await runtime.context.for_scope(current.scope_id).prepare(
                PrepareContextRequest.model_validate({
                    "query": "OpenAPI client",
                    "assembly": {
                        "sections": [{"family": "profile", "limit": 1}, {"family": "topic-memory", "limit": 2}]
                    },
                })
            )
            assert prepared.status == "ready"
            assert prepared.content is not None
            assert prepared.content.index("## Profile") < prepared.content.index("## Topic Memory")
            assert "Current profile prefers verified OpenAPI changes." in prepared.content
            assert "Shared profile must not replace" not in prepared.content
            assert prepared.content.count('family="topic-memory"') == 1
            assert 'family="topic-memory", id="assembly-topic", revision=1' in prepared.content
            assert f"Title: OpenAPI client topic in {current.scope_id}" in prepared.content
            assert shared.scope_id not in prepared.content
            expected = ArtifactRef(family="topic-memory", artifact_id="assembly-topic", revision=1)
            exact = await runtime.topic_memory.for_scope(current.scope_id).get(GetTopicMemoryRequest(artifact=expected))
            assert exact.topic.as_ref() == expected
            assert exact.topic.content.detail == "OpenAPI client contract details. " * 100
            assert exact.topic.content.detail not in prepared.content
            assert prepared.content_bytes == len(prepared.content.encode("utf-8"))
            vectors = await runtime.artifacts.for_scope(current.scope_id).search(
                "topic-memory", {"query": "OpenAPI client", "mode": "vector", "limit": 2}
            )
            assert tuple(match.artifact_ref for match in vectors.matches) == (expected,)

    asyncio.run(scenario())
