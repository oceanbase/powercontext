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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext, suppress
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.routing import APIRoute
from starlette.middleware import Middleware

from powercontext._logging import log_safely
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryGenerationPipeline
from powercontext.builtin.artifacts.experience import ExperienceCandidatePipeline, ExperienceGenerator
from powercontext.builtin.artifacts.handoff import HandoffGenerationPipeline
from powercontext.builtin.artifacts.memory import CandidatePipeline
from powercontext.builtin.artifacts.profile.service import ProfileGenerator
from powercontext.builtin.artifacts.skill import ExternalSkillProvider, SkillGenerator
from powercontext.builtin.dream.generation import DreamGenerator
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import BaseOperationNotSupportedError
from powercontext.builtin.runtime import (
    BuiltinRuntime,
    ExperienceIncubationResult,
    MemoryFlushResult,
)
from powercontext.builtin.runtime.application import ScheduledExperienceRunner, ScheduledSourceRunner
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import processing_capabilities
from powercontext.builtin.sources import CONTENT_SOURCE_NAME
from powercontext.http import (
    ArtifactDreamingOperationCapability,
    Capabilities,
    ExtractionStatus,
    MemorySearchMode,
    PreparedContextSchema,
    PromptCapability,
    ReadinessResponse,
    ReadinessStatus,
)
from powercontext.paths import default_scheduler_path
from powercontext.server.access import HttpAccessLogMiddleware
from powercontext.server.app import _bind_evidence_access, access_readiness_checks, create_app
from powercontext.server.authentication import (
    AuthenticationProvider,
    MultiBearerAuthenticationProvider,
    StaticBearerAuthenticationProvider,
)
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessControlService,
    CandidateOwnerAttestation,
    PrincipalRef,
    ResourceRef,
    access_control_for_mode,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.owner_recovery import (
    OwnerRecovery,
    flush_owner_writes,
    owner_recovery_session,
    owner_write_access,
)
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.server.context import current_principal, current_request_id
from powercontext.server.cursor_secret import resolve_cursor_secret
from powercontext.server.dashboard import mount_dashboard
from powercontext.server.dream_access import DreamAccess
from powercontext.server.http_limits import HttpResourceLimitsMiddleware
from powercontext.server.identity import ServerIdentityRepository
from powercontext.server.info import server_info
from powercontext.server.logging import worker_logging
from powercontext.server.mcp import mount_mcp
from powercontext.server.metrics import CONTENT_TYPE_LATEST, HttpMetricsMiddleware, ServerMetrics
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.processing_security import build_worker_security
from powercontext.server.request_id import HttpRequestIdMiddleware
from powercontext.server.settings import MissingAuthenticationProviderError, ServerSettings
from powercontext.server.tracing import HttpTracingMiddleware, ServerTracing
from powercontext.server.worker_tracing import WorkerTracing

logger = logging.getLogger(__name__)


class BackgroundRoleRequiresBackgroundRunnerError(RuntimeError):
    """Prevent a background-only process from accidentally exposing HTTP/MCP."""

    def __init__(self) -> None:
        super().__init__("artifact processing role 'background' must use the background service runner")


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
        runtime = request.app.state.application
        self._metrics.refresh_runtime(runtime)
        return Response(self._metrics.render(), media_type=CONTENT_TYPE_LATEST)


