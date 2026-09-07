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

"""Ready-to-run Server composition over the built-in runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.routing import APIRoute
from starlette.middleware import Middleware

from powercontext._logging import log_safely
from powercontext.builtin.artifacts.experience import ExperienceCandidatePipeline, ExperienceGenerator
from powercontext.builtin.artifacts.handoff import HandoffGenerationPipeline
from powercontext.builtin.artifacts.memory import CandidatePipeline
from powercontext.builtin.artifacts.skill import ExternalSkillProvider, SkillGenerator
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinRuntime,
    ExperienceIncubationResult,
    MemoryEntryRecord,
    MemoryFlushResult,
)
from powercontext.builtin.runtime.application import ScheduledExperienceRunner, ScheduledSourceRunner
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.sources import CONTENT_SOURCE_NAME
from powercontext.http import Capabilities, MemorySearchMode, PreparedContextSchema, ReadinessResponse, ReadinessStatus
from powercontext.paths import default_scheduler_path
from powercontext.server.access import HttpAccessLogMiddleware
from powercontext.server.app import create_app
from powercontext.server.authentication import (
    AuthenticationProvider,
    StaticBearerAuthenticationProvider,
)
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessControlService,
    MemoryEntrySelector,
    PrincipalRef,
    ResourceRef,
    access_control_for_mode,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.context import current_principal, current_request_id
from powercontext.server.cursor_secret import resolve_cursor_secret
from powercontext.server.mcp import mount_mcp
from powercontext.server.metrics import CONTENT_TYPE_LATEST, HttpMetricsMiddleware, ServerMetrics
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.settings import MissingAuthenticationProviderError, ServerSettings
from powercontext.server.tracing import HttpTracingMiddleware, ServerTracing
from powercontext.server.web import mount_web_ui

logger = logging.getLogger(__name__)


class _MetricsEndpoint:
    def __init__(self, metrics: ServerMetrics) -> None:
        self._metrics = metrics

    async def __call__(self, request: Request) -> Response:
        access = access_control_for_mode(
            request.app.state.access_control,
            mode=request.app.state.access_mode,
        )
        if access is not None:
            await access.require(
                current_principal(),
                AccessAction.SERVER_OBSERVE,
                ResourceRef.server(access.deployment_id),
                context=AccessAuditContext(
                    transport="http",
                    operation="get_metrics",
                    request_id=current_request_id(),
                ),
            )
        return Response(self._metrics.render(), media_type=CONTENT_TYPE_LATEST)


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
    access_control: AccessControlService | None = None,
    authentication_provider: AuthenticationProvider | None = None,
) -> FastAPI:
    """Build the Server process and mount MCP when configured."""

    resolved = ServerSettings() if settings is None else settings
    static_principal, configured_authentication, configured_access_control, legacy_static_admin = (
        _resolve_security_providers(
            resolved,
            access_control=access_control,
            authentication_provider=authentication_provider,
        )
    )
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
    readiness_probe = _ServerReadinessProbe(metrics, tracing=resolved_tracing)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _log_lifecycle("server.starting", "PowerContext Server is starting")
        if resolved.allow_insecure_http:
            _log_insecure_remote_http_warning()
        if isinstance(config.database, SQLiteConfig) and config.database.is_in_memory:
            _log_in_memory_database_warning()
        configured_cursor_secret = (
            None if resolved.cursor_signing_secret is None else resolved.cursor_signing_secret.get_secret_value()
        )
        cursor_secret = resolve_cursor_secret(config.database, configured_cursor_secret)
        async with AsyncExitStack() as resources:
            active_access_control = configured_access_control
            if active_access_control is None and resolved.access.mode == "enforced":
                active_access_control = await resources.enter_async_context(
                    open_builtin_access_control(
                        resolved.database,
                        bootstrap_administrators=(static_principal,) if legacy_static_admin else (),
                        deployment_id=resolved.access.deployment_id,
                    )
                )
            scheduled_source_runner, scheduled_experience_runner = _scheduled_access_runners(
                resolved,
                active_access_control,
                legacy_static_principal=static_principal if legacy_static_admin else None,
            )
            runtime = await resources.enter_async_context(
                open_builtin_runtime(
                    config,
                    scheduler_path=default_scheduler_path() if scheduler_path is None else scheduler_path,
                    candidate_pipeline=candidate_pipeline,
                    experience_pipeline=experience_pipeline,
                    experience_generator=experience_generator,
                    skill_generator=skill_generator,
                    external_skill_provider=external_skill_provider,
                    handoff_pipeline=handoff_pipeline,
                    embedding_model=embedding_model,
                    instrumentation=resolved_tracing.instrumentation,
                    scope_cache_observer=None if metrics is None else metrics.set_runtime_scopes,
                    tracing=resolved_tracing,
                    scheduled_source_runner=scheduled_source_runner,
                    scheduled_experience_runner=scheduled_experience_runner,
                    cursor_secret=cursor_secret,
                )
            )
            readiness_probe.bind(runtime)
            app.state.application = runtime
            app.state.access_control = active_access_control
            app.state.authentication_provider = configured_authentication
            app.state.capabilities = await _server_capabilities(runtime)
            await readiness_probe()
            try:
                yield
            finally:
                _log_lifecycle("server.stopping", "PowerContext Server is stopping")
                readiness_probe.unbind()
                app.state.application = None
                app.state.access_control = configured_access_control
                app.state.authentication_provider = configured_authentication
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
    if configured_authentication is not None:
        configured_middleware.insert(
            0,
            Middleware(
                AuthenticationMiddleware,
                provider=configured_authentication,
            ),
        )

    app = create_app(
        lifespan=lifespan,
        readiness_probe=readiness_probe,
        middleware=configured_middleware,
        metrics=metrics,
        tracing=resolved_tracing,
        handoff_report_enabled=resolved.handoff_report.enabled,
        access_control=configured_access_control,
        access_mode=resolved.access.mode,
        authentication_provider=configured_authentication,
        allow_insecure_remote_http=resolved.allow_insecure_http,
    )
    _mount_optional_web_ui(app, resolved)
    if metrics is not None:
        app.add_api_route(
            "/metrics",
            _MetricsEndpoint(metrics),
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


def _resolve_security_providers(
    settings: ServerSettings,
    *,
    access_control: AccessControlService | None,
    authentication_provider: AuthenticationProvider | None,
) -> tuple[PrincipalRef, AuthenticationProvider | None, AccessControlService | None, bool]:
    static_principal = PrincipalRef(
        type="service",
        id="server-token",
        description="PowerContext static bearer",
    )
    if settings.access.mode == "disabled":
        if access_control is not None or authentication_provider is not None:
            raise ValueError("disabled Access Mode cannot load security Providers")  # noqa: TRY003
        return static_principal, None, None, False
    if authentication_provider is not None:
        return static_principal, authentication_provider, access_control, False
    if settings.auth.token is None:
        raise MissingAuthenticationProviderError
    authentication = StaticBearerAuthenticationProvider(
        settings.auth.token.get_secret_value(),
        static_principal,
    )
    return static_principal, authentication, access_control, True


def _scheduled_access_runners(
    settings: ServerSettings,
    access: AccessControlService | None,
    *,
    legacy_static_principal: PrincipalRef | None,
) -> tuple[ScheduledSourceRunner | None, ScheduledExperienceRunner | None]:
    source_scheduled = settings.runtime.schedule_seconds is not None
    experience_scheduled = settings.runtime.experience_schedule_seconds is not None
    if settings.access.mode == "disabled" or not (source_scheduled or experience_scheduled):
        return None, None
    if access is None:
        raise ValueError("scheduled processing in enforced mode requires an Authorization Provider")  # noqa: TRY003
    principal = _scheduled_principal(settings, legacy_static_principal=legacy_static_principal)

    async def process_sources(scope_id: str, runtime: BuiltinRuntime) -> MemoryFlushResult:
        context = AccessAuditContext(transport="background", operation="process_source_window")
        await access.bootstrap_static_scope(principal, scope_id, context=context)
        await access.require(principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=context)
        memory = runtime.memory.for_scope(scope_id)
        before = await memory.list(include_inactive=True)
        before_keys = {_memory_resource(scope_id, entry).key for entry in before.entries}
        await access.require_all(
            principal,
            tuple((AccessAction.ARTIFACT_WRITE, _memory_resource(scope_id, entry)) for entry in before.entries),
            context=context,
        )
        result = await memory.flush()
        after = await memory.list(include_inactive=True)
        for entry in after.entries:
            resource = _memory_resource(scope_id, entry)
            if resource.key not in before_keys:
                await access.establish_artifact_owner(
                    resource,
                    principal,
                    idempotency_key=f"background-memory-owner:{scope_id}:{resource.artifact_id}:{entry.citation.entry_id}",
                    context=context,
                )
        return result

    async def incubate_experience(scope_id: str, runtime: BuiltinRuntime) -> ExperienceIncubationResult:
        context = AccessAuditContext(transport="background", operation="incubate_experience_candidates")
        await access.bootstrap_static_scope(principal, scope_id, context=context)
        await access.require(principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=context)
        result = await runtime.experience.for_scope(scope_id).incubate()
        for candidate_id in result.candidate_ids:
            await access.attest_candidate_owner(
                scope_id=scope_id,
                candidate_id=candidate_id,
                family="experience",
                proposed_owner=principal,
                target=None,
                idempotency_key=f"background-candidate-owner:{scope_id}:{candidate_id}",
            )
        return result

    return (
        process_sources if source_scheduled else None,
        incubate_experience if experience_scheduled else None,
    )


def _scheduled_principal(
    settings: ServerSettings,
    *,
    legacy_static_principal: PrincipalRef | None,
) -> PrincipalRef:
    if settings.access.background_principal_id is not None:
        return PrincipalRef(
            type="service",
            id=settings.access.background_principal_id,
            description=settings.access.background_principal_description,
        )
    if legacy_static_principal is not None:
        return legacy_static_principal
    raise ValueError("scheduled processing in enforced mode requires ACCESS_BACKGROUND_PRINCIPAL_ID")  # noqa: TRY003


def _memory_resource(scope_id: str, entry: MemoryEntryRecord) -> ResourceRef:
    citation = entry.citation
    return ResourceRef.artifact(
        scope_id,
        family="memory",
        artifact_id=citation.memory_ref.artifact_id,
        selector=MemoryEntrySelector(entry_id=citation.entry_id),
    )


def _mount_optional_web_ui(app: FastAPI, settings: ServerSettings) -> None:
    app.state.dashboard_started = False
    app.state.dashboard_startup_error = None
    if not (settings.dashboard.enabled or settings.handoff_report.enabled):
        return
    try:
        mount_web_ui(
            app,
            dashboard_enabled=settings.dashboard.enabled,
            handoff_report_enabled=settings.handoff_report.enabled,
            authentication_required=settings.access.mode == "enforced",
            agent_skill_targets=settings.external_skills.agent_targets,
            public_server_url=settings.public_url,
            allow_insecure_http=settings.allow_insecure_http,
        )
        if settings.dashboard.enabled:
            app.state.dashboard_started = True
    except Exception as error:
        app.state.dashboard_startup_error = str(error)
        unit = "Dashboard" if settings.dashboard.enabled else "Handoff Report"
        log_safely(
            logger,
            logging.WARNING,
            f"PowerContext {unit} failed to start: {error}",
            exc_info=error,
            extra={"event": "web_ui.start_failed", "unit": "web_ui"},
        )


class _ServerReadinessProbe:
    def __init__(self, metrics: ServerMetrics | None, *, tracing: ServerTracing) -> None:
        self._metrics = metrics
        self._tracing = tracing
        self._runtime: BuiltinRuntime | None = None
        self._last_status: ReadinessStatus | None = None

    def bind(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def unbind(self) -> None:
        self._runtime = None
        self._last_status = None
        if self._metrics is not None:
            self._metrics.set_ready(False)

    async def __call__(self) -> ReadinessResponse:
        with self._tracing._suppress_readiness_spans():
            runtime = self._runtime
            if runtime is None:
                response = ReadinessResponse(
                    status=ReadinessStatus.NOT_READY,
                    checks={"runtime": "not_ready"},
                )
            else:
                response = await self._check(runtime)
        self._observe(response.status)
        return response

    async def _check(self, runtime: BuiltinRuntime) -> ReadinessResponse:
        try:
            readiness = await runtime.readiness()
        except asyncio.CancelledError:
            raise
        except Exception:
            return ReadinessResponse(
                status=ReadinessStatus.NOT_READY,
                checks={"runtime": "unavailable"},
            )
        return ReadinessResponse(
            status=ReadinessStatus(readiness.status.value),
            checks={name: str(status) for name, status in readiness.checks.items()},
        )

    def _observe(self, status: ReadinessStatus) -> None:
        if self._metrics is not None:
            self._metrics.set_ready(status is not ReadinessStatus.NOT_READY)
        if status is self._last_status:
            return
        self._last_status = status
        event, message = {
            ReadinessStatus.READY: ("server.ready", "PowerContext Server is ready"),
            ReadinessStatus.DEGRADED: ("server.degraded", "PowerContext Server is degraded"),
            ReadinessStatus.NOT_READY: ("server.not_ready", "PowerContext Server is not ready"),
        }[status]
        _log_lifecycle(event, message)


def _log_lifecycle(event: str, message: str) -> None:
    log_safely(
        logger,
        logging.INFO,
        message,
        extra={"event": event, "unit": "server"},
    )


def _log_in_memory_database_warning() -> None:
    log_safely(
        logger,
        logging.WARNING,
        "PowerContext Server is using an in-memory SQLite database; "
        "all main database data will be lost when the process stops",
        extra={"event": "server.database.in_memory", "unit": "server"},
    )


def _log_insecure_remote_http_warning() -> None:
    log_safely(
        logger,
        logging.WARNING,
        "PowerContext remote Skill Receiver cleartext HTTP opt-in is enabled; "
        "use it only on a protected private test network",
        extra={"event": "server.remote_skills.insecure_http_enabled", "unit": "server"},
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
