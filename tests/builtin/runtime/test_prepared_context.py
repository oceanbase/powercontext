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

import json
from typing import TypedDict, cast

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceSearchHit
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.prepared_context import (
    PreparedContextBuilder,
    PreparedExperienceCandidates,
    PreparedMemoryCandidates,
)

MEMORY_REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)


class _PreparedCitation(TypedDict, total=False):
    entry_id: str
    memory_ref: object
    memory: object
    artifact: object


class _PreparedItem(TypedDict):
    kind: str
    citation: _PreparedCitation
    content: str
    truncated: bool


def _hit(entry_id: str, text: str, *, memory_ref: ArtifactRef = MEMORY_REF) -> MemoryHit:
    return MemoryHit(
        memory_ref=memory_ref,
        entry_id=entry_id,
        entry_version_id=f"{entry_id}-v1",
        text=text,
        score=1.0,
        matched_by=("fts",),
    )


def _items(content: str | None) -> list[_PreparedItem]:
    assert content is not None
    return cast(list[_PreparedItem], json.loads(content.splitlines()[-2])["items"])


def _experience_hit(artifact_id: str = "experience-1", revision: int = 1) -> ExperienceSearchHit:
    return ExperienceSearchHit(
        artifact_ref=ArtifactRef(family="experience", artifact_id=artifact_id, revision=revision),
        content=ExperienceContent(
            situation="The generated API client is stale after an OpenAPI change.",
            action="Regenerate the client before running contract tests.",
            outcome="The checked-in transport matches the public contract.",
            lesson="Regenerate and inspect the client before contract tests.",
        ),
    )


def test_builder_preserves_order_and_filters_duplicate_or_invalid_hits() -> None:
    first = _hit("first", "First entry")
    prepared = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(
            first,
            first.model_copy(),
            _hit("invalid-text", "   "),
            _hit("", "Missing entry ID"),
            _hit("later", "Later entry"),
        ),
        request=PrepareContextRequest(query="entry"),
    )

    assert prepared.status == "ready"
    items = _items(prepared.content)
    assert [item["citation"]["entry_id"] for item in items] == ["first", "later"]
    assert prepared.content is not None
    assert prepared.content_bytes == len(prepared.content.encode("utf-8"))


def test_builder_truncates_unicode_and_owns_the_final_output_budget() -> None:
    text = "记忆🙂é" * 400
    request = PrepareContextRequest(query="记忆", max_bytes=800)

    first = PreparedContextBuilder().build(memory_ref=MEMORY_REF, hits=(_hit("unicode", text),), request=request)
    second = PreparedContextBuilder().build(memory_ref=MEMORY_REF, hits=(_hit("unicode", text),), request=request)

    assert first == second
    assert first.status == "ready"
    assert first.content_bytes <= request.max_bytes
    item = _items(first.content)[0]
    assert item["truncated"] is True
    assert str(item["content"]).endswith("…")


def test_builder_does_not_accept_truncated_unicode_below_the_minimum_byte_size() -> None:
    hit = _hit("emoji", "🙂" * 200)

    too_small = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(hit,),
        request=PrepareContextRequest(query="emoji", max_bytes=590),
    )
    large_enough = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(hit,),
        request=PrepareContextRequest(query="emoji", max_bytes=594),
    )

    assert too_small.status == "empty"
    item = _items(large_enough.content)[0]
    assert len(item["content"].encode("utf-8")) >= 64


def test_builder_skips_an_entry_that_cannot_fit_but_keeps_a_later_shorter_one() -> None:
    long_identifier = "a" * 128
    prepared = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(
            _hit(long_identifier, "long content " * 200),
            _hit("short", "small"),
        ),
        request=PrepareContextRequest(query="entry", max_bytes=620),
    )

    assert prepared.status == "ready"
    assert [item["citation"]["entry_id"] for item in _items(prepared.content)] == ["short"]
    assert prepared.content_bytes <= 620


