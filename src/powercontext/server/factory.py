"""Ready-to-run Server composition over the built-in runtime."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.routing import APIRoute
from starlette.middleware import Middleware

from powercontext._logging import log_safely
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
from powercontext.server.access import HttpAccessLogMiddleware
from powercontext.server.app import create_app
from powercontext.server.mcp import mount_mcp
from powercontext.server.metrics import CONTENT_TYPE_LATEST, HttpMetricsMiddleware, ServerMetrics
from powercontext.server.middleware import StaticBearerMiddleware
from powercontext.server.settings import ServerSettings
from powercontext.server.tracing import HttpTracingMiddleware, ServerTracing
from powercontext.server.web import mount_web_ui

logger = logging.getLogger(__name__)


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
    tracing: ServerTracing | None = None,
) -> FastAPI:
    """Build the Server process and mount MCP when configured."""

    resolved = ServerSettings() if settings is None else settings
    config = BuiltinConfig(
        runtime=resolved.runtime,
        database=resolved.database,
        handoff_report=resolved.handoff_report,
        inference=resolved.inference,
        external_skills=resolved.external_skills,
    )
    metrics = ServerMetrics() if resolved.metrics.enabled else None
    resolved_tracing = ServerTracing.context_only() if tracing is None else tracing
    if metrics is not None:
        metrics.set_ready(False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _log_lifecycle("server.starting", "PowerContext Server is starting")
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
            if metrics is not None:
                metrics.set_ready(True)
            _log_lifecycle("server.ready", "PowerContext Server is ready")
            try:
                yield
            finally:
                _log_lifecycle("server.stopping", "PowerContext Server is stopping")
                app.state.application = None
                if metrics is not None:
                    metrics.set_ready(False)
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
        _log_lifecycle("server.stopped", "PowerContext Server stopped")

    configured_middleware = list(middleware)
    auth_token = resolved.auth.token
    if resolved.auth.enabled and auth_token is not None:
        configured_middleware.insert(
            0,
            Middleware(StaticBearerMiddleware, token=auth_token.get_secret_value()),
        )

    app = create_app(
        lifespan=lifespan,
        middleware=configured_middleware,
        metrics=metrics,
        tracing=resolved_tracing,
        handoff_report_enabled=resolved.handoff_report.enabled,
    )
    if resolved.dashboard.enabled:
        mount_web_ui(
            app,
            scopes={scope.scope_id: scope.display_name for scope in resolved.dashboard.scopes},
            handoff_report_enabled=resolved.handoff_report.enabled,
        )
    if metrics is not None:
        app.add_api_route(
            "/metrics",
            lambda: Response(metrics.render(), media_type=CONTENT_TYPE_LATEST),
            include_in_schema=False,
        )
        operations = _http_operations(app)
        app.add_middleware(
            HttpMetricsMiddleware,
            metrics=metrics,
            operations=operations,
            skip_paths=("/health/live", "/health/ready", "/metrics", resolved.mcp.path),
        )
    if resolved.logging.access:
        app.add_middleware(
            HttpAccessLogMiddleware,
            skip_paths=("/health/live", "/health/ready", "/metrics", resolved.mcp.path),
        )
    app.add_middleware(
        HttpTracingMiddleware,
        tracing=resolved_tracing,
        operations=_http_operations(app),
        skip_paths=("/health/live", "/health/ready", "/metrics", resolved.mcp.path),
    )
    if resolved.mcp.enabled:
        mount_mcp(
            app,
            path=resolved.mcp.path,
            access_log=resolved.logging.access,
            metrics=metrics,
            tracing=resolved_tracing,
        )
    return app


def _log_lifecycle(event: str, message: str) -> None:
    log_safely(
        logger,
        logging.INFO,
        message,
        extra={"event": event, "unit": "server"},
    )


def _http_operations(app: FastAPI) -> dict[tuple[str, str], str]:
    return {
        (method, route.path): route.operation_id
        for route in app.routes
        if isinstance(route, APIRoute) and route.operation_id is not None
        for method in route.methods or ()
    }


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
