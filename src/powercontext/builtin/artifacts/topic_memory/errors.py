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

"""Typed failures for Topic Memory storage and retrieval."""

from powercontext.errors import PowerContextError


class TopicMemoryError(PowerContextError):
    """Base error for Topic Memory domain operations."""


class TopicMemoryCapabilityError(TopicMemoryError, RuntimeError):
    def __init__(self, capability: str, detail: str | None = None) -> None:
        self.capability = capability
        self.detail = detail
        message = f"topic-memory capability is not supported: {capability}"
        if detail is not None:
            message = f"{message} ({detail})"
        super().__init__(message)


class TopicMemoryProjectionError(TopicMemoryError, ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "content": "Topic Memory projection content does not match the draft",
            "fts": "Topic Memory publication requires both FTS channels",
            "vector-incomplete": "Topic Memory vector publication requires the topic and every detail chunk",
            "vector-unconfigured": "Topic Memory projection includes vectors but the deployment is FTS-only",
            "embedding-profile": "Topic Memory projection uses a different embedding profile",
            "embedding-values": "Topic Memory projection contains an invalid embedding vector",
        }
        super().__init__(messages.get(code, f"invalid Topic Memory projection: {code}"))


class TopicMemoryStorageInvariantError(TopicMemoryError, RuntimeError):
    def __init__(self, code: str, identity: object) -> None:
        self.code = code
        self.identity = identity
        super().__init__(f"Topic Memory storage invariant failed ({code}): {identity!r}")


__all__ = [
    "TopicMemoryCapabilityError",
    "TopicMemoryError",
    "TopicMemoryProjectionError",
    "TopicMemoryStorageInvariantError",
]
