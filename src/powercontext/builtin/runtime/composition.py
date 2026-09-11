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

import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from pydantic import AnyHttpUrl, JsonValue, SecretStr
from typing_extensions import override

from powercontext._logging import log_safely
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
from powercontext.builtin.artifacts.profile.generation import PROFILE_INSTRUCTIONS, LLMProfileGenerator
from powercontext.builtin.artifacts.profile.service import (
    ProfileGenerationInput,
    ProfileGenerationOutput,
    ProfileGenerator,
)
from powercontext.builtin.artifacts.prompt import PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.artifacts.prompt.service import DemonstrationGenerator
from powercontext.builtin.artifacts.skill import AgentSkillProvider, ExternalSkillProvider, SkillGenerator
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryGenerationError,
    topic_memory_stage_budget,
    validate_topic_memory_stage_capacity,
)
from powercontext.builtin.dream.generation import (
    DREAM_INSTRUCTIONS,
    DreamGenerationInput,
    DreamGenerator,
    LLMDreamGenerator,
)
from powercontext.builtin.dream.models import DreamBudget, DreamPlan
from powercontext.builtin.dream.service import CandidateAttester, DreamAuthorizer
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.evidence.resolver import AuthorizationContext
from powercontext.builtin.handoff_report.adapters import RuntimeHandoffReadAdapter
from powercontext.builtin.handoff_report.application import HandoffReportApplication
from powercontext.builtin.inference import EmbeddingModel, TokenEstimator, character_token_estimator
from powercontext.builtin.inference.usage import (
    UsageReportingEmbeddingModel,
    UsageReportingStructuredGenerator,
)
from powercontext.builtin.persistence.dream_schema import ensure_dream_schema
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, MemoryIndex
from powercontext.builtin.persistence.oceanbase.experience_index import OceanBaseExperienceFTSIndex
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.topic_memory_index import (
    OceanBaseTopicMemoryFTSIndex,
    OceanBaseTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.processing_migration import (
    assert_processing_schema_ready,
    bootstrap_processing_schema,
)
from powercontext.builtin.persistence.scope_search_schema import ensure_scope_search_schema
from powercontext.builtin.persistence.seekdb.profile import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.skill_distribution_schema import ensure_skill_distribution_schema
from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
from powercontext.builtin.persistence.sqlite.memory_index import SQLiteMemoryFTSIndex, SQLiteMemoryVectorIndex
from powercontext.builtin.persistence.sqlite.profile import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import (
    SQLiteTopicMemoryFTSIndex,
    SQLiteTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import (
    CompositeTopicMemoryIndex,
    TopicMemoryIndex,
)
from powercontext.builtin.runtime._scope_cache import ScopeCacheObserver
from powercontext.builtin.runtime.application import BuiltinRuntime, ScheduledExperienceRunner, ScheduledSourceRunner
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.config import BuiltinConfig, ExternalSkillsConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS, FamilyWorkerSpec, run_family_worker
from powercontext.builtin.runtime.models import MemorySearchMode, RuntimeCapabilities
from powercontext.builtin.runtime.processing_discovery import SourceProcessingPendingProvider, enabled_profile_scopes
from powercontext.builtin.runtime.processing_registry import (
    canonical_processing_manifest,
    dream_operations,
    processing_capabilities,
)
from powercontext.builtin.runtime.protocols import RuntimeTracing
from powercontext.builtin.runtime.readiness import (
    CachedReadinessProbe,
    ReadinessProbe,
    ReadinessProbeDefinition,
    RuntimeReadinessChecks,
    dependency_readiness_probe,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.runtime.topic_memory_processing import (
    TopicMemoryWorkerSpec,
    run_topic_memory_worker,
    validate_topic_memory_provider_settings,
)
from powercontext.builtin.sources import (
    BUILTIN_SOURCE_REGISTRY,
    TEXT_EVIDENCE_PROJECTION_KEY,
)
from powercontext.errors import InvalidSourceProjectionError, SourceProjectionNotFoundError
from powercontext.sources import Source, SourceDefinitionRegistry, SourceProjectionKey

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.models.instrumented import InstrumentationSettings
    from pydantic_ai.providers import Provider
    from pydantic_ai.settings import ModelSettings

    from powercontext.builtin.inference.pydantic_ai import InferenceLimits

ValueT = TypeVar("ValueT")
logger = logging.getLogger(__name__)


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
            "scheduled-profile-generation": "scheduled Profile processing requires reconstructible generation",
            "topic-memory-child-resources": (
                "Topic Memory processing requires child-reconstructible inference resources"
            ),
            "topic-memory-generation-budget": "Topic Memory generation budget is not executable",
            "topic-memory-provider-budget": "Topic Memory workers require OpenAI/Anthropic SDK providers with transport retries disabled and bounded stateless model settings",
            "topic-memory-generation": "Topic Memory processing requires a configured generation model",
            "topic-memory-database": "Topic Memory workers require file-backed SQLite; an in-memory database cannot be shared with spawned workers",
            "artifact-processing-worker-authorization-provider": (
                "Background processing requires a reconstructible Authorization Provider with transactional ownership and audit"
            ),
            "artifact-processing-child-resources": "Background processing requires child-reconstructible inference resources",
            "artifact-processing-source-registry": (
                "Built-in background workers require the built-in Source definitions; "
                "provide custom processing bindings or disable built-in background families for a custom Source registry"
            ),
            "artifact-processing-families": "Declared background families must have one matching registration and reconstructible models",
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
    profile_generator: ProfileGenerator | None = None,
    skill_generator: SkillGenerator | None = None,
    dream_generator: DreamGenerator | None = None,
    dream_authorizer: DreamAuthorizer | None = None,
    dream_authorization_context: AuthorizationContext = nullcontext,
    dream_candidate_attester: CandidateAttester | None = None,
    external_skill_provider: ExternalSkillProvider | None = None,
    handoff_pipeline: HandoffGenerationPipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
    token_estimator: TokenEstimator | None = None,
    memory_reranker: MemoryReranker | None = None,
    instrumentation: InstrumentationSettings | None = None,
    scope_cache_observer: ScopeCacheObserver | None = None,
    topic_memory_search_observer: Callable[[str, bool], None] | None = None,
    tracing: RuntimeTracing | None = None,
    artifact_processing_bindings: Sequence[ArtifactProcessingBinding] = (),
    worker_security: dict[str, Any] | None = None,
    scheduled_source_runner: ScheduledSourceRunner | None = None,
    scheduled_experience_runner: ScheduledExperienceRunner | None = None,
    scheduled_profile_runner: Any = None,
    source_registry: SourceDefinitionRegistry | None = None,
    cursor_secret: bytes | None = None,
    handoff_verification_keys: tuple[bytes, ...] = (),
) -> AsyncIterator[BuiltinRuntime]:
    """Open the selected database, inference adapters, and built-in runtime."""

    async with AsyncExitStack() as resources:
        configured_source_registry = source_registry or BUILTIN_SOURCE_REGISTRY
        _validate_processing_source_registry(config, artifact_processing_bindings, configured_source_registry)
        prompt_demonstrators: dict[str, DemonstrationGenerator] = {}
        (
            generated_profile,
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
                prompt_demonstrators=prompt_demonstrators,
            )
            if (
                profile_generator is None
                or candidate_pipeline is None
                or experience_pipeline is None
                or experience_generator is None
                or skill_generator is None
                or handoff_pipeline is None
                or (config.runtime.memory_rerank_enabled and memory_reranker is None)
            )
            else (None, None, None, None, None, None, None, None, None)
        )
        configured_pipeline = generated_memory if candidate_pipeline is None else candidate_pipeline
        configured_incubation = generated_incubation if experience_pipeline is None else experience_pipeline
        configured_experience = generated_experience if experience_generator is None else experience_generator
        configured_skill = generated_skill if skill_generator is None else skill_generator
        configured_handoff = generated_handoff if handoff_pipeline is None else handoff_pipeline
        configured_reranker = generated_reranker if memory_reranker is None else memory_reranker
        components = (
            ("profile.generate", profile_generator, generated_profile),
            ("memory.extract", candidate_pipeline, generated_memory),
            ("memory.rerank", memory_reranker, generated_reranker),
            ("experience.incubate", experience_pipeline, generated_incubation),
            ("experience.generate", experience_generator, generated_experience),
            ("skill.generate", skill_generator, generated_skill),
            ("handoff.generate", handoff_pipeline, generated_handoff),
        )
        prompt_registry = _prompt_registry(config.runtime, components)
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
        configured_embedding = _usage_reporting_embedding_model(configured_embedding_source)
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
                cursor_secret=cursor_secret,
                prompt_registry=prompt_registry,
                prompt_demonstrators=prompt_demonstrators,
                handoff_verification_keys=handoff_verification_keys,
            )
        )
        contexts.profiles.generator = generated_profile if profile_generator is None else profile_generator
        contexts.profiles.max_sources = config.runtime.profile_max_sources_per_window
        if scheduled_profile_runner is not None:

            async def run_profile(scope_id, high):
                return await scheduled_profile_runner(scope_id, high, contexts.profiles)

            contexts.profiles.scheduled_runner = run_profile
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
        processing_bindings = _artifact_processing_bindings(
            config,
            contexts,
            artifact_processing_bindings,
            injected_embedding_model=embedding_model,
            injected_token_estimator=token_estimator,
            injected_pipelines={
                "memory": candidate_pipeline,
                "experience": experience_pipeline if experience_pipeline is not None else dream_generator,
                "profile": profile_generator,
                "skill": dream_generator,
            },
            worker_security=worker_security,
            source_registry=configured_source_registry,
        )
        log_safely(
            logger,
            logging.INFO,
            "Artifact processing configuration",
            extra={
                "event": "artifact_processing.configured",
                "mode": config.runtime.artifact_processing_supervisor_mode,
                "role": config.runtime.artifact_processing_role,
                "compatibility_aliases": config.runtime._processing_aliases_used,
                "families": [
                    {
                        "binding": b.binding_name,
                        "family": b.artifact_family,
                        "workers": b.max_workers,
                        "timeout_seconds": b.worker_timeout_seconds,
                        "interval_seconds": None
                        if b.automatic_processing_interval is None
                        else b.automatic_processing_interval.total_seconds(),
                        "cron": b.cron,
                        "timezone": b.timezone,
                    }
                    for b in processing_bindings
                ],
            },
        )
        configured_operations = dream_operations(config)
        if dream_generator is not None and config.runtime.dream_enabled:
            registered_families = {binding.artifact_family for binding in processing_bindings}
            configured_operations = tuple(
                operation
                for operation, family in (("refine_experience", "experience"), ("derive_skill", "skill"))
                if family in registered_families
            )
        topic_memory_processing_available = _topic_memory_processing_available(config, processing_bindings)
        runtime = await resources.enter_async_context(
            BuiltinRuntime(
                provider=contexts,
                capabilities=RuntimeCapabilities(
                    memory_extraction=contexts.memory_extraction,
                    experience_generation=contexts.experience_generation,
                    managed_skill_generation=contexts.managed_skill_generation,
                    artifact_dreaming=bool(configured_operations),
                    external_skill_registry=contexts.external_skill_registry,
                    memory_search_modes=_search_modes(contexts.index.capabilities),
                    handoff_generation=contexts.handoff_generation,
                    prompts=dict(contexts.prompt_registry.capabilities),
                ),
                source_window_limit=config.runtime.source_window_limit,
                context_assembly_max_entries=config.runtime.context_assembly_max_entries,
                scope_cache_size=config.runtime.scope_cache_size,
                scope_evictor=contexts.evict,
                scope_cache_observer=scope_cache_observer,
                scope_ids=contexts.scope_ids,
                review_service=contexts.review,
                profiles=contexts.profiles,
                subject_sources=contexts.subject_sources,
                generation_service=contexts.generation,
                dream_service=contexts.dream(
                    dream_generator,
                    operations=configured_operations,
                    budget=config.runtime.dream_budget,
                    max_pending_per_scope=config.runtime.dream_max_pending_per_scope,
                    authorize=dream_authorizer,
                    authorization_context=dream_authorization_context,
                    attest_candidate=dream_candidate_attester,
                ),
                generation_concurrency=config.runtime.generation_concurrency,
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
                topic_memory_search=contexts.search_topic_memories,
                topic_memory_get=contexts.get_topic_memory,
                topic_memory_browse=contexts.browse_topic_memories,
                topic_memory_flush=contexts.request_topic_memory_flush,
                topic_memory_embedding_model=configured_embedding,
                topic_memory_processing_available=topic_memory_processing_available,
                topic_memory_search_observer=topic_memory_search_observer,
                external_skill_registry=contexts.external_skills if contexts.external_skill_registry else None,
                external_skill_importer=contexts.import_external_skill if contexts.external_skill_registry else None,
                skill_publication_service=contexts.skill_publications,
                remote_skill_distribution=contexts.remote_skill_distribution(),
                statistics_service=contexts.statistics,
                record_service=contexts.records,
                prompt_service=contexts.prompts,
                recall_token_estimator=contexts.estimate_recall_tokens,
                publication_application=contexts.publications,
                scope_application=contexts.scopes,
                readiness=RuntimeReadinessChecks(readiness_probes),
                tracing=tracing,
                scheduled_source_runner=scheduled_source_runner,
                scheduled_experience_runner=scheduled_experience_runner,
                remote_ingestion=contexts,
            )
        )
        runtime.artifact_processing_supervisor = await resources.enter_async_context(
            _open_artifact_processing_supervisor(config, contexts, processing_bindings, tracing=tracing)
        )
        contexts.profiles.operation_context = runtime._operation
        if config.handoff_report.enabled:
            runtime.handoff_report = HandoffReportApplication(
                contexts.scopes,
                RuntimeHandoffReadAdapter(runtime.handoff),
            )
        if config.runtime.memory_rerank_enabled and configured_reranker is None:
            raise BuiltinConfigurationError("memory-reranker")
        yield runtime


