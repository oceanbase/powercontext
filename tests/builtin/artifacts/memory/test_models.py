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

from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryEntryVersion


def test_memory_entry_kind_is_an_open_non_empty_string() -> None:
    entry = MemoryEntryVersion(
        memory_artifact_id="memory-a",
        entry_id="entry-a",
        entry_version_id="version-a1",
        version=1,
        previous_version_id=None,
        kind="integration-owned-kind",
        text="Durable text.",
        entry_content_hash="b" * 64,
        created_in_revision=1,
    )
    candidate = MemoryEntryInput(kind="integration-owned-kind", text="Durable text.")

    assert entry.kind == candidate.kind == "integration-owned-kind"
    assert entry.sources == candidate.sources == ()
    assert entry.artifacts == ()
