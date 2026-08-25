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

import asyncio
from collections.abc import Mapping
from functools import cache
from typing import Literal

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import AgentKind, AgentSkillTarget, ExternalSkillResolutionStatus
from powercontext.builtin.artifacts.skill.projection import (
    AgentSkillProjectionConflictError,
    AgentSkillProjectionState,
    inspect_skill_projection,
    publish_skill_projection,
)
from powercontext.builtin.review import CandidateStatus
from powercontext.builtin.runtime import GetArtifactCandidateRequest, GetSkillRequest, ListExternalSkillsRequest
from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH

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
    candidate_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    artifact: ArtifactRef

    @model_validator(mode="after")
    def require_skill_artifact(self) -> DashboardSkillProjectionRequest:
        if self.artifact.family != "skill":
            raise ValueError("artifact must identify a managed Skill")  # noqa: TRY003
        return self


class DashboardSkillPublishRequest(DashboardSkillProjectionRequest):
    """Explicitly publish one exact approved managed Skill Revision."""

    target_id: str = Field(min_length=1, max_length=64)


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


class DashboardSkillProjection(BaseModel):
    """Publication state for one exact approved managed Skill Revision."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    name: str
    targets: list[DashboardSkillProjectionTarget]


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
        expected = await asyncio.to_thread(inspect_skill_projection, skill.as_ref(), skill.content, target)
        try:
            await asyncio.to_thread(
                publish_skill_projection,
                skill.as_ref(),
                skill.content,
                target,
                expected=expected,
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
        await application.external_skills.for_scope(request.scope_id).scan()
        return await _skill_projection_response(application, request.scope_id, skill, self._targets)


def mount_web_ui(
    app: FastAPI,
    *,
    scopes: Mapping[str, str],
    dashboard_enabled: bool = False,
    handoff_report_enabled: bool = False,
    authentication_required: bool = False,
    agent_skill_targets: tuple[AgentSkillTarget, ...] = (),
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
    candidate = await application.review.for_scope(selection.scope_id).get(
        GetArtifactCandidateRequest(candidate_id=selection.candidate_id)
    )
    if (
        candidate.family != "skill"
        or candidate.status is not CandidateStatus.APPROVED
        or candidate.result_artifact != selection.artifact
    ):
        return _web_error(
            409,
            "skill_projection_not_approved",
            "The selected Artifact is not the exact approved result of this Skill Candidate.",
        )
    skill = await application.skill.for_scope(selection.scope_id).get(GetSkillRequest(artifact=selection.artifact))
    return application, skill


async def _skill_projection_response(
    application,
    scope_id: str,
    skill,
    targets_config: tuple[AgentSkillTarget, ...],
) -> DashboardSkillProjection:
    registrations = (
        ()
        if not targets_config
        else await application.external_skills.for_scope(scope_id).list(
            ListExternalSkillsRequest(include_unavailable=True)
        )
    )
    targets = []
    for target in targets_config:
        status = await asyncio.to_thread(inspect_skill_projection, skill.as_ref(), skill.content, target)
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


__all__ = [
    "DashboardScope",
    "DashboardSkillProjection",
    "DashboardSkillProjectionRequest",
    "DashboardSkillProjectionTarget",
    "DashboardSkillPublishRequest",
    "mount_web_ui",
]