def _validate_processing_source_registry(
    config: BuiltinConfig,
    bindings: Sequence[ArtifactProcessingBinding],
    source_registry: SourceDefinitionRegistry,
) -> None:
    """Reject unsupported child Sources before database bootstrap commits deployment identity."""

    if config.runtime.artifact_processing_role == "api":
        return
    registered = {binding.artifact_family for binding in bindings}
    if all(family in registered for family in processing_capabilities(config)):
        return
    definitions = source_registry.definitions
    builtin_definitions = BUILTIN_SOURCE_REGISTRY.definitions
    if (
        type(source_registry) is not SourceDefinitionRegistry
        or len(definitions) != len(builtin_definitions)
        or any(
            definition is not expected for definition, expected in zip(definitions, builtin_definitions, strict=True)
        )
    ):
        raise BuiltinConfigurationError("artifact-processing-source-registry")


def _artifact_processing_bindings(  # noqa: C901 - validate and assemble one registration per Family
    config: BuiltinConfig,
    contexts: RelationalContexts,
    bindings: Sequence[ArtifactProcessingBinding],
    *,
    injected_embedding_model: EmbeddingModel | None = None,
    injected_token_estimator: TokenEstimator | None = None,
    injected_pipelines: Mapping[str, object | None] | None = None,
    worker_security: dict[str, Any] | None = None,
    source_registry: SourceDefinitionRegistry = BUILTIN_SOURCE_REGISTRY,
) -> tuple[ArtifactProcessingBinding, ...]:
    configured = list(bindings)
    _validate_processing_registrations(configured)
    capabilities = processing_capabilities(config)
    canonical = {**FAMILY_BINDINGS, "topic-memory": TOPIC_MEMORY_SOURCE_WINDOW_BINDING}
    registered = {binding.artifact_family for binding in configured}
    declared = set(capabilities)
    if declared - (set(canonical) | registered):
        raise BuiltinConfigurationError("artifact-processing-families")
    automatic = {
        "memory": config.runtime.memory_schedule_seconds,
        "topic-memory": config.runtime.topic_memory_schedule_seconds,
        "experience": config.runtime.experience_schedule_seconds,
        "profile": config.runtime.profile_schedule_enabled or None,
        "skill": None,
    }
    for family, schedule in automatic.items():
        if schedule is not None and family not in declared | registered:
            issue = {
                "memory": "scheduled-pipeline",
                "experience": "scheduled-experience-pipeline",
                "topic-memory": "topic-memory-generation",
                "profile": "scheduled-profile-generation",
            }[family]
            raise BuiltinConfigurationError(issue)
    if config.runtime.artifact_processing_role == "api":
        return tuple(configured)
    _validate_processing_source_registry(config, configured, source_registry)
    injected_pipelines = injected_pipelines or {}
    for family in capabilities:
        if family in registered:
            continue
        if config.inference.generation_model is None:
            raise BuiltinConfigurationError("artifact-processing-families")
        if (
            injected_embedding_model is not None
            or injected_token_estimator is not None
            or injected_pipelines.get(family) is not None
        ):
            raise BuiltinConfigurationError("artifact-processing-child-resources")
        if isinstance(config.database, SQLiteConfig) and config.database.is_in_memory:
            raise BuiltinConfigurationError("topic-memory-database")
        prefix = family.replace("-", "_")
        if family == "topic-memory":
            validate_topic_memory_provider_settings(config.inference)
            try:
                budget = topic_memory_stage_budget(
                    context_window_tokens=config.inference.generation_model_context_window_tokens,
                    max_requests=config.inference.generation_max_requests,
                    model_settings=config.inference.generation_model_settings,
                )
                validate_topic_memory_stage_capacity(budget, contexts.token_estimator)
            except TopicMemoryGenerationError as error:
                raise BuiltinConfigurationError("topic-memory-generation-budget") from error
            entrypoint = partial(
                run_topic_memory_worker, TopicMemoryWorkerSpec(config=config, worker_security=worker_security)
            )
        else:
            entrypoint = partial(run_family_worker, FamilyWorkerSpec(config=config, worker_security=worker_security))
        interval = None if family == "profile" else automatic[family]
        binding = canonical[family]
        configured.append(
            ArtifactProcessingBinding(
                binding_name=binding,
                artifact_family=family,
                launcher=SpawnArtifactProcessingWorkerLauncher(entrypoint),
                max_workers=getattr(config.runtime, f"{prefix}_max_workers"),
                worker_timeout_seconds=getattr(config.runtime, f"{prefix}_worker_timeout_seconds"),
                automatic_processing_interval=None if interval is None else timedelta(seconds=float(interval)),
                cron=config.runtime.profile_cron
                if family == "profile" and config.runtime.profile_schedule_enabled
                else None,
                timezone=config.runtime.profile_timezone if family == "profile" else "Asia/Shanghai",
                pending_provider=None
                if family == "skill"
                else SourceProcessingPendingProvider(contexts.database, binding, family),
                automatic_scope_filter=enabled_profile_scopes if family == "profile" else None,
            )
        )
    _validate_processing_registrations(configured)
    return tuple(configured)