def test_builder_rejects_a_hit_from_a_different_memory_head() -> None:
    other_ref = MEMORY_REF.model_copy(update={"revision": 4})

    with pytest.raises(PreparedContextInvariantError, match="memory-ref-mismatch"):
        PreparedContextBuilder().build(
            memory_ref=MEMORY_REF,
            hits=(_hit("other", "Other head", memory_ref=other_ref),),
            request=PrepareContextRequest(query="head"),
        )


def test_builder_prepares_experience_without_a_memory_head_and_keeps_v1_envelope() -> None:
    prepared = PreparedContextBuilder().build(
        experience_hits=(_experience_hit(),),
        request=PrepareContextRequest(query="Regenerate client contract tests"),
    )

    assert prepared.status == "ready"
    assert prepared.schema_version == "powercontext.prepared-context.v1"
    assert prepared.content is not None
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in prepared.content
    item = _items(prepared.content)[0]
    assert item["kind"] == "experience"
    assert item["citation"] == {
        "artifact_ref": {
            "family": "experience",
            "artifact_id": "experience-1",
            "revision": 1,
        }
    }
    assert item["content"].endswith("Lesson: Regenerate and inspect the client before contract tests.")


def test_builder_qualifies_only_cross_scope_citations() -> None:
    builder = PreparedContextBuilder()
    prepared = builder.build_scopes_result(
        request=PrepareContextRequest(query="shared evidence"),
        current_scope_id="current",
        memory_candidates=(
            PreparedMemoryCandidates(scope_id="current", memory_ref=MEMORY_REF, hits=(_hit("local", "Local"),)),
            PreparedMemoryCandidates(scope_id="shared", memory_ref=MEMORY_REF, hits=(_hit("shared", "Shared"),)),
        ),
        experience_candidates=(PreparedExperienceCandidates(scope_id="shared", hits=(_experience_hit(),)),),
    ).context

    local, experience, shared = _items(prepared.content)
    assert local["citation"]["memory_ref"] == MEMORY_REF.model_dump(mode="json")
    assert shared["citation"]["memory"] == {
        "scope_id": "shared",
        "artifact": MEMORY_REF.model_dump(mode="json"),
    }
    assert experience["citation"]["artifact"] == {
        "scope_id": "shared",
        "artifact": {
            "family": "experience",
            "artifact_id": "experience-1",
            "revision": 1,
        },
    }


def test_builder_keeps_memory_primary_and_bounds_experience_share() -> None:
    experiences = tuple(_experience_hit(f"experience-{index}") for index in range(1, 5))
    prepared = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(_hit("first", "First Memory entry"), _hit("second", "Second Memory entry")),
        experience_hits=experiences,
        request=PrepareContextRequest(query="client"),
    )

    items = _items(prepared.content)
    assert [item.get("kind", "memory") for item in items] == [
        "memory",
        "experience",
        "memory",
        "experience",
    ]


def test_builder_rejects_non_experience_recall_hits() -> None:
    hit = _experience_hit().model_copy(
        update={"artifact_ref": ArtifactRef(family="skill", artifact_id="skill-1", revision=1)}
    )

    with pytest.raises(PreparedContextInvariantError, match="experience-family-mismatch"):
        PreparedContextBuilder().build(
            experience_hits=(hit,),
            request=PrepareContextRequest(query="client"),
        )


@pytest.mark.parametrize(
    "value",
    [
        {"query": "   "},
        {"query": "query", "max_bytes": True},
        {"query": "query", "max_bytes": 511},
        {"query": "query", "max_bytes": 32769},
    ],
)
def test_prepare_request_rejects_values_outside_the_runtime_contract(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate(value)


def test_empty_context_has_no_source_specific_status_or_content() -> None:
    prepared = PreparedContextBuilder().empty()

    assert prepared.status == "empty"
    assert prepared.content is None
    assert prepared.content_bytes == 0
