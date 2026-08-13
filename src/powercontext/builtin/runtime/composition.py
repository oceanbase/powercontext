"""Composition and lifecycle for one configured built-in runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from pydantic import JsonValue

from powercontext.builtin.artifacts.experience import ExperienceCandidatePipeline, ExperienceGenerator
from powercontext.builtin.artifacts.handoff import (
    DefaultHandoffEvidenceProjector,
    HandoffGenerationPipeline,
)
from powercontext.builtin.artifacts.memory import (
    CandidatePipeline,
    DefaultMemoryEvidenceProjector,
    MemoryCapabilities,
    MemoryReranker,
)
from powercontext.builtin.artifacts.skill import CodexSkillProvider, ExternalSkillProvider, SkillGenerator
from powercontext.builtin.handoff_report.adapters import RuntimeHandoffReadAdapter
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
from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
from powercontext.builtin.persistence.sqlite.memory_index import SQLiteMemoryFTSIndex, SQLiteMemoryVec1Index
from powercontext.builtin.persistence.sqlite.profile import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.runtime.config import BuiltinConfig, ExternalSkillsConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.models import MemorySearchMode, RuntimeCapabilities
from powercontext.builtin.runtime.readiness import (
    READINESS_PROBE_TIMEOUT_SECONDS,
    CachedReadinessProbe,
    ReadinessProbe,
    ReadinessProbeDefinition,
    RuntimeReadinessChecks,
    dependency_readiness_probe,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import CONTENT_SOURCE_NAME, ContentSource
from powercontext.sources import Source

if TYPE_CHECKING:
    from pydantic_ai.models.instrumented import InstrumentationSettings

ValueT = TypeVar("ValueT")


class BuiltinConfigurationError(RuntimeError):
    """Report a configuration that cannot assemble the built-in runtime."""

    def __init__(self, issue: str) -> None:
        messages = {
            "external-skill-host": "external Skill roots require a host identity",
            "inference-profile": "validated inference profile is incomplete",
            "memory-reranker": "Memory reranking requires a configured generation model or injected reranker",
            "scheduled-experience-pipeline": "scheduled Experience incubation requires a candidate pipeline",
            "scheduled-pipeline": "scheduled Source processing requires a candidate pipeline",
            "database": "unsupported built-in database",
        }
        super().__init__(messages[issue])


class _ContentEvidenceProjector(DefaultMemoryEvidenceProjector):
    def project_source(self, source: Source, /) -> JsonValue:
        if isinstance(source, ContentSource):
            return {
                "source_type": CONTENT_SOURCE_NAME,
                "source_id": source.name,
                "content": source.content,
                "metadata": source.model_dump(mode="json")["metadata"],
            }
        return super().project_source(source)


class _ContentHandoffEvidenceProjector(DefaultHandoffEvidenceProjector):
    def project_source(self, source: Source, /) -> JsonValue:
        if isinstance(source, ContentSource):
            return {
                "source_type": CONTENT_SOURCE_NAME,
                "source_id": source.name,
                "content": source.content,
                "metadata": source.model_dump(mode="json")["metadata"],
            }
        return super().project_source(source)


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
) -> AsyncIterator[BuiltinRuntime]:
    """Open the selected database, inference adapters, and built-in runtime."""

    async with AsyncExitStack() as resources:
        (
            generated_memory,
            generated_incubation,
            generated_experience,
            generated_skill,
            generated_handoff,
            generated_reranker,
            generation_readiness,
        ) = (
            await _generation_pipelines(config.inference, config.runtime, resources, instrumentation)
            if (
                candidate_pipeline is None
                or experience_pipeline is None
                or experience_generator is None
                or skill_generator is None
                or handoff_pipeline is None
                or (config.runtime.memory_rerank_enabled and memory_reranker is None)
            )
            else (None, None, None, None, None, None, None)
        )
        configured_pipeline = generated_memory if candidate_pipeline is None else candidate_pipeline
        configured_incubation = generated_incubation if experience_pipeline is None else experience_pipeline
        configured_experience = generated_experience if experience_generator is None else experience_generator
        configured_skill = generated_skill if skill_generator is None else skill_generator
        configured_handoff = generated_handoff if handoff_pipeline is None else handoff_pipeline
        configured_reranker = generated_reranker if memory_reranker is None else memory_reranker
        configured_embedding_source = (
            await _embedding_model(config.inference, resources, instrumentation)
            if embedding_model is None
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
            )
        )
        readiness_probes: dict[str, ReadinessProbeDefinition] = {
            "database": ReadinessProbeDefinition(
                probe=dependency_readiness_probe(contexts.database.ping),
                blocking=True,
            ),
        }
        if generation_readiness is not None:
            readiness_probes["inference.generation"] = ReadinessProbeDefinition(
                probe=generation_readiness,
                blocking=False,
            )
        if configured_embedding_source is not None:
            readiness_probes["inference.embedding"] = ReadinessProbeDefinition(
                probe=_embedding_readiness_probe(configured_embedding_source),
                blocking=False,
            )
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
                scope_ids=contexts.scope_ids,
                review_service=contexts.review,
                generation_service=contexts.generation,
                experience_recall=contexts.search_experience,
                experience_incubator=contexts.incubate_experience if contexts.experience_incubation else None,
                external_skill_registry=contexts.external_skills if contexts.external_skill_registry else None,
                external_skill_importer=contexts.import_external_skill if contexts.external_skill_registry else None,
                statistics_service=contexts.statistics,
                recall_token_estimator=contexts.estimate_recall_tokens,
                readiness=RuntimeReadinessChecks(readiness_probes),
            )
        )
        if config.handoff_report.enabled:
            runtime.handoff_report = HandoffReportApplication(
                contexts.database,
                RuntimeHandoffReadAdapter(runtime.handoff),
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
) -> AsyncIterator[RelationalContexts]:
    """Open the selected database and expose scope-bound PowerContext providers."""

    database = config.database
    report_tables = HANDOFF_REPORT_TABLES if config.handoff_report.enabled else ()
    configured_token_estimator = character_token_estimator() if token_estimator is None else token_estimator
    if isinstance(database, SQLiteConfig):
        experience_index = SQLiteExperienceFTSIndex()
        indexes: list[MemoryIndex] = [SQLiteMemoryFTSIndex()]
        if database.vec1_extension is not None:
            if embedding_model is None:
                raise ValueError("SQLite Vec1 requires an embedding model")  # noqa: TRY003
            indexes.append(SQLiteMemoryVec1Index(database.vec1_extension, embedding_model.profile))
        index = CompositeMemoryIndex(*indexes)
        async with SQLiteProfile.open(
            database,
            tables=BUILTIN_TABLES + report_tables + index.tables,
        ) as profile:
            async with profile.database.transaction() as connection:
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
            )
        return
    if not isinstance(database, OceanBaseConfig):
        raise BuiltinConfigurationError("database")

    experience_index = OceanBaseExperienceFTSIndex()
    indexes = [OceanBaseMemoryFTSIndex()]
    if embedding_model is not None:
        indexes.append(OceanBaseMemoryVectorIndex(embedding_model.profile))
    index = CompositeMemoryIndex(*indexes)
    async with OceanBaseProfile.open(
        database,
        tables=BUILTIN_TABLES + report_tables + index.tables,
    ) as profile:
        async with profile.database.transaction() as connection:
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
        )


async def _generation_pipelines(
    settings: InferenceConfig,
    runtime: RuntimeConfig,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
) -> tuple[
    CandidatePipeline | None,
    ExperienceCandidatePipeline | None,
    ExperienceGenerator | None,
    SkillGenerator | None,
    HandoffGenerationPipeline | None,
    MemoryReranker | None,
    ReadinessProbe | None,
]:
    if settings.generation_model is None:
        return None, None, None, None, None, None, None

    from pydantic_ai.models import infer_model
    from pydantic_ai.models.instrumented import InstrumentedModel
    from pydantic_ai.settings import ModelSettings

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

    provider_model = await resources.enter_async_context(infer_model(settings.generation_model))
    model = provider_model if instrumentation is None else InstrumentedModel(provider_model, instrumentation)

    async def probe_generation() -> None:
        # Readiness probing runs outside any operation span; keep it out of traces.
        await probe_pydantic_ai_model(provider_model, timeout_seconds=READINESS_PROBE_TIMEOUT_SECONDS)

    limits = InferenceLimits(
        timeout_seconds=settings.generation_timeout_seconds,
        max_requests=settings.generation_max_requests,
    )
    memory_generator = PydanticAIStructuredGenerator(
        model=model,
        instructions=memory_extraction_instructions(runtime.memory_extraction_profile),
        input_type=MemoryExtractionInput,
        output_type=MemoryExtractionOutput,
        limits=limits,
        name="memory_extraction",
    )
    experience_generator = PydanticAIStructuredGenerator(
        model=model,
        instructions=EXPERIENCE_INCUBATION_INSTRUCTIONS,
        input_type=ExperienceIncubationInput,
        output_type=ExperienceIncubationOutput,
        limits=limits,
        name="experience_incubation",
    )
    explicit_experience_generator = PydanticAIStructuredGenerator(
        model=model,
        instructions=EXPERIENCE_GENERATION_INSTRUCTIONS,
        input_type=ArtifactGenerationInput,
        output_type=ExperienceGenerationOutput,
        limits=limits,
        name="experience_generation",
    )
    skill_generator = PydanticAIStructuredGenerator(
        model=model,
        instructions=SKILL_GENERATION_INSTRUCTIONS,
        input_type=ArtifactGenerationInput,
        output_type=SkillGenerationOutput,
        limits=limits,
        name="skill_generation",
    )
    handoff_generator = PydanticAIStructuredGenerator(
        model=model,
        instructions=HANDOFF_GENERATION_INSTRUCTIONS,
        input_type=HandoffGenerationInput,
        output_type=HandoffGenerationOutput,
        limits=limits,
        name="handoff_generation",
    )
    rerank_generator = (
        PydanticAIStructuredGenerator(
            model=model,
            instructions=MEMORY_RERANK_INSTRUCTIONS,
            input_type=MemoryRerankInput,
            output_type=MemoryRerankOutput,
            limits=limits,
            model_settings=ModelSettings(temperature=0.0),
            name="memory_rerank",
        )
        if runtime.memory_rerank_enabled
        else None
    )
    return (
        LLMMemoryCandidatePipeline(
            UsageReportingStructuredGenerator(memory_generator),
            evidence_projector=_ContentEvidenceProjector(),
        ),
        LLMExperienceCandidatePipeline(UsageReportingStructuredGenerator(experience_generator)),
        LLMExperienceGenerator(UsageReportingStructuredGenerator(explicit_experience_generator)),
        LLMSkillGenerator(UsageReportingStructuredGenerator(skill_generator)),
        LLMHandoffGenerationPipeline(
            UsageReportingStructuredGenerator(handoff_generator),
            evidence_projector=_ContentHandoffEvidenceProjector(),
        ),
        (None if rerank_generator is None else LLMMemoryReranker(UsageReportingStructuredGenerator(rerank_generator))),
        CachedReadinessProbe(dependency_readiness_probe(probe_generation)),
    )


async def _embedding_model(
    settings: InferenceConfig,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
) -> EmbeddingModel | None:
    if settings.embedding_model is None:
        return None

    from pydantic_ai import Embedder
    from pydantic_ai.embeddings import infer_embedding_model
    from pydantic_ai.providers import Provider, infer_provider

    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIEmbeddingModel

    providers: list[Provider[object]] = []

    def provider_factory(provider_name: str) -> Provider[object]:
        provider = infer_provider(provider_name)
        providers.append(provider)
        return provider

    model = infer_embedding_model(settings.embedding_model, provider_factory=provider_factory)
    for provider in providers:
        await resources.enter_async_context(provider)
    return PydanticAIEmbeddingModel(
        embedder=Embedder(model, instrument=instrumentation),
        batch_size=settings.embedding_batch_size,
        profile=EmbeddingProfile(
            profile_id=_required(settings.embedding_profile_id),
            model=settings.embedding_model,
            dimension=_required(settings.embedding_dimension),
            distance="l2",
            normalization=settings.embedding_normalization,
        ),
        limits=InferenceLimits(timeout_seconds=settings.embedding_timeout_seconds),
    )


def _embedding_readiness_probe(model: EmbeddingModel) -> ReadinessProbe:
    async def probe_embedding() -> None:
        await model.embed(("PowerContext readiness probe",))

    return CachedReadinessProbe(dependency_readiness_probe(probe_embedding))


def _required(value: ValueT | None) -> ValueT:
    if value is None:
        raise BuiltinConfigurationError("inference-profile")
    return value


def _external_skill_provider(settings: ExternalSkillsConfig) -> ExternalSkillProvider | None:
    if not settings.codex_roots:
        return None
    if settings.host_id is None:
        raise BuiltinConfigurationError("external-skill-host")
    return CodexSkillProvider(host_id=settings.host_id, roots=settings.codex_roots)


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
