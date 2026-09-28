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

"""Application service for durable operation submission and control."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from pydantic import ValidationError

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError, RepositoryNotFoundError
from powercontext.builtin.persistence.work import EnqueueResult, StoredWork, WorkRepository, WorkStatus
from powercontext.builtin.runtime.config import OperationsConfig, WorkerConfig
from powercontext.builtin.runtime.models import MemoryFlushResult
from powercontext.builtin.runtime.protocols import RuntimeSpan, RuntimeTracing
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.runtime.work_handlers import (
    EXPERIENCE_WORK_KIND,
    MEMORY_WORK_KIND,
    WorkRequester,
    enqueue_memory_work,
)
from powercontext.builtin.runtime.work_observability import WorkObserver, refresh_work_queue
from powercontext.builtin.runtime.worker import DurableWorker

_TERMINAL = frozenset({WorkStatus.SUCCEEDED, WorkStatus.FAILED, WorkStatus.CANCELLED})
_PUBLIC_KINDS = frozenset({MEMORY_WORK_KIND, EXPERIENCE_WORK_KIND})


@dataclass(frozen=True)
class OperationPage:
    items: tuple[StoredWork, ...]
    next_cursor: str | None


class RuntimeOperationError(RuntimeError):
    """Base class for durable operation facade errors."""


class RuntimeOperationPendingError(RuntimeOperationError):
    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"operation {operation_id} is still pending")


class RuntimeOperationFailedError(RuntimeOperationError):
    def __init__(self, operation: StoredWork) -> None:
        self.operation = operation
        super().__init__(f"operation {operation.work_id} failed with {operation.error_code or 'unknown'}")


class RuntimeOperationCancelledError(RuntimeOperationError):
    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"operation {operation_id} was cancelled")


class OperationManager:
    """Submit, wait for, query, and mutate durable work records."""

    def __init__(
        self,
        *,
        contexts: RelationalContexts,
        operations: OperationsConfig,
        worker: WorkerConfig,
        local_worker: DurableWorker | None,
        payload_version: int,
        memory_window_limit: int,
        repository: WorkRepository | None = None,
        observer: WorkObserver | None = None,
        tracing: RuntimeTracing | None = None,
    ) -> None:
        self._contexts = contexts
        self._database: AsyncDatabase = contexts.database
        self._operations = operations
        self._worker_config = worker
        self._local_worker = local_worker
        self._payload_version = payload_version
        self._memory_window_limit = memory_window_limit
        self._repository = WorkRepository(observer=observer) if repository is None else repository
        self._observer = observer
        self._tracing = tracing

    @property
    def default_wait_seconds(self) -> float:
        return self._operations.default_wait_seconds

    @property
    def maximum_wait_seconds(self) -> float:
        return self._operations.maximum_wait_seconds

    @property
    def memory_window_limit(self) -> int:
        return self._memory_window_limit

    @property
    def database(self) -> AsyncDatabase:
        """Expose the shared database to process-level coordination adapters."""

        return self._database

    async def submit_memory(
        self,
        scope_id: str,
        /,
        *,
        limit: int,
        requester: WorkRequester | None = None,
    ) -> MemoryFlushResult | EnqueueResult:
        with self._stage(
            "work.enqueue",
            attributes={
                "powercontext.work.kind": "powercontext.memory.source-window",
                "powercontext.work.payload_version": self._payload_version,
            },
        ) as span:
            submission = await enqueue_memory_work(
                self._contexts,
                scope_id,
                limit=limit,
                max_attempts=self._worker_config.max_attempts,
                payload_version=self._payload_version,
                requester=requester,
                repository=self._repository,
            )
            if span is not None:
                span.set_outcome(
                    "idle"
                    if isinstance(submission, MemoryFlushResult)
                    else "created"
                    if submission.created
                    else "joined"
                )
        if isinstance(submission, EnqueueResult) and self._local_worker is not None:
            self._local_worker.notify()
        await refresh_work_queue(self._database, self._repository, self._observer)
        return submission

    async def flush_memory(self, scope_id: str, /, *, limit: int) -> MemoryFlushResult:
        submission = await self.submit_memory(scope_id, limit=limit)
        if isinstance(submission, MemoryFlushResult):
            return submission
        operation = await self.wait(
            submission.work.work_id,
            timeout_seconds=self._operations.maximum_wait_seconds,
        )
        if operation is None:
            raise RuntimeOperationPendingError(submission.work.work_id)
        return self.memory_result(operation)

    def memory_result(self, operation: StoredWork, /) -> MemoryFlushResult:
        if operation.status is WorkStatus.FAILED:
            raise RuntimeOperationFailedError(operation)
        if operation.status is WorkStatus.CANCELLED:
            raise RuntimeOperationCancelledError(operation.work_id)
        if operation.status is not WorkStatus.SUCCEEDED or operation.result_payload is None:
            raise RuntimeOperationPendingError(operation.work_id)
        try:
            return MemoryFlushResult.model_validate(operation.result_payload)
        except ValidationError as error:
            raise RuntimeOperationFailedError(operation) from error

    async def wait(self, operation_id: str, /, *, timeout_seconds: float) -> StoredWork | None:
        if timeout_seconds < 0 or timeout_seconds > self._operations.maximum_wait_seconds:
            raise InvalidRepositoryArgumentError(
                "timeout_seconds",
                f"must be between 0 and {self._operations.maximum_wait_seconds}",
            )
        deadline = monotonic() + timeout_seconds
        while True:
            operation = await self.get(operation_id)
            if operation.status in _TERMINAL:
                return operation
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(self._operations.poll_seconds, remaining))

    async def get(self, operation_id: str, /) -> StoredWork:
        async with self._database.transaction() as connection:
            operation = await self._repository.get(connection, operation_id)
        return _require_public_operation(operation)

    async def list(
        self,
        /,
        *,
        scope_id: str | None,
        kind: str | None,
        status: WorkStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperationPage:
        before = None if cursor is None else _decode_cursor(cursor)
        async with self._database.transaction() as connection:
            items = await self._repository.list(
                connection,
                scope_id=scope_id,
                kind=kind,
                allowed_kinds=_PUBLIC_KINDS,
                status=status,
                limit=limit,
                before=before,
            )
        next_cursor = None
        if len(items) == limit:
            last = items[-1]
            next_cursor = _encode_cursor(last.created_at, last.work_id)
        return OperationPage(items=items, next_cursor=next_cursor)

    async def cancel(self, operation_id: str, /, *, expected_version: int) -> StoredWork:
        async with self._database.transaction() as connection:
            _require_public_operation(await self._repository.get(connection, operation_id))
            operation = await self._repository.cancel(connection, operation_id, expected_version=expected_version)
        await refresh_work_queue(self._database, self._repository, self._observer)
        return operation

    async def retry(self, operation_id: str, /, *, expected_version: int) -> StoredWork:
        async with self._database.transaction() as connection:
            _require_public_operation(await self._repository.get(connection, operation_id))
            operation = await self._repository.retry(connection, operation_id, expected_version=expected_version)
        if self._local_worker is not None:
            self._local_worker.notify()
        await refresh_work_queue(self._database, self._repository, self._observer)
        return operation

    def _stage(
        self,
        name: str,
        *,
        attributes: dict[str, str | bool | int | float],
    ) -> AbstractContextManager[RuntimeSpan | None]:
        if self._tracing is None:
            return nullcontext(None)
        return self._tracing.stage(name, attributes=attributes)


def _require_public_operation(operation: StoredWork) -> StoredWork:
    if operation.kind not in _PUBLIC_KINDS:
        raise RepositoryNotFoundError("operation", operation.work_id)
    return operation


def _encode_cursor(created_at: datetime, work_id: str) -> str:
    payload = json.dumps([created_at.isoformat(), work_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(value) % 4)
        created_at, work_id = json.loads(base64.urlsafe_b64decode(f"{value}{padding}"))
        return datetime.fromisoformat(created_at), str(work_id)
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        raise InvalidRepositoryArgumentError("cursor", "must be a valid operation cursor") from None


__all__ = [
    "OperationManager",
    "OperationPage",
    "RuntimeOperationCancelledError",
    "RuntimeOperationError",
    "RuntimeOperationFailedError",
    "RuntimeOperationPendingError",
]
