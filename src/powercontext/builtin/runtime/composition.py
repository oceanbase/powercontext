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

"""Composition and lifecycle for one configured built-in runtime."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from pydantic import AnyHttpUrl, JsonValue, SecretStr
from typing_extensions import override

from powercontext.builtin.artifacts.experience import ExperienceCandidatePipeline, ExperienceGenerator
from powercontext.builtin.artifacts.handoff import (
    DefaultHandoffEvidenceProjector,
    HandoffGenerationPipeline,
)
from powercontext.builtin.artifacts.memory import (
    CandidatePipeline,
    DefaultMemoryEvidenceProjector,
    MemoryCapabilities,
    MemoryHit,
    MemoryRerankDecision,
    MemoryReranker,
)
from powercontext.builtin.artifacts.skill import AgentSkillProvider, ExternalSkillProvider, SkillGenerator
from powercontext.builtin.handoff_report.adapters import RuntimeHandoffReadAdapter, RuntimeWorkContinuityReadAdapter
from powercontext.builtin.handoff_report.application import HandoffReportApplication
from powercontext.builtin.handoff_report.sqlite import HANDOFF_REPORT_TABLES
from powercontext.builtin.inference import EmbeddingModel, TokenEstimator, character_token_estimator
from powercontext.builtin.inference.usage import (
    UsageReportingEmbeddingModel,
    UsageReportingStructuredGenerator,
)
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, MemoryIndex
from powercontext.builtin.persistence.oceanbase.experience_index import OceanBaseExperienceFTSIndex
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb.profile import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.skill_distribution_schema import ensure_skill_distribution_schema
from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
from powercontext.builtin.persistence.sqlite.memory_index import SQLiteMemoryFTSIndex, SQLiteMemoryVectorIndex
from powercontext.builtin.persistence.sqlite.profile import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime._scope_cache import ScopeCacheObserver
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.runtime.config import BuiltinConfig, ExternalSkillsConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.models import MemorySearchMode, RuntimeCapabilities
from powercontext.builtin.runtime.protocols import RuntimeTracing
from powercontext.builtin.runtime.readiness import (
    CachedReadinessProbe,
    ReadinessProbe,
    ReadinessProbeDefinition,
    RuntimeReadinessChecks,
    dependency_readiness_probe,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, TEXT_EVIDENCE_PROJECTION_KEY
from powercontext.errors import InvalidSourceProjectionError, SourceProjectionNotFoundError
from powercontext.sources import Source, SourceDefinitionRegistry, SourceProjectionKey

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.models.instrumented import InstrumentationSettings
    from pydantic_ai.providers import Provider

ValueT = TypeVar("ValueT")


class BuiltinConfigurationError(RuntimeError):
    """Report a configuration that cannot assemble the built-in runtime."""

    def __init__(self, issue: str) -> None:
        messages = {
            "external-skill-host": "external Skill roots require a host identity",
            "inference-endpoint-provider": (
                "custom inference endpoints require an OpenAI- or Anthropic-compatible model identifier"
            ),
            "inference-profile": "validated inference profile is incomplete",
            "memory-reranker": "Memory reranking requires a configured generation or rerank model, or injected reranker",
            "scheduled-experience-pipeline": "scheduled Experience incubation requires a candidate pipeline",
            "scheduled-pipeline": "scheduled Source processing requires a candidate pipeline",
            "database": "unsupported built-in database",
        }
        super().__init__(messages[issue])


class _DefinitionEvidenceProjector(DefaultMemoryEvidenceProjector):
    def __init__(self, definitions: SourceDefinitionRegistry, projection: SourceProjectionKey) -> None:
        self._definitions = definitions
        self._projection = projection

    @override
    def project_source(self, source: Source, /) -> JsonValue:
        try:
            return self._definitions.project(source, self._projection)
        except (InvalidSourceProjectionError, SourceProjectionNotFoundError):
            return super().project_source(source)


class _DefinitionHandoffEvidenceProjector(DefaultHandoffEvidenceProjector):
    def __init__(self, definitions: SourceDefinitionRegistry, projection: SourceProjectionKey) -> None:
        self._definitions = definitions
        self._projection = projection

    @override
    def project_source(self, source: Source, /) -> JsonValue:
        try:
            return self._definitions.project(source, self._projection)
        except (InvalidSourceProjectionError, SourceProjectionNotFoundError):
            return super().project_source(source)


class _TracingMemoryReranker:
    """Trace one configured reranker without exposing Memory content."""

    def __init__(self, delegate: MemoryReranker, tracing: RuntimeTracing) -> None:
        self._delegate = delegate
        self._tracing = tracing
        self.policy_id = delegate.policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        with self._tracing.stage(
            "memory.rerank",
            attributes={
                "powercontext.memory.rerank.candidate_count": len(candidates),
                "powercontext.memory.rerank.limit": limit,
            },
        ) as span:
            decision = await self._delegate.rerank(query, candidates, limit)
            span.set_attributes({
                "powercontext.memory.rerank.selected_count": len(decision.selected_ranks),
                "powercontext.memory.rerank.discarded_rank_count": decision.discarded_rank_count,
                "powercontext.memory.rerank.used_fallback": decision.used_fallback,
            })
            return decision


@asynccontextmanager
async def open_builtin_runtime(
    config: BuiltinConfig,
    *,
    scheduler_path: str | Path = "powercontext.scheduler.db",
    candidate_pipeline: CandidatePipeline | None = None,
    experience_pipeline: ExperienceCandidatePipeline | None = None,
    experience_generator: ExperienceGenerator | None = None,
    skill_generator: SkillGenerator | None = None,
    external_skill_provider: ExternalSkillProvider | None = None,
    handoff_pipeline: HandoffGenerationPipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
    token_estimator: TokenEstimator | None = None,
    memory_reranker: MemoryReranker | None = None,
    instrumentation: InstrumentationSettings | None = None,
    scope_cache_observer: ScopeCacheObserver | None = None,
    tracing: RuntimeTracing | None = None,
    source_registry: SourceDefinitionRegistry | None = None,
) -> AsyncIterator[BuiltinRuntime]:
    """Open the selected database, inference adapters, and built-in runtime."""

    async with AsyncExitStack() as resources:
        configured_source_registry = source_registry or BUILTIN_SOURCE_REGISTRY
        (
            generated_memory,
            generated_incubation,
            generated_experience,
            generated_skill,
            generated_handoff,
            generated_reranker,
            generation_readiness,
            rerank_readiness,
        ) = (
            await _generation_pipelines(
                config.inference,
                config.runtime,
                resources,
                instrumentation,
                configured_source_registry,
            )
            if (
                candidate_pipeline is None
                or experience_pipeline is None
                or experience_generator is None
                or skill_generator is None
                or handoff_pipeline is None
                or (config.runtime.memory_rerank_enabled and memory_reranker is None)
            )
            else (None, None, None, None, None, None, None, None)
        )
        configured_pipeline = generated_memory if candidate_pipeline is None else candidate_pipeline
        configured_incubation = generated_incubation if experience_pipeline is None else experience_pipeline
        configured_experience = generated_experience if experience_generator is None else experience_generator
        configured_skill = generated_skill if skill_generator is None else skill_generator
        configured_handoff = generated_handoff if handoff_pipeline is None else handoff_pipeline
        configured_reranker = generated_reranker if memory_reranker is None else memory_reranker
        if configured_reranker is not None and tracing is not None:
            configured_reranker = _TracingMemoryReranker(configured_reranker, tracing)
        if embedding_model is None:
            configured_embedding_source, readiness_embedding = await _embedding_models(
                config.inference,
                resources,
                instrumentation,
            )
        else:
            from powercontext.builtin.inference.pydantic_ai import PydanticAIEmbeddingModel

            configured_embedding_source = embedding_model
            readiness_embedding = (
                embedding_model._without_instrumentation()
                if isinstance(embedding_model, PydanticAIEmbeddingModel)
                else embedding_model
            )
        configured_embedding = (
            None if configured_embedding_source is None else UsageReportingEmbeddingModel(configured_embedding_source)
        )
        configured_external_skills = (
            _external_skill_provider(config.external_skills)
            if external_skill_provider is None
            else external_skill_provider
        )
        contexts = await resources.enter_async_context(
            open_builtin_contexts(
                config,
                candidate_pipeline=configured_pipeline,
                experience_pipeline=configured_incubation,
                experience_generator=configured_experience,
                skill_generator=configured_skill,
                external_skill_provider=configured_external_skills,
                handoff_pipeline=configured_handoff,
                embedding_model=configured_embedding,
                token_estimator=token_estimator,
                memory_reranker=configured_reranker,
                source_registry=configured_source_registry,
            )
        )
        readiness_probes: dict[str, ReadinessProbeDefinition] = {
            "database": ReadinessProbeDefinition(
                probe=dependency_readiness_probe(contexts.database.ping),
                blocking=True,
            ),
        }
        inference_readiness = (
            ("inference.generation", generation_readiness),
            ("inference.rerank", rerank_readiness),
            (
                "inference.embedding",
                None if readiness_embedding is None else _embedding_readiness_probe(readiness_embedding),
            ),
        )
        for name, readiness_probe in inference_readiness:
            if readiness_probe is not None:
                readiness_probes[name] = ReadinessProbeDefinition(probe=readiness_probe, blocking=False)
        runtime = await resources.enter_async_context(
            BuiltinRuntime(
                provider=contexts,
                capabilities=RuntimeCapabilities(
                    memory_extraction=contexts.memory_extraction,
                    experience_generation=contexts.experience_generation,
                    managed_skill_generation=contexts.managed_skill_generation,
                    external_skill_registry=contexts.external_skill_registry,
                    memory_search_modes=_search_modes(contexts.index.capabilities),
                    handoff_generation=contexts.handoff_generation,
                ),
                source_window_limit=config.runtime.source_window_limit,
                scope_cache_size=config.runtime.scope_cache_size,
                scope_evictor=contexts.evict,
                scope_cache_observer=scope_cache_observer,
                scope_ids=contexts.scope_ids,
                review_service=contexts.review,
                generation_service=contexts.generation,
                experience_recall=contexts.search_experience,
                skill_recall=contexts.search_skills,
                skill_lister=contexts.list_skills,
                skill_origin_reader=contexts.get_skill_origins,
                skill_governance_reader=contexts.get_skill_governance,
                skill_governance_updater=contexts.update_skill_lifecycle,
                skill_package_resolver=contexts.skill_package,
                package_snapshot_resolver=contexts.package_snapshot,
                skill_package_uploader=contexts.upload_skill_package,
                skill_usage_recorder=contexts.record_skill_usage,
                experience_incubator=contexts.incubate_experience if contexts.experience_incubation else None,
                external_skill_registry=contexts.external_skills if contexts.external_skill_registry else None,
                external_skill_importer=contexts.import_external_skill if contexts.external_skill_registry else None,
                skill_publication_service=contexts.skill_publications,
                remote_skill_distribution=contexts.remote_skill_distribution(),
                statistics_service=contexts.statistics,
                recall_token_estimator=contexts.estimate_recall_tokens,
                readiness=RuntimeReadinessChecks(readiness_probes),
                tracing=tracing,
                remote_ingestion=contexts,
            )
        )
        if config.handoff_report.enabled:
            runtime.handoff_report = HandoffReportApplication(
                contexts.database,
                RuntimeHandoffReadAdapter(runtime.handoff),
                continuity=RuntimeWorkContinuityReadAdapter(runtime.work),
                scope_ids=contexts.handoff_scope_ids,
            )
        if config.runtime.schedule_seconds is not None and configured_pipeline is None:
            raise BuiltinConfigurationError("scheduled-pipeline")
        if config.runtime.experience_schedule_seconds is not None and configured_incubation is None:
            raise BuiltinConfigurationError("scheduled-experience-pipeline")
        if config.runtime.memory_rerank_enabled and configured_reranker is None:
            raise BuiltinConfigurationError("memory-reranker")
        if config.runtime.schedule_seconds is not None or config.runtime.experience_schedule_seconds is not None:
            runtime.start_scheduler(
                scheduler_path,
                config.runtime.schedule_seconds,
                experience_schedule_seconds=config.runtime.experience_schedule_seconds,
            )
        yield runtime


@asynccontextmanager
async def open_builtin_contexts(
    config: BuiltinConfig,
    *,
    candidate_pipeline: CandidatePipeline | None = None,
    experience_pipeline: ExperienceCandidatePipeline | None = None,
    experience_generator: ExperienceGenerator | None = None,
    skill_generator: SkillGenerator | None = None,
    external_skill_provider: ExternalSkillProvider | None = None,
    handoff_pipeline: HandoffGenerationPipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
    token_estimator: TokenEstimator | None = None,
    memory_reranker: MemoryReranker | None = None,
    source_registry: SourceDefinitionRegistry | None = None,
) -> AsyncIterator[RelationalContexts]:
    """Open the selected database and expose scope-bound PowerContext providers."""

    database = config.database
    report_tables = HANDOFF_REPORT_TABLES if config.handoff_report.enabled else ()
    configured_token_estimator = character_token_estimator() if token_estimator is None else token_estimator
    if isinstance(database, SQLiteConfig):
        experience_index = SQLiteExperienceFTSIndex()
        indexes: list[MemoryIndex] = [SQLiteMemoryFTSIndex()]
        if embedding_model is not None:
            indexes.append(SQLiteMemoryVectorIndex(embedding_model.profile))
        index = CompositeMemoryIndex(*indexes)
        async with SQLiteProfile.open(
            database,
            tables=BUILTIN_TABLES + report_tables + index.tables,
            load_vector_extension=embedding_model is not None,
        ) as profile:
            async with profile.database.transaction() as connection:
                await ensure_skill_distribution_schema(connection)
                await index.initialize(connection)
                await experience_index.initialize(connection)
            yield RelationalContexts(
                database=profile.database,
                index=index,
                experience_index=experience_index,
                candidate_pipeline=candidate_pipeline,
                experience_pipeline=experience_pipeline,
                experience_generator=experience_generator,
                skill_generator=skill_generator,
                external_skill_provider=external_skill_provider,
                handoff_pipeline=handoff_pipeline,
                embedding_model=embedding_model,
                token_estimator=configured_token_estimator,
                memory_reranker=memory_reranker,
                memory_rerank_candidate_limit=config.runtime.memory_rerank_candidate_limit,
                source_registry=source_registry,
            )
        return
    experience_index = OceanBaseExperienceFTSIndex()
    indexes = [OceanBaseMemoryFTSIndex()]
    if embedding_model is not None:
        indexes.append(OceanBaseMemoryVectorIndex(embedding_model.profile))
    index = CompositeMemoryIndex(*indexes)
    tables = BUILTIN_TABLES + report_tables + index.tables
    if isinstance(database, OceanBaseConfig):
        profile_context = OceanBaseProfile.open(database, tables=tables)
    elif isinstance(database, SeekDBConfig):
        profile_context = SeekDBProfile.open(database, tables=tables)
    else:
        raise BuiltinConfigurationError("database")
    async with profile_context as profile:
        async with profile.database.transaction() as connection:
            await ensure_skill_distribution_schema(connection)
            await index.initialize(connection)
            await experience_index.initialize(connection)
        yield RelationalContexts(
            database=profile.database,
            index=index,
            experience_index=experience_index,
            candidate_pipeline=candidate_pipeline,
            experience_pipeline=experience_pipeline,
            experience_generator=experience_generator,
            skill_generator=skill_generator,
            external_skill_provider=external_skill_provider,
            handoff_pipeline=handoff_pipeline,
            embedding_model=embedding_model,
            token_estimator=configured_token_estimator,
            memory_reranker=memory_reranker,
            memory_rerank_candidate_limit=config.runtime.memory_rerank_candidate_limit,
            source_registry=source_registry,
        )


async def _generation_pipelines(
    settings: InferenceConfig,
    runtime: RuntimeConfig,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
    source_registry: SourceDefinitionRegistry,
) -> tuple[
    CandidatePipeline | None,
    ExperienceCandidatePipeline | None,
    ExperienceGenerator | None,
    SkillGenerator | None,
    HandoffGenerationPipeline | None,
    MemoryReranker | None,
    ReadinessProbe | None,
    ReadinessProbe | None,
]:
    if settings.generation_model is None and (not runtime.memory_rerank_enabled or settings.rerank_model is None):
        return None, None, None, None, None, None, None, None

    from pydantic_ai.settings import ModelSettings, merge_model_settings

    from powercontext.builtin.artifacts.experience import (
        EXPERIENCE_GENERATION_INSTRUCTIONS,
        EXPERIENCE_INCUBATION_INSTRUCTIONS,
        ExperienceGenerationOutput,
        ExperienceIncubationInput,
        ExperienceIncubationOutput,
        LLMExperienceCandidatePipeline,
        LLMExperienceGenerator,
    )
    from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
    from powercontext.builtin.artifacts.handoff import (
        HANDOFF_GENERATION_INSTRUCTIONS,
        HandoffGenerationInput,
        HandoffGenerationOutput,
        LLMHandoffGenerationPipeline,
    )
    from powercontext.builtin.artifacts.memory import (
        MEMORY_RERANK_INSTRUCTIONS,
        LLMMemoryCandidatePipeline,
        LLMMemoryReranker,
        MemoryExtractionInput,
        MemoryExtractionOutput,
        MemoryRerankInput,
        MemoryRerankOutput,
        memory_extraction_instructions,
    )
    from powercontext.builtin.artifacts.skill import (
        SKILL_GENERATION_INSTRUCTIONS,
        LLMSkillGenerator,
        SkillGenerationOutput,
    )
    from powercontext.builtin.inference.pydantic_ai import (
        InferenceLimits,
        PydanticAIStructuredGenerator,
        probe_pydantic_ai_model,
    )

    generated_memory: CandidatePipeline | None = None
    generated_incubation: ExperienceCandidatePipeline | None = None
    generated_experience: ExperienceGenerator | None = None
    generated_skill: SkillGenerator | None = None
    generated_handoff: HandoffGenerationPipeline | None = None
    generated_reranker: MemoryReranker | None = None
    generation_readiness: ReadinessProbe | None = None
    rerank_readiness: ReadinessProbe | None = None

    generation_provider_model: Model | None = None
    generation_model: Model | None = None
    generation_request_settings: ModelSettings | None = None
    if settings.generation_model is not None:
        generation_provider_model, generation_model = await _open_pydantic_ai_model(
            settings.generation_model,
            base_url=settings.generation_base_url,
            headers=settings.generation_headers,
            resources=resources,
            instrumentation=instrumentation,
        )
        generation_request_settings = cast(ModelSettings, dict(settings.generation_model_settings)) or None
        generation_limits = InferenceLimits(
            timeout_seconds=settings.generation_timeout_seconds,
            max_requests=settings.generation_max_requests,
        )
        memory_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=memory_extraction_instructions(runtime.memory_extraction_profile),
            input_type=MemoryExtractionInput,
            output_type=MemoryExtractionOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="memory_extraction",
        )
        experience_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=EXPERIENCE_INCUBATION_INSTRUCTIONS,
            input_type=ExperienceIncubationInput,
            output_type=ExperienceIncubationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="experience_incubation",
        )
        explicit_experience_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=EXPERIENCE_GENERATION_INSTRUCTIONS,
            input_type=ArtifactGenerationInput,
            output_type=ExperienceGenerationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="experience_generation",
        )
        skill_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=SKILL_GENERATION_INSTRUCTIONS,
            input_type=ArtifactGenerationInput,
            output_type=SkillGenerationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="skill_generation",
        )
        handoff_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=HANDOFF_GENERATION_INSTRUCTIONS,
            input_type=HandoffGenerationInput,
            output_type=HandoffGenerationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="handoff_generation",
        )
        generated_memory = LLMMemoryCandidatePipeline(
            UsageReportingStructuredGenerator(memory_generator),
            evidence_projector=_DefinitionEvidenceProjector(source_registry, TEXT_EVIDENCE_PROJECTION_KEY),
        )
        generated_incubation = LLMExperienceCandidatePipeline(UsageReportingStructuredGenerator(experience_generator))
        generated_experience = LLMExperienceGenerator(UsageReportingStructuredGenerator(explicit_experience_generator))
        generated_skill = LLMSkillGenerator(UsageReportingStructuredGenerator(skill_generator))
        generated_handoff = LLMHandoffGenerationPipeline(
            UsageReportingStructuredGenerator(handoff_generator),
            evidence_projector=_DefinitionHandoffEvidenceProjector(source_registry, TEXT_EVIDENCE_PROJECTION_KEY),
        )

        async def probe_generation() -> None:
            # Readiness probing runs outside any operation span; keep it out of traces.
            await probe_pydantic_ai_model(
                generation_provider_model,
                timeout_seconds=settings.generation_timeout_seconds,
                model_settings=generation_request_settings,
            )

        generation_readiness = CachedReadinessProbe(
            dependency_readiness_probe(probe_generation, timeout_seconds=settings.generation_timeout_seconds)
        )

    if runtime.memory_rerank_enabled:
        rerank_provider_model = generation_provider_model
        rerank_model = generation_model
        inherits_generation = settings.rerank_model is None
        rerank_headers = (
            _merge_headers(settings.generation_headers, settings.rerank_headers)
            if inherits_generation
            else settings.rerank_headers
        )
        separate_rerank_model = settings.rerank_model is not None or bool(settings.rerank_headers)
        if separate_rerank_model:
            rerank_model_name = settings.rerank_model or settings.generation_model
            if rerank_model_name is None:
                raise BuiltinConfigurationError("memory-reranker")
            rerank_provider_model, rerank_model = await _open_pydantic_ai_model(
                rerank_model_name,
                base_url=settings.rerank_base_url
                if settings.rerank_model is not None
                else settings.generation_base_url,
                headers=rerank_headers,
                resources=resources,
                instrumentation=instrumentation,
            )
        if rerank_provider_model is not None and rerank_model is not None:
            rerank_values = (
                settings.generation_model_settings | settings.rerank_model_settings
                if inherits_generation
                else settings.rerank_model_settings
            )
            rerank_request_settings = cast(ModelSettings, dict(rerank_values))
            rerank_request_settings = merge_model_settings(
                rerank_request_settings,
                ModelSettings(temperature=0.0),
            )
            rerank_generator = PydanticAIStructuredGenerator(
                model=rerank_model,
                instructions=MEMORY_RERANK_INSTRUCTIONS,
                input_type=MemoryRerankInput,
                output_type=MemoryRerankOutput,
                limits=InferenceLimits(
                    timeout_seconds=settings.rerank_timeout_seconds or settings.generation_timeout_seconds,
                    max_requests=settings.rerank_max_requests or settings.generation_max_requests,
                ),
                model_settings=rerank_request_settings,
                name="memory_rerank",
            )
            generated_reranker = LLMMemoryReranker(UsageReportingStructuredGenerator(rerank_generator))

            if separate_rerank_model or settings.rerank_model_settings:

                async def probe_rerank() -> None:
                    timeout_seconds = settings.rerank_timeout_seconds or settings.generation_timeout_seconds
                    await probe_pydantic_ai_model(
                        rerank_provider_model,
                        timeout_seconds=timeout_seconds,
                        model_settings=rerank_request_settings,
                    )

                rerank_readiness = CachedReadinessProbe(
                    dependency_readiness_probe(
                        probe_rerank,
                        timeout_seconds=settings.rerank_timeout_seconds or settings.generation_timeout_seconds,
                    )
                )

    return (
        generated_memory,
        generated_incubation,
        generated_experience,
        generated_skill,
        generated_handoff,
        generated_reranker,
        generation_readiness,
        rerank_readiness,
    )


async def _open_pydantic_ai_model(
    model_name: str,
    *,
    base_url: AnyHttpUrl | None,
    headers: Mapping[str, SecretStr],
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
) -> tuple[Model, Model]:
    from pydantic_ai.models import infer_model
    from pydantic_ai.models.instrumented import InstrumentedModel

    if (base_url is not None or headers) and ":" not in model_name:
        raise BuiltinConfigurationError("inference-endpoint-provider")
    inferred_model = (
        infer_model(model_name)
        if base_url is None and not headers
        else infer_model(
            model_name,
            provider_factory=_provider_factory(
                base_url,
                headers,
                workload="generation",
                resources=resources,
            ),
        )
    )
    provider_model = await resources.enter_async_context(inferred_model)
    model = provider_model if instrumentation is None else InstrumentedModel(provider_model, instrumentation)
    return provider_model, model


def _provider_factory(
    base_url: AnyHttpUrl | None,
    headers: Mapping[str, SecretStr],
    *,
    workload: Literal["generation", "embedding"],
    resources: AsyncExitStack,
) -> Callable[[str], Provider[Any]]:
    from pydantic_ai.providers import infer_provider

    if base_url is None and not headers:
        return infer_provider

    from pydantic_ai.providers.openai import OpenAIProvider

    def create_provider(provider_name: str) -> Provider[Any]:
        if provider_name in {"openai", "openai-chat", "openai-responses"}:
            if headers:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(
                    base_url=None if base_url is None else str(base_url),
                    api_key=os.getenv("OPENAI_API_KEY") or "api-key-not-set",
                    default_headers=_resolve_headers(headers),
                )
                resources.push_async_callback(client.close)
                return OpenAIProvider(openai_client=client)
            return OpenAIProvider(base_url=str(base_url))
        if provider_name == "anthropic" and workload == "generation":
            from anthropic import AsyncAnthropic
            from pydantic_ai.providers.anthropic import AnthropicProvider

            if headers:
                default_headers = _resolve_headers(headers)
                api_key = _pop_header(default_headers, "x-api-key")
                client = AsyncAnthropic(
                    base_url=None if base_url is None else str(base_url),
                    api_key=api_key or os.getenv("ANTHROPIC_API_KEY") or "api-key-not-set",
                    default_headers=default_headers,
                )
                resources.push_async_callback(client.close)
                return AnthropicProvider(anthropic_client=client)
            return AnthropicProvider(
                base_url=str(base_url),
                api_key=os.getenv("ANTHROPIC_API_KEY") or "api-key-not-set",
            )
        raise BuiltinConfigurationError("inference-endpoint-provider")

    return create_provider


def _resolve_headers(headers: Mapping[str, SecretStr]) -> dict[str, str]:
    return {name: value.get_secret_value() for name, value in headers.items()}


def _pop_header(headers: dict[str, str], name: str) -> str | None:
    expected = name.casefold()
    for existing_name in headers:
        if existing_name.casefold() == expected:
            return headers.pop(existing_name)
    return None


def _merge_headers(*values: Mapping[str, SecretStr]) -> dict[str, SecretStr]:
    merged: dict[str, SecretStr] = {}
    names: dict[str, str] = {}
    for headers in values:
        for name, value in headers.items():
            normalized_name = name.casefold()
            if previous_name := names.get(normalized_name):
                del merged[previous_name]
            merged[name] = value
            names[normalized_name] = name
    return merged


async def _embedding_models(
    settings: InferenceConfig,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
) -> tuple[EmbeddingModel | None, EmbeddingModel | None]:
    if settings.embedding_model is None:
        return None, None

    from pydantic_ai import Embedder
    from pydantic_ai.embeddings import EmbeddingSettings, infer_embedding_model

    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIEmbeddingModel

    providers: list[Provider[Any]] = []
    create_provider = _provider_factory(
        settings.embedding_base_url,
        settings.embedding_headers,
        workload="embedding",
        resources=resources,
    )

    def provider_factory(provider_name: str) -> Provider[Any]:
        provider = create_provider(provider_name)
        providers.append(provider)
        return provider

    model = infer_embedding_model(settings.embedding_model, provider_factory=provider_factory)
    for provider in providers:
        await resources.enter_async_context(provider)
    profile = EmbeddingProfile(
        profile_id=_required(settings.embedding_profile_id),
        model=settings.embedding_model,
        dimension=_required(settings.embedding_dimension),
        distance="l2",
        normalization=settings.embedding_normalization,
    )
    limits = InferenceLimits(timeout_seconds=settings.embedding_timeout_seconds)
    embedding_settings = cast(
        EmbeddingSettings,
        settings.embedding_model_settings | {"dimensions": profile.dimension},
    )

    def adapter(instrument: InstrumentationSettings | bool | None) -> EmbeddingModel:
        return PydanticAIEmbeddingModel(
            embedder=Embedder(model, settings=embedding_settings, instrument=instrument),
            batch_size=settings.embedding_batch_size,
            profile=profile,
            limits=limits,
        )

    # Readiness runs outside an application operation, so use the same provider model
    # without instrumentation to avoid exporting an orphan inference span.
    return adapter(instrumentation), adapter(False)


def _embedding_readiness_probe(model: EmbeddingModel) -> ReadinessProbe:
    async def probe_embedding() -> None:
        await model.embed(("PowerContext readiness probe",))

    return CachedReadinessProbe(dependency_readiness_probe(probe_embedding))


def _required(value: ValueT | None) -> ValueT:
    if value is None:
        raise BuiltinConfigurationError("inference-profile")
    return value


def _external_skill_provider(settings: ExternalSkillsConfig) -> ExternalSkillProvider | None:
    if not settings.agent_targets:
        return None
    if settings.host_id is None:
        raise BuiltinConfigurationError("external-skill-host")
    return AgentSkillProvider(host_id=settings.host_id, targets=settings.agent_targets)


def _search_modes(capabilities: MemoryCapabilities) -> tuple[MemorySearchMode, ...]:
    modes: list[MemorySearchMode] = []
    if capabilities.fts or capabilities.hybrid:
        modes.append("auto")
    if capabilities.fts:
        modes.append("fts")
    if capabilities.vector:
        modes.append("vector")
    if capabilities.hybrid:
        modes.append("hybrid")
    return tuple(modes)


__all__ = ["BuiltinConfigurationError", "open_builtin_contexts", "open_builtin_runtime"]
