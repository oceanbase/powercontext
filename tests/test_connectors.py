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
from copy import deepcopy

import pytest
from pydantic import JsonValue

from powercontext import ConnectorSubmissionRejectedError, InvalidConnectorError, InvalidConnectorRunError
from powercontext.builtin.sources import CONTENT_SOURCE_NAME, ContentCapture
from powercontext.sources import (
    ConnectorBinding,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
    SourceRef,
)
from powercontext.sources.connectors import ConnectorLifecycle


class AcceptingSourceSink:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def submit(
        self,
        binding: ConnectorBinding,
        item_id: str,
        definition_name: str,
        value: object,
        /,
    ) -> SourceRef:
        del binding, value
        self.events.append(f"source:{item_id}")
        return SourceRef(source_type=definition_name, source_id=item_id)


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
        checkpoints = MemoryCheckpointStore(events)
        lifecycle = ConnectorLifecycle(
            sink=AcceptingSourceSink(events),
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

        replay = await lifecycle.run(connector, _binding())
        assert replay.previous_checkpoint == {"cursor": 1}
        assert events == ["source:note-1", "checkpoint", "source:note-1"]

    asyncio.run(scenario())


def test_connector_exposes_failed_items_and_does_not_advance_checkpoint() -> None:
    class RejectingConnector(ContentConnector):
        async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
            session.reject(self.capture.source_id, CONTENT_SOURCE_NAME, "unsupported value")
            return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint={"cursor": 1})

    async def scenario() -> None:
        events: list[str] = []
        checkpoints = MemoryCheckpointStore(events)
        lifecycle = ConnectorLifecycle(sink=AcceptingSourceSink(events), checkpoints=checkpoints)

        result = await lifecycle.run(
            RejectingConnector(ContentCapture(source_id="note-1", content="Remember this.")),
            _binding(),
        )

        assert result.status is ConnectorRunStatus.INCOMPLETE
        assert result.proposed_checkpoint == {"cursor": 1}
        assert result.committed_checkpoint is None
        assert result.items[0].status is ConnectorSubmissionStatus.REJECTED
        assert result.items[0].detail == "unsupported value"
        assert events == []

    asyncio.run(scenario())


def test_connector_does_not_advance_an_incomplete_run_checkpoint() -> None:
    class IncompleteConnector(ContentConnector):
        async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
            await session.submit(self.capture.source_id, CONTENT_SOURCE_NAME, self.capture)
            return ConnectorRunCompletion(
                status=ConnectorRunStatus.INCOMPLETE,
                checkpoint={"cursor": 1},
            )

    async def scenario() -> None:
        events: list[str] = []
        lifecycle = ConnectorLifecycle(
            sink=AcceptingSourceSink(events),
            checkpoints=MemoryCheckpointStore(events),
        )

        result = await lifecycle.run(
            IncompleteConnector(ContentCapture(source_id="note-1", content="Remember this.")),
            _binding(),
        )

        assert result.status is ConnectorRunStatus.INCOMPLETE
        assert result.proposed_checkpoint == {"cursor": 1}
        assert result.committed_checkpoint is None
        assert events == ["source:note-1"]

    asyncio.run(scenario())


def test_connector_rejects_duplicate_items_and_binding_mismatches() -> None:
    class DuplicateConnector(ContentConnector):
        async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
            await session.submit("note-1", CONTENT_SOURCE_NAME, self.capture)
            await session.submit("note-1", CONTENT_SOURCE_NAME, self.capture)
            return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE)

    async def scenario() -> None:
        events: list[str] = []
        lifecycle = ConnectorLifecycle(
            sink=AcceptingSourceSink(events),
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


def test_connector_propagates_sink_failures_without_committing_a_checkpoint() -> None:
    class SinkFailure(RuntimeError):
        pass

    class FailingSink:
        async def submit(
            self,
            binding: ConnectorBinding,
            item_id: str,
            definition_name: str,
            value: object,
            /,
        ) -> SourceRef:
            del binding, item_id, definition_name, value
            raise SinkFailure

    async def scenario() -> None:
        events: list[str] = []
        lifecycle = ConnectorLifecycle(
            sink=FailingSink(),
            checkpoints=MemoryCheckpointStore(events),
        )

        with pytest.raises(SinkFailure):
            await lifecycle.run(
                ContentConnector(ContentCapture(source_id="note-1", content="Remember this.")),
                _binding(),
            )
        assert events == []

    asyncio.run(scenario())


def test_connector_records_a_typed_submission_rejection_without_committing_a_checkpoint() -> None:
    class RejectingSink:
        async def submit(
            self,
            binding: ConnectorBinding,
            item_id: str,
            definition_name: str,
            value: object,
            /,
        ) -> SourceRef:
            del binding, item_id, definition_name, value
            raise ConnectorSubmissionRejectedError(  # noqa: TRY003
                "materialized value exceeds the submission budget"
            )

    async def scenario() -> None:
        events: list[str] = []
        lifecycle = ConnectorLifecycle(
            sink=RejectingSink(),
            checkpoints=MemoryCheckpointStore(events),
        )

        result = await lifecycle.run(
            ContentConnector(ContentCapture(source_id="note-1", content="Remember this.")),
            _binding(),
        )

        assert result.status is ConnectorRunStatus.INCOMPLETE
        assert result.committed_checkpoint is None
        assert result.items[0].status is ConnectorSubmissionStatus.REJECTED
        assert result.items[0].detail == "materialized value exceeds the submission budget"
        assert events == []

    asyncio.run(scenario())
