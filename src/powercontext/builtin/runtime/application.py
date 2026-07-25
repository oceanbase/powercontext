"""Business-specific Runtime operations over composed built-in contexts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from powercontext.builtin.artifacts.memory import (
    Memory,
    MemoryCitation,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.errors import MemoryEntryNotFoundError
from powercontext.builtin.components import (
    BuiltinArtifacts,
    BuiltinSources,
    MemoryFlushResult,
    SourceWindowApplication,
)
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.builtin.runtime.models import (
    CaptureSource,
    GetMemoryEntryRequest,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemoryMutationResult,
    MemorySearchPage,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    RuntimeCapabilities,
    SearchMemoryRequest,
    SourceReceipt,
)
from powercontext.builtin.runtime.protocols import PowerContextProvider
from powercontext.builtin.sources import ContentCapture, SourceCursor, validate_scope_id
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError

logger = logging.getLogger(__name__)

ScopeIds = Callable[[], Awaitable[tuple[str, ...]]]


class _RuntimeScheduler(Protocol):
    running: bool

    def start(self, paused: bool = False) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def shutdown(self, wait: bool = True) -> None: ...


class _RuntimeConfigurationError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(f"{field} must be positive")


class _RuntimeStateError(RuntimeError):
    def __init__(self, code: str) -> None:
        messages = {
            "closed": "Built-in Runtime is closed",
            "empty-write": "explicit Memory write did not produce a Memory",
            "scheduler": "Built-in Runtime scheduler is already started",
        }
        super().__init__(messages[code])


class ScopedSourceApplication:
    """Capture raw integration content in one Source partition."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def capture(self, value: CaptureSource, /) -> SourceReceipt:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            source, sequence = await context.sources.capture(
                ContentCapture(
                    source_id=value.source_id,
                    content=value.content,
                    metadata=value.model_dump(mode="json")["metadata"],
                )
            )
            return SourceReceipt(source_ref=context.sources.catalog.as_ref(source), sequence=sequence)


class SourceApplication:
    """Select a scoped Source application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceApplication:
        return ScopedSourceApplication(self._runtime, scope_id)


class ScopedMemoryApplication:
    """Operate one Memory Artifact identity and its Source trigger state."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def remember(self, request: RememberMemoryRequest, /) -> MemoryMutationResult:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            async with self._runtime._lock(self.scope_id):
                service = context.artifacts.memory
                current = await _head_or_none(service, context.artifacts.memory_artifact_id)
                _validate_expected_revision(current, request.expected_revision)
                updated = await service.remember(memory=current, entries=request.entries, mode="append")
            if updated is None:
                raise _RuntimeStateError("empty-write")
            return MemoryMutationResult(
                previous_revision=None if current is None else current.revision,
                memory_ref=updated.as_ref(),
                entry=(
                    None
                    if current is not None and updated.as_ref() == current.as_ref()
                    else await _last_changed_entry(service, updated)
                ),
            )

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            async with self._runtime._lock(self.scope_id):
                service = context.artifacts.memory
                current = await _head_or_none(service, context.artifacts.memory_artifact_id)
                if current is None:
                    return MemorySearchPage(memory_ref=None, mode=None)
                result = await service.search(
                    request.query,
                    memories=(current,),
                    limit=request.limit,
                    mode=request.mode,
                )
            return MemorySearchPage(
                memory_ref=current.as_ref(),
                mode=result.mode,
                hits=result.hits,
            )

    async def list(self) -> MemoryEntriesPage:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                return MemoryEntriesPage(memory_ref=None)
            return MemoryEntriesPage(
                memory_ref=current.as_ref(),
                entries=tuple(_entry_record(current, entry) for entry in await service.entries(current)),
            )

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            service = context.artifacts.memory
            citation = request.citation
            memory = await service.revision(citation.memory_ref)
            _validate_memory_identity(context.artifacts.memory_artifact_id, memory)
            return _entry_record(memory, await _cited_entry(service, memory, citation))

    async def revise(self, request: ReviseMemoryEntryRequest, /) -> MemoryMutationResult:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            async with self._runtime._lock(self.scope_id):
                service = context.artifacts.memory
                current, entry = await _current_citation(
                    service,
                    context.artifacts.memory_artifact_id,
                    request.citation,
                )
                updated = await service.remember(
                    memory=current,
                    entries=(
                        MemoryEntryInput(
                            entry=entry,
                            kind=request.kind,
                            text=request.text,
                            reason=request.reason,
                        ),
                    ),
                    mode="append",
                )
            if updated is None:
                raise _RuntimeStateError("empty-write")
            revised = next(item for item in await service.entries(updated) if item.entry_id == entry.entry_id)
            return MemoryMutationResult(
                previous_revision=current.revision,
                memory_ref=updated.as_ref(),
                entry=_entry_record(updated, revised),
            )

    async def retire(self, request: RetireMemoryEntryRequest, /) -> MemoryMutationResult:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            async with self._runtime._lock(self.scope_id):
                service = context.artifacts.memory
                current, entry = await _current_citation(
                    service,
                    context.artifacts.memory_artifact_id,
                    request.citation,
                )
                updated = await service.forget(current, entries=(entry,), reason=request.reason)
            retired = next(item for item in await service.entries(updated) if item.entry_id == entry.entry_id)
            return MemoryMutationResult(
                previous_revision=current.revision,
                memory_ref=updated.as_ref(),
                entry=_entry_record(updated, retired),
            )

    async def changes(self, *, since_revision: int | None = None) -> MemoryChangesPage:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                return MemoryChangesPage(memory_ref=None)
            if since_revision is not None and since_revision > current.revision:
                raise InvalidRuntimeRequestError("since-revision")
            return MemoryChangesPage(
                memory_ref=current.as_ref(),
                revisions=await service.changes(current, since_revision=since_revision),
            )

    async def flush(self, /, *, limit: int | None = None) -> MemoryFlushResult:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            window_limit = self._runtime.source_window_limit if limit is None else limit
            async with self._runtime._lock(self.scope_id):
                return await context.triggers.flush(limit=window_limit)

    async def cursor(self) -> SourceCursor:
        async with self._runtime._operation():
            context = await self._runtime._context(self.scope_id)
            return await context.triggers.cursor()


