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

"""Typed content for the built-in Topic Memory Artifact family."""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, field_validator

from powercontext.artifacts import Artifact, ArtifactDraft

MAX_TOPIC_MEMORY_TITLE_LENGTH = 512
MAX_TOPIC_MEMORY_SUMMARY_LENGTH = 8_000
MAX_TOPIC_MEMORY_DETAIL_LENGTH = 125_000

TopicMemoryTitle = Annotated[str, Field(min_length=1, max_length=MAX_TOPIC_MEMORY_TITLE_LENGTH)]
TopicMemorySummary = Annotated[str, Field(min_length=1, max_length=MAX_TOPIC_MEMORY_SUMMARY_LENGTH)]
TopicMemoryDetail = Annotated[str, Field(min_length=1, max_length=MAX_TOPIC_MEMORY_DETAIL_LENGTH)]


class TopicMemoryContent(BaseModel):
    """Progressively disclosed content for one durable topic."""

    title: TopicMemoryTitle
    summary: TopicMemorySummary
    detail: TopicMemoryDetail

    @field_validator("title", "summary", "detail")
    @classmethod
    def reject_blank_sections(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Topic Memory sections must not be blank")  # noqa: TRY003
        return value


class TopicMemory(Artifact[TopicMemoryContent]):
    """An immutable Topic Memory revision."""

    family: ClassVar[str] = "topic-memory"


class TopicMemoryDraft(ArtifactDraft[TopicMemoryContent]):
    """Complete Topic Memory content and evidence ready for commit."""

    family: ClassVar[str] = "topic-memory"


__all__ = [
    "MAX_TOPIC_MEMORY_DETAIL_LENGTH",
    "MAX_TOPIC_MEMORY_SUMMARY_LENGTH",
    "MAX_TOPIC_MEMORY_TITLE_LENGTH",
    "TopicMemory",
    "TopicMemoryContent",
    "TopicMemoryDraft",
]
