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

# ruff: noqa: RUF001
"""Interactive MCP selection for one Handoff Report Workstream."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Generic, Literal, TypeVar, cast

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.elicitation import AcceptedElicitation
from mcp.types import ClientCapabilities, ElicitationCapability, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from powercontext.client import PowerContextClient
from powercontext.http import (
    ListHandoffReportProjectsRequest,
    ListHandoffReportWorkstreamsRequest,
    ProjectDescriptor,
    WorkstreamDescriptor,
)

PickerLocale = Literal["zh-CN", "en"]
PickerStatus = Literal["selected", "needs_selection", "empty", "cancelled", "declined"]
PickerStage = Literal["project", "workstream"]

_MAX_PICKER_CHOICES = 100
_FORM_ELICITATION_CAPABILITY = ClientCapabilities(elicitation=ElicitationCapability())
_ChoiceT = TypeVar("_ChoiceT")


class HandoffProjectChoice(BaseModel):
    """One project that can be used to narrow the Workstream picker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    project_key: str
    title: str


class HandoffWorkstreamChoice(BaseModel):
    """One validated Workstream selection returned to an Agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    work_id: str
    scope_id: str
    project_id: str
    project_key: str
    title: str
    kind: str
    catalog_version: int


class HandoffWorkstreamSelection(BaseModel):
    """Stable result for native and text-fallback selection flows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PickerStatus
    message: str
    stage: PickerStage | None = None
    selected: HandoffWorkstreamChoice | None = None
    project_choices: list[HandoffProjectChoice] = Field(default_factory=list, max_length=_MAX_PICKER_CHOICES)
    workstream_choices: list[HandoffWorkstreamChoice] = Field(default_factory=list, max_length=_MAX_PICKER_CHOICES)
    truncated: bool = False


def register_handoff_workstream_picker(server: FastMCP, http_client: httpx.AsyncClient) -> None:
    """Register the Report-backed Workstream picker on an existing MCP server."""

    # ``http_client`` is the Server's own in-process ASGI transport; ``http://fastapi`` is only a
    # routing label, so vouch for it explicitly rather than have the loopback guard reject it.
    picker = _HandoffWorkstreamPicker(
        PowerContextClient("http://fastapi", http_client=http_client, trust_transport_security=True)
    )
    server.tool(
        picker.select,
        name="select_handoff_workstream",
        title="Select Handoff Workstream",
        description=(
            "Select one Report Workstream before handoff_current_work or continue_handoff. "
            "Uses a native MCP picker when supported and returns validated structured choices otherwise."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )


class _HandoffWorkstreamPicker:
    def __init__(self, client: PowerContextClient) -> None:
        self._client = client

    async def select(
        self,
        ctx: Context,
        project_id: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=256,
                pattern=r".*\S.*",
                description="Exact Report Project ID. Omit it to choose a Project interactively.",
            ),
        ] = None,
        work_id: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=256,
                pattern=r".*\S.*",
                description="Workstream key or scope ID returned by an earlier picker result.",
            ),
        ] = None,
        query: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=256,
                pattern=r".*\S.*",
                description="Optional case-insensitive filter over Workstream title, key, scope, kind, and labels.",
            ),
        ] = None,
        include_archived: Annotated[
            bool,
            Field(description="Include archived Projects and Workstreams in the choices."),
        ] = False,
        locale: Annotated[
            PickerLocale,
            Field(description="Language used for picker prompts and result messages."),
        ] = "zh-CN",
    ) -> HandoffWorkstreamSelection:
        """Select a catalog Workstream before handing off or continuing work.

        A client with MCP form elicitation gets a native picker. Other clients
        receive structured choices and can call this tool again with project_id
        and work_id. Selecting a Workstream never creates or commits a Handoff.
        """

        projects, projects_truncated = await _list_projects(self._client, include_archived=include_archived)
        if not projects:
            return _selection(status="empty", locale=locale, message_key="no_projects")

        project_result = await _choose_project(
            ctx,
            projects=projects,
            project_id=project_id,
            locale=locale,
            truncated=projects_truncated,
        )
        if isinstance(project_result, HandoffWorkstreamSelection):
            return project_result
        project = project_result

        workstreams, workstreams_truncated = await _list_workstreams(
            self._client,
            project.project_id,
            include_archived=include_archived,
        )
        filtered_workstreams = _filter_workstreams(workstreams, query)
        choices = [_workstream_choice(item, project) for item in filtered_workstreams]
        truncated = projects_truncated or workstreams_truncated
        if not choices:
            return _selection(
                status="empty",
                locale=locale,
                message_key="no_matching_workstreams" if query else "no_workstreams",
                stage="workstream",
                truncated=truncated,
            )

        return await _choose_workstream(
            ctx,
            choices=choices,
            work_id=work_id,
            locale=locale,
            truncated=truncated,
        )