class MemoryApplication:
    """Select the application service for one Memory family scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedMemoryApplication:
        return ScopedMemoryApplication(self._runtime, scope_id)


class ScheduledSourceProcessor:
    """Map APScheduler activations to scoped Source-window policies."""

    def __init__(self, runtime: BuiltinRuntime, scope_ids: ScopeIds) -> None:
        self._runtime = runtime
        self._scope_ids = scope_ids

    async def run(self) -> None:
        async with self._runtime._processor_lock:
            if self._runtime._closing or self._runtime._closed:
                return
            for scope_id in await self._scope_ids():
                if self._runtime._closing or self._runtime._closed:
                    return
                try:
                    await self._runtime.memory.for_scope(scope_id).flush()
                except Exception:
                    logger.exception("scheduled Source processing failed", extra={"scope_id": scope_id})


class BuiltinRuntime:
    """Add business-specific operations over composed built-in contexts."""

    def __init__(
        self,
        *,
        provider: PowerContextProvider[BuiltinSources, BuiltinArtifacts, SourceWindowApplication],
        capabilities: RuntimeCapabilities,
        source_window_limit: int = 100,
        scope_ids: ScopeIds | None = None,
    ) -> None:
        if source_window_limit < 1:
            raise _RuntimeConfigurationError("source_window_limit")
        self._provider = provider
        self._capabilities = capabilities
        self.source_window_limit = source_window_limit
        self._locks: dict[str, asyncio.Lock] = {}
        self._processor_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._lifecycle = asyncio.Condition()
        self._active_operations = 0
        self._closing = False
        self._closed = False
        self._scheduler: _RuntimeScheduler | None = None
        self._scheduler_runtime_key: str | None = None
        self.sources = SourceApplication(self)
        self.memory = MemoryApplication(self)
        self.processor = None if scope_ids is None else ScheduledSourceProcessor(self, scope_ids)

    async def __aenter__(self) -> BuiltinRuntime:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    async def capabilities(self) -> RuntimeCapabilities:
        async with self._operation():
            return self._capabilities

    def start_scheduler(self, scheduler_path: str | Path, schedule_seconds: float) -> None:
        """Start the APScheduler time adapter for this Runtime."""

        if self.processor is None:
            raise _RuntimeConfigurationError("scope_ids")
        if schedule_seconds <= 0:
            raise _RuntimeConfigurationError("schedule_seconds")
        if self._scheduler is not None:
            raise _RuntimeStateError("scheduler")
        from powercontext.builtin.runtime.scheduler import (
            configure_source_window_job,
            create_scheduler,
            register_processor,
            scheduler_runtime_key,
            unregister_processor,
        )

        runtime_key = scheduler_runtime_key(scheduler_path)
        scheduler: _RuntimeScheduler | None = None
        register_processor(runtime_key, self.processor.run)
        self._scheduler_runtime_key = runtime_key
        try:
            scheduler = create_scheduler(scheduler_path)
            self._scheduler = scheduler
            scheduler.start(paused=True)
            configure_source_window_job(
                scheduler,
                runtime_key=runtime_key,
                schedule_seconds=schedule_seconds,
            )
            scheduler.resume()
        except BaseException:
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            unregister_processor(runtime_key)
            self._scheduler_runtime_key = None
            self._scheduler = None
            raise

    async def close(self) -> None:
        """Stop accepting work and await in-flight operations without closing the provider."""

        async with self._close_lock:
            if self._closed:
                return
            if self._scheduler is not None and self._scheduler.running:
                self._scheduler.pause()
            async with self._lifecycle:
                self._closing = True
                await self._lifecycle.wait_for(lambda: self._active_operations == 0)
            async with self._processor_lock:
                pass
            try:
                if self._scheduler is not None and self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                    await asyncio.sleep(0)
            finally:
                if self._scheduler_runtime_key is not None:
                    from powercontext.builtin.runtime.scheduler import unregister_processor

                    unregister_processor(self._scheduler_runtime_key)
                    self._scheduler_runtime_key = None
                self._scheduler = None
            self._closed = True

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        async with self._lifecycle:
            if self._closing or self._closed:
                raise _RuntimeStateError("closed")
            self._active_operations += 1
        try:
            yield
        finally:
            async with self._lifecycle:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._lifecycle.notify_all()

    async def _context(self, scope_id: str):
        return await self._provider.get(validate_scope_id(scope_id))

    def _lock(self, scope_id: str) -> asyncio.Lock:
        return self._locks.setdefault(validate_scope_id(scope_id), asyncio.Lock())


async def _head_or_none(service: MemoryService, artifact_id: str) -> Memory | None:
    try:
        return await service.head(artifact_id)
    except ArtifactNotFoundError:
        return None


def _validate_expected_revision(memory: Memory | None, expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    if memory is None:
        raise ArtifactNotFoundError(expected_revision)
    if memory.revision != expected_revision:
        raise RevisionConflictError(expected_revision, memory)


def _validate_memory_identity(memory_artifact_id: str, memory: Memory) -> None:
    if memory.artifact_id != memory_artifact_id:
        raise ArtifactNotFoundError(memory.as_ref())


async def _current_citation(
    service: MemoryService,
    memory_artifact_id: str,
    citation: MemoryCitation,
) -> tuple[Memory, MemoryEntryVersion]:
    current = await service.head(memory_artifact_id)
    if citation.memory_ref.artifact_id != current.artifact_id:
        raise ArtifactNotFoundError(citation.memory_ref)
    if citation.memory_ref.revision != current.revision:
        raise RevisionConflictError(citation.memory_ref, current)
    return current, await _cited_entry(service, current, citation)


async def _cited_entry(
    service: MemoryService,
    memory: Memory,
    citation: MemoryCitation,
) -> MemoryEntryVersion:
    if not any(
        item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id
        for item in memory.content.manifest.entries
    ):
        raise MemoryEntryNotFoundError(citation.entry_id)
    return await service.validate_citation(citation)


def _entry_record(memory: Memory, entry: MemoryEntryVersion) -> MemoryEntryRecord:
    manifest_entry = next(
        item
        for item in memory.content.manifest.entries
        if item.entry_id == entry.entry_id and item.entry_version_id == entry.entry_version_id
    )
    return MemoryEntryRecord(
        memory_ref=memory.as_ref(),
        state=manifest_entry.state,
        entry=entry,
    )


async def _last_changed_entry(service: MemoryService, memory: Memory) -> MemoryEntryRecord | None:
    if not memory.content.changes:
        return None
    entry_id = memory.content.changes[-1].entry_id
    entry = next((item for item in await service.entries(memory) if item.entry_id == entry_id), None)
    return None if entry is None else _entry_record(memory, entry)
