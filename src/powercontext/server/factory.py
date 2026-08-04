"""Ready-to-run Server composition over the built-in runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware import Middleware

from powercontext.builtin.artifacts.experience import ExperienceCandidatePipeline, ExperienceGenerator
from powercontext.builtin.artifacts.handoff import HandoffGenerationPipeline
from powercontext.builtin.artifacts.memory import CandidatePipeline
from powercontext.builtin.artifacts.skill import ExternalSkillProvider, SkillGenerator
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.runtime import BuiltinRuntime
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.sources import CONTENT_SOURCE_NAME
from powercontext.http import Capabilities, MemorySearchMode, PreparedContextSchema
from powercontext.paths import default_scheduler_path
from powercontext.server.app import create_app
from powercontext.server.mcp import mount_mcp
from powercontext.server.middleware import StaticBearerMiddleware
from powercontext.server.settings import ServerSettings


def create_server_app(
    *,
    settings: ServerSettings | None = None,
    scheduler_path: str | Path | None = None,
    candidate_pipeline: CandidatePipeline | None = None,
    experience_pipeline: ExperienceCandidatePipeline | None = None,
    experience_generator: ExperienceGenerator | None = None,
    skill_generator: SkillGenerator | None = None,
    external_skill_provider: ExternalSkillProvider | None = None,
    handoff_pipeline: HandoffGenerationPipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
    middleware: Sequence[Middleware] = (),
) -> FastAPI:
    """Build the Server process and mount MCP when configured."""

    resolved = ServerSettings() if settings is None else settings
    config = BuiltinConfig(
        runtime=resolved.runtime,
        database=resolved.database,
        inference=resolved.inference,
        external_skills=resolved.external_skills,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_builtin_runtime(
            config,
            scheduler_path=default_scheduler_path() if scheduler_path is None else scheduler_path,
            candidate_pipeline=candidate_pipeline,
            experience_pipeline=experience_pipeline,
            experience_generator=experience_generator,
            skill_generator=skill_generator,
            external_skill_provider=external_skill_provider,
            handoff_pipeline=handoff_pipeline,
            embedding_model=embedding_model,
        ) as runtime:
            app.state.application = runtime
            app.state.capabilities = await _server_capabilities(runtime)
            try:
                yield
            finally:
                app.state.application = None
                app.state.capabilities = Capabilities(
                    source_types=[],
                    artifact_families=[],
                    memory_extraction=False,
                    experience_generation=False,
                    managed_skill_generation=False,
                    external_skill_registry=False,
                    handoff_generation=False,
                    search_modes=[],
                    context_versions=[],
                )

    configured_middleware = list(middleware)
    auth_token = resolved.auth.token
    if resolved.auth.enabled and auth_token is not None:
        configured_middleware.insert(
            0,
            Middleware(StaticBearerMiddleware, token=auth_token.get_secret_value()),
        )

    app = create_app(lifespan=lifespan, middleware=configured_middleware)
    if resolved.mcp.enabled:
        mount_mcp(app, path=resolved.mcp.path)
    return app


async def _server_capabilities(runtime: BuiltinRuntime) -> Capabilities:
    capabilities = await runtime.capabilities()
    return Capabilities(
        source_types=[CONTENT_SOURCE_NAME],
        artifact_families=["memory", "experience", "skill", "handoff"],
        memory_extraction=capabilities.memory_extraction,
        experience_generation=capabilities.experience_generation,
        managed_skill_generation=capabilities.managed_skill_generation,
        external_skill_registry=capabilities.external_skill_registry,
        handoff_generation=capabilities.handoff_generation,
        search_modes=[MemorySearchMode(mode) for mode in capabilities.memory_search_modes],
        context_versions=[PreparedContextSchema(version) for version in capabilities.context_versions],
    )


__all__ = [
    "create_server_app",
]