@dataclass(frozen=True, slots=True)
class _ElicitedChoice(Generic[_ChoiceT]):
    status: Literal["selected", "cancelled", "declined"]
    value: _ChoiceT | None


async def _elicit_choice(
    ctx: Context,
    *,
    message: str,
    response_title: str,
    values: Sequence[_ChoiceT],
    title: Callable[[_ChoiceT], str],
) -> _ElicitedChoice[_ChoiceT]:
    option_values = {f"option-{index + 1}": value for index, value in enumerate(values)}
    options = {token: {"title": title(value)} for token, value in option_values.items()}
    result = await ctx.elicit(
        message,
        options,
        response_title=response_title,
    )
    if not isinstance(result, AcceptedElicitation):
        status: Literal["cancelled", "declined"] = "cancelled" if result.action == "cancel" else "declined"
        return _ElicitedChoice(status=status, value=None)
    selected_token = str(result.data)
    return _ElicitedChoice(status="selected", value=option_values[selected_token])


async def _choose_project(
    ctx: Context,
    *,
    projects: Sequence[ProjectDescriptor],
    project_id: str | None,
    locale: PickerLocale,
    truncated: bool,
) -> ProjectDescriptor | HandoffWorkstreamSelection:
    project = _project_by_id(projects, project_id)
    if project_id is not None and project is None:
        return _selection(
            status="needs_selection",
            locale=locale,
            message_key="project_not_found",
            stage="project",
            project_choices=[_project_choice(item) for item in projects],
            truncated=truncated,
        )
    if project is not None:
        return project
    if len(projects) == 1:
        return projects[0]
    if not _supports_form_elicitation(ctx):
        return _selection(
            status="needs_selection",
            locale=locale,
            message_key="choose_project_fallback",
            stage="project",
            project_choices=[_project_choice(item) for item in projects],
            truncated=truncated,
        )
    result = await _elicit_choice(
        ctx,
        message=_copy(locale, "choose_project"),
        response_title=_copy(locale, "project_field"),
        values=projects,
        title=_project_title,
    )
    if result.status != "selected":
        return _selection(
            status=result.status,
            locale=locale,
            message_key=result.status,
            stage="project",
        )
    return cast(ProjectDescriptor, result.value)


async def _choose_workstream(
    ctx: Context,
    *,
    choices: Sequence[HandoffWorkstreamChoice],
    work_id: str | None,
    locale: PickerLocale,
    truncated: bool,
) -> HandoffWorkstreamSelection:
    selected = _workstream_by_id(choices, work_id)
    if work_id is not None and selected is None:
        return _selection(
            status="needs_selection",
            locale=locale,
            message_key="workstream_not_found",
            stage="workstream",
            workstream_choices=list(choices),
            truncated=truncated,
        )
    if selected is None and len(choices) == 1:
        selected = choices[0]
    if selected is None and not _supports_form_elicitation(ctx):
        return _selection(
            status="needs_selection",
            locale=locale,
            message_key="choose_workstream_fallback",
            stage="workstream",
            workstream_choices=list(choices),
            truncated=truncated,
        )
    if selected is None:
        result = await _elicit_choice(
            ctx,
            message=_copy(locale, "choose_workstream"),
            response_title=_copy(locale, "workstream_field"),
            values=choices,
            title=_workstream_title,
        )
        if result.status != "selected":
            return _selection(
                status=result.status,
                locale=locale,
                message_key=result.status,
                stage="workstream",
            )
        selected = cast(HandoffWorkstreamChoice, result.value)
    return _selection(
        status="selected",
        locale=locale,
        message_key="selected",
        selected=selected,
        truncated=truncated,
    )


async def _list_projects(
    client: PowerContextClient,
    *,
    include_archived: bool,
) -> tuple[list[ProjectDescriptor], bool]:
    page = await client.list_handoff_report_projects(
        ListHandoffReportProjectsRequest(
            limit=_MAX_PICKER_CHOICES,
            include_archived=include_archived,
        )
    )
    return page.items, page.next_cursor is not None


async def _list_workstreams(
    client: PowerContextClient,
    project_id: str,
    *,
    include_archived: bool,
) -> tuple[list[WorkstreamDescriptor], bool]:
    page = await client.list_handoff_report_workstreams(
        ListHandoffReportWorkstreamsRequest(
            project_id=project_id,
            limit=_MAX_PICKER_CHOICES,
            include_archived=include_archived,
        )
    )
    return page.items, page.next_cursor is not None


def _supports_form_elicitation(ctx: Context) -> bool:
    return ctx.session.check_client_capability(_FORM_ELICITATION_CAPABILITY)


def _project_by_id(projects: Sequence[ProjectDescriptor], project_id: str | None) -> ProjectDescriptor | None:
    if project_id is None:
        return None
    return next((project for project in projects if project.project_id == project_id), None)


