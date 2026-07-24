"""Family-scoped application services for the local runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from powercontext.context import PowerContext, Sources
from powercontext.errors import ArtifactNotFoundError, MemoryEntryNotFoundError, RevisionConflictError
from powercontext.inference import EmbeddingModel
from powercontext.memory import (
    CandidatePipeline,
    EmbeddingProfile,
    Memory,
    MemoryCitation,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryService,
)
from powercontext.runtime.errors import InvalidRuntimeRequestError
from powercontext.runtime.models import (
    GetMemoryEntryRequest,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryMutationResult,
    MemorySearchPage,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
    SourceHighWatermark,
    SourceReceipt,
)
from powercontext.runtime.protocols import RuntimeScopeStorage, RuntimeStorage, ScopedSourceBackend
from powercontext.runtime.scheduler import (
    configure_source_window_job,
    create_scheduler,
    register_processor,
    scheduler_runtime_key,
    unregister_processor,
)
from powercontext.runtime.triggers import SourceWindowTrigger
from powercontext.sources import ContentCapture, ContentSource, ContentSourceAdapter, SourceCatalog
from powercontext.sources.journal import SourceCursor, validate_scope_id

SOURCE_WINDOW_TRIGGER = "memory-source-window"
logger = logging.getLogger(__name__)


class _RuntimeConfigurationError(ValueError):
    def __init__(self, code: str) -> None:
        messages = {
            "source_window_limit": "source_window_limit must be positive",
            "schedule_seconds": "schedule_seconds must be positive",
            "scheduled_pipeline": "scheduled Source processing requires a candidate pipeline",
        }
        super().__init__(messages[code])


class _RuntimeStateError(RuntimeError):
    def __init__(self, code: str) -> None:
        messages = {
            "closed": "PowerContext runtime is closed",
            "empty-write": "explicit Memory write did not produce a Memory",
        }
        super().__init__(messages[code])


class _InvalidRuntimeResultError(TypeError):
    def __init__(self) -> None:
        super().__init__("Content Source adapter returned an unexpected Source")


@dataclass(frozen=True, slots=True)
class MemoryArtifacts:
    """Typed Artifact-family components for one Memory scope."""

    memory: MemoryService


@dataclass(frozen=True, slots=True)
class MemoryTriggers:
    """Typed Trigger components used to evolve one Memory scope."""

    source_window: SourceWindowTrigger


@dataclass(slots=True)
class _ScopedRuntime:
    context: PowerContext[MemoryTriggers, MemoryArtifacts]
    memory_artifact_id: str
    source_backend: ScopedSourceBackend
    storage: RuntimeScopeStorage
    lock: asyncio.Lock


class ScopedSourceApplication:
    """Capture raw integration content in one Source partition."""

    def __init__(self, runtime: PowerContextRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def capture(self, value: ContentCapture, /) -> SourceReceipt:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            resolved = await scoped.context.sources.resolve(value)
            source = await scoped.context.sources.add(resolved)
            if type(source) is not ContentSource:
                raise _InvalidRuntimeResultError
            return SourceReceipt(source=source, sequence=await scoped.source_backend.position(source))


class SourceApplication:
    """Select a scoped Source application service."""

    def __init__(self, runtime: PowerContextRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceApplication:
        return ScopedSourceApplication(self._runtime, scope_id)


class ScopedMemoryApplication:
    """Operate one Memory Artifact identity and its Source trigger state."""

    def __init__(self, runtime: PowerContextRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def remember(self, request: RememberMemoryRequest, /) -> MemoryMutationResult:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            async with scoped.lock:
                service = scoped.context.artifacts.memory
                current = await _head_or_none(service, scoped.memory_artifact_id)
                _validate_expected_revision(current, request.expected_revision)
                updated = await service.remember(memory=current, entries=request.entries, mode="append")
            if updated is None:
                raise _RuntimeStateError("empty-write")
            previous_revision = None if current is None else current.revision
            return MemoryMutationResult(
                previous_revision=previous_revision,
                memory_ref=updated.ref,
                entry=(
                    None
                    if current is not None and updated.ref == current.ref
                    else await _last_changed_entry(service, updated)
                ),
            )

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            service = scoped.context.artifacts.memory
            current = await _head_or_none(service, scoped.memory_artifact_id)
            if current is None:
                return MemorySearchPage(memory_ref=None, mode=None)
            result = await service.search(
                request.query,
                memories=(current,),
                limit=request.limit,
                mode=request.mode,
            )
            return MemorySearchPage(memory_ref=current.ref, mode=result.mode, hits=result.hits)

    async def list(self) -> MemoryEntriesPage:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            service = scoped.context.artifacts.memory
            current = await _head_or_none(service, scoped.memory_artifact_id)
            if current is None:
                return MemoryEntriesPage(memory_ref=None)
            return MemoryEntriesPage(
                memory_ref=current.ref,
                entries=tuple(_entry_record(current, entry) for entry in await service.entries(current)),
            )

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            service = scoped.context.artifacts.memory
            memory = await service.revision(request.citation.memory_ref)
            _validate_memory_identity(scoped.memory_artifact_id, memory)
            return _entry_record(memory, await _cited_entry(service, memory, request.citation))

    async def revise(self, request: ReviseMemoryEntryRequest, /) -> MemoryMutationResult:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            async with scoped.lock:
                service = scoped.context.artifacts.memory
                current, entry = await _current_citation(service, scoped.memory_artifact_id, request.citation)
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
                memory_ref=updated.ref,
                entry=_entry_record(updated, revised),
            )

    async def retire(self, request: RetireMemoryEntryRequest, /) -> MemoryMutationResult:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            async with scoped.lock:
                service = scoped.context.artifacts.memory
                current, entry = await _current_citation(service, scoped.memory_artifact_id, request.citation)
                updated = await service.forget(current, entries=(entry,), reason=request.reason)
            retired = next(item for item in await service.entries(updated) if item.entry_id == entry.entry_id)
            return MemoryMutationResult(
                previous_revision=current.revision,
                memory_ref=updated.ref,
                entry=_entry_record(updated, retired),
            )

    async def changes(self, *, since_revision: int | None = None) -> MemoryChangesPage:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            service = scoped.context.artifacts.memory
            current = await _head_or_none(service, scoped.memory_artifact_id)
            if current is None:
                return MemoryChangesPage(memory_ref=None)
            if since_revision is not None and since_revision > current.revision:
                raise InvalidRuntimeRequestError("since-revision")
            return MemoryChangesPage(
                memory_ref=current.ref,
                revisions=await service.changes(current, since_revision=since_revision),
            )

    async def flush(self, /, *, limit: int | None = None) -> MemoryFlushResult:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            window_limit = self._runtime.source_window_limit if limit is None else limit
            async with scoped.lock:
                state = await scoped.source_backend.load_cursor(SOURCE_WINDOW_TRIGGER)
                high_watermark = await scoped.source_backend.high_watermark()
                transition = scoped.context.triggers.source_window.activate(
                    SourceHighWatermark(sequence=high_watermark, limit=window_limit),
                    state,
                )
                if not transition.actions:
                    return MemoryFlushResult(
                        previous_cursor=state.sequence,
                        high_watermark=high_watermark,
                        current_cursor=state.sequence,
                        source_count=0,
                        memory_ref=None,
                    )

                action = transition.actions[0]
                records = await scoped.source_backend.list_between(action.after, action.through)
                service = scoped.context.artifacts.memory
                current = await _head_or_none(service, scoped.memory_artifact_id)
                updated = await service.remember(
                    memory=current,
                    sources=tuple(record.source for record in records),
                    mode="extract",
                )
                await scoped.source_backend.save_cursor(SOURCE_WINDOW_TRIGGER, transition.state)
                return MemoryFlushResult(
                    previous_cursor=action.after,
                    high_watermark=high_watermark,
                    current_cursor=action.through,
                    source_count=len(records),
                    memory_ref=None if updated is None else updated.ref,
                )

    async def cursor(self) -> SourceCursor:
        async with self._runtime._operation():
            scoped = await self._runtime._scope(self.scope_id)
            return await scoped.source_backend.load_cursor(SOURCE_WINDOW_TRIGGER)


class MemoryApplication:
    """Select the application service for one Memory family scope."""

    def __init__(self, runtime: PowerContextRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedMemoryApplication:
        return ScopedMemoryApplication(self._runtime, scope_id)


class ScheduledSourceProcessor:
    """Map APScheduler activations to pending scoped Memory windows."""

    def __init__(self, runtime: PowerContextRuntime) -> None:
        self._runtime = runtime

    async def run(self) -> None:
        async with self._runtime._processor_lock:
            if self._runtime._closing or self._runtime._closed:
                return
            scopes = await self._runtime._storage.pending_scopes(SOURCE_WINDOW_TRIGGER)
            for scope_id in scopes:
                if self._runtime._closing or self._runtime._closed:
                    return
                try:
                    await self._runtime.memory.for_scope(scope_id).flush()
                except Exception:
                    logger.exception("scheduled Source processing failed", extra={"scope_id": scope_id})


class PowerContextRuntime:
    """Own local resources and expose Source and Memory family services."""

    def __init__(
        self,
        *,
        storage: RuntimeStorage,
        candidate_pipeline: CandidatePipeline | None,
        embedding_model: EmbeddingModel | None,
        source_window_limit: int,
        scheduler: AsyncIOScheduler | None,
    ) -> None:
        if source_window_limit < 1:
            raise _RuntimeConfigurationError("source_window_limit")
        self._storage = storage
        self._candidate_pipeline = candidate_pipeline
        self._embedding_model = embedding_model
        self.source_window_limit = source_window_limit
        self._scheduler = scheduler
        self._scheduler_runtime_key: str | None = None
        self._scopes: dict[str, _ScopedRuntime] = {}
        self._scopes_lock = asyncio.Lock()
        self._processor_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._lifecycle = asyncio.Condition()
        self._active_operations = 0
        self._closing = False
        self._closed = False
        self.sources = SourceApplication(self)
        self.memory = MemoryApplication(self)
        self.processor = ScheduledSourceProcessor(self)

    @classmethod
    async def assemble(
        cls,
        *,
        storage: RuntimeStorage,
        candidate_pipeline: CandidatePipeline | None = None,
        embedding_model: EmbeddingModel | None = None,
        source_window_limit: int = 100,
    ) -> PowerContextRuntime:
        """Initialize a Runtime from backend-neutral storage."""

        runtime = cls(
            storage=storage,
            candidate_pipeline=candidate_pipeline,
            embedding_model=embedding_model,
            source_window_limit=source_window_limit,
            scheduler=None,
        )
        try:
            await storage.initialize()
        except BaseException:
            await storage.close()
            raise
        return runtime

    @classmethod
    async def open(
        cls,
        database: str | Path,
        *,
        candidate_pipeline: CandidatePipeline | None = None,
        embedding_model: EmbeddingModel | None = None,
        embedding_profile: EmbeddingProfile | None = None,
        vec1_extension: str | Path | None = None,
        source_window_limit: int = 100,
        schedule_seconds: float | None = None,
    ) -> PowerContextRuntime:
        """Initialize a SQLite runtime and optionally start interval processing."""

        from powercontext.runtime.backends.sqlite import SQLiteRuntimeStorage

        if schedule_seconds is not None:
            if schedule_seconds <= 0:
                raise _RuntimeConfigurationError("schedule_seconds")
            if candidate_pipeline is None:
                raise _RuntimeConfigurationError("scheduled_pipeline")
        database_path = str(database) if str(database) == ":memory:" else str(Path(database).expanduser().resolve())
        storage = SQLiteRuntimeStorage(
            database_path,
            embedding_profile=embedding_profile,
            vec1_extension=vec1_extension,
        )
        runtime = await cls.assemble(
            storage=storage,
            candidate_pipeline=candidate_pipeline,
            embedding_model=embedding_model,
            source_window_limit=source_window_limit,
        )
        if schedule_seconds is not None:
            runtime_key = scheduler_runtime_key(database_path)
            try:
                register_processor(runtime_key, runtime.processor.run)
            except Exception:
                await runtime.close()
                raise
            runtime._scheduler_runtime_key = runtime_key
            try:
                scheduler = create_scheduler(database_path)
                runtime._scheduler = scheduler
                scheduler.start(paused=True)
                configure_source_window_job(
                    scheduler,
                    runtime_key=runtime_key,
                    schedule_seconds=schedule_seconds,
                )
                scheduler.resume()
            except Exception:
                await runtime.close()
                raise
        return runtime

    async def __aenter__(self) -> PowerContextRuntime:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    async def close(self) -> None:
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
            if self._scheduler is not None and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
                await asyncio.sleep(0)
            try:
                for scoped in tuple(self._scopes.values()):
                    await scoped.storage.close()
                await self._storage.close()
            finally:
                if self._scheduler_runtime_key is not None:
                    unregister_processor(self._scheduler_runtime_key)
                    self._scheduler_runtime_key = None
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

    async def _scope(self, scope_id: str) -> _ScopedRuntime:
        normalized_scope = validate_scope_id(scope_id)
        existing = self._scopes.get(normalized_scope)
        if existing is not None:
            return existing
        async with self._scopes_lock:
            existing = self._scopes.get(normalized_scope)
            if existing is not None:
                return existing
            storage = await self._storage.open_scope(normalized_scope)
            source_backend = storage.sources
            sources = Sources(
                catalog=SourceCatalog(backend=source_backend, adapters=(ContentSourceAdapter(),)),
                store=source_backend,
            )
            memory_service = MemoryService(
                backend=storage.memory,
                candidate_pipeline=self._candidate_pipeline,
                embedding_model=self._embedding_model,
                evidence_codec=storage.evidence_codec,
                source_resolver=sources,
                id_factory=_memory_id_factory(storage.memory_artifact_id),
            )
            scoped = _ScopedRuntime(
                context=PowerContext(
                    sources=sources,
                    artifacts=MemoryArtifacts(memory=memory_service),
                    triggers=MemoryTriggers(source_window=SourceWindowTrigger()),
                ),
                memory_artifact_id=storage.memory_artifact_id,
                source_backend=source_backend,
                storage=storage,
                lock=asyncio.Lock(),
            )
            self._scopes[normalized_scope] = scoped
            return scoped


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
        raise ArtifactNotFoundError(memory.ref)


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
    return MemoryEntryRecord(memory_ref=memory.ref, state=manifest_entry.state, entry=entry)


async def _last_changed_entry(service: MemoryService, memory: Memory) -> MemoryEntryRecord | None:
    if not memory.content.changes:
        return None
    entry_id = memory.content.changes[-1].entry_id
    entry = next((item for item in await service.entries(memory) if item.entry_id == entry_id), None)
    return None if entry is None else _entry_record(memory, entry)


def _memory_id_factory(memory_artifact_id: str):
    def new_id(kind: str) -> str:
        return memory_artifact_id if kind == "memory" else str(uuid4())

    return new_id
