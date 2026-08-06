"""Scope-bound built-in contexts over one SQLAlchemy async database."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.experience import (
    EXPERIENCE_INCUBATION_CURSOR_NAME,
    Experience,
    ExperienceCandidateInput,
    ExperienceCandidatePipeline,
    ExperienceContent,
    ExperienceGenerator,
    ExperienceSearchHit,
)
from powercontext.builtin.artifacts.handoff import (
    ActivateHandoff,
    Handoff,
    HandoffActivation,
    HandoffEvidenceUnavailableError,
    HandoffGenerationPipeline,
    HandoffService,
    HandoffSourceCitation,
)
from powercontext.builtin.artifacts.memory import (
    CandidatePipeline,
    Memory,
    MemoryService,
    MemoryWritePlan,
)
from powercontext.builtin.artifacts.skill import (
    ExternalSkillProvider,
    ExternalSkillRegistryUnavailableError,
    Skill,
    SkillContent,
    SkillGenerator,
)
from powercontext.builtin.artifacts.skill.registry import ExternalSkillRegistryService
from powercontext.builtin.context import BuiltinArtifacts, BuiltinSources
from powercontext.builtin.inference import EmbeddingModel, InvalidInferenceOutputError, TokenEstimator
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.experience_index import ExperienceIndex, NoExperienceIndex
from powercontext.builtin.persistence.external_skills import ExternalSkillRepository
from powercontext.builtin.persistence.handoff import (
    RelationalHandoffBackend,
    RelationalHandoffEvidenceResolver,
)
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.memory_index import MemoryIndex, NoMemoryIndex
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.persistence.statistics import StatisticsRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.review.generation import (
    GeneratedCandidateResult,
    GenerationCapabilityUnavailableError,
    ReviewedGenerationService,
    SkillGenerationOrigin,
)
from powercontext.builtin.review.service import ReviewService
from powercontext.builtin.runtime.models import ExperienceIncubationResult, MemoryFlushResult
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild
from powercontext.builtin.runtime.protocols import BuiltinTriggers
from powercontext.builtin.runtime.recall import RelationalRecallTokenEstimator
from powercontext.builtin.runtime.statistics import RelationalScopedStatistics
from powercontext.builtin.sources import (
    CONTENT_SOURCE_ADAPTER,
    EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER,
    ExternalSkillImportMode,
    ExternalSkillSnapshotCapture,
    SourceCursor,
    validate_scope_id,
)
from powercontext.builtin.statistics import RecallTokenMeasurement
from powercontext.builtin.triggers import (
    HANDOFF_BOUNDARY_TRIGGER_NAME,
    SOURCE_WINDOW_TRIGGER_NAME,
    HandoffBoundary,
    HandoffTrigger,
    ProcessSourceWindow,
    SourceHighWatermark,
    SourceWindowTrigger,
)
from powercontext.context import PowerContext
from powercontext.errors import ArtifactNotFoundError, SourceConflictError, SourceNotFoundError
from powercontext.sources import (
    Source,
    SourceAdapter,
    SourceCatalog,
    SourceRef,
)

IdFactory = Callable[[str], str]
_SOURCE_ADAPTERS: tuple[SourceAdapter[Any, Any, Any], ...] = (
    CONTENT_SOURCE_ADAPTER,
    EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER,
)


@dataclass(frozen=True, slots=True)
class _Repositories:
    """Repositories shared by every scoped context."""

    sources: SourceRepository
    artifacts: ArtifactRepository
    candidates: CandidateRepository
    cursors: SourceCursorRepository
    external_skills: ExternalSkillRepository
    statistics: StatisticsRepository


@dataclass(frozen=True, slots=True)
class _ScopedServices:
    """Centralize relational service wiring for one scope."""

    database: AsyncDatabase
    scope_id: str
    repositories: _Repositories
    index: MemoryIndex
    experience_index: ExperienceIndex
    candidate_pipeline: CandidatePipeline | None
    experience_pipeline: ExperienceCandidatePipeline | None
    experience_generator: ExperienceGenerator | None
    skill_generator: SkillGenerator | None
    handoff_pipeline: HandoffGenerationPipeline | None
    embedding_model: EmbeddingModel | None
    id_factory: IdFactory
    handoff_artifact_id: str
    memory_artifact_id: str
    source_lock: asyncio.Lock
    token_estimator: TokenEstimator | None

    def sources(
        self,
        connection: AsyncConnection | None = None,
    ) -> tuple[_RelationalSources, SourceCatalog]:
        backend = _RelationalSources(
            database=self.database,
            scope_id=self.scope_id,
            adapters=_SOURCE_ADAPTERS,
            repository=self.repositories.sources,
            write_lock=self.source_lock,
            connection=connection,
        )
        return backend, SourceCatalog(backend=backend, adapters=_SOURCE_ADAPTERS)

    def memory(
        self,
        source_resolver: SourceCatalog,
        connection: AsyncConnection | None = None,
    ) -> MemoryService:
        return MemoryService(
            backend=RelationalMemoryBackend(
                database=self.database,
                scope_id=self.scope_id,
                artifacts=self.repositories.artifacts,
                index=self.index,
                connection=connection,
            ),
            candidate_pipeline=self.candidate_pipeline,
            embedding_model=self.embedding_model,
            source_resolver=source_resolver,
            artifact_resolver=_RelationalArtifactResolver(
                database=self.database,
                scope_id=self.scope_id,
                repository=self.repositories.artifacts,
                connection=connection,
            ),
            id_factory=self.id_factory,
        )

    def review(self, connection: AsyncConnection | None = None) -> ReviewService:
        return ReviewService(
            database=self.database,
            scope_id=self.scope_id,
            candidates=self.repositories.candidates,
            artifacts=self.repositories.artifacts,
            experience_index=self.experience_index,
            sources=self.repositories.sources,
            id_factory=self.id_factory,
            connection=connection,
        )

    def generation(self) -> ReviewedGenerationService:
        return ReviewedGenerationService(
            database=self.database,
            scope_id=self.scope_id,
            sources=self.repositories.sources,
            artifacts=self.repositories.artifacts,
            review=self.review(),
            experience_generator=self.experience_generator,
            skill_generator=self.skill_generator,
        )

    def handoff(
        self,
        source_resolver: SourceCatalog,
    ) -> HandoffService:
        memory = self.memory(source_resolver)
        return HandoffService(
            scope_id=self.scope_id,
            artifact_id=self.handoff_artifact_id,
            backend=RelationalHandoffBackend(
                database=self.database,
                scope_id=self.scope_id,
                artifacts=self.repositories.artifacts,
            ),
            evidence_resolver=RelationalHandoffEvidenceResolver(
                database=self.database,
                scope_id=self.scope_id,
                sources=self.repositories.sources,
                artifacts=self.repositories.artifacts,
                memory=memory,
            ),
            generation_pipeline=self.handoff_pipeline,
        )

    def statistics(self) -> RelationalScopedStatistics:
        """Return the scoped statistics service over shared repositories."""

        def memory_service(connection: AsyncConnection) -> MemoryService:
            _, source_catalog = self.sources(connection)
            return self.memory(source_catalog, connection)

        return RelationalScopedStatistics(
            database=self.database,
            scope_id=self.scope_id,
            memory_artifact_id=self.memory_artifact_id,
            memory_service=memory_service,
            cursors=self.repositories.cursors,
            repository=self.repositories.statistics,
            token_estimator=None if self.token_estimator is None else self.token_estimator.profile,
        )

    def recall_tokens(self) -> RelationalRecallTokenEstimator | None:
        if self.token_estimator is None:
            return None

        def memory_service(connection: AsyncConnection) -> MemoryService:
            _, source_catalog = self.sources(connection)
            return self.memory(source_catalog, connection)

        return RelationalRecallTokenEstimator(
            database=self.database,
            scope_id=self.scope_id,
            sources=self.repositories.sources,
            artifacts=self.repositories.artifacts,
            memory_service=memory_service,
            estimator=self.token_estimator,
        )


class RelationalContexts:
    """Compose typed, scope-bound contexts without owning the database lifecycle."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        index: MemoryIndex | None = None,
        experience_index: ExperienceIndex | None = None,
        candidate_pipeline: CandidatePipeline | None = None,
        experience_pipeline: ExperienceCandidatePipeline | None = None,
        experience_generator: ExperienceGenerator | None = None,
        skill_generator: SkillGenerator | None = None,
        external_skill_provider: ExternalSkillProvider | None = None,
        handoff_pipeline: HandoffGenerationPipeline | None = None,
        embedding_model: EmbeddingModel | None = None,
        token_estimator: TokenEstimator | None = None,
        id_factory: IdFactory | None = None,
        handoff_artifact_id: str = "handoff",
        memory_artifact_id: str = "memory",
    ) -> None:
        self.database = database
        self.index = NoMemoryIndex() if index is None else index
        self.experience_index = NoExperienceIndex() if experience_index is None else experience_index
        self.repositories = _Repositories(
            sources=SourceRepository(_SOURCE_ADAPTERS),
            artifacts=ArtifactRepository((Handoff, Memory, Experience, Skill)),
            candidates=CandidateRepository({
                Experience.family: ExperienceContent,
                Skill.family: SkillContent,
            }),
            cursors=SourceCursorRepository(),
            external_skills=ExternalSkillRepository(),
            statistics=StatisticsRepository(),
        )
        self._candidate_pipeline = candidate_pipeline
        self.memory_extraction = candidate_pipeline is not None
        self._experience_pipeline = experience_pipeline
        self.experience_incubation = experience_pipeline is not None
        self._experience_generator = experience_generator
        self.experience_generation = experience_generator is not None
        self._skill_generator = skill_generator
        self.managed_skill_generation = skill_generator is not None
        self._external_skill_provider = external_skill_provider
        self.external_skill_registry = external_skill_provider is not None
        self._handoff_pipeline = handoff_pipeline
        self.handoff_generation = handoff_pipeline is not None
        self._embedding_model = embedding_model
        self._token_estimator = token_estimator
        self._id_factory = _scoped_id_factory(memory_artifact_id, id_factory)
        self._handoff_artifact_id = handoff_artifact_id
        self._memory_artifact_id = memory_artifact_id
        self._contexts: dict[
            str,
            PowerContext[BuiltinSources, BuiltinArtifacts, BuiltinTriggers],
        ] = {}
        self._source_locks: dict[str, asyncio.Lock] = {}
        self._activation_locks: dict[str, asyncio.Lock] = {}
        self._experience_locks: dict[str, asyncio.Lock] = {}

    def review(self, scope_id: str, /) -> ReviewService:
        """Return Candidate and reviewed Artifact operations bound to one scope."""

        return self._services_for(scope_id).review()

    def generation(self, scope_id: str, /) -> ReviewedGenerationService:
        """Return model-backed reviewed generation bound to one scope."""

        return self._services_for(scope_id).generation()

    def statistics(self, scope_id: str, /) -> RelationalScopedStatistics:
        """Return product statistics bound to one scope."""

        return self._services_for(scope_id).statistics()

    async def estimate_recall_tokens(
        self,
        scope_id: str,
        build: PreparedContextBuild,
        /,
    ) -> RecallTokenMeasurement | None:
        estimator = self._services_for(scope_id).recall_tokens()
        return None if estimator is None else await estimator.estimate(build)

    async def search_experience(
        self,
        scope_id: str,
        query: str,
        limit: int,
        /,
    ) -> tuple[ExperienceSearchHit, ...]:
        """Recall relevant approved Experience heads in one scope."""

        if limit < 1:
            raise ValueError("Experience search limit must be positive")  # noqa: TRY003
        scope = validate_scope_id(scope_id)
        async with self.database.transaction() as connection:
            return await self.experience_index.search(connection, scope, query, limit)

    def external_skills(self, scope_id: str, /) -> ExternalSkillRegistryService:
        """Return the host-local external Skill Registry bound to one scope."""

        if self._external_skill_provider is None:
            raise ExternalSkillRegistryUnavailableError
        return ExternalSkillRegistryService(
            database=self.database,
            scope_id=validate_scope_id(scope_id),
            repository=self.repositories.external_skills,
            provider=self._external_skill_provider,
        )

    async def import_external_skill(
        self,
        scope_id: str,
        external_skill_id: str,
        fingerprint: str,
        mode: ExternalSkillImportMode,
        reason: str | None,
        /,
    ) -> GeneratedCandidateResult:
        """Snapshot an exact external package only for an explicit managed import or fork."""

        if self._skill_generator is None:
            raise GenerationCapabilityUnavailableError(Skill.family)
        scope = validate_scope_id(scope_id)
        snapshot = await self.external_skills(scope).snapshot(external_skill_id, fingerprint)
        source = await EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER.resolve(
            ExternalSkillSnapshotCapture(snapshot=snapshot, mode=mode)
        )
        async with self.database.transaction() as connection:
            stored = await self.repositories.sources.add(connection, scope, source)
        return await self.generation(scope).skill(
            origin=SkillGenerationOrigin.SOURCE,
            sources=(stored.ref,),
            artifacts=(),
            target=None,
            reason=reason,
        )

    async def scope_ids(self) -> tuple[str, ...]:
        """Return scopes with a Source journal, in deterministic order."""

        async with self.database.transaction() as connection:
            values = (
                await connection.execute(
                    select(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id).order_by(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id)
                )
            ).scalars()
            return tuple(str(value) for value in values)

    async def incubate_experience(self, scope_id: str, limit: int, /) -> ExperienceIncubationResult:
        """Process one independent Task Outcome Source window for Review."""

        services = self._services_for(scope_id)
        if services.experience_pipeline is None:
            raise RuntimeError("Experience incubation pipeline is not configured")  # noqa: TRY003
        return await _RelationalExperienceIncubator(
            services=services,
            lock=self._experience_locks.setdefault(services.scope_id, asyncio.Lock()),
        ).flush(limit=limit)

    async def get(
        self,
        scope_id: str,
        /,
    ) -> PowerContext[BuiltinSources, BuiltinArtifacts, BuiltinTriggers]:
        scope = validate_scope_id(scope_id)
        existing = self._contexts.get(scope)
        if existing is not None:
            return existing

        services = self._services_for(scope)
        sources_backend, source_catalog = services.sources()
        triggers = _RelationalTriggers(
            services=services,
            lock=self._activation_locks.setdefault(scope, asyncio.Lock()),
        )
        context: PowerContext[BuiltinSources, BuiltinArtifacts, BuiltinTriggers] = PowerContext(
            sources=BuiltinSources(
                catalog=source_catalog,
                store=sources_backend,
                journal=sources_backend,
            ),
            artifacts=BuiltinArtifacts(
                handoff=services.handoff(source_catalog),
                handoff_artifact_id=self._handoff_artifact_id,
                memory=services.memory(source_catalog),
                memory_artifact_id=self._memory_artifact_id,
            ),
            triggers=triggers,
        )
        return self._contexts.setdefault(scope, context)

    def _services_for(self, scope_id: str) -> _ScopedServices:
        scope = validate_scope_id(scope_id)
        return _ScopedServices(
            database=self.database,
            scope_id=scope,
            repositories=self.repositories,
            index=self.index,
            experience_index=self.experience_index,
            candidate_pipeline=self._candidate_pipeline,
            experience_pipeline=self._experience_pipeline,
            experience_generator=self._experience_generator,
            skill_generator=self._skill_generator,
            handoff_pipeline=self._handoff_pipeline,
            embedding_model=self._embedding_model,
            id_factory=self._id_factory,
            handoff_artifact_id=self._handoff_artifact_id,
            memory_artifact_id=self._memory_artifact_id,
            source_lock=self._source_locks.setdefault(scope, asyncio.Lock()),
            token_estimator=self._token_estimator,
        )


