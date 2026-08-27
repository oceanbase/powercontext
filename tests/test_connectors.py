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
import builtins
from copy import deepcopy

import pytest
from pydantic import JsonValue

from powercontext import (
    CatalogConnectorSourceSink,
    ConnectorBinding,
    ConnectorCapability,
    ConnectorLifecycle,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionResult,
    ConnectorSubmissionStatus,
    InvalidConnectorError,
    InvalidConnectorRunError,
    Source,
    SourceCatalog,
    SourceConflictError,
    SourceRef,
)
from powercontext.builtin.sources import (
    BUILTIN_SOURCE_REGISTRY,
    CONTENT_SOURCE_NAME,
    TEXT_EVIDENCE_PROJECTION_KEY,
    ContentCapture,
)


class IdempotentSourceStore:
    def __init__(self, events: builtins.list[str]) -> None:
        self.events = events
        self.sources: dict[tuple[str, str], Source] = {}

    async def add(self, source: Source, /) -> Source:
        definition = BUILTIN_SOURCE_REGISTRY.definition_for_source(source)
        ref = SourceRef(source_type=definition.name, source_id=source.name)
        key = (ref.source_type, ref.source_id)
        existing = self.sources.get(key)
        if existing is not None and existing != source:
            raise SourceConflictError("identity", ref)
        self.events.append(f"source:{ref.source_id}")
        self.sources.setdefault(key, deepcopy(source))
        return self.sources[key]

    async def get(self, source: Source, /) -> Source:
        definition = BUILTIN_SOURCE_REGISTRY.definition_for_source(source)
        return self.sources[(definition.name, source.name)]

    async def list(self) -> tuple[Source, ...]:
        return tuple(self.sources.values())


class MemoryCheckpointStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.values: dict[str, JsonValue | None] = {}

    async def load(self, binding: ConnectorBinding, /) -> JsonValue | None:
        return deepcopy(self.values.get(binding.binding_id))

    async def save(
        self,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        /,
        *,
        expected: JsonValue | None,
    ) -> None:
        assert self.values.get(binding.binding_id) == expected
        self.events.append("checkpoint")
        self.values[binding.binding_id] = deepcopy(checkpoint)


class ContentConnector:
    name = "test-content"
    version = "1"
    source_definitions = frozenset({CONTENT_SOURCE_NAME})
    capabilities = frozenset({ConnectorCapability.CHECKPOINT_RESUME})

    def __init__(self, capture: ContentCapture) -> None:
        self.capture = capture

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        await session.submit(self.capture.source_id, CONTENT_SOURCE_NAME, self.capture)
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint={"cursor": 1})


def _binding() -> ConnectorBinding:
    return ConnectorBinding(
        scope_id="scope-a",
        binding_id="content-a",
        connector_name="test-content",
        connector_version="1",
    )


def test_connector_commits_checkpoint_after_durable_source_acceptance() -> None:
    async def scenario() -> None:
        events: list[str] = []
        store = IdempotentSourceStore(events)
        catalog = SourceCatalog(backend=store, registry=BUILTIN_SOURCE_REGISTRY)
        checkpoints = MemoryCheckpointStore(events)
        lifecycle = ConnectorLifecycle(
            sink=CatalogConnectorSourceSink(scope_id="scope-a", catalog=catalog, store=store),
            checkpoints=checkpoints,
        )
        connector = ContentConnector(ContentCapture(source_id="note-1", content="Remember this."))

        result = await lifecycle.run(connector, _binding())

        assert events == ["source:note-1", "checkpoint"]
        assert result.status is ConnectorRunStatus.COMPLETE
        assert result.previous_checkpoint is None
        assert result.committed_checkpoint == {"cursor": 1}
        assert result.items[0].status is ConnectorSubmissionStatus.ACCEPTED
        assert result.items[0].source_ref == SourceRef(source_type=CONTENT_SOURCE_NAME, source_id="note-1")
        assert len(store.sources) == 1
        stored = next(iter(store.sources.values()))
        assert catalog.project(stored, TEXT_EVIDENCE_PROJECTION_KEY) == {
            "source_type": CONTENT_SOURCE_NAME,
            "source_id": "note-1",
            "content": "Remember this.",
            "metadata": {},
        }
        assert catalog.project(stored, TEXT_EVIDENCE_PROJECTION_KEY) == catalog.project(
            stored,
            TEXT_EVIDENCE_PROJECTION_KEY,
        )

        replay = await lifecycle.run(connector, _binding())
        assert replay.previous_checkpoint == {"cursor": 1}
        assert len(store.sources) == 1
        assert events == ["source:note-1", "checkpoint", "source:note-1"]

    asyncio.run(scenario())


