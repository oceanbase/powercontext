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

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemoryContent, TopicMemoryDraft


def test_topic_memory_content_preserves_progressively_disclosed_sections() -> None:
    content = TopicMemoryContent(
        title="Supervisor recovery",
        summary="Leases fence stale workers.",
        detail="# Recovery\nRetry pending windows.",
    )

    assert content.model_dump() == {
        "title": "Supervisor recovery",
        "summary": "Leases fence stale workers.",
        "detail": "# Recovery\nRetry pending windows.",
    }


@pytest.mark.parametrize("field", ["title", "summary", "detail"])
def test_topic_memory_content_rejects_blank_sections(field: str) -> None:
    payload = {
        "title": "Supervisor recovery",
        "summary": "Leases fence stale workers.",
        "detail": "Full detail.",
    }
    payload[field] = "\n\t"

    with pytest.raises(ValidationError):
        TopicMemoryContent.model_validate(payload)


def test_topic_memory_types_use_the_topic_memory_family() -> None:
    content = TopicMemoryContent(title="Title", summary="Summary", detail="Detail")

    assert TopicMemory(artifact_id="topic-1", revision=1, content=content).as_ref().family == "topic-memory"
    assert TopicMemoryDraft(content=content).family == "topic-memory"
