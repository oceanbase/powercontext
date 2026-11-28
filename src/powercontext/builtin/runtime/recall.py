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

"""Estimate token reduction for one final prepared context."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from contextvars import Context
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext._logging import current_log_context, log_context, log_safely
from powercontext.artifacts import ArtifactAddress, ArtifactLineage, ArtifactRef
from powercontext.builtin.inference import TokenEstimator
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase, is_transaction_contention
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.statistics import StatisticsRepository
from powercontext.builtin.persistence.tables import SCOPES_TABLE
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild, PreparedContextOrigin
from powercontext.builtin.sources import ContentSource, ExternalSkillSnapshotSource
from powercontext.builtin.statistics import RecallTokenMeasurement
from powercontext.sources import Source, SourceRef

_LOGGER = logging.getLogger(__name__)


class RecallTokenEstimationError(RuntimeError):
    """Report a Source type without a stable primary-text projection."""

    def __init__(self, source: Source) -> None:
        super().__init__(f"no recall token projection is registered for {type(source).__name__}")


SourceAddress = tuple[str, str, str]


class _RecallOriginResolver:
    def __init__(
        self,
        *,
        connection: AsyncConnection,
        current_scope_id: str,
        artifacts: ArtifactRepository,
        lineages: tuple[tuple[ArtifactAddress, ArtifactLineage], ...],
    ) -> None:
        self._connection = connection
        self._current_scope_id = current_scope_id
        self._artifacts = artifacts
        self._lineages = {
            _artifact_identity(address.scope_id, address.artifact): lineage for address, lineage in lineages
        }
        self._artifact_sources: dict[tuple[str, str, str, int], frozenset[SourceAddress]] = {}
        self._resolving_artifacts: set[tuple[str, str, str, int]] = set()

    async def resolve(self, origins: tuple[PreparedContextOrigin, ...], /) -> tuple[frozenset[SourceAddress], ...]:
        roots = tuple(
            (origin.scope_id, origin.artifact)
            if isinstance(origin, ArtifactAddress)
            else (self._current_scope_id, origin)
            for origin in origins
        )
        pending = roots
        expanded: set[tuple[str, str, str, int]] = set()
        while pending:
            grouped: dict[str, list[ArtifactRef]] = defaultdict(list)
            for scope_id, ref in pending:
                if _artifact_identity(scope_id, ref) not in self._lineages:
                    grouped[scope_id].append(ref)
            for scope_id, refs in grouped.items():
                artifacts = await self._artifacts.get_many(self._connection, scope_id, refs)
                for artifact in artifacts:
                    self._lineages[_artifact_identity(scope_id, artifact.as_ref())] = artifact.lineage
            expanded.update(_artifact_identity(scope_id, ref) for scope_id, ref in pending)
            parents = {
                _artifact_identity(scope_id, parent): (scope_id, parent)
                for scope_id, ref in pending
                for parent in self._lineages[_artifact_identity(scope_id, ref)].artifacts
                if _artifact_identity(scope_id, parent) not in expanded
            }
            pending = tuple(parents.values())
        return tuple(self._artifact(scope_id, ref) for scope_id, ref in roots)

    def _artifact(self, scope_id: str, artifact_ref: ArtifactRef) -> frozenset[SourceAddress]:
        identity = _artifact_identity(scope_id, artifact_ref)
        if identity in self._artifact_sources:
            return self._artifact_sources[identity]
        if identity in self._resolving_artifacts:
            return frozenset()
        self._resolving_artifacts.add(identity)
        lineage = self._lineages[identity]
        resolved = _source_identities(scope_id, lineage.sources)
        for parent in lineage.artifacts:
            resolved.update(self._artifact(scope_id, parent))
        self._resolving_artifacts.remove(identity)
        result = frozenset(resolved)
        self._artifact_sources[identity] = result
        return result


class RelationalRecallTokenEstimator:
    """Resolve exact recall lineage and estimate its token reduction."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        estimator: TokenEstimator,
        read_timeout_seconds: float,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._sources = sources
        self._artifacts = artifacts
        self._estimator = estimator
        self._read_timeout_seconds = read_timeout_seconds

    async def estimate(self, build: PreparedContextBuild, /) -> RecallTokenMeasurement:
        ready = build.context.status == "ready"
        incomparable = RecallTokenMeasurement(
            estimator=self._estimator.profile, ready=ready, comparable=False, baseline_tokens=0, recalled_tokens=0
        )
        if not ready or not build.origins:
            return incomparable

        source_refs: set[tuple[str, str, str]] = set()

        async with self._database.statistics_transaction(self._read_timeout_seconds) as connection:
            resolver = _RecallOriginResolver(
                connection=connection,
                current_scope_id=self._scope_id,
                artifacts=self._artifacts,
                lineages=build.lineages,
            )
            for origin_sources in await resolver.resolve(build.origins):
                if not origin_sources:
                    return incomparable
                source_refs.update(origin_sources)

            texts = []
            grouped_sources: dict[str, list[SourceRef]] = defaultdict(list)
            for scope_id, source_type, source_id in sorted(
                source_refs,
                key=lambda ref: (ref[0].encode(), ref[1].encode(), ref[2].encode()),
            ):
                grouped_sources[scope_id].append(SourceRef(source_type=source_type, source_id=source_id))
            for scope_id, refs in grouped_sources.items():
                for offset in range(0, len(refs), 100):
                    stored = await self._sources.get_many(connection, scope_id, refs[offset : offset + 100])
                    texts.extend(_source_text(source.value) for source in stored)

        final_content = build.context.content or ""
        return RecallTokenMeasurement(
            estimator=self._estimator.profile,
            ready=True,
            comparable=True,
            baseline_tokens=sum(self._estimator.estimate(text) for text in texts),
            recalled_tokens=self._estimator.estimate(final_content),
        )


