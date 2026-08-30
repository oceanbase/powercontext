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

"""Server-owned HTML pages and their supporting endpoints."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import cache
from typing import Literal

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import (
    AgentKind,
    AgentSkillTarget,
    ExternalSkillResolutionStatus,
    Skill,
    SkillCompatibilityState,
    SkillContent,
    SkillOrigin,
    SkillPackageRef,
    SkillPackageSnapshot,
    assess_skill_compatibility,
    package_file,
)
from powercontext.builtin.artifacts.skill.projection import (
    AgentSkillProjectionConflictError,
    AgentSkillProjectionState,
)
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactLifecycleState,
)
from powercontext.builtin.runtime import GetSkillRequest, ListExternalSkillsRequest
from powercontext.errors import ArtifactNotFoundError
from powercontext.http import (
    ErrorDetail,
    ErrorResponse,
    SkillPackageFile,
    SkillPackageManifest,
    SkillPackageReference,
)
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH
from powercontext.sources import SourceRef

logger = logging.getLogger(__name__)

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
    ),
}


class DashboardScope(BaseModel):
    """One Server scope exposed by the personal Dashboard."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str
    display_name: str


class DashboardSkillProjectionRequest(BaseModel):
    """Select one exact approved managed Skill Revision from the Review UI."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=256)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    artifact: ArtifactRef

    @model_validator(mode="after")
    def require_skill_artifact(self) -> DashboardSkillProjectionRequest:
        if self.artifact.family != "skill":
            raise ValueError("artifact must identify a managed Skill")  # noqa: TRY003
        return self


class DashboardSkillPublishRequest(DashboardSkillProjectionRequest):
    """Explicitly publish one exact approved managed Skill Revision."""

    target_id: str = Field(min_length=1, max_length=64)
    allow_deprecated: bool = False


class DashboardSkillUnpublishRequest(DashboardSkillPublishRequest):
    """Explicitly remove an exact unmodified managed publication."""


class DashboardSkillProjectionTarget(BaseModel):
    """One configured host-local Agent publication target and its exact state."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    agent_kind: AgentKind
    installation_scope: Literal["user", "project", "plugin"]
    destination: str
    state: AgentSkillProjectionState
    published_revision: int | None = None
    reason: str | None = None
    discovery: Literal["available", "unavailable", "not_published"]
    external_skill_id: str | None = None
    generation: int | None = None
    tree_digest: str | None = None
    compatibility: SkillCompatibilityState
    compatibility_reasons: tuple[str, ...]
    environment_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class DashboardSkillProjection(BaseModel):
    """Publication state for one exact approved managed Skill Revision."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    name: str
    blocker: Literal["standard_package_required"] | None = None
    targets: list[DashboardSkillProjectionTarget]


class DashboardSkillLibraryRequest(BaseModel):
    """Search current managed Skill heads without reconstructing them from Review history."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=256)
    query: str | None = Field(default=None, max_length=2_000)
    include_deprecated: bool = False
    limit: int = Field(default=100, ge=1, le=200)


class DashboardManagedSkill(BaseModel):
    """One current managed Skill Library row with governance and exact lineage."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    content: SkillContent
    sources: tuple[SourceRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    governance: ArtifactGovernance
    origin: SkillOrigin


class DashboardSkillLifecycleRequest(BaseModel):
    """CAS lifecycle transition for one logical managed Skill."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=256)
    artifact_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    expected_generation: int = Field(ge=0)
    lifecycle_state: ArtifactLifecycleState
    replacement_artifact_id: str | None = Field(default=None, min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)


