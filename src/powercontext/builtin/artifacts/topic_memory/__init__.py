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

"""The built-in Topic Memory Artifact family."""

from powercontext.builtin.artifacts.topic_memory.models import (
    MAX_TOPIC_MEMORY_DETAIL_LENGTH,
    MAX_TOPIC_MEMORY_SUMMARY_LENGTH,
    MAX_TOPIC_MEMORY_TITLE_LENGTH,
    TopicMemory,
    TopicMemoryContent,
    TopicMemoryDraft,
)

TOPIC_MEMORY_SOURCE_WINDOW_BINDING = "topic-memory-source-window"

__all__ = [
    "MAX_TOPIC_MEMORY_DETAIL_LENGTH",
    "MAX_TOPIC_MEMORY_SUMMARY_LENGTH",
    "MAX_TOPIC_MEMORY_TITLE_LENGTH",
    "TOPIC_MEMORY_SOURCE_WINDOW_BINDING",
    "TopicMemory",
    "TopicMemoryContent",
    "TopicMemoryDraft",
]
