"""Runtime-backed Server composition."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from pydantic import JsonValue

from powercontext.api import Capabilities
from powercontext.inference import EmbeddingModel
from powercontext.mcp.server import mount_mcp
from powercontext.memory import (
    MEMORY_EXTRACTION_INSTRUCTIONS,
    CandidatePipeline,
    DefaultMemoryEvidenceProjector,
    EmbeddingProfile,
    LLMMemoryCandidatePipeline,
    MemoryExtractionInput,
    MemoryExtractionOutput,
)
from powercontext.runtime import PowerContextRuntime, RuntimeCapabilities
from powercontext.server.app import create_app
from powercontext.server.settings import InferenceSettings, ServerSettings
from powercontext.sources import CONTENT_SOURCE_NAME, ContentSource, Source


class ServerRuntimeConfigurationError(RuntimeError):
    """Raised when Server settings cannot assemble the configured Runtime."""

    def __init__(self, code: str) -> None:
        messages = {
            "scheduled-extraction": (
                "scheduled Source processing requires a configured generation model or candidate pipeline"
            ),
            "generation-model": "generation_model is required",
            "embedding-model": "embedding_model is required",
            "embedding-profile": "embedding model profile does not match Server settings",
            "sqlite-vector": "SQLite vector search requires both embedding settings and a Vec1 extension",
        }
        super().__init__(messages[code])


class _ContentEvidenceProjector(DefaultMemoryEvidenceProjector):
    def project_source(self, source: Source, /) -> JsonValue:
        if isinstance(source, ContentSource):
            return {
                "source_type": CONTENT_SOURCE_NAME,
                "source_id": source.name,
                "content": source.content,
                "metadata": dict(source.metadata),
            }
        return super().project_source(source)


def create_runtime_app(
    *,
    settings: ServerSettings | None = None,
    candidate_pipeline: CandidatePipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
) -> FastAPI:
    """Build a Server that owns one local Runtime through its lifespan."""

    resolved_settings = ServerSettings() if settings is None else settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as resources:
            pipeline = candidate_pipeline
            inference = resolved_settings.inference
            if pipeline is None and inference.generation_model is not None:
                pipeline = await _build_candidate_pipeline(inference, resources)
            if pipeline is None and resolved_settings.runtime.schedule_seconds is not None:
                raise ServerRuntimeConfigurationError("scheduled-extraction")

            embedding_profile = _embedding_profile(inference)
            vec1_extension = resolved_settings.storage.vec1_extension
            if (embedding_profile is None) != (vec1_extension is None):
                raise ServerRuntimeConfigurationError("sqlite-vector")
            configured_embedding_model = embedding_model
            if configured_embedding_model is None and embedding_profile is not None:
                configured_embedding_model = await _build_embedding_model(inference, embedding_profile, resources)
            if configured_embedding_model is not None and configured_embedding_model.profile != embedding_profile:
                raise ServerRuntimeConfigurationError("embedding-profile")

            runtime = await resources.enter_async_context(
                await PowerContextRuntime.open(
                    resolved_settings.storage.path,
                    candidate_pipeline=pipeline,
                    embedding_model=configured_embedding_model,
                    embedding_profile=embedding_profile,
                    vec1_extension=vec1_extension,
                    source_window_limit=resolved_settings.runtime.source_window_limit,
                    schedule_seconds=resolved_settings.runtime.schedule_seconds,
                )
            )
            app.state.application = runtime
            app.state.capabilities = _runtime_capabilities(await runtime.capabilities())
            try:
                yield
            finally:
                app.state.application = None
                app.state.capabilities = _empty_capabilities()

    return create_app(lifespan=lifespan)


def create_server_app(
    *,
    settings: ServerSettings | None = None,
    candidate_pipeline: CandidatePipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
) -> FastAPI:
    """Build the production Server with its HTTP and MCP transports."""

    resolved_settings = ServerSettings() if settings is None else settings
    app = create_runtime_app(
        settings=resolved_settings,
        candidate_pipeline=candidate_pipeline,
        embedding_model=embedding_model,
    )
    mount_mcp(app, path=resolved_settings.mcp.path)
    return app


async def _build_candidate_pipeline(settings: InferenceSettings, resources: AsyncExitStack) -> CandidatePipeline:
    from pydantic_ai.models import infer_model

    from powercontext.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator

    if settings.generation_model is None:
        raise ServerRuntimeConfigurationError("generation-model")
    limits = InferenceLimits(
        timeout_seconds=settings.generation_timeout_seconds,
        max_requests=settings.generation_max_requests,
    )
    generator = PydanticAIStructuredGenerator(
        model=await resources.enter_async_context(infer_model(settings.generation_model)),
        instructions=MEMORY_EXTRACTION_INSTRUCTIONS,
        input_type=MemoryExtractionInput,
        output_type=MemoryExtractionOutput,
        limits=limits,
    )
    return LLMMemoryCandidatePipeline(generator, evidence_projector=_ContentEvidenceProjector())


def _embedding_profile(settings: InferenceSettings) -> EmbeddingProfile | None:
    if settings.embedding_model is None:
        return None
    if settings.embedding_profile_id is None or settings.embedding_dimension is None:
        raise ServerRuntimeConfigurationError("embedding-profile")
    return EmbeddingProfile(
        profile_id=settings.embedding_profile_id,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        distance="l2",
        normalization=settings.embedding_normalization,
    )


async def _build_embedding_model(
    settings: InferenceSettings,
    profile: EmbeddingProfile,
    resources: AsyncExitStack,
) -> EmbeddingModel:
    from pydantic_ai import Embedder
    from pydantic_ai.embeddings import infer_embedding_model
    from pydantic_ai.providers import Provider, infer_provider

    from powercontext.inference.pydantic_ai import InferenceLimits, PydanticAIEmbeddingModel

    if settings.embedding_model is None:
        raise ServerRuntimeConfigurationError("embedding-model")
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
        profile=profile,
        limits=InferenceLimits(
            timeout_seconds=settings.embedding_timeout_seconds,
        ),
    )


def _runtime_capabilities(value: RuntimeCapabilities) -> Capabilities:
    return Capabilities(
        source_types=[CONTENT_SOURCE_NAME],
        artifact_families=["memory"],
        memory_extraction=value.memory_extraction,
        search_modes=list(value.memory_search_modes),
    )


def _empty_capabilities() -> Capabilities:
    return Capabilities(
        source_types=[],
        artifact_families=[],
        memory_extraction=False,
        search_modes=[],
    )


__all__ = [
    "ServerRuntimeConfigurationError",
    "create_runtime_app",
    "create_server_app",
]
