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

"""Accepted Source declarations do not alter recall or consumer revision citations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import cast

import httpx
import pytest

from powercontext.builtin.artifacts.handoff import PrepareHandoff
from powercontext.builtin.runtime import (
    BuiltinConfig,
    HandoffArtifactCitation,
    PrepareContextRequest,
    RuntimeConfig,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import CONTENT_SOURCE_DEFINITION
from powercontext.server.app import ServerApplication, create_app
from powercontext.sources import AdapterSourceDefinition, SourceDefinitionRegistry, SourceRef
from powercontext.sources.evidence import MemoryEvidenceDeclaration
from tests.builtin.persistence.contract import NoteAdapter, NoteInput
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.test_atomic_memory_source_evidence import _commit
from tests.e2e.test_handoff_runtime import _EchoHandoffPipeline

pytestmark = pytest.mark.parametrize("migration_backend", ["sqlite", "oceanbase"], indirect=True)


def test_source_declarations_preserve_recall_and_consumers(migration_backend: MigrationBackend) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=migration_backend.config,
            runtime=RuntimeConfig(artifact_processing_families=(), atomic_memory_related_mode="fts"),
        )
        arms = []
        neutral = MemoryEvidenceDeclaration()
        candidates = (
            (SourceRef(source_type="note", source_id="note"), "memory-a", "alpha acceptance"),
            (SourceRef(source_type="content", source_id="source"), "memory-b", "alpha guidance"),
        )
        for declaration in (
            neutral,
            MemoryEvidenceDeclaration(authority="system_attested", verification="verified"),
        ):
            registry = SourceDefinitionRegistry((
                replace(CONTENT_SOURCE_DEFINITION, memory_evidence=declaration),
                AdapterSourceDefinition(NoteAdapter()),
            ))
            async with open_builtin_runtime(
                config,
                scheduler_path=migration_backend.directory / "consumer-scheduler.db",
                source_registry=registry,
                handoff_pipeline=_EchoHandoffPipeline(),
            ) as runtime:
                assert runtime.scopes is not None and runtime.atomic_memory is not None
                scope = await runtime.scopes.create(
                    ScopeDraft(
                        title="Recall",
                        summary="Evidence neutrality.",
                        idempotency_key=declaration.authority,
                    )
                )
                scope_id = scope.scope_id
                async with open_builtin_contexts(config, source_registry=registry) as contexts:
                    await contexts.records.capture_source(scope_id, "content", "source", "alpha guidance", {})
                    context = await contexts.get(scope_id)
                    note = await context.sources.resolve(NoteInput(note_id="note", body="alpha acceptance"))
                    await context.sources.add(note)
                    memories = []
                    for source, artifact_id, text in candidates:
                        memory = await _commit(contexts, scope_id, artifact_id, text, sources=(source,))
                        annotation = await contexts.atomic_memory.for_scope(scope_id).evidence(artifact_id, revision=1)
                        assert annotation.artifact_ref == memory.ref
                        expected = declaration if source.source_type == "content" else neutral
                        assert tuple((item.source_ref, item.declaration) for item in annotation.sources) == (
                            (source, expected),
                        )
                        memories.append(memory)
                page = await runtime.atomic_memory.for_scope(scope_id).search("alpha", mode="text", limit=2)
                assert tuple((hit.hit.artifact_ref, hit.text, hit.matched_by) for hit in page.hits) == tuple(
                    (memory.ref, text, ("text",)) for memory, (_, _, text) in zip(memories, candidates, strict=True)
                )
                recalled = tuple((hit.hit.artifact_ref.model_dump(mode="json"), hit.text) for hit in page.hits)
                app = create_app(application=cast(ServerApplication, runtime))
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    response = await client.post("/v1/memory/search", json={"scope_id": scope_id, "query": "alpha"})
                assert response.status_code == 200, response.text
                public = response.json()
                assert (
                    tuple((item["memory"]["artifact"], item["memory"]["text"]) for item in public["hits"]) == recalled
                )
                prepared = await runtime.context.for_scope(scope_id).prepare(PrepareContextRequest(query="alpha"))
                assert prepared.status == "ready" and prepared.content is not None
                items = json.loads(prepared.content.splitlines()[-2])["items"]
                assert tuple((item["citation"]["artifact_ref"], item["content"]) for item in items) == recalled
                citation = HandoffArtifactCitation(artifact_ref=page.hits[-1].hit.artifact_ref)
                handoffs = runtime.handoff.for_scope(scope_id)
                finalized = await handoffs.finalize(
                    await handoffs.prepare(
                        PrepareHandoff(objective="Keep the recalled revision.", evidence=(citation,))
                    )
                )
                assert finalized.content.state[0].citations == (citation,)
                assert finalized.content.next_action is not None
                assert finalized.content.next_action.citations == (citation,)
                arms.append((page.hits, public, prepared.content.replace(scope_id, "scope"), finalized.content))
        assert arms[0] == arms[1]

    asyncio.run(scenario())
