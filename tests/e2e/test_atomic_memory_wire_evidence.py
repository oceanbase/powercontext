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

"""Wire evidence survives a SQLite round trip without duplicating the body."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.models import SubmitSourceObservation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
)
from powercontext.sources.observations import manifest_for_definition, project_source_for_transport
from tests.e2e.dream_support import atomic_memory_pipeline

BODY = "The deploy uses canary."
WIRE = {"constraint": "Pause when errors rise."}
BULK_BODY = "a" * 50_000


class _WireCapture(BaseModel):
    source_id: str
    content: str
    constraint: str


class _WireSource(Source):
    content: str
    wire_content: str | dict[str, Any]
    wire_content_present: bool = True


class _WireAdapter:
    input_class = _WireCapture
    name = "wire-note"
    source_class = _WireSource

    async def resolve(self, value: _WireCapture, /) -> _WireSource:
        return _WireSource(
            name=value.source_id,
            materialization=SourceMaterialization.CAPTURED,
            content=value.content,
            wire_content={"constraint": value.constraint},
        )

    async def read(self, source: _WireSource, /) -> TextEvidence:
        return TextEvidence(source_type=self.name, source_id=source.name, content=source.content)


class _TextProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = _WireSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: _WireSource) -> TextEvidence:
        return TextEvidence(source_type="wire-note", source_id=source.name, content=source.content)


class _RecordingExtractor:
    def __init__(self) -> None:
        self.requests: list[AtomicMemoryExtractionInput] = []

    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        self.requests.append(request)
        return GenerationResult(output=AtomicMemoryExtractionOutput())


def _registry() -> SourceDefinitionRegistry:
    return SourceDefinitionRegistry((
        *BUILTIN_SOURCE_REGISTRY.definitions,
        AdapterSourceDefinition(_WireAdapter(), projections=(_TextProjection(),)),
    ))


def test_sqlite_native_wire_evidence_survives_process_memory(tmp_path) -> None:
    extractor = _RecordingExtractor()

    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'wire-native.db'}")),
            source_registry=_registry(),
            candidate_pipeline=atomic_memory_pipeline(extractor),
        ) as contexts:
            context = await contexts.get("scope-a")
            source = await context.sources.resolve(
                _WireCapture(source_id="deploy", content=BODY, constraint=WIRE["constraint"])
            )
            await context.sources.add(source)
            result = await contexts.process_memory("scope-a", 1)
            assert result.source_count == 1

    asyncio.run(scenario())

    assert len(extractor.requests) == 1
    metadata = extractor.requests[0].evidence[0].source_metadata
    assert isinstance(metadata, dict)
    assert metadata["wire_content"] == WIRE
    assert metadata["wire_content_present"] is True
    assert "content" not in metadata


def test_sqlite_observation_duplicate_wire_stays_within_input_budget(tmp_path) -> None:
    extractor = _RecordingExtractor()
    source = _WireSource(
        name="bulk",
        materialization=SourceMaterialization.CAPTURED,
        content=BULK_BODY,
        wire_content=BULK_BODY,
    )

    async def scenario() -> None:
        registry = _registry()
        observation = project_source_for_transport(registry, source)
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'wire-bulk.db'}")),
            candidate_pipeline=atomic_memory_pipeline(extractor),
        ) as contexts:
            await contexts.register_source_definition(
                manifest_for_definition(registry.definition_for_name("wire-note"))
            )
            await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            result = await contexts.process_memory("scope-a", 1)
            assert result.source_count == 1

    asyncio.run(scenario())

    assert len(extractor.requests) == 1
    serialized = extractor.requests[0].model_dump_json()
    assert serialized.count(BULK_BODY) == 1
    assert character_token_estimator().estimate(serialized) < 24_000
