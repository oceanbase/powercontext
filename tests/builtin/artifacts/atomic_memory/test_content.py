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

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent


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
