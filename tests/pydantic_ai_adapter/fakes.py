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

from typing import Any, ClassVar

from powercontext.http import (
    ArtifactReference,
    CaptureContentSourceResponse,
    CaptureStatus,
    FlushMemoryResponse,
    FlushStatus,
    MemoryCitation,
    MemoryEntry,
    MemoryEntryState,
    MemoryMatchedBy,
    MemoryMutationResponse,
    MemoryUsedSearchMode,
    PreparedContext,
    SearchMemoryHit,
    SearchMemoryResponse,
    SourceReference,
)


def artifact(revision: int = 7) -> ArtifactReference:
    return ArtifactReference(family="memory", artifact_id="project-memory", revision=revision)


def search_response() -> SearchMemoryResponse:
    memory = artifact()
    return SearchMemoryResponse(
        memory=memory,
        mode=MemoryUsedSearchMode.FTS,
        hits=[
            SearchMemoryHit(
                citation=MemoryCitation(
                    memory_ref=memory,
                    entry_id="entry-1",
                    entry_version_id="entry-version-1",
                ),
                text="Keep the public response intact.",
                score=0.875,
                matched_by=[MemoryMatchedBy.FTS],
            )
        ],
    )


def remember_response() -> MemoryMutationResponse:
    memory = artifact(revision=8)
    citation = MemoryCitation(
        memory_ref=memory,
        entry_id="entry-2",
        entry_version_id="entry-version-2",
    )
    return MemoryMutationResponse(
        memory=memory,
        entry=MemoryEntry(
            citation=citation,
            version=1,
            kind="decision",
            text="Use the public client.",
            state=MemoryEntryState.ACTIVE,
            source_refs=[],
            artifact_refs=[],
        ),
    )


def prepared_response(content: str | None = "Prepared memory evidence.") -> PreparedContext:
    return PreparedContext.model_validate({
        "schema": "powercontext.prepared-context.v1",
        "status": "ready" if content else "empty",
        "content": content,
        "content_bytes": len((content or "").encode()),
    })


class RecordingClient:
    """Configurable async client double that records the adapter boundary."""

    instances: ClassVar[list[RecordingClient]] = []
    search_result: ClassVar[Any] = search_response()
    remember_result: ClassVar[Any] = remember_response()
    prepare_result: ClassVar[Any] = prepared_response()
    capture_error: ClassVar[Exception | None] = None
    flush_error: ClassVar[Exception | None] = None
    capture_position_offset: ClassVar[int] = 0
    flush_cursors: ClassVar[tuple[int, ...] | None] = None

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self.closed = False
        self.search_requests: list[Any] = []
        self.remember_requests: list[Any] = []
        self.prepare_requests: list[Any] = []
        self.capture_requests: list[Any] = []
        self.flush_requests: list[Any] = []
        self._last_flush_cursor = 0
        type(self).instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.search_result = search_response()
        cls.remember_result = remember_response()
        cls.prepare_result = prepared_response()
        cls.capture_error = None
        cls.flush_error = None
        cls.capture_position_offset = 0
        cls.flush_cursors = None

    async def __aenter__(self) -> RecordingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info
        self.closed = True

    async def search_memory(self, request: Any) -> Any:
        self.search_requests.append(request)
        return _result_or_raise(type(self).search_result)

    async def remember_memory(self, request: Any) -> Any:
        self.remember_requests.append(request)
        return _result_or_raise(type(self).remember_result)

    async def prepare_context(self, request: Any) -> Any:
        self.prepare_requests.append(request)
        return _result_or_raise(type(self).prepare_result)

    async def capture_content_source(self, request: Any) -> CaptureContentSourceResponse:
        self.capture_requests.append(request)
        capture_error = type(self).capture_error
        if capture_error is not None:
            raise capture_error
        return CaptureContentSourceResponse(
            status=CaptureStatus.ACCEPTED,
            source=SourceReference(name="content", source_id=request.source_id),
            position=type(self).capture_position_offset + len(self.capture_requests),
        )

    async def flush_memory(self, request: Any) -> FlushMemoryResponse:
        self.flush_requests.append(request)
        flush_error = type(self).flush_error
        if flush_error is not None:
            raise flush_error
        flush_cursors = type(self).flush_cursors
        if flush_cursors is None:
            current_cursor = type(self).capture_position_offset + len(self.capture_requests)
        else:
            current_cursor = flush_cursors[min(len(self.flush_requests) - 1, len(flush_cursors) - 1)]
        previous_cursor = self._last_flush_cursor
        self._last_flush_cursor = current_cursor
        return FlushMemoryResponse(
            status=FlushStatus.PROCESSED,
            previous_cursor=previous_cursor,
            current_cursor=current_cursor,
            high_watermark=max(type(self).capture_position_offset + len(self.capture_requests), current_cursor),
            processed_source_count=max(0, current_cursor - previous_cursor),
            memory=None,
        )


def _result_or_raise(value: Any) -> Any:
    if isinstance(value, Exception):
        raise value
    return value