def _workstream_by_id(
    choices: Sequence[HandoffWorkstreamChoice],
    work_id: str | None,
) -> HandoffWorkstreamChoice | None:
    if work_id is None:
        return None
    matches = [choice for choice in choices if choice.work_id == work_id or choice.scope_id == work_id]
    return matches[0] if len(matches) == 1 else None


def _filter_workstreams(
    workstreams: Sequence[WorkstreamDescriptor],
    query: str | None,
) -> list[WorkstreamDescriptor]:
    normalized_query = "" if query is None else query.strip().casefold()
    if not normalized_query:
        return list(workstreams)
    return [workstream for workstream in workstreams if normalized_query in _workstream_search_text(workstream)]


def _workstream_search_text(workstream: WorkstreamDescriptor) -> str:
    values = (
        workstream.title,
        workstream.key or "",
        workstream.scope_id,
        str(workstream.kind),
        *(label.root for label in workstream.labels),
    )
    return "\n".join(values).casefold()


def _project_choice(project: ProjectDescriptor) -> HandoffProjectChoice:
    return HandoffProjectChoice(
        project_id=project.project_id,
        project_key=project.project_key,
        title=project.title,
    )


def _workstream_choice(
    workstream: WorkstreamDescriptor,
    project: ProjectDescriptor,
) -> HandoffWorkstreamChoice:
    return HandoffWorkstreamChoice(
        work_id=workstream.key or workstream.scope_id,
        scope_id=workstream.scope_id,
        project_id=project.project_id,
        project_key=project.project_key,
        title=workstream.title,
        kind=str(workstream.kind),
        catalog_version=workstream.version,
    )


def _project_title(project: ProjectDescriptor) -> str:
    return f"{project.title} · {project.project_key}"


def _workstream_title(workstream: HandoffWorkstreamChoice) -> str:
    return f"{workstream.title} · {workstream.work_id} · {workstream.kind}"


def _selection(
    *,
    status: PickerStatus,
    locale: PickerLocale,
    message_key: str,
    stage: PickerStage | None = None,
    selected: HandoffWorkstreamChoice | None = None,
    project_choices: list[HandoffProjectChoice] | None = None,
    workstream_choices: list[HandoffWorkstreamChoice] | None = None,
    truncated: bool = False,
) -> HandoffWorkstreamSelection:
    return HandoffWorkstreamSelection(
        status=status,
        message=_copy(locale, message_key),
        stage=stage,
        selected=selected,
        project_choices=[] if project_choices is None else project_choices,
        workstream_choices=[] if workstream_choices is None else workstream_choices,
        truncated=truncated,
    )


def _copy(locale: PickerLocale, key: str) -> str:
    return _COPY[locale][key]


_COPY: dict[PickerLocale, dict[str, str]] = {
    "zh-CN": {
        "cancelled": "已取消工作选择，未产生任何交接写入。",
        "choose_project": "选择这次交接所属的项目。",
        "choose_project_fallback": "当前客户端不支持原生选择框，请从 project_choices 选择并重新调用。",
        "choose_workstream": "选择要交接或继续的工作。",
        "choose_workstream_fallback": "当前客户端不支持原生选择框，请从 workstream_choices 选择并重新调用。",
        "declined": "已拒绝工作选择，未产生任何交接写入。",
        "no_matching_workstreams": "没有与查询条件匹配的工作。",
        "no_projects": "没有可供选择的交接项目。",
        "no_workstreams": "所选项目中没有可供选择的工作。",
        "project_field": "项目",
        "project_not_found": "找不到指定项目，请从 project_choices 重新选择。",
        "selected": "已选择工作；此操作尚未创建或提交交接。",
        "workstream_field": "工作",
        "workstream_not_found": "找不到指定工作，请从 workstream_choices 重新选择。",
    },
    "en": {
        "cancelled": "Work selection was cancelled; no Handoff data was written.",
        "choose_project": "Choose the Project that owns this Handoff.",
        "choose_project_fallback": "This client has no native picker; choose from project_choices and call again.",
        "choose_workstream": "Choose the work to hand off or continue.",
        "choose_workstream_fallback": (
            "This client has no native picker; choose from workstream_choices and call again."
        ),
        "declined": "Work selection was declined; no Handoff data was written.",
        "no_matching_workstreams": "No work matches the query.",
        "no_projects": "No Handoff Projects are available.",
        "no_workstreams": "The selected Project has no available work.",
        "project_field": "Project",
        "project_not_found": "The requested Project was not found; choose from project_choices.",
        "selected": "Work selected; this operation has not created or committed a Handoff.",
        "workstream_field": "Work",
        "workstream_not_found": "The requested work was not found; choose from workstream_choices.",
    },
}


__all__ = [
    "HandoffProjectChoice",
    "HandoffWorkstreamChoice",
    "HandoffWorkstreamSelection",
    "register_handoff_workstream_picker",
]