def test_connector_exposes_failed_items_and_does_not_advance_checkpoint() -> None:
    class RejectingSink:
        async def submit(self, binding, item_id, definition_name, value, /) -> ConnectorSubmissionResult:
            return ConnectorSubmissionResult(status=ConnectorSubmissionStatus.REJECTED, detail="unsupported value")

    async def scenario() -> None:
        events: list[str] = []
        checkpoints = MemoryCheckpointStore(events)
        lifecycle = ConnectorLifecycle(sink=RejectingSink(), checkpoints=checkpoints)

        result = await lifecycle.run(
            ContentConnector(ContentCapture(source_id="note-1", content="Remember this.")),
            _binding(),
        )

        assert result.status is ConnectorRunStatus.INCOMPLETE
        assert result.proposed_checkpoint == {"cursor": 1}
        assert result.committed_checkpoint is None
        assert result.items[0].status is ConnectorSubmissionStatus.REJECTED
        assert events == []

    asyncio.run(scenario())


def test_connector_rejects_duplicate_items_and_binding_mismatches() -> None:
    class DuplicateConnector(ContentConnector):
        async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
            await session.submit("note-1", CONTENT_SOURCE_NAME, self.capture)
            await session.submit("note-1", CONTENT_SOURCE_NAME, self.capture)
            return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE)

    async def scenario() -> None:
        events: list[str] = []
        store = IdempotentSourceStore(events)
        lifecycle = ConnectorLifecycle(
            sink=CatalogConnectorSourceSink(
                scope_id="scope-a",
                catalog=SourceCatalog(backend=store, registry=BUILTIN_SOURCE_REGISTRY),
                store=store,
            ),
            checkpoints=MemoryCheckpointStore(events),
        )
        capture = ContentCapture(source_id="note-1", content="Remember this.")

        with pytest.raises(InvalidConnectorRunError) as duplicate:
            await lifecycle.run(DuplicateConnector(capture), _binding())
        assert duplicate.value.issue == "duplicate-item"

        mismatched = _binding().model_copy(update={"connector_version": "2"})
        with pytest.raises(InvalidConnectorError) as binding_error:
            await lifecycle.run(ContentConnector(capture), mismatched)
        assert binding_error.value.field == "version"

    asyncio.run(scenario())


def test_catalog_connector_sink_rejects_a_different_scope_before_storage() -> None:
    async def scenario() -> None:
        events: list[str] = []
        store = IdempotentSourceStore(events)
        lifecycle = ConnectorLifecycle(
            sink=CatalogConnectorSourceSink(
                scope_id="scope-b",
                catalog=SourceCatalog(backend=store, registry=BUILTIN_SOURCE_REGISTRY),
                store=store,
            ),
            checkpoints=MemoryCheckpointStore(events),
        )

        result = await lifecycle.run(
            ContentConnector(ContentCapture(source_id="note-1", content="Remember this.")),
            _binding(),
        )

        assert result.status is ConnectorRunStatus.INCOMPLETE
        assert result.items[0].status is ConnectorSubmissionStatus.FAILED
        assert result.items[0].detail == "InvalidConnectorRunError"
        assert store.sources == {}
        assert events == []

    asyncio.run(scenario())
