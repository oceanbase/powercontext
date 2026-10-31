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

from typing import Any, cast

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactLineage
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.service import AtomicMemoryMergeAdapter


def test_historical_restore_preserves_absent_description() -> None:
    adapter = AtomicMemoryMergeAdapter(cast(Any, None))
    content = {"kind": "fact", "text": "A fact"}
    restored = adapter.draft(content, ArtifactLineage(), historical=True)
    assert restored.content.description is None


def test_new_write_preserves_description() -> None:
    adapter = AtomicMemoryMergeAdapter(cast(Any, None))
    draft = adapter.draft({"kind": "fact", "text": "A fact", "description": "A summary"}, ArtifactLineage())
    assert draft.content.description == "A summary"


def test_legacy_content_serialization_preserves_missing_description() -> None:
    payload = {"schema": "powercontext.atomic-memory.v1", "kind": "fact", "text": "A fact", "creation": None}
    content = AtomicMemoryContent.model_validate(payload)
    assert content.description is None
    assert content.model_dump(mode="json", by_alias=True) == payload


def test_content_serialization_preserves_description() -> None:
    content = AtomicMemoryContent(kind="fact", text="A complete fact", description="A summary")
    assert content.model_dump(mode="json")["description"] == "A summary"


@pytest.mark.parametrize("description", ["", "   ", "a\nb", "a\rb", "a" * 201])
def test_content_rejects_invalid_description(description: str) -> None:
    with pytest.raises(ValidationError):
        AtomicMemoryContent(kind="fact", text="A fact", description=description)


def test_atomic_memory_content_decodes_legacy_text_that_expands_under_nfc() -> None:
    text = "\N{DEVANAGARI LETTER QA}" * 1_366
    content = AtomicMemoryContent.model_validate({"kind": "fact", "text": text})

    assert content.text == text


@pytest.mark.parametrize(
    "text", [" " + "a" * 8_192 + " ", "e\N{COMBINING ACUTE ACCENT}" * 4_096], ids=["trimmed-limit", "nfc-limit"]
)
def test_atomic_memory_content_measures_byte_limit_after_normalization(text: str) -> None:
    content = AtomicMemoryContent(kind="fact", text=text)

    # Content is also the persisted revision model; writes normalize at their domain boundary.
    assert content.text == text


@pytest.mark.parametrize(
    "text", [" ", "a" * 8_193, "界" * 2_731, "🧠" * 2_049], ids=["blank", "ascii", "chinese", "emoji"]
)
def test_atomic_memory_content_rejects_empty_or_oversized_normalized_text(text: str) -> None:
    with pytest.raises(ValidationError):
        AtomicMemoryContent(kind="fact", text=text)


def test_legacy_candidate_proposal_remains_transport_readable() -> None:
    from powercontext.server.mapping import reviewed_proposal

    legacy = AtomicMemoryContent(kind="fact", text="A historical candidate")
    mapped = reviewed_proposal(legacy)
    from powercontext.http import AtomicMemoryReadContent

    assert isinstance(mapped, AtomicMemoryReadContent)
    assert mapped.text == legacy.text
    assert mapped.description is None