class DashboardSkillPackageRequest(BaseModel):
    """Resolve a package reference already visible in a scoped Candidate or Artifact."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=256)
    package: SkillPackageRef


class DashboardSkillPackageFileRequest(DashboardSkillPackageRequest):
    """Select one exact package path for inert bounded preview."""

    path: str = Field(min_length=1, max_length=512)


class DashboardSkillPackageFilePreview(BaseModel):
    """Inert text preview; binary files expose metadata but never content."""

    model_config = ConfigDict(extra="forbid")

    path: str
    media_type: str
    content: str | None = None
    binary: bool
    truncated: bool


class _DashboardSkillProjectionRoutes:
    def __init__(self, scope_ids: frozenset[str], targets: tuple[AgentSkillTarget, ...]) -> None:
        self._scope_ids = scope_ids
        self._targets = targets

    async def inspect(
        self,
        request: DashboardSkillProjectionRequest,
        http_request: Request,
    ) -> DashboardSkillProjection | JSONResponse:
        resolved = await _dashboard_managed_skill(http_request, request, self._scope_ids)
        if isinstance(resolved, JSONResponse):
            return resolved
        application, skill = resolved
        return await _skill_projection_response(application, request.scope_id, skill, self._targets)

    async def publish(
        self,
        request: DashboardSkillPublishRequest,
        http_request: Request,
    ) -> DashboardSkillProjection | JSONResponse:
        resolved = await _dashboard_managed_skill(http_request, request, self._scope_ids)
        if isinstance(resolved, JSONResponse):
            return resolved
        application, skill = resolved
        target = next((item for item in self._targets if item.target_id == request.target_id), None)
        if target is None:
            return _web_error(
                404, "skill_publish_target_not_found", "The Agent Skill publication target was not found."
            )
        try:
            await application.skill.for_scope(request.scope_id).publish(
                skill.as_ref(), target, allow_deprecated=request.allow_deprecated
            )
        except AgentSkillProjectionConflictError as error:
            return _web_error(
                409,
                "skill_projection_conflict",
                "The Agent Skill publication target changed or cannot be updated safely.",
                details={"state": error.status.state.value, "reason": error.status.reason},
            )
        except (OSError, UnicodeError, ValueError) as error:
            return _web_error(
                422,
                "skill_projection_failed",
                "The approved managed Skill could not be published to the configured Agent target.",
                details={"reason": str(error)},
            )
        # The publication itself succeeded above; registry bookkeeping failure must not turn the
        # response into a 500 because _skill_projection_response reports on-disk state anyway.
        try:
            await application.external_skills.for_scope(request.scope_id).scan()
        except Exception as error:
            log_safely(
                logger, logging.WARNING, "PowerContext external Skill scan failed after publication", exc_info=error
            )
        return await _skill_projection_response(application, request.scope_id, skill, self._targets)

    async def unpublish(
        self,
        request: DashboardSkillUnpublishRequest,
        http_request: Request,
    ) -> DashboardSkillProjection | JSONResponse:
        resolved = await _dashboard_managed_skill(http_request, request, self._scope_ids)
        if isinstance(resolved, JSONResponse):
            return resolved
        application, skill = resolved
        target = next((item for item in self._targets if item.target_id == request.target_id), None)
        if target is None:
            return _web_error(
                404, "skill_publish_target_not_found", "The Agent Skill publication target was not found."
            )
        try:
            await application.skill.for_scope(request.scope_id).unpublish(skill.as_ref(), target)
        except AgentSkillProjectionConflictError as error:
            return _web_error(
                409,
                "skill_projection_conflict",
                "The Agent Skill publication changed or cannot be removed safely.",
                details={"state": error.status.state.value, "reason": error.status.reason},
            )
        except (OSError, UnicodeError, ValueError) as error:
            return _web_error(
                422,
                "skill_projection_failed",
                "The approved managed Skill could not be unpublished from the configured Agent target.",
                details={"reason": str(error)},
            )
        # The publication removal already succeeded; keep registry bookkeeping best-effort for
        # the same reason as the publish path above.
        try:
            await application.external_skills.for_scope(request.scope_id).scan()
        except Exception as error:
            log_safely(
                logger, logging.WARNING, "PowerContext external Skill scan failed after unpublication", exc_info=error
            )
        return await _skill_projection_response(application, request.scope_id, skill, self._targets)


def mount_web_ui(  # noqa: C901
    app: FastAPI,
    *,
    scopes: Mapping[str, str],
    dashboard_enabled: bool = False,
    handoff_report_enabled: bool = False,
    authentication_required: bool = False,
    agent_skill_targets: tuple[AgentSkillTarget, ...] = (),
    public_server_url: str | None = None,
    allow_insecure_http: bool = False,
) -> None:
    """Mount Server-owned pages, static assets, and UI support endpoints."""

    dashboard_scopes = tuple(DashboardScope(scope_id=scope_id, display_name=name) for scope_id, name in scopes.items())
    dashboard_scope_ids = frozenset(scopes)
    publish_targets = tuple(target for target in agent_skill_targets if target.allow_managed_publish)
    skill_projection_routes = _DashboardSkillProjectionRoutes(dashboard_scope_ids, publish_targets)
    templates = _templates()
    if dashboard_enabled:
        templates.env.get_template("pages/dashboard.html")
        templates.env.get_template("pages/review.html")
        templates.env.get_template("pages/skills.html")
    if handoff_report_enabled:
        templates.env.get_template("pages/handoff_report.html")
    static_files = StaticFiles(packages=[("powercontext.server", "static")])

    router = APIRouter(include_in_schema=False)

    async def dashboard_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="pages/dashboard.html",
            context={
                "active_page": "dashboard",
                "dashboard_enabled": True,
                "skills_enabled": True,
                "review_enabled": True,
                "handoff_report_enabled": handoff_report_enabled,
                "home_route": "dashboard_home",
                "authentication_required": authentication_required,
            },
            headers=_PAGE_HEADERS,
        )

    async def skills_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="pages/skills.html",
            context={
                "active_page": "skills",
                "dashboard_enabled": True,
                "skills_enabled": True,
                "review_enabled": True,
                "handoff_report_enabled": handoff_report_enabled,
                "home_route": "dashboard_home",
                "authentication_required": authentication_required,
                "public_server_url": public_server_url,
                "allow_insecure_http": allow_insecure_http,
            },
            headers=_PAGE_HEADERS,
        )

    async def review_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="pages/review.html",
            context={
                "active_page": "review",
                "dashboard_enabled": True,
                "skills_enabled": True,
                "review_enabled": True,
                "handoff_report_enabled": handoff_report_enabled,
                "home_route": "dashboard_home",
                "authentication_required": authentication_required,
            },
            headers=_PAGE_HEADERS,
        )

    async def handoff_report_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="pages/handoff_report.html",
            context={
                "active_page": "handoff_report",
                "dashboard_enabled": dashboard_enabled,
                "skills_enabled": dashboard_enabled,
                "review_enabled": dashboard_enabled,
                "handoff_report_enabled": True,
                "home_route": "dashboard_home" if dashboard_enabled else "handoff_report_dashboard",
                "authentication_required": authentication_required,
            },
            headers=_PAGE_HEADERS,
        )

    async def list_dashboard_scopes(response: Response) -> tuple[DashboardScope, ...]:
        response.headers["Cache-Control"] = "no-store"
        return dashboard_scopes

    async def list_managed_skills(
        request: DashboardSkillLibraryRequest,
        http_request: Request,
    ) -> list[DashboardManagedSkill] | JSONResponse:
        if request.scope_id not in dashboard_scope_ids:
            return _web_error(404, "dashboard_scope_not_found", "The Dashboard scope was not found.")
        application = http_request.app.state.application
        if application is None:
            return _web_error(503, "runtime_not_ready", "The Runtime is not ready.")
        scoped = application.skill.for_scope(request.scope_id)
        values: list[tuple[Skill, ArtifactGovernance]] = []
        query = "" if request.query is None else request.query.strip()
        if query:
            for hit in await scoped.search(query, request.limit):
                skill = await scoped.get(GetSkillRequest(artifact=hit.artifact_ref))
                values.append((skill, await scoped.governance(skill.artifact_id)))
        else:
            values.extend(await scoped.list(include_deprecated=request.include_deprecated, limit=request.limit))
        if query and request.include_deprecated:
            seen = {skill.artifact_id for skill, _governance in values}
            for skill, governance in await scoped.list(include_deprecated=True, limit=request.limit):
                if (
                    governance.lifecycle_state is ArtifactLifecycleState.DEPRECATED
                    and skill.artifact_id not in seen
                    and query.casefold() in _skill_library_search_text(skill.content).casefold()
                ):
                    values.append((skill, governance))
        selected = values[: request.limit]
        origins = await scoped.origins(tuple(skill for skill, _governance in selected))
        return [
            DashboardManagedSkill(
                artifact=skill.as_ref(),
                content=skill.content,
                sources=skill.lineage.sources,
                artifacts=skill.lineage.artifacts,
                governance=governance,
                origin=origin,
            )
            for (skill, governance), origin in zip(selected, origins, strict=True)
        ]

    async def update_skill_lifecycle(
        request: DashboardSkillLifecycleRequest,
        http_request: Request,
    ) -> ArtifactGovernance | JSONResponse:
        if request.scope_id not in dashboard_scope_ids:
            return _web_error(404, "dashboard_scope_not_found", "The Dashboard scope was not found.")
        application = http_request.app.state.application
        if application is None:
            return _web_error(503, "runtime_not_ready", "The Runtime is not ready.")
        try:
            return await application.skill.for_scope(request.scope_id).update_lifecycle(
                request.artifact_id,
                request.expected_generation,
                request.lifecycle_state,
                request.replacement_artifact_id,
            )
        except ValueError as error:
            return _web_error(
                422,
                "skill_lifecycle_invalid",
                "The requested Skill lifecycle transition is not allowed.",
                details={"reason": str(error)},
            )

    async def get_package_manifest(
        request: DashboardSkillPackageRequest,
        http_request: Request,
    ) -> SkillPackageManifest | JSONResponse:
        resolved = await _dashboard_package(http_request, request, dashboard_scope_ids)
        if isinstance(resolved, JSONResponse):
            return resolved
        return _dashboard_package_manifest(resolved)

    async def preview_package_file(
        request: DashboardSkillPackageFileRequest,
        http_request: Request,
    ) -> DashboardSkillPackageFilePreview | JSONResponse:
        resolved = await _dashboard_package(http_request, request, dashboard_scope_ids)
        if isinstance(resolved, JSONResponse):
            return resolved
        entry = next((entry for entry in resolved.entries if entry.path == request.path), None)
        if entry is None:
            return _web_error(404, "skill_package_file_not_found", "The package file was not found.")
        content = package_file(resolved, request.path)
        bounded = content[: 64 * 1024]
        try:
            preview = bounded.decode("utf-8")
        except UnicodeDecodeError:
            preview = None
        return DashboardSkillPackageFilePreview(
            path=entry.path,
            media_type=entry.media_type,
            content=preview,
            binary=preview is None,
            truncated=len(content) > len(bounded),
        )

    if dashboard_enabled:
        router.add_api_route(
            "/",
            dashboard_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="dashboard_home",
        )
        router.add_api_route(
            "/dashboard/scopes",
            list_dashboard_scopes,
            methods=["GET"],
            response_model=list[DashboardScope],
            name="dashboard_scopes",
        )
        router.add_api_route(
            "/skills",
            skills_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="skills_library",
        )
        router.add_api_route(
            "/dashboard/skills/library",
            list_managed_skills,
            methods=["POST"],
            response_model=list[DashboardManagedSkill],
            name="dashboard_skills_library_data",
        )
        router.add_api_route(
            "/dashboard/skills/lifecycle",
            update_skill_lifecycle,
            methods=["POST"],
            response_model=ArtifactGovernance,
            name="dashboard_skill_lifecycle",
        )
        router.add_api_route(
            "/dashboard/skill-packages/manifest",
            get_package_manifest,
            methods=["POST"],
            response_model=SkillPackageManifest,
            name="dashboard_skill_package_manifest",
        )
        router.add_api_route(
            "/dashboard/skill-packages/preview",
            preview_package_file,
            methods=["POST"],
            response_model=DashboardSkillPackageFilePreview,
            name="dashboard_skill_package_preview",
        )
        router.add_api_route(
            "/reviews",
            review_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="review_inbox",
        )
        router.add_api_route(
            "/dashboard/skill-projections/status",
            skill_projection_routes.inspect,
            methods=["POST"],
            response_model=DashboardSkillProjection,
            name="dashboard_skill_projection_status",
        )
        router.add_api_route(
            "/dashboard/skill-projections/publish",
            skill_projection_routes.publish,
            methods=["POST"],
            response_model=DashboardSkillProjection,
            name="dashboard_skill_projection_publish",
        )
        router.add_api_route(
            "/dashboard/skill-projections/unpublish",
            skill_projection_routes.unpublish,
            methods=["POST"],
            response_model=DashboardSkillProjection,
            name="dashboard_skill_projection_unpublish",
        )
    if handoff_report_enabled:
        router.add_api_route(
            "/handoff-reports",
            handoff_report_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="handoff_report_dashboard",
        )

    app.mount(
        "/static",
        static_files,
        name="web_static",
    )
    app.include_router(router)


@cache
def _templates() -> Jinja2Templates:
    environment = Environment(
        loader=PackageLoader("powercontext.server"),
        autoescape=select_autoescape(),
    )
    return Jinja2Templates(env=environment)


async def _dashboard_managed_skill(
    request: Request,
    selection: DashboardSkillProjectionRequest,
    dashboard_scope_ids: frozenset[str],
):
    if selection.scope_id not in dashboard_scope_ids:
        return _web_error(404, "dashboard_scope_not_found", "The Dashboard scope was not found.")
    application = request.app.state.application
    if application is None:
        return _web_error(503, "runtime_not_ready", "The Runtime is not ready.")
    try:
        skill = await application.skill.for_scope(selection.scope_id).get(GetSkillRequest(artifact=selection.artifact))
    except ArtifactNotFoundError:
        return _web_error(
            409,
            "skill_projection_not_approved",
            "The selected Artifact is not an exact approved managed Skill Revision.",
        )
    return application, skill


async def _skill_projection_response(
    application,
    scope_id: str,
    skill,
    targets_config: tuple[AgentSkillTarget, ...],
) -> DashboardSkillProjection:
    if not skill.content.package_backed:
        return DashboardSkillProjection(
            artifact=skill.as_ref(),
            name=skill.content.name,
            blocker="standard_package_required",
            targets=[],
        )
    if not targets_config:
        return DashboardSkillProjection(artifact=skill.as_ref(), name=skill.content.name, targets=[])
    # Registry discovery is best-effort bookkeeping; when it cannot be read (for example an
    # unavailable registry database), report on-disk state with stale discovery instead of
    # failing the whole response after the projection was already changed.
    try:
        registrations = await application.external_skills.for_scope(scope_id).list(
            ListExternalSkillsRequest(include_unavailable=True)
        )
    except Exception as error:
        log_safely(logger, logging.WARNING, "PowerContext external Skill registry discovery failed", exc_info=error)
        registrations = ()
    targets = []
    package = await application.skill.for_scope(scope_id).package(skill.as_ref())
    for target in targets_config:
        status = await application.skill.for_scope(scope_id).inspect_publication(skill.as_ref(), target)
        compatibility = assess_skill_compatibility(skill.content, package, target)
        registration = next(
            (
                item
                for item in registrations
                if item.status is ExternalSkillResolutionStatus.AVAILABLE
                and item.registration.agent_kind == target.agent_kind
                and item.registration.locator == str(status.destination)
            ),
            None,
        )
        if status.state is AgentSkillProjectionState.CURRENT:
            discovery = "available" if registration is not None else "unavailable"
        else:
            discovery = "not_published"
        targets.append(
            DashboardSkillProjectionTarget(
                target_id=target.target_id,
                agent_kind=target.agent_kind,
                installation_scope=target.installation_scope,
                destination=str(status.destination),
                state=status.state,
                published_revision=(None if status.published_artifact is None else status.published_artifact.revision),
                reason=status.reason,
                discovery=discovery,
                external_skill_id=(None if registration is None else registration.registration.external_skill_id),
                generation=status.generation,
                tree_digest=status.published_tree_digest,
                compatibility=compatibility.state,
                compatibility_reasons=compatibility.reasons,
                environment_fingerprint=compatibility.environment_fingerprint,
            )
        )
    return DashboardSkillProjection(artifact=skill.as_ref(), name=skill.content.name, targets=targets)


def _web_error(
    response_status: int,
    code: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    error = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=response_status, content=error.model_dump(mode="json"))


def _skill_library_search_text(content: SkillContent) -> str:
    return "\n".join((content.name, content.description, content.instructions, *content.metadata.values()))


async def _dashboard_package(
    request: Request,
    selection: DashboardSkillPackageRequest,
    dashboard_scope_ids: frozenset[str],
) -> SkillPackageSnapshot | JSONResponse:
    if selection.scope_id not in dashboard_scope_ids:
        return _web_error(404, "dashboard_scope_not_found", "The Dashboard scope was not found.")
    application = request.app.state.application
    if application is None:
        return _web_error(503, "runtime_not_ready", "The Runtime is not ready.")
    return await application.skill.for_scope(selection.scope_id).package_snapshot(selection.package)


def _dashboard_package_manifest(package: SkillPackageSnapshot) -> SkillPackageManifest:
    return SkillPackageManifest(
        package=SkillPackageReference.model_validate(package.reference.model_dump()),
        name=package.metadata.name,
        description=package.metadata.description,
        license=package.metadata.license,
        compatibility=package.metadata.compatibility,
        metadata=package.metadata.metadata,
        allowed_tools=package.metadata.allowed_tools,
        files=[
            SkillPackageFile(
                path=entry.path,
                digest=entry.digest,
                size=entry.size,
                media_type=entry.media_type,
                executable=bool(entry.mode & 0o111),
            )
            for entry in package.entries
        ],
    )


__all__ = [
    "DashboardScope",
    "DashboardSkillPackageFilePreview",
    "DashboardSkillPackageFileRequest",
    "DashboardSkillPackageRequest",
    "DashboardSkillProjection",
    "DashboardSkillProjectionRequest",
    "DashboardSkillProjectionTarget",
    "DashboardSkillPublishRequest",
    "DashboardSkillUnpublishRequest",
    "mount_web_ui",
]
