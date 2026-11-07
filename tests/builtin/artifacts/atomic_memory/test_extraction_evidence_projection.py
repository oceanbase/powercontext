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
from typing import cast

from pydantic import JsonValue

from powercontext import SourceMaterialization
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    project_atomic_memory_evidence,
)
from powercontext.builtin.persistence.records import _canonical_source_text
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, CONTENT_SOURCE_ADAPTER, ContentCapture, ContentSource
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    Source,
    SourceObservation,
    SourceProjectionKey,
    SourceProjectionValue,
    SourceRef,
    SourceSequenceLocator,
    TextEvidence,
    project_source_for_transport,
)

MARKER = "UNIQUE_EVIDENCE_MARKER"
BODY = f"{MARKER} Refactor the recall builder so the host skips retrieval when no history is relevant."


def _source(*, wire_content: JsonValue | None = None) -> ContentSource:
    """Mirror the writer path: ``content`` is the canonical text of ``wire_content``."""

    return ContentSource(
        name="turn-1",
        materialization=SourceMaterialization.CAPTURED,
        content=BODY if wire_content is None else _canonical_source_text(wire_content),
        metadata={"origin": "pi", "event": "user_prompt_submit", "session_id": "session-1"},
        wire_content=wire_content,
        wire_content_present=wire_content is not None,
    )


class _CustomSource(Source):
    """A Source whose wire payload is independent of its text body."""

    content: str
    wire_content: JsonValue
    wire_content_present: bool = True


class _TextEvidenceSources:
    """Adapter stand-in that exposes ``content`` as the evidence body."""

    def __init__(self, source: _CustomSource) -> None:
        self._source = source

    async def read_value(self, *_args: object, **_kwargs: object) -> TextEvidence:
        return TextEvidence(source_type="custom", source_id=self._source.name, content=self._source.content)


class _InMemorySources:
    """Stand-in for ``SourceRepository.read_value`` on the native path."""

    def __init__(self, source: ContentSource) -> None:
        self._source = source

    async def read_value(self, *_args: object, **_kwargs: object) -> ContentCapture:
        return await CONTENT_SOURCE_ADAPTER.read(self._source)


async def _evidence(stored_value: Source, native_source: ContentSource) -> AtomicMemoryEvidence:
    stored = StoredSource(
        ref=SourceRef(source_type="content", source_id=native_source.name),
        value=stored_value,
        journal_position=1,
    )
    return await project_atomic_memory_evidence(
        stored,
        cast("SourceRepository", _InMemorySources(native_source)),
        evidence_id="source:1",
    )


def _serialized(evidence: AtomicMemoryEvidence) -> str:
    return AtomicMemoryExtractionInput(evidence=(evidence,)).model_dump_json()


def test_native_source_body_is_serialized_once() -> None:
    """The native path sends the body once and keeps its provenance fields."""

    async def scenario() -> None:
        source = _source()
        evidence = await _evidence(source, source)

        payload = _serialized(evidence)
        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)

        assert payload.count(MARKER) == 1
        assert "content" not in metadata
        assert metadata["metadata"] == source.metadata
        assert metadata["name"] == source.name

        assert evidence.evidence_id == "source:1"
        assert evidence.journal_position == 1
        assert evidence.source_ref == SourceRef(
            source_type="content", source_id="turn-1", locator=SourceSequenceLocator(sequence=1)
        )
        assert isinstance(evidence.content, dict)
        assert evidence.content["content"] == BODY

    asyncio.run(scenario())


def test_projected_observation_body_is_serialized_once() -> None:
    """The projected observation sends the body once and keeps payload metadata."""

    async def scenario() -> None:
        source = _source()
        observation = project_source_for_transport(BUILTIN_SOURCE_REGISTRY, source)
        evidence = await _evidence(observation, source)

        payload = _serialized(evidence)
        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)

        assert payload.count(MARKER) == 1
        assert "projections" not in metadata
        assert metadata["source_type"] == "content"
        payload_metadata = metadata["payload"]
        assert isinstance(payload_metadata, dict)
        assert "content" not in payload_metadata
        assert payload_metadata["metadata"] == source.metadata
        assert payload_metadata["name"] == source.name

        assert evidence.evidence_id == "source:1"
        assert evidence.journal_position == 1
        assert evidence.source_ref == SourceRef(
            source_type="content", source_id="turn-1", locator=SourceSequenceLocator(sequence=1)
        )
        assert isinstance(evidence.content, dict)
        assert evidence.content["content"] == BODY

    asyncio.run(scenario())


def test_native_wire_content_duplicate_is_serialized_once() -> None:
    """A wire payload equal to the body is not serialized twice."""

    async def scenario() -> None:
        source = _source(wire_content=BODY)
        evidence = await _evidence(source, source)

        assert _serialized(evidence).count(MARKER) == 1
        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        assert "wire_content" not in metadata
        assert "wire_content_present" not in metadata
        assert metadata["metadata"] == source.metadata

    asyncio.run(scenario())


def test_native_structured_wire_content_is_serialized_once() -> None:
    """A structured wire payload is not serialized beside its canonical text."""

    async def scenario() -> None:
        wire = {"text": BODY, "kind": "note"}
        source = _source(wire_content=wire)
        evidence = await _evidence(source, source)

        assert _serialized(evidence).count(MARKER) == 1
        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        assert "wire_content" not in metadata
        assert "wire_content_present" not in metadata
        assert metadata["metadata"] == source.metadata
        assert metadata["name"] == source.name

    asyncio.run(scenario())