def _validate_processing_registrations(configured: Sequence[ArtifactProcessingBinding]) -> None:
    for attribute in ("binding_name", "artifact_family", "config_prefix"):
        identifiers = [getattr(binding, attribute) for binding in configured]
        if len(identifiers) != len(set(identifiers)):
            raise BuiltinConfigurationError("artifact-processing-families")


def _topic_memory_processing_available(
    config: BuiltinConfig,
    bindings: Sequence[ArtifactProcessingBinding],
) -> bool:
    """Report declared cross-role processing ability without probing worker liveness."""

    return any(binding.binding_name == TOPIC_MEMORY_SOURCE_WINDOW_BINDING for binding in bindings) or (
        config.runtime.artifact_processing_role == "api" and "topic-memory" in processing_capabilities(config)
    )


def _topic_memory_provider_available(config: BuiltinConfig) -> bool:
    try:
        validate_topic_memory_provider_settings(config.inference)
    except BuiltinConfigurationError:
        return False
    return True


@asynccontextmanager
async def _open_artifact_processing_supervisor(
    config: BuiltinConfig,
    contexts: RelationalContexts,
    bindings: Sequence[ArtifactProcessingBinding],
    *,
    tracing: RuntimeTracing | None = None,
) -> AsyncIterator[ArtifactProcessingSupervisors | None]:
    if config.runtime.artifact_processing_role == "api" or not bindings:
        yield None
        return
    groups = (
        [("global", tuple(bindings))]
        if config.runtime.artifact_processing_supervisor_mode == "global"
        else [(f"artifact:{binding.artifact_family}", (binding,)) for binding in bindings]
    )
    async with AsyncExitStack() as resources:
        supervisors = []
        for name, group in groups:
            supervisor = await resources.enter_async_context(
                ArtifactProcessingSupervisor(
                    database=contexts.database,
                    bindings=group,
                    supervisor_group=name,
                    lease_mode="oceanbase" if isinstance(config.database, OceanBaseConfig) else "single-process",
                    leases=contexts.repositories.processing_leases,
                    binding_states=contexts.repositories.processing_binding_states,
                    tracing=tracing,
                )
            )
            supervisors.append(supervisor)
        yield ArtifactProcessingSupervisors(tuple(supervisors))


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
    cursor_secret: bytes | None = None,
    prompt_registry: PromptRegistry | None = None,
    prompt_demonstrators: dict[str, DemonstrationGenerator] | None = None,
    handoff_verification_keys: tuple[bytes, ...] = (),
    _topic_memory_worker: bool = False,
) -> AsyncIterator[RelationalContexts]:
    """Open the selected database and expose scope-bound PowerContext providers."""

    database = config.database
    configured_token_estimator = character_token_estimator() if token_estimator is None else token_estimator
    if isinstance(database, SQLiteConfig):
        experience_index = SQLiteExperienceFTSIndex()
        indexes: list[MemoryIndex] = [SQLiteMemoryFTSIndex()]
        if embedding_model is not None:
            indexes.append(SQLiteMemoryVectorIndex(embedding_model.profile))
        index = CompositeMemoryIndex(*indexes)
        topic_indexes: list[TopicMemoryIndex] = [SQLiteTopicMemoryFTSIndex()]
        if embedding_model is not None:
            topic_indexes.append(SQLiteTopicMemoryVectorIndex(embedding_model.profile))
        topic_index = CompositeTopicMemoryIndex(*topic_indexes)
        async with SQLiteProfile.open(
            database,
            tables=BUILTIN_TABLES + index.tables + topic_index.tables,
            load_vector_extension=embedding_model is not None,
        ) as profile:
            async with profile.database.transaction() as connection:
                await bootstrap_processing_schema(connection, canonical_processing_manifest(config))
                await assert_processing_schema_ready(connection, canonical_processing_manifest(config))
                await ensure_skill_distribution_schema(connection)
                await ensure_dream_schema(connection)
                await ensure_scope_search_schema(connection)
                # A Topic child reuses its parent's schema. It never reads or
                # writes Memory/Experience projections; rebuilding their FTS
                # indexes here would take the shared SQLite write lock once
                # per Window. Normal runtime startup retains index recovery.
                if not _topic_memory_worker:
                    await index.initialize(connection)
                    await experience_index.initialize(connection)
                await TopicMemoryRepository(index=topic_index).initialize(
                    connection, configure_retrieval_shape=not _topic_memory_worker
                )
            contexts = RelationalContexts(
                database=profile.database,
                index=index,
                topic_memory_index=topic_index,
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
                prompt_registry=prompt_registry,
                prompt_demonstrators=prompt_demonstrators,
                handoff_verification_keys=handoff_verification_keys,
                source_registry=source_registry,
                cursor_secret=cursor_secret,
            )
            await contexts.scopes.bootstrap_default()
            yield contexts
        return
    experience_index = OceanBaseExperienceFTSIndex()
    indexes = [OceanBaseMemoryFTSIndex()]
    if embedding_model is not None:
        indexes.append(OceanBaseMemoryVectorIndex(embedding_model.profile))
    index = CompositeMemoryIndex(*indexes)
    topic_indexes: list[TopicMemoryIndex] = [OceanBaseTopicMemoryFTSIndex()]
    if embedding_model is not None:
        topic_indexes.append(OceanBaseTopicMemoryVectorIndex(embedding_model.profile))
    topic_index = CompositeTopicMemoryIndex(*topic_indexes)
    tables = BUILTIN_TABLES + index.tables + topic_index.tables
    if isinstance(database, OceanBaseConfig):
        profile_context = OceanBaseProfile.open(database, tables=tables)
    elif isinstance(database, SeekDBConfig):
        profile_context = SeekDBProfile.open(database, tables=tables)
    else:
        raise BuiltinConfigurationError("database")
    async with profile_context as profile:
        async with profile.database.transaction() as connection:
            await bootstrap_processing_schema(connection, canonical_processing_manifest(config))
            await assert_processing_schema_ready(connection, canonical_processing_manifest(config))
            await ensure_skill_distribution_schema(connection)
            await ensure_dream_schema(connection)
            await ensure_scope_search_schema(connection)
            if not _topic_memory_worker:
                await index.initialize(connection)
                await experience_index.initialize(connection)
            await TopicMemoryRepository(index=topic_index).initialize(
                connection, configure_retrieval_shape=not _topic_memory_worker
            )
        contexts = RelationalContexts(
            database=profile.database,
            index=index,
            topic_memory_index=topic_index,
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
            prompt_registry=prompt_registry,
            prompt_demonstrators=prompt_demonstrators,
            handoff_verification_keys=handoff_verification_keys,
            source_registry=source_registry,
            cursor_secret=cursor_secret,
        )
        await contexts.scopes.bootstrap_default()
        yield contexts