class _RelationalSources:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        adapters: tuple[SourceAdapter[Any, Any, Any], ...],
        repository: SourceRepository,
        write_lock: asyncio.Lock,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._source_names = {adapter.source_class: adapter.name for adapter in adapters}
        self._repository = repository
        self._write_lock = write_lock
        self._bound_connection = connection

    async def add(self, source: Source, /) -> Source:
        async with self._write_lock:
            try:
                async with self._database.connection(self._bound_connection) as connection:
                    return (await self._repository.add(connection, self._scope_id, source)).value
            except StoredPayloadConflictError as error:
                raise SourceConflictError("identity", error.identity) from None

    async def get(self, source: Source, /) -> Source:
        ref = self._as_ref(source)
        try:
            async with self._database.connection(self._bound_connection) as connection:
                return (await self._repository.get(connection, self._scope_id, ref)).value
        except RepositoryNotFoundError:
            raise SourceNotFoundError(source) from None

    async def list(self) -> tuple[Source, ...]:
        async with self._database.connection(self._bound_connection) as connection:
            rows = await self._repository.list(connection, self._scope_id)
        return tuple(row.value for row in rows)

    async def position(self, source: Source, /) -> int:
        ref = self._as_ref(source)
        try:
            async with self._database.connection(self._bound_connection) as connection:
                return (await self._repository.get(connection, self._scope_id, ref)).journal_position
        except RepositoryNotFoundError:
            raise SourceNotFoundError(source) from None

    def _as_ref(self, source: Source) -> SourceRef:
        return SourceRef(source_type=self._source_names[type(source)], source_id=source.name)