def create_server_app(  # noqa: C901
    *,
    settings: ServerSettings | None = None,
    scheduler_path: str | Path | None = None,
    candidate_pipeline: CandidatePipeline | AtomicMemoryGenerationPipeline | None = None,
    experience_pipeline: ExperienceCandidatePipeline | None = None,
    experience_generator: ExperienceGenerator | None = None,
    profile_generator: ProfileGenerator | None = None,
    skill_generator: SkillGenerator | None = None,
    dream_generator: DreamGenerator | None = None,
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
    if resolved.dashboard.enabled and (authentication_provider is not None or access_control is not None):
        raise ValueError("Dashboard supports only the built-in static Bearer profile")  # noqa: TRY003
    if resolved.runtime.artifact_processing_role == "background":
        raise BackgroundRoleRequiresBackgroundRunnerError
    static_principal, configured_authentication, configured_access_control, legacy_static_admin = (
        _resolve_security_providers(
            resolved,
            access_control=access_control,
            authentication_provider=authentication_provider,
        )
    )
    from powercontext.source_management.deployment import prepare as prepare_source_deployment

    configured_authentication, _ = prepare_source_deployment(resolved, configured_authentication)
    config = BuiltinConfig(
        code=resolved.code,
        runtime=resolved.runtime,
        database=resolved.database,
        handoff_report=resolved.handoff_report,
        inference=resolved.inference,
        external_skills=resolved.external_skills,
    )
    metrics = ServerMetrics(role=resolved.runtime.artifact_processing_role) if resolved.metrics.enabled else None
    resolved_tracing = (
        ServerTracing.context_only(role=resolved.runtime.artifact_processing_role) if tracing is None else tracing
    )
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
        from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage

        await asyncio.to_thread(
            prepare_server_storage,
            resolved,
            embedding_profile=None if embedding_model is None else embedding_model.profile,
        )
        configured_cursor_secret = (
            None if resolved.cursor_signing_secret is None else resolved.cursor_signing_secret.get_secret_value()
        )
        cursor_secret = resolve_cursor_secret(config.database, configured_cursor_secret)
        async with AsyncExitStack() as resources:
            logging_bootstrap = resources.enter_context(
                worker_logging(resolved.logging, role=resolved.runtime.artifact_processing_role)
            )
            active_access_control = configured_access_control
            if active_access_control is None and resolved.access.mode == "enforced":
                active_access_control = await resources.enter_async_context(
                    open_builtin_access_control(
                        resolved.database,
                        bootstrap_administrators=(static_principal,) if legacy_static_admin else (),
                        server_administrators=(
                            tuple(
                                principal
                                for principal in configured_authentication.principals()
                                if f"{principal.type}:{principal.id}" in resolved.auth.administrators
                            )
                            if isinstance(configured_authentication, MultiBearerAuthenticationProvider)
                            else ()
                        ),
                        deployment_id=resolved.access.deployment_id,
                    )
                )
            scheduled_source_runner, scheduled_experience_runner = _scheduled_access_runners(
                resolved,
                active_access_control,
                legacy_static_principal=static_principal if legacy_static_admin else None,
            )
            dream_access = (
                DreamAccess(active_access_control)
                if resolved.access.mode == "enforced" and isinstance(active_access_control, AccessControlService)
                else None
            )
            worker_security = build_worker_security(
                resolved,
                active_access_control,
                legacy_static_principal=static_principal if legacy_static_admin else None,
                enabled=config.runtime.artifact_processing_role != "api" and bool(processing_capabilities(config)),
                injected=configured_access_control is not None,
            )
            runtime = await resources.enter_async_context(
                open_builtin_runtime(
                    config,
                    scheduler_path=default_scheduler_path() if scheduler_path is None else scheduler_path,
                    candidate_pipeline=candidate_pipeline,
                    experience_pipeline=experience_pipeline,
                    experience_generator=experience_generator,
                    profile_generator=profile_generator,
                    skill_generator=skill_generator,
                    dream_generator=dream_generator,
                    dream_authorizer=None if dream_access is None else dream_access.authorize,
                    dream_authorization_context=nullcontext
                    if dream_access is None
                    else dream_access.access.defer_decision_audit,
                    dream_candidate_attester=None if dream_access is None else dream_access.attest_candidate,
                    dream_candidate_authorizer=None if dream_access is None else dream_access.authorize_candidate,
                    external_skill_provider=external_skill_provider,
                    handoff_pipeline=handoff_pipeline,
                    embedding_model=embedding_model,
                    instrumentation=resolved_tracing.instrumentation,
                    scope_cache_observer=None if metrics is None else metrics.set_runtime_scopes,
                    topic_memory_search_observer=None if metrics is None else metrics.observe_topic_memory_search,
                    tracing=resolved_tracing,
                    model_usage_observer=None if metrics is None else metrics.observe_model_usage,
                    worker_model_usage_observer=None if metrics is None else metrics.observe_worker_model_usage,
                    processing_observer=None if metrics is None else metrics.observe_processing,
                    scheduled_source_runner=scheduled_source_runner,
                    scheduled_experience_runner=scheduled_experience_runner,
                    worker_security=worker_security,
                    worker_initializer=logging_bootstrap,
                    worker_entrypoint_wrapper=WorkerTracing(resolved.tracing).wrap,
                    scheduled_profile_runner=_scheduled_profile_runner(
                        resolved,
                        active_access_control,
                        enabled=config.runtime.profile_schedule_enabled
                        and (profile_generator is not None or config.inference.generation_model is not None),
                        legacy_static_principal=static_principal if legacy_static_admin else None,
                    ),
                    cursor_secret=cursor_secret,
                    handoff_verification_keys=tuple(
                        secret.get_secret_value().encode()
                        for secret in resolved.handoff_generation_verification_secrets
                    ),
                )
            )
            if (
                active_access_control is not None
                and runtime.owner_write_database is not None
                and not active_access_control.supports_write_transaction(runtime.owner_write_database.engine)
            ):
                recovery = OwnerRecovery(runtime.owner_write_database, active_access_control)
                await recovery.initialize()
                app.state.owner_recovery = recovery
                await resources.enter_async_context(recovery.monitor())
            identity_repository = ServerIdentityRepository(runtime.primary_database)
            await identity_repository.initialize()
            server_id = await identity_repository.load_or_create()
            _bind_evidence_access(runtime, active_access_control, resolved.access.mode)
            if active_access_control is not None:
                migrated, unresolved = await runtime._records().migrate_handoff_receipts(
                    active_access_control.committed_receipt_identity,
                )
                _log_lifecycle(
                    "server.receipt_migration",
                    f"Receipt migration: {migrated} attested, {unresolved} pending in pc_receipt_migration_review",
                )
                await _remove_legacy_topic_owners(active_access_control)
            readiness_probe.bind(runtime)
            gateway = await _source_gateway(runtime, resolved)
            await resources.enter_async_context(gateway)
            app.state.source_gateway = gateway
            app.state.application = runtime
            app.state.dashboard_runtime = runtime
            app.state.access_control = active_access_control
            app.state.authentication_provider = configured_authentication
            readiness_probe.set_access_checks(_state_access_checks(app))
            capabilities = await _server_capabilities(runtime)

            def current_capabilities() -> Capabilities:
                extraction = runtime.extraction_status()
                return capabilities.model_copy(
                    update={
                        "extraction": (
                            None if extraction is None else ExtractionStatus.model_validate(extraction.model_dump())
                        ),
                    }
                )

            app.state.capabilities = capabilities
            app.state.capability_provider = current_capabilities
            app.state.server_info = server_info(server_id)
            await readiness_probe()
            deployment_task = None
            if resolved.source_plugins_dir is not None or resolved.source_management.autostart:
                from powercontext.source_management.deployment import DeploymentCoordinator

                deployment_task = asyncio.create_task(DeploymentCoordinator(resolved).startup())
            if metrics is not None:
                await resources.enter_async_context(readiness_probe.monitor())
            try:
                yield
            finally:
                if deployment_task is not None:
                    deployment_task.cancel()
                    await asyncio.gather(deployment_task, return_exceptions=True)
                _log_lifecycle("server.stopping", "PowerContext Server is stopping")
                readiness_probe.unbind()
                app.state.application = None
                app.state.source_gateway = None
                app.state.dashboard_runtime = None
                app.state.capability_provider = None
                app.state.access_control = configured_access_control
                app.state.authentication_provider = configured_authentication
                app.state.owner_recovery = None
                app.state.server_info = None
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
                dashboard_enabled=resolved.dashboard.enabled,
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
    # Admission must see the outward send; only non-buffering instrumentation
    # wraps it so early rejections retain the same correlation and observations.
    app.add_middleware(
        HttpResourceLimitsMiddleware,
        max_request_body_bytes=resolved.http.max_request_body_bytes,
        max_concurrent_requests=resolved.http.max_concurrent_requests,
    )
    app.add_middleware(HttpRequestIdMiddleware)
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
    if resolved.dashboard.enabled:
        mount_dashboard(app)
    return app


async def _source_gateway(runtime: BuiltinRuntime, settings: ServerSettings):
    from powercontext.source_management.gateway import SourceGateway
    from powercontext.source_management.write import SourceWritePlugins

    records = runtime._records()
    write_plugins = None
    if isinstance(records, RelationalRecordService):
        write_plugins = SourceWritePlugins(records.database, settings.workspace)
        await write_plugins.restore()
    return SourceGateway(settings, write_plugins)


def _state_access_checks(app: FastAPI) -> Callable[[], Awaitable[Mapping[str, str]]]:
    async def checks() -> dict[str, str]:
        return await access_readiness_checks(app.state)

    return checks


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
    if settings.auth.identities_file is not None:
        authentication = MultiBearerAuthenticationProvider(settings.auth.identities_file)
        return static_principal, authentication, access_control, False
    if settings.auth.token is None:
        raise MissingAuthenticationProviderError
    authentication = StaticBearerAuthenticationProvider(
        settings.auth.token.get_secret_value(),
        static_principal,
    )
    return static_principal, authentication, access_control, True


async def _remove_legacy_topic_owners(access: AccessControlService) -> None:
    """Drop the Artifact owner rows older versions retained for Topic Memory.

    Access tables are open only while a real service is configured, and a
    deployment that ran an older version may still hold stale rows that the
    owner-derived authorized resource filter reads back.
    """
    if not isinstance(access, AccessControlService):
        return
    relationships = access.relationships
    if not isinstance(relationships, RelationalAccessRepository):
        return
    removed = await relationships.delete_legacy_topic_memory_owners()
    if removed:
        _log_lifecycle("server.topic_owner_cleanup", f"Removed {removed} legacy topic-memory Artifact owner rows")


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
        if runtime.atomic_memory is None:
            raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "runtime application")
        return await runtime.atomic_memory.for_scope(scope_id).flush(
            context=ArtifactSearchExecutionContext(principal=principal, access=access, audit=context),
        )

    async def incubate_experience(scope_id: str, runtime: BuiltinRuntime) -> ExperienceIncubationResult:
        context = AccessAuditContext(transport="background", operation="incubate_experience_candidates")
        await access.bootstrap_static_scope(principal, scope_id, context=context)
        await access.require(principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=context)

        async def on_commit(connection, candidates):
            bound = owner_write_access(access, connection)
            await bound.attest_candidate_owners(
                tuple(
                    CandidateOwnerAttestation(
                        scope_id=scope_id,
                        candidate_id=candidate.candidate_id,
                        family="experience",
                        proposed_owner=principal,
                        target=None
                        if candidate.target is None
                        else ResourceRef.artifact(
                            scope_id,
                            family=candidate.target.family,
                            artifact_id=candidate.target.artifact_id,
                        ),
                        idempotency_key=f"background-candidate-owner:{scope_id}:{candidate.candidate_id}",
                    )
                    for candidate in candidates
                )
            )

        if runtime.owner_write_database is not None:
            recovery = (
                None
                if access.supports_write_transaction(runtime.owner_write_database.engine)
                else OwnerRecovery(runtime.owner_write_database, access)
            )
            async with owner_recovery_session(recovery):
                return await runtime.experience.for_scope(scope_id).incubate(on_commit=on_commit)
        result = await runtime.experience.for_scope(scope_id).incubate()
        await access.attest_candidate_owners(
            tuple(
                CandidateOwnerAttestation(
                    scope_id=scope_id,
                    candidate_id=candidate_id,
                    family="experience",
                    proposed_owner=principal,
                    target=None,
                    idempotency_key=f"background-candidate-owner:{scope_id}:{candidate_id}",
                )
                for candidate_id in result.candidate_ids
            )
        )
        return result

    return (
        process_sources if source_scheduled else None,
        incubate_experience if experience_scheduled else None,
    )