def _register_prompt_demonstrators(
    target: dict[str, DemonstrationGenerator] | None,
    keys: tuple[str, ...],
    model: Model,
    limits: InferenceLimits,
    settings: ModelSettings | None,
) -> None:
    if target is None:
        return
    from powercontext.builtin.inference.prompt_demonstrations import PromptDemonstrationGenerator

    generator = PromptDemonstrationGenerator(model, limits=limits, model_settings=settings)
    target.update(dict.fromkeys(keys, generator))


async def _dream_generator(
    settings: InferenceConfig,
    budget: DreamBudget,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
) -> DreamGenerator | None:
    if settings.generation_model is None:
        return None
    if settings.generation_model.split(":", 1)[0] not in {
        "openai",
        "openai-chat",
        "openai-responses",
        "anthropic",
    }:
        # Dream requires an adapter whose SDK retries can be disabled explicitly.
        # Other inference workloads retain their existing provider support.
        return None
    from pydantic_ai.settings import ModelSettings

    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator

    provider_model, model = await _open_pydantic_ai_model(
        settings.generation_model,
        base_url=settings.generation_base_url,
        headers=settings.generation_headers,
        resources=resources,
        instrumentation=instrumentation,
        disable_provider_retries=True,
    )
    model_settings = cast(ModelSettings, dict(settings.generation_model_settings))
    model_settings["max_tokens"] = min(int(model_settings.get("max_tokens") or 4096), budget.max_output_tokens)
    identity = settings.model_dump_json(
        include={
            "generation_model",
            "generation_base_url",
            "generation_model_settings",
            "generation_timeout_seconds",
        }
    )
    identity = json.dumps(
        [identity, getattr(provider_model, "base_url", None), model_settings],
        sort_keys=True,
        default=str,
    )
    generator = PydanticAIStructuredGenerator(
        model=model,
        instructions=DREAM_INSTRUCTIONS,
        input_type=DreamGenerationInput,
        output_type=DreamPlan,
        limits=InferenceLimits(
            timeout_seconds=min(settings.generation_timeout_seconds, budget.timeout_seconds), max_requests=1
        ),
        model_settings=model_settings,
        name="artifact_dreaming",
    )
    return LLMDreamGenerator(
        UsageReportingStructuredGenerator(generator),
        config_id=content_digest(identity.encode()),
    )


