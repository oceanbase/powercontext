from __future__ import annotations

import json
from typing import TypedDict, cast

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder

MEMORY_REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)


class _PreparedCitation(TypedDict):
    entry_id: str


class _PreparedItem(TypedDict):
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
