"""Composition and lifecycle for one configured built-in runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TypeVar

from pydantic import JsonValue

from powercontext.builtin.artifacts.memory import (
    CandidatePipeline,
    DefaultMemoryEvidenceProjector,
    MemoryCapabilities,
)
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, MemoryIndex
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite.memory_index import SQLiteMemoryFTSIndex, SQLiteMemoryVec1Index
from powercontext.builtin.persistence.sqlite.profile import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig
from powercontext.builtin.runtime.models import MemorySearchMode, RuntimeCapabilities
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import CONTENT_SOURCE_NAME, ContentSource
from powercontext.sources import Source

ValueT = TypeVar("ValueT")


class BuiltinConfigurationError(RuntimeError):
    """Report a configuration that cannot assemble the built-in runtime."""

    def __init__(self, issue: str) -> None:
        messages = {
            "inference-profile": "validated inference profile is incomplete",
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


@asynccontextmanager
async def open_builtin_runtime(
    config: BuiltinConfig,
    *,
    candidate_pipeline: CandidatePipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
) -> AsyncIterator[BuiltinRuntime]:
    """Open the selected database, inference adapters, and built-in runtime."""

    async with AsyncExitStack() as resources:
        configured_pipeline = (
            await _candidate_pipeline(config.inference, resources) if candidate_pipeline is None else candidate_pipeline
        )
        configured_embedding = (
            await _embedding_model(config.inference, resources) if embedding_model is None else embedding_model
        )
        contexts = await resources.enter_async_context(
            open_builtin_contexts(
                config,
                candidate_pipeline=configured_pipeline,
                embedding_model=configured_embedding,
            )
        )
        runtime = await resources.enter_async_context(
            BuiltinRuntime(
                provider=contexts,
                capabilities=RuntimeCapabilities(
                    memory_extraction=contexts.memory_extraction,
                    memory_search_modes=_search_modes(contexts.index.capabilities),
                ),
                source_window_limit=config.runtime.source_window_limit,
                scope_ids=contexts.scope_ids,
                review_service=contexts.review,
            )
        )
        if config.runtime.schedule_seconds is not None:
            if configured_pipeline is None:
                raise BuiltinConfigurationError("scheduled-pipeline")
            runtime.start_scheduler(
                config.runtime.scheduler_path,
                config.runtime.schedule_seconds,
            )
        yield runtime


@asynccontextmanager
async def open_builtin_contexts(
    config: BuiltinConfig,
    *,
    candidate_pipeline: CandidatePipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
) -> AsyncIterator[RelationalContexts]:
    """Open the selected database and expose scope-bound PowerContext providers."""

    database = config.database
    if isinstance(database, SQLiteConfig):
        indexes: list[MemoryIndex] = [SQLiteMemoryFTSIndex()]
        if database.vec1_extension is not None:
            if embedding_model is None:
                raise ValueError("SQLite Vec1 requires an embedding model")  # noqa: TRY003
            indexes.append(SQLiteMemoryVec1Index(database.vec1_extension, embedding_model.profile))
        index = CompositeMemoryIndex(*indexes)
        async with SQLiteProfile.open(database, tables=BUILTIN_TABLES + index.tables) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
            yield RelationalContexts(
                database=profile.database,
                index=index,
                candidate_pipeline=candidate_pipeline,
                embedding_model=embedding_model,
            )
        return
    if not isinstance(database, OceanBaseConfig):
        raise BuiltinConfigurationError("database")

    indexes = [OceanBaseMemoryFTSIndex()]
    if embedding_model is not None:
        indexes.append(OceanBaseMemoryVectorIndex(embedding_model.profile))
    index = CompositeMemoryIndex(*indexes)
    async with OceanBaseProfile.open(database, tables=BUILTIN_TABLES + index.tables) as profile:
        async with profile.database.transaction() as connection:
            await index.initialize(connection)
        yield RelationalContexts(
            database=profile.database,
            index=index,
            candidate_pipeline=candidate_pipeline,
            embedding_model=embedding_model,
        )


async def _candidate_pipeline(settings: InferenceConfig, resources: AsyncExitStack) -> CandidatePipeline | None:
    if settings.generation_model is None:
        return None

    from pydantic_ai.models import infer_model

    from powercontext.builtin.artifacts.memory import (
        MEMORY_EXTRACTION_INSTRUCTIONS,
        LLMMemoryCandidatePipeline,
        MemoryExtractionInput,
        MemoryExtractionOutput,
    )
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator

    generator = PydanticAIStructuredGenerator(
        model=await resources.enter_async_context(infer_model(settings.generation_model)),
        instructions=MEMORY_EXTRACTION_INSTRUCTIONS,
        input_type=MemoryExtractionInput,
        output_type=MemoryExtractionOutput,
        limits=InferenceLimits(
            timeout_seconds=settings.generation_timeout_seconds,
            max_requests=settings.generation_max_requests,
        ),
    )
    return LLMMemoryCandidatePipeline(generator, evidence_projector=_ContentEvidenceProjector())


async def _embedding_model(settings: InferenceConfig, resources: AsyncExitStack) -> EmbeddingModel | None:
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
        embedder=Embedder(model),
        profile=EmbeddingProfile(
            profile_id=_required(settings.embedding_profile_id),
            model=settings.embedding_model,
            dimension=_required(settings.embedding_dimension),
            distance="l2",
            normalization=settings.embedding_normalization,
        ),
        limits=InferenceLimits(timeout_seconds=settings.embedding_timeout_seconds),
    )


def _required(value: ValueT | None) -> ValueT:
    if value is None:
        raise BuiltinConfigurationError("inference-profile")
    return value


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