def _scheduled_profile_runner(settings, access, *, enabled, legacy_static_principal):
    if settings.access.mode == "disabled" or not enabled:
        return None
    if access is None:
        raise ValueError("Profile scheduling requires an Authorization Provider")  # noqa: TRY003
    principal = _scheduled_principal(settings, legacy_static_principal=legacy_static_principal)

    async def run(scope_id, high, profiles):
        context = AccessAuditContext(transport="background", operation="flush_profile")
        await access.bootstrap_static_scope(principal, scope_id, context=context)
        await access.require(principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=context)
        resource = ResourceRef.artifact(scope_id, family="profile", artifact_id="profile")

        async def authorize_snapshot(current):
            if current is not None:
                await access.require(principal, AccessAction.ARTIFACT_WRITE, resource, context=context)

        async def on_commit(connection, artifact, candidate):
            bound = owner_write_access(access, connection)
            if artifact is not None and await access.for_read(connection).artifact_owner(resource) is None:
                await bound.establish_artifact_owner(
                    resource,
                    principal,
                    idempotency_key=f"profile-owner:{scope_id}",
                    context=context,
                )
            if candidate is not None:
                await bound.attest_candidate_owner(
                    scope_id=scope_id,
                    candidate_id=candidate.candidate_id,
                    family="profile",
                    proposed_owner=principal,
                    target=None if candidate.target is None else resource,
                    idempotency_key=f"candidate-owner:{scope_id}:{candidate.candidate_id}",
                )

        recovery = (
            None
            if access.supports_write_transaction(profiles.database.engine)
            else OwnerRecovery(profiles.database, access)
        )
        async with owner_recovery_session(recovery):
            result = await profiles.flush(
                scope_id, high_watermark=high, authorize_snapshot=authorize_snapshot, on_commit=on_commit
            )
            await flush_owner_writes(
                artifact=resource,
                candidate=None if result.candidate_id is None else (scope_id, result.candidate_id),
            )
            return result

    return run


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


