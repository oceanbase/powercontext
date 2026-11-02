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

"""One Source body, one copy: the extraction input must not re-send text the model already read.

``AtomicMemoryEvidence.content`` carries the body. A full Source dump used to carry it again inside
``source_metadata`` — twice more for an observation — so a batch crossed the input token budget on
bytes that were already in the prompt. These tests pin the body count and, just as important, that
nothing else was dropped on the way.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from pydantic import JsonValue

from powercontext.builtin.artifacts.atomic_memory.extraction import project_atomic_memory_evidence
from powercontext.builtin.persistence.records import _canonical_source_text
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.sources.content import CONTENT_SOURCE_NAME, ContentCapture, ContentSource
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceMaterialization,
    SourceObservation,
    SourceProjectionKey,
    SourceProjectionValue,
    SourceRef,
    TextEvidence,
)

_BODY = (
    "Some context before we start: last week the team decided that the ticket service database will run "
    "on OceanBase with the shard count fixed at 12, because it has to stay compatible with our existing "
    "MySQL clients."
)
_METADATA: dict[str, JsonValue] = {"event": "tool_result", "session": "s-42"}
_FINGERPRINT = "sha256:" + "0" * 64


class _ReadsThroughAdapter:
    """The only thing the projection needs from a Repository: the adapter read of one Source."""

    def __init__(self, capture: ContentCapture) -> None:
        self._capture = capture

    async def read_value(self, source: object, /) -> object:
        return self._capture


def _native_source(*, content: str = _BODY, **overrides: Any) -> ContentSource:
    return ContentSource(
        name="capture-1",
        materialization=SourceMaterialization.CAPTURED,
        content=content,
        metadata=_METADATA,
        **overrides,
    )


def _observation(projections: tuple[SourceProjectionValue, ...]) -> SourceObservation:
    return SourceObservation(
        name="observation-1",
        materialization=SourceMaterialization.CAPTURED,
        source_type="tool-result",
        definition_fingerprint=_FINGERPRINT,
        payload={
            "name": "observation-1",
            "definition_version": "1",
            "materialization": SourceMaterialization.CAPTURED.value,
            "description": None,
            "content": _BODY,
            "metadata": _METADATA,
            "tool": "bash",
        },
        projections=projections,
    )


def _text_projection() -> SourceProjectionValue:
    return SourceProjectionValue(
        key=TEXT_EVIDENCE_PROJECTION_KEY,
        value=TextEvidence(
            source_type="tool-result",
            source_id="observation-1",
            content=_BODY,
            metadata=_METADATA,
        ).model_dump(mode="json"),
    )


async def _evidence(value: ContentSource | SourceObservation, *, read: str = _BODY) -> Any:
    stored = StoredSource(
        ref=SourceRef(source_type=CONTENT_SOURCE_NAME, source_id=value.name),
        value=value,
        journal_position=1,
    )
    capture = ContentCapture(source_id=value.name, content=read, metadata=_METADATA)
    sources = cast("SourceRepository", _ReadsThroughAdapter(capture))
    return await project_atomic_memory_evidence(stored, sources, evidence_id="source:1")


def _copies(evidence: Any, text: str = _BODY) -> int:
    return json.dumps(evidence.model_dump(mode="json")).count(text)


def _body_copies(evidence: Any) -> int:
    return _copies(evidence)


def test_a_native_source_body_appears_once() -> None:
    """The adapter read is ``content``; ``source_metadata.content`` was the second copy."""

    evidence = asyncio.run(_evidence(_native_source()))

    assert _body_copies(evidence) == 1
    assert evidence.content == {"source_id": "capture-1", "content": _BODY, "metadata": _METADATA}
    assert "content" not in evidence.source_metadata


def test_a_native_source_keeps_every_other_provenance_field() -> None:
    """Only the body goes: the model still needs the identity and the caller's metadata."""

    evidence = asyncio.run(_evidence(_native_source(working_note=True, description="a capture")))

    assert evidence.source_metadata["name"] == "capture-1"
    assert evidence.source_metadata["description"] == "a capture"
    assert evidence.source_metadata["definition_version"] == "1"
    assert evidence.source_metadata["working_note"] is True
    assert evidence.source_metadata["metadata"] == _METADATA


def test_wire_bytes_that_repeat_the_body_are_dropped() -> None:
    """A capture that stored the same text as its wire body paid for it twice."""

    evidence = asyncio.run(_evidence(_native_source(wire_content=_BODY, wire_content_present=True)))

    assert _body_copies(evidence) == 1
    assert "wire_content" not in evidence.source_metadata


def test_wire_bytes_that_carry_something_else_survive() -> None:
    """``wire_content`` is only redundant when it *is* the body; a structured wire body is evidence."""

    wire = {"messages": [{"role": "user"}]}
    evidence = asyncio.run(_evidence(_native_source(wire_content=wire, wire_content_present=True)))

    assert evidence.source_metadata["wire_content"] == wire


