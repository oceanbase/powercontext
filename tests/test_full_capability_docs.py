# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "document",
    [
        "docs/en/docs/how-to/full-capability-runtime.md",
        "docs/zh/docs/how-to/full-capability-runtime.md",
    ],
)
def test_full_capability_guide_binds_memory_evidence_to_the_captured_source(document: str) -> None:
    content = Path(document).read_text(encoding="utf-8")

    assert 'SOURCE_ID="quickstart-$(date +%s)-$$"' in content
    assert "/v1/memory/entries/list" in content
    assert "source_refs" in content
    assert "current_cursor" in content and "position" in content
    assert "entry_id" in content and "matched_by" in content