def test_observation_without_text_projection_is_serialized_once() -> None:
    """Without a projection, the payload is the body and is removed from metadata."""

    async def scenario() -> None:
        source = _source()
        observation = project_source_for_transport(BUILTIN_SOURCE_REGISTRY, source)
        observation = observation.model_copy(update={"projections": ()})
        evidence = await _evidence(observation, source)

        assert _serialized(evidence).count(MARKER) == 1
        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        assert "payload" not in metadata
        assert metadata["source_type"] == "content"
        assert evidence.content == observation.payload

    asyncio.run(scenario())


def test_observation_keeps_projections_other_than_text_evidence() -> None:
    """Only the consumed text-evidence projection is dropped."""

    async def scenario() -> None:
        source = _source()
        observation = project_source_for_transport(BUILTIN_SOURCE_REGISTRY, source)
        summary = SourceProjectionValue(
            key=SourceProjectionKey(name="custom.summary", version="1"),
            value={"note": "keep"},
        )
        observation = observation.model_copy(update={"projections": (*observation.projections, summary)})
        evidence = await _evidence(observation, source)

        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        projections = metadata["projections"]
        assert [projection["key"]["name"] for projection in projections] == ["custom.summary"]
        assert _serialized(evidence).count(MARKER) == 1

    asyncio.run(scenario())


def test_observation_keeps_payload_content_that_differs_from_projection() -> None:
    """Payload content is only removed when it equals the projected evidence."""

    async def scenario() -> None:
        source = _source()
        observation = project_source_for_transport(BUILTIN_SOURCE_REGISTRY, source)
        payload = dict(observation.payload)
        payload["content"] = "DIFFERENT_BODY"
        observation = observation.model_copy(update={"payload": payload})
        evidence = await _evidence(observation, source)

        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        payload_metadata = metadata["payload"]
        assert payload_metadata["content"] == "DIFFERENT_BODY"
        assert _serialized(evidence).count(MARKER) == 1

    asyncio.run(scenario())


def test_native_keeps_wire_evidence_that_differs_from_body() -> None:
    """The native path keeps wire evidence that differs from the body text."""

    async def scenario() -> None:
        wire = {"constraint": "Pause when errors rise."}
        source = _CustomSource(
            name="deploy",
            materialization=SourceMaterialization.CAPTURED,
            content=BODY,
            wire_content=wire,
        )
        stored = StoredSource(
            ref=SourceRef(source_type="custom", source_id="deploy"),
            value=source,
            journal_position=1,
        )
        evidence = await project_atomic_memory_evidence(
            stored,
            cast("SourceRepository", _TextEvidenceSources(source)),
            evidence_id="source:1",
        )

        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        assert "content" not in metadata
        assert metadata["wire_content"] == wire
        assert metadata["wire_content_present"] is True
        assert _serialized(evidence).count(MARKER) == 1

    asyncio.run(scenario())


def test_observation_payload_drops_wire_content_equal_to_body() -> None:
    """A payload wire carrier that repeats the projected body is removed."""

    async def scenario() -> None:
        source = _source(wire_content=BODY)
        observation = project_source_for_transport(BUILTIN_SOURCE_REGISTRY, source)
        evidence = await _evidence(observation, source)

        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        payload_metadata = metadata["payload"]
        assert isinstance(payload_metadata, dict)
        assert "content" not in payload_metadata
        assert "wire_content" not in payload_metadata
        assert "wire_content_present" not in payload_metadata
        assert _serialized(evidence).count(MARKER) == 1

    asyncio.run(scenario())


def test_observation_payload_keeps_wire_evidence_that_differs_from_body() -> None:
    """A payload wire carrier with independent evidence survives compaction."""

    async def scenario() -> None:
        wire = {"constraint": "Pause when errors rise."}
        observation = SourceObservation(
            name="deploy",
            definition_version="1",
            materialization=SourceMaterialization.CAPTURED,
            source_type="custom",
            definition_fingerprint="sha256:" + "0" * 64,
            payload={
                "name": "deploy",
                "definition_version": "1",
                "materialization": "captured",
                "description": None,
                "content": BODY,
                "wire_content": wire,
                "wire_content_present": True,
            },
            projections=(
                SourceProjectionValue(
                    key=TEXT_EVIDENCE_PROJECTION_KEY,
                    value={"source_type": "custom", "source_id": "deploy", "content": BODY, "metadata": {}},
                ),
            ),
        )
        stored = StoredSource(
            ref=SourceRef(source_type="custom", source_id="deploy"),
            value=observation,
            journal_position=1,
        )
        evidence = await project_atomic_memory_evidence(
            stored,
            cast("SourceRepository", _InMemorySources(_source())),
            evidence_id="source:1",
        )

        metadata = evidence.source_metadata
        assert isinstance(metadata, dict)
        payload_metadata = metadata["payload"]
        assert isinstance(payload_metadata, dict)
        assert "content" not in payload_metadata
        assert payload_metadata["wire_content"] == wire
        assert payload_metadata["wire_content_present"] is True
        assert _serialized(evidence).count(MARKER) == 1

    asyncio.run(scenario())