def test_a_structured_wire_body_that_encodes_the_body_is_also_redundant() -> None:
    """Capture stores the canonical wire text as ``content``, so a JSON body repeats it too.

    Comparing the raw value against the body string would never match a dict and would leave the
    whole structured capture in the prompt; the comparison has to go through the same canonical
    encoding the write path used.
    """

    wire = {"text": _BODY, "kind": "note"}
    stored_text = _canonical_source_text(wire)
    evidence = asyncio.run(
        _evidence(
            _native_source(content=stored_text, wire_content=wire, wire_content_present=True),
            read=stored_text,
        )
    )

    assert _body_copies(evidence) == 1
    assert "wire_content" not in evidence.source_metadata
    assert "content" not in evidence.source_metadata
    assert evidence.content["content"] == stored_text


def test_a_read_that_disagrees_with_the_stored_body_keeps_both() -> None:
    """The adapter may materialise something the stored ``content`` does not say; that is evidence."""

    read = "The deployment region must be eu-west-1."
    evidence = asyncio.run(_evidence(_native_source(), read=read))

    assert evidence.content["content"] == read
    assert evidence.source_metadata["content"] == _BODY


def test_a_projection_content_that_differs_from_the_body_is_not_a_copy() -> None:
    """Three different facts share the key ``content``; only the one the input already carries goes.

    Dropping them by name would remove two facts the extractor never saw while the cursor still
    advanced past the Source, which is silent data loss rather than a token saving.
    """

    canary = "Use canary deployment."
    region = "The deployment region must be eu-west-1."
    budget = "The deployment budget is 37 units."
    observation = SourceObservation(
        name="observation-1",
        materialization=SourceMaterialization.CAPTURED,
        source_type="tool-result",
        definition_fingerprint=_FINGERPRINT,
        payload={
            "name": "observation-1",
            "definition_version": "1",
            "materialization": SourceMaterialization.CAPTURED.value,
            "description": None,
            "content": region,
            "metadata": _METADATA,
            "tool": "bash",
        },
        projections=(
            SourceProjectionValue(
                key=TEXT_EVIDENCE_PROJECTION_KEY,
                value=TextEvidence(
                    source_type="tool-result",
                    source_id="observation-1",
                    content=canary,
                    metadata=_METADATA,
                ).model_dump(mode="json"),
            ),
            SourceProjectionValue(
                key=SourceProjectionKey(name="powercontext.deploy-budget", version="1"),
                value={"content": budget, "units": "USD"},
            ),
        ),
    )

    evidence = asyncio.run(_evidence(observation, read=canary))

    for text in (canary, region, budget):
        assert _copies(evidence, text) == 1, text
    assert evidence.content["content"] == canary
    assert evidence.source_metadata["payload"]["content"] == region
    assert evidence.source_metadata["projections"] == [
        {
            "key": {"name": "powercontext.deploy-budget", "version": "1"},
            "value": {"content": budget, "units": "USD"},
        }
    ]


def test_an_observation_that_projections_text_keeps_one_body() -> None:
    """``content`` is the text-evidence projection, so the projection and ``payload.content`` both repeat it."""

    evidence = asyncio.run(_evidence(_observation((_text_projection(),))))

    assert _body_copies(evidence) == 1
    assert evidence.content["content"] == _BODY
    assert "content" not in evidence.source_metadata["payload"]
    # The projection's value was the ``content`` object itself, so the entry carries nothing new.
    assert "projections" not in evidence.source_metadata


def test_an_observation_keeps_metadata_and_the_rest_of_its_payload() -> None:
    evidence = asyncio.run(_evidence(_observation((_text_projection(),))))

    assert evidence.source_metadata["payload"]["metadata"] == _METADATA
    assert evidence.source_metadata["payload"]["tool"] == "bash"
    assert evidence.source_metadata["source_type"] == "tool-result"
    assert evidence.source_metadata["definition_fingerprint"] == _FINGERPRINT


def test_an_observation_without_a_text_projection_still_pays_once() -> None:
    """Without the projection ``content`` is the payload, and ``payload.content`` repeated it."""

    evidence = asyncio.run(_evidence(_observation(())))

    assert _body_copies(evidence) == 1
    assert "payload" not in evidence.source_metadata
    assert evidence.content["tool"] == "bash"


def test_another_projection_keeps_its_keys_but_not_a_repeated_body() -> None:
    """Only the body is redundant; a second projection may carry facts the extraction needs."""

    other = SourceProjectionValue(
        key=SourceProjectionKey(name="powercontext.ticket-summary", version="1"),
        value={"content": _BODY, "database": "OceanBase", "shards": 12},
    )
    evidence = asyncio.run(_evidence(_observation((_text_projection(), other))))

    assert _body_copies(evidence) == 1
    assert evidence.source_metadata["projections"] == [
        {
            "key": {"name": "powercontext.ticket-summary", "version": "1"},
            "value": {"database": "OceanBase", "shards": 12},
        }
    ]