class _ServerReadinessProbe:
    def __init__(
        self,
        metrics: ServerMetrics | None,
        *,
        tracing: ServerTracing,
        access_checks: Callable[[], Awaitable[Mapping[str, str]]] | None = None,
    ) -> None:
        self._metrics = metrics
        self._tracing = tracing
        self._access_checks = access_checks
        self._runtime: BuiltinRuntime | None = None
        self._last_status: ReadinessStatus | None = None

    def bind(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def set_access_checks(self, access_checks: Callable[[], Awaitable[Mapping[str, str]]] | None) -> None:
        self._access_checks = access_checks

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
        if self._metrics is not None:
            await self._publish(response)
        self._observe(response.status)
        return response

    async def _publish(self, response: ReadinessResponse) -> None:
        """Publish the readiness aggregate that `/health/ready` would return."""

        metrics = self._metrics
        if metrics is None:
            return
        status = response.status
        checks: dict[str, str] = dict(response.checks)
        provider = self._access_checks
        if provider is not None:
            try:
                checks = {**checks, **await provider()}
            except Exception:
                # An unreadable access layer only means the Server is not ready.
                checks = {**checks, "access_provider": "not_ready"}
            if any(checks.get(name) == "not_ready" for name in ("authentication_provider", "access_provider")):
                status = ReadinessStatus.NOT_READY
        metrics.observe_readiness(status.value, checks)

    @asynccontextmanager
    async def monitor(self) -> AsyncIterator[None]:
        async def refresh() -> None:
            while True:
                await asyncio.sleep(30)
                await self()

        task = asyncio.create_task(refresh(), name="powercontext-readiness")
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

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
        artifact_families=[
            "memory",
            "atomic-memory",
            "topic-memory",
            "experience",
            "skill",
            "handoff",
            "profile",
            "prompt",
        ],
        prompts={
            key: PromptCapability.model_validate_json(value.model_dump_json())
            for key, value in capabilities.prompts.items()
        },
        memory_extraction=capabilities.memory_extraction,
        experience_generation=capabilities.experience_generation,
        managed_skill_generation=capabilities.managed_skill_generation,
        artifact_dreaming=capabilities.artifact_dreaming,
        artifact_dreaming_operations=[
            ArtifactDreamingOperationCapability.model_validate(value.model_dump())
            for value in capabilities.artifact_dreaming_operations
        ],
        external_skill_registry=capabilities.external_skill_registry,
        handoff_generation=capabilities.handoff_generation,
        search_modes=[MemorySearchMode(mode) for mode in capabilities.memory_search_modes],
        context_versions=[PreparedContextSchema(version) for version in capabilities.context_versions],
    )


__all__ = [
    "create_background_metrics_app",
    "create_server_app",
]


def create_background_metrics_app(
    settings: ServerSettings,
    runtime: BuiltinRuntime | None,
    metrics: ServerMetrics,
    access_control: AccessControlService | None,
) -> FastAPI:
    """Expose infrastructure only, reusing the Server authentication/error contracts."""
    _, authentication, access, _ = _resolve_security_providers(
        settings, access_control=access_control, authentication_provider=None
    )
    middleware = [] if authentication is None else [Middleware(AuthenticationMiddleware, provider=authentication)]
    app = create_app(
        infrastructure_only=True,
        middleware=middleware,
        metrics=metrics,
        access_control=access,
        access_mode=settings.access.mode,
        authentication_provider=authentication,
    )
    app.state.application = runtime
    if settings.metrics.enabled:
        app.add_api_route("/metrics", _MetricsEndpoint(metrics), include_in_schema=False)
    return app