class _RelationalArtifactResolver:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        repository: ArtifactRepository,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._repository = repository
        self._bound_connection = connection

    async def get(self, artifact: Artifact[object], /) -> Artifact[object]:
        try:
            async with self._database.connection(self._bound_connection) as connection:
                return cast(
                    Artifact[object],
                    await self._repository.get(connection, self._scope_id, artifact.as_ref()),
                )
        except RepositoryNotFoundError:
            raise ArtifactNotFoundError(artifact) from None


class _RelationalTriggers:
    def __init__(
        self,
        *,
        services: _ScopedServices,
        lock: asyncio.Lock,
    ) -> None:
        self._services = services
        self._lock = lock
        self._handoff_trigger = HandoffTrigger()
        self._trigger = SourceWindowTrigger()

    async def activate_handoff(self, request: ActivateHandoff, /) -> HandoffActivation:
        async with self._lock:
            async with self._services.database.transaction() as connection:
                try:
                    source = await self._services.repositories.sources.get(
                        connection,
                        self._services.scope_id,
                        request.boundary_source,
                    )
                except RepositoryNotFoundError:
                    raise HandoffEvidenceUnavailableError(
                        HandoffSourceCitation(source_ref=request.boundary_source)
                    ) from None
                state_row = await self._services.repositories.cursors.load(
                    connection,
                    self._services.scope_id,
                    HANDOFF_BOUNDARY_TRIGGER_NAME,
                )
                state = self._handoff_trigger.initial_state() if state_row is None else state_row.cursor
                transition = self._handoff_trigger.activate(
                    HandoffBoundary(
                        position=source.journal_position,
                        activation=request,
                    ),
                    state,
                )
            if not transition.actions:
                return HandoffActivation(
                    status="ignored",
                    boundary_source=request.boundary_source,
                    previous_position=state.sequence,
                    current_position=state.sequence,
                    draft=None,
                )

            _, source_catalog = self._services.sources()
            draft = await self._services.handoff(source_catalog).prepare(transition.actions[0])
            async with self._services.database.transaction() as connection:
                await self._services.repositories.cursors.save(
                    connection,
                    self._services.scope_id,
                    HANDOFF_BOUNDARY_TRIGGER_NAME,
                    transition.state,
                    expected_generation=None if state_row is None else state_row.generation,
                )
            return HandoffActivation(
                status="generated",
                boundary_source=request.boundary_source,
                previous_position=state.sequence,
                current_position=transition.state.sequence,
                draft=draft,
            )

    async def cursor(self) -> SourceCursor:
        async with self._services.database.transaction() as connection:
            state = await self._services.repositories.cursors.load(
                connection,
                self._services.scope_id,
                SOURCE_WINDOW_TRIGGER_NAME,
            )
        return self._trigger.initial_state() if state is None else state.cursor

    async def flush(self, *, limit: int) -> MemoryFlushResult:
        async with self._lock:
            async with self._services.database.transaction() as connection:
                state_row = await self._services.repositories.cursors.load(
                    connection,
                    self._services.scope_id,
                    SOURCE_WINDOW_TRIGGER_NAME,
                )
                state = self._trigger.initial_state() if state_row is None else state_row.cursor
                high_watermark = await self._services.repositories.sources.journal_position(
                    connection,
                    self._services.scope_id,
                )
                signal = SourceHighWatermark(sequence=high_watermark, limit=limit)
                transition = self._trigger.activate(signal, state)
                sources = () if not transition.actions else await self._sources(connection, transition.actions[0])
            if not transition.actions:
                return MemoryFlushResult(
                    previous_cursor=state.sequence,
                    high_watermark=high_watermark,
                    current_cursor=state.sequence,
                    source_count=0,
                    memory_ref=None,
                )

            action = transition.actions[0]
            prepared = await self._prepare_memory(sources)
            async with self._services.database.transaction() as connection:
                _, source_catalog = self._services.sources(connection)
                updated = await self._services.memory(source_catalog, connection).apply(prepared)
                await self._services.repositories.cursors.save(
                    connection,
                    self._services.scope_id,
                    SOURCE_WINDOW_TRIGGER_NAME,
                    transition.state,
                    expected_generation=None if state_row is None else state_row.generation,
                )
            return MemoryFlushResult(
                previous_cursor=action.after,
                high_watermark=high_watermark,
                current_cursor=action.through,
                source_count=len(sources),
                memory_ref=None if updated is None else updated.as_ref(),
            )

    async def _sources(
        self,
        connection: AsyncConnection,
        action: ProcessSourceWindow,
    ) -> tuple[Source, ...]:
        rows = await self._services.repositories.sources.list(
            connection,
            self._services.scope_id,
            after=action.after,
        )
        return tuple(row.value for row in rows if row.journal_position <= action.through)

    async def _prepare_memory(self, sources: tuple[Source, ...]) -> MemoryWritePlan:
        _, source_catalog = self._services.sources()
        service = self._services.memory(source_catalog)
        try:
            current = await service.head(self._services.memory_artifact_id)
        except ArtifactNotFoundError:
            current = None
        return await service.plan_remember(memory=current, sources=sources, mode="extract")