@dataclass(frozen=True, slots=True)
class _RecallTokenJob:
    scope_id: str
    usage_date: date
    build: PreparedContextBuild
    log_fields: Mapping[str, str | int | None]
    settled: asyncio.Future[None]


class RecallTokenRecorder:
    """Estimate and record recall tokens on one owned, bounded consumer."""

    def __init__(
        self,
        database: AsyncDatabase,
        repository: StatisticsRepository,
        estimate: Callable[[str, PreparedContextBuild], Awaitable[RecallTokenMeasurement | None]],
        *,
        queue_capacity: int,
        write_timeout_seconds: float,
        flush_timeout_seconds: float,
    ) -> None:
        if queue_capacity < 1 or write_timeout_seconds <= 0 or flush_timeout_seconds <= 0:
            raise ValueError("recall token queue capacity and timeouts must be positive")  # noqa: TRY003
        self._database = database
        self._repository = repository
        self._estimate = estimate
        self._write_timeout_seconds = write_timeout_seconds
        self._flush_timeout_seconds = flush_timeout_seconds
        self._pending: asyncio.Queue[_RecallTokenJob | None] = asyncio.Queue(maxsize=queue_capacity)
        self._consumer: asyncio.Task[None] | None = None
        self._last: asyncio.Future[None] | None = None
        self._closed = False
        self._stopping = False

    def offer(self, scope_id: str, build: PreparedContextBuild, usage_date: date, /) -> None:
        """Capture just the final text and its exact evidence, without database I/O."""

        if self._closed:
            return
        if self._pending.full():
            _log_recall_failure("Recall token queue full; record dropped")
            return
        try:
            loop = asyncio.get_running_loop()
            # The public PreparedContext is frozen, but its exact references are mutable models.
            snapshot = PreparedContextBuild(
                context=build.context,
                origins=tuple(ref.model_copy(deep=True) for ref in build.origins),
                lineages=tuple(
                    (address.model_copy(deep=True), lineage.model_copy(deep=True))
                    for address, lineage in build.lineages
                ),
            )
            context = current_log_context()
            fields: dict[str, str | int | None] = {
                key: context[key]
                for key in ("request_id", "trace_id", "span_id", "worker_id", "binding", "request_generation")
                if key in context
            }
            fields["scope"] = scope_id
            settled = loop.create_future()
            if self._consumer is None:
                self._consumer = loop.create_task(self._consume(), name="powercontext-recall-tokens", context=Context())
                self._consumer.add_done_callback(self._consumer_finished, context=Context())
            self._pending.put_nowait(_RecallTokenJob(scope_id, usage_date, snapshot, fields, settled))
            self._last = settled
        except Exception as error:
            _log_recall_failure("Recall token record could not be queued", error)

    async def flush(self) -> None:
        """Bound the wait to the jobs accepted before the statistics read."""

        await self._wait_accepted(self._flush_timeout_seconds, "Recall token flush timed out")

    async def _wait_accepted(self, timeout_seconds: float, timeout_message: str) -> None:
        last = self._last
        if last is None or last.done():
            return
        try:
            async with asyncio.timeout(timeout_seconds):
                await asyncio.shield(last)
        except TimeoutError:
            _log_recall_failure(timeout_message)

    def _drop_pending(self) -> int:
        dropped = 0
        while not self._pending.empty():
            job = self._pending.get_nowait()
            if job is not None:
                dropped += 1
                if not job.settled.done():
                    job.settled.set_result(None)
        return dropped

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        consumer = self._consumer
        if consumer is None:
            return
        try:
            await self._wait_accepted(
                self._write_timeout_seconds + self._flush_timeout_seconds,
                "Recall token shutdown timed out while draining",
            )
        finally:
            self._stopping = True
            if self._drop_pending():
                _log_recall_failure("Recall token shutdown dropped queued records")
            if not consumer.done():
                self._pending.put_nowait(None)
            # Let the in-flight bounded database operation release its driver before closing it,
            # without cancelling SQLite or blocking shutdown indefinitely on a stalled driver.
            _, pending = await asyncio.wait((consumer,), timeout=self._write_timeout_seconds)
            if pending:
                _log_recall_failure("Recall token shutdown cleanup timed out")

    async def _consume(self) -> None:
        try:
            while (job := await self._pending.get()) is not None:
                try:
                    with log_context(job.log_fields):
                        # Shield bounded database operations from external task cancellation so
                        # SQLite driver cleanup finishes without invalidating a shared pool.
                        await asyncio.shield(self._record(job))
                finally:
                    if not job.settled.done():
                        job.settled.set_result(None)
                    del job
        finally:
            self._closed = True
            self._stopping = True
            self._drop_pending()

    def _consumer_finished(self, consumer: asyncio.Task[None]) -> None:
        if consumer.cancelled():
            return
        error = consumer.exception()
        if isinstance(error, Exception):
            _log_recall_failure("Recall token consumer stopped unexpectedly", error)

    async def _record(self, job: _RecallTokenJob) -> None:
        try:
            measurement = await self._estimate(job.scope_id, job.build)
        except Exception as error:
            _log_recall_failure(
                "Recall token estimation failed", error, event="statistics.recall_tokens.estimation_failed"
            )
            return
        if measurement is None or self._stopping:
            return
        try:
            await self._write(job, measurement)
        except Exception as error:
            _log_recall_failure("Recall token recording failed", error)

    async def _write(self, job: _RecallTokenJob, measurement: RecallTokenMeasurement, /) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._write_timeout_seconds
        while (remaining := deadline - loop.time()) > 0:
            try:
                async with self._database.statistics_transaction(remaining) as connection:
                    # Serialize with Scope deletion so a delayed job cannot recreate deleted statistics.
                    scope = await connection.scalar(
                        select(SCOPES_TABLE.c.scope_id).where(SCOPES_TABLE.c.scope_id == job.scope_id).with_for_update()
                    )
                    if scope is not None:
                        await self._repository.record_recall(connection, job.scope_id, job.usage_date, measurement)
            except OperationalError as error:
                if not is_transaction_contention(error):
                    raise
                # A concurrent writer can invalidate SQLite's read snapshot before the upsert.
                # Only retry a known conflict after transaction cleanup completed.
                await asyncio.sleep(min(0.02, max(0.0, deadline - loop.time())))
            else:
                return
        raise TimeoutError


def _log_recall_failure(
    message: str, error: Exception | None = None, *, event: str = "statistics.recall_tokens.failed"
) -> None:
    log_safely(
        _LOGGER,
        logging.WARNING,
        message,
        extra={
            "event": event,
            "outcome": "failure",
            "unit": "statistics",
            "exception_type": None if error is None else type(error).__name__,
        },
    )


def _source_identities(scope_id: str, sources: tuple[SourceRef, ...], /) -> set[SourceAddress]:
    return {(scope_id, source.source_type, source.source_id) for source in sources}


def _artifact_identity(scope_id: str, artifact: ArtifactRef, /) -> tuple[str, str, str, int]:
    return scope_id, artifact.family, artifact.artifact_id, artifact.revision


def _source_text(source: Source, /) -> str:
    if isinstance(source, ContentSource):
        return source.content
    if isinstance(source, ExternalSkillSnapshotSource):
        return source.snapshot.manifest
    raise RecallTokenEstimationError(source)


__all__ = ["RecallTokenEstimationError", "RecallTokenRecorder", "RelationalRecallTokenEstimator"]