async def _generation_pipelines(
    settings: InferenceConfig,
    runtime: RuntimeConfig,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
    source_registry: SourceDefinitionRegistry,
    *,
    prompt_demonstrators: dict[str, DemonstrationGenerator] | None = None,
) -> tuple[
    ProfileGenerator | None,
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
        return None, None, None, None, None, None, None, None, None

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

    generated_profile: ProfileGenerator | None = None
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
        _register_prompt_demonstrators(
            prompt_demonstrators,
            (
                "profile.generate",
                "memory.extract",
                "experience.incubate",
                "experience.generate",
                "skill.generate",
                "handoff.generate",
            ),
            generation_model,
            generation_limits,
            generation_request_settings,
        )
        generated_profile = LLMProfileGenerator(
            UsageReportingStructuredGenerator(
                PydanticAIStructuredGenerator(
                    model=generation_model,
                    instructions=PROFILE_INSTRUCTIONS,
                    input_type=ProfileGenerationInput,
                    output_type=ProfileGenerationOutput,
                    limits=generation_limits,
                    model_settings=generation_request_settings,
                    name="profile_generation",
                    prompt_key="profile.generate",
                )
            )
        )
        memory_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=memory_extraction_instructions(runtime.memory_extraction_profile),
            input_type=MemoryExtractionInput,
            output_type=MemoryExtractionOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="memory_extraction",
            prompt_key="memory.extract",
        )
        experience_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=EXPERIENCE_INCUBATION_INSTRUCTIONS,
            input_type=ExperienceIncubationInput,
            output_type=ExperienceIncubationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="experience_incubation",
            prompt_key="experience.incubate",
        )
        explicit_experience_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=EXPERIENCE_GENERATION_INSTRUCTIONS,
            input_type=ArtifactGenerationInput,
            output_type=ExperienceGenerationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="experience_generation",
            prompt_key="experience.generate",
        )
        skill_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=SKILL_GENERATION_INSTRUCTIONS,
            input_type=ArtifactGenerationInput,
            output_type=SkillGenerationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="skill_generation",
            prompt_key="skill.generate",
        )
        handoff_generator = PydanticAIStructuredGenerator(
            model=generation_model,
            instructions=HANDOFF_GENERATION_INSTRUCTIONS,
            input_type=HandoffGenerationInput,
            output_type=HandoffGenerationOutput,
            limits=generation_limits,
            model_settings=generation_request_settings,
            name="handoff_generation",
            prompt_key="handoff.generate",
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
                prompt_key="memory.rerank",
            )
            generated_reranker = LLMMemoryReranker(UsageReportingStructuredGenerator(rerank_generator))
            _register_prompt_demonstrators(
                prompt_demonstrators,
                ("memory.rerank",),
                rerank_model,
                InferenceLimits(
                    timeout_seconds=settings.rerank_timeout_seconds or settings.generation_timeout_seconds,
                    max_requests=settings.rerank_max_requests or settings.generation_max_requests,
                ),
                rerank_request_settings,
            )

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
        generated_profile,
        generated_memory,
        generated_incubation,
        generated_experience,
        generated_skill,
        generated_handoff,
        generated_reranker,
        generation_readiness,
        rerank_readiness,
    )