class _RelationalExperienceIncubator:
    """Advance one independent Source cursor with atomic Candidate writes."""

    def __init__(
        self,
        *,
        services: _ScopedServices,
        lock: asyncio.Lock,
    ) -> None:
        self._services = services
        self._lock = lock
        self._trigger = SourceWindowTrigger()

    async def flush(self, *, limit: int) -> ExperienceIncubationResult:
        async with self._lock:
            async with self._services.database.transaction() as connection:
                state_row = await self._services.repositories.cursors.load(
                    connection,
                    self._services.scope_id,
                    EXPERIENCE_INCUBATION_CURSOR_NAME,
                )
                state = self._trigger.initial_state() if state_row is None else state_row.cursor
                high_watermark = await self._services.repositories.sources.journal_position(
                    connection,
                    self._services.scope_id,
                )
                transition = self._trigger.activate(
                    SourceHighWatermark(sequence=high_watermark, limit=limit),
                    state,
                )
                rows = () if not transition.actions else await self._sources(connection, transition.actions[0])
            if not transition.actions:
                return ExperienceIncubationResult(
                    previous_cursor=state.sequence,
                    high_watermark=high_watermark,
                    current_cursor=state.sequence,
                    source_count=0,
                    candidate_count=0,
                )

            pipeline = self._services.experience_pipeline
            if pipeline is None:
                raise RuntimeError("Experience incubation pipeline is not configured")  # noqa: TRY003
            plans = await pipeline.incubate(tuple(row.value for row in rows))
            _validate_experience_plans(plans, rows)
            action = transition.actions[0]
            async with self._services.database.transaction() as connection:
                review = self._services.review(connection)
                for plan in plans:
                    await review.propose_experience(
                        plan.proposal,
                        sources=plan.sources,
                        artifacts=(),
                        target=None,
                        reason=plan.reason,
                    )
                await self._services.repositories.cursors.save(
                    connection,
                    self._services.scope_id,
                    EXPERIENCE_INCUBATION_CURSOR_NAME,
                    transition.state,
                    expected_generation=None if state_row is None else state_row.generation,
                )
            return ExperienceIncubationResult(
                previous_cursor=action.after,
                high_watermark=high_watermark,
                current_cursor=action.through,
                source_count=len(rows),
                candidate_count=len(plans),
            )

    async def _sources(
        self,
        connection: AsyncConnection,
        action: ProcessSourceWindow,
    ) -> tuple[StoredSource, ...]:
        return await self._services.repositories.sources.list(
            connection,
            self._services.scope_id,
            after=action.after,
            limit=action.through - action.after,
        )


def _validate_experience_plans(
    plans: tuple[ExperienceCandidateInput, ...],
    rows: tuple[StoredSource, ...],
) -> None:
    available = {(row.ref.source_type, row.ref.source_id) for row in rows}
    if any((source.source_type, source.source_id) not in available for plan in plans for source in plan.sources):
        raise InvalidInferenceOutputError(
            "experience-incubate",
            "pipeline cited a Source outside the current incubation window",
        )


def _scoped_id_factory(memory_artifact_id: str, delegate: IdFactory | None) -> IdFactory:
    def new_id(kind: str) -> str:
        if kind == "memory":
            return memory_artifact_id
        if delegate is not None:
            return delegate(kind)
        prefixes = {
            "candidate": "cand",
            "entry": "mem_ent",
            "experience": "exp",
            "skill": "skill",
            "version": "mem_ver",
        }
        return f"{prefixes[kind]}_{uuid4().hex}"

    return new_id