async def preflight_builtin_runtime(config: BuiltinConfig) -> None:
    """Validate Runtime composition without opening persistence or making requests."""

    async with AsyncExitStack() as resources:
        await _generation_pipelines(
            config.inference,
            config.runtime,
            resources,
            None,
            BUILTIN_SOURCE_REGISTRY,
        )
        if config.inference.embedding_model is not None:
            await _embedding_models(config.inference, resources, None)
        if config.runtime.schedule_seconds is not None and config.inference.generation_model is None:
            raise BuiltinConfigurationError("scheduled-pipeline")
        if config.runtime.experience_schedule_seconds is not None and config.inference.generation_model is None:
            raise BuiltinConfigurationError("scheduled-experience-pipeline")
        if config.runtime.memory_rerank_enabled and (
            config.inference.generation_model is None and config.inference.rerank_model is None
        ):
            raise BuiltinConfigurationError("memory-reranker")


async def _open_pydantic_ai_model(
    model_name: str,
    *,
    base_url: AnyHttpUrl | None,
    headers: Mapping[str, SecretStr],
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
    disable_provider_retries: bool = False,
) -> tuple[Model, Model]:
    from pydantic_ai.models import infer_model
    from pydantic_ai.models.instrumented import InstrumentedModel

    if (base_url is not None or headers) and ":" not in model_name:
        raise BuiltinConfigurationError("inference-endpoint-provider")
    inferred_model = (
        infer_model(model_name)
        if base_url is None and not headers and not disable_provider_retries
        else infer_model(
            model_name,
            provider_factory=_provider_factory(
                base_url,
                headers,
                workload="generation",
                resources=resources,
                disable_retries=disable_provider_retries,
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
    disable_retries: bool = False,
) -> Callable[[str], Provider[Any]]:
    create = _unbounded_provider_factory(base_url, headers, workload=workload, resources=resources)
    if not disable_retries:
        return create

    def bounded(provider_name: str) -> Provider[Any]:
        from anthropic import AsyncAnthropic
        from openai import AsyncOpenAI

        provider = create(provider_name)
        client = provider.client
        if not isinstance(client, (AsyncOpenAI, AsyncAnthropic)):
            # No unmetered provider-specific retry policy in a Topic Worker.
            raise BuiltinConfigurationError("topic-memory-provider-budget")
        client.max_retries = 0
        return provider

    return bounded


def _unbounded_provider_factory(
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


def _prompt_registry(
    runtime: RuntimeConfig,
    components: tuple[tuple[str, object | None, object | None], ...],
) -> PromptRegistry:
    """Infer Prompt support from executable generated components in either process."""

    injected = frozenset(key for key, supplied, _ in components if supplied is not None)
    return PromptRegistry(
        builtin_prompt_definitions(runtime.memory_extraction_profile),
        supported=frozenset(
            key for key, supplied, generated in components if supplied is None and generated is not None
        ),
        injected=injected,
        disabled=frozenset({"memory.rerank"})
        if not runtime.memory_rerank_enabled and "memory.rerank" not in injected
        else frozenset(),
    )


def _usage_reporting_embedding_model(model: EmbeddingModel | None) -> EmbeddingModel | None:
    """Attribute operational embeddings once, leaving readiness adapters unwrapped."""

    if model is None or isinstance(model, UsageReportingEmbeddingModel):
        return model
    return UsageReportingEmbeddingModel(model)


async def _embedding_models(
    settings: InferenceConfig,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None,
    *,
    disable_provider_retries: bool = False,
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
        disable_retries=disable_provider_retries,
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


__all__ = ["BuiltinConfigurationError", "open_builtin_contexts", "open_builtin_runtime", "preflight_builtin_runtime"]
