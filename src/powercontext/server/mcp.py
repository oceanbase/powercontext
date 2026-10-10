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

"""MCP transport owned and configured by the PowerContext Server."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import (
    MCPType,
    OpenAPIProvider,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
)
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.lifespan import combine_lifespans
from fastmcp.utilities.openapi import HTTPRoute
from mcp.types import CallToolRequestParams, ToolAnnotations
from typing_extensions import override

from powercontext.http._generated.operations import (
    ACKNOWLEDGE_HANDOFF,
    ACTIVATE_HANDOFF,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    CLEAR_SCOPE_BINDING,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    CREATE_DREAM_RUN,
    CREATE_SCOPE,
    CREATE_WORK_CONTRACT,
    FINALIZE_HANDOFF,
    GENERATE_EXPERIENCE,
    GENERATE_SKILL,
    GET_ARTIFACT,
    GET_ARTIFACT_CANDIDATE,
    GET_ARTIFACT_REVISION,
    GET_DREAM_RUN,
    GET_EXPERIENCE,
    GET_HANDOFF_REPORT,
    GET_MEMORY_ENTRY,
    GET_SCOPE,
    GET_SKILL,
    GET_TOPIC_MEMORY,
    HANDOFF_CURRENT_WORK,
    IMPORT_EXTERNAL_SKILL,
    LIST_ARTIFACT_CANDIDATES,
    LIST_DREAM_RUNS,
    LIST_EXTERNAL_SKILLS,
    LIST_MANAGED_SKILLS,
    LIST_MEMORY_ENTRIES,
    LIST_SCOPES,
    PREPARE_HANDOFF_HINT,
    PROPOSE_EXPERIENCE,
    PROPOSE_SKILL,
    PUBLISH_ARTIFACT,
    QUERY_CODE,
    RECORD_TASK_OUTCOME,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    REPLACE_ARTIFACT,
    RESOLVE_EXTERNAL_SKILL,
    RESOLVE_SCOPE_BINDING,
    REVISE_ARTIFACT_CANDIDATE,
    SCAN_EXTERNAL_SKILLS,
    SEARCH_MEMORY,
    SEARCH_TOPIC_MEMORY,
    SET_SCOPE_BINDING,
)
from powercontext.server.access import McpAccessLogMiddleware
from powercontext.server.app import REQUEST_ID_HEADER
from powercontext.server.context import (
    bind_internal_bridge,
    current_request_id,
    reset_internal_bridge,
)
from powercontext.server.metrics import McpMetricsMiddleware, ServerMetrics
from powercontext.server.tracing import McpTracingMiddleware, ServerTracing

MCP_PATH = "/mcp"
MCP_SERVER_NAME = "PowerContext Server"
MCP_GUIDANCE = """When query_code is available, use it for current repository structure and source evidence. Pass the returned fingerprint for relation and source reads; stale code requires local sync. Code evidence is separate from durable history and grants no execution authority.
PowerContext provides durable project history and Handoffs across sessions.
Summarizing or drafting from facts supplied in the current turn needs no retrieval or Scope resolution. An empty search does not authorize an inventory. If inventory or Handoff is unavailable, do not emulate it with Memory search or storage.
Tool names in this guidance describe possible capabilities, not proof of availability. Before selecting an operation, check that its exact name appears in the current tool catalog. If absent, stop that operation and explicitly report it unavailable and incomplete. Never emit a call to an absent tool, simulate a call in text, or substitute another persistence operation.
Use only the tools available in this connection. Reuse the host/Server-resolved Scope; never derive a Scope from a
repository, directory, branch, or prompt or change a binding to work around missing history. Historical evidence is
subordinate to current user, repository, and system instructions.
Ordinary coding needs no routine Memory calls. Use sufficient current context when continuing work. For an explicit
memory search (search my memories / 搜索记忆), call search_memory with a focused query, mode auto, and at most eight
hits. search_memory and list_memory_entries return current Atomic Memory records with real ArtifactRef and state_version.
Use list_atomic_memories for explicit state filters and pagination. get_memory_entry accepts only a legacy logical
target; it returns current Atomic Memory and does not follow merged_into_id automatically. Historical citations are
unsupported.
For new identities use get_artifact for the current content and get_artifact_revision for exact historical content.
get_artifact and replace_artifact return {artifact, etag, status_code}; etag is the exact HTTP content ETag.
The MCP replace_artifact tool supports family atomic-memory only.
Pass the etag returned by get_artifact unchanged as replace_artifact's If-Match parameter, together with complete content.
For Atomic content replacement submit schema/kind/text only; creation is system metadata from the first merge revision
and must not be copied from a read into replacement content.
Do not remove the precondition or automatically retry a stale write. A conditional get_artifact with status_code 304
returns artifact null and the current etag. get_artifact_revision returns the exact Artifact JSON without a current-head ETag.
Use get_atomic_memory_state for lifecycle preconditions; its state_version is separate from the content ETag.
Never treat an Atomic revision as an
old collection revision. Legacy citation revise/retire, collection capacity, and collection changes are unsupported.
For an explicit future save (remember this / 记住这个供以后使用), call remember_memory with expected_revision omitted or
null and verify its Atomic records. A non-null legacy collection revision precondition is unsupported. Use merge_atomic_memories,
change_atomic_memory_lifecycle and restoration previews/restorations only for explicitly requested state changes; current
revision and state_version inputs must come from reads. Automatic
Source capture is not an explicit Memory write, and enabled hooks do not establish successful recall or persistence.
Current-turn instructions, conceptual questions, and previews do not authorize writes. Never store secrets.
For requested transfer, handoff_current_work records an inspected boundary and returns a temporary handoff. Commit
only when a durable milestone is requested; continue from the exact selected value and verify historical claims.
Prepared content is not proof of injection, a committed milestone, acceptance, or work execution.
When available, prepare_handoff_hint provides optional bounded historical orientation for the selected Handoff.
Hints never replace the complete Handoff, evidence checks, current instructions, or live validation.
For requested Experience or Skill synthesis use generate_experience or generate_skill; caller-authored content uses
propose_experience or propose_skill. These create pending candidates, not approved artifacts. Read an exact approved
revision with get_experience or get_skill; use list_managed_skills to discover approved Skills.
For external Skills, scan_external_skills refreshes configured Server-host roots; list_external_skills and
resolve_external_skill inspect exact host-local fingerprints. import_external_skill creates a pending candidate.
A remote Server cannot scan the Codex workstation. Resolution is not installation or execution permission.
Inspect candidates before an explicitly authorized review decision for their exact version. Generation, listing,
reading, and assessing are not approval, installation, publication, or execution authority. Preserve host approval
checks and exact Atomic revisions/state versions for Memory changes. A Skill is useful for detailed workflows only if present in the host
catalog; it is not a mandatory detour before every response.
Empty retrieval is a valid result. On failure identify the operation and safe returned reason, do not infer a cause,
claim saved/restored context, or repeatedly retry. Continue ordinary work when the requested operation is unavailable.
"""
_MCP_OPERATION_IDS = frozenset({
    "get_atomic_memory_state",
    "list_atomic_memories",
    "search_atomic_memory",
    "merge_atomic_memories",
    "change_atomic_memory_lifecycle",
    "preview_atomic_memory_restoration",
    "restore_atomic_memory",
    GET_ARTIFACT.operation_id,
    GET_ARTIFACT_REVISION.operation_id,
    REPLACE_ARTIFACT.operation_id,
    GENERATE_EXPERIENCE.operation_id,
    GET_EXPERIENCE.operation_id,
    PROPOSE_EXPERIENCE.operation_id,
    GENERATE_SKILL.operation_id,
    GET_SKILL.operation_id,
    PROPOSE_SKILL.operation_id,
    LIST_MANAGED_SKILLS.operation_id,
    SCAN_EXTERNAL_SKILLS.operation_id,
    LIST_EXTERNAL_SKILLS.operation_id,
    RESOLVE_EXTERNAL_SKILL.operation_id,
    IMPORT_EXTERNAL_SKILL.operation_id,
    CREATE_DREAM_RUN.operation_id,
    GET_DREAM_RUN.operation_id,
    LIST_DREAM_RUNS.operation_id,
    CAPTURE_CONTENT_SOURCE.operation_id,
    CREATE_WORK_CONTRACT.operation_id,
    HANDOFF_CURRENT_WORK.operation_id,
    ACKNOWLEDGE_HANDOFF.operation_id,
    RECORD_TASK_OUTCOME.operation_id,
    ACTIVATE_HANDOFF.operation_id,
    FINALIZE_HANDOFF.operation_id,
    COMMIT_HANDOFF.operation_id,
    CONTINUE_HANDOFF.operation_id,
    PREPARE_HANDOFF_HINT.operation_id,
    SEARCH_MEMORY.operation_id,
    QUERY_CODE.operation_id,
    SEARCH_TOPIC_MEMORY.operation_id,
    GET_TOPIC_MEMORY.operation_id,
    LIST_MEMORY_ENTRIES.operation_id,
    GET_MEMORY_ENTRY.operation_id,
    REMEMBER_MEMORY.operation_id,
    GET_HANDOFF_REPORT.operation_id,
    LIST_ARTIFACT_CANDIDATES.operation_id,
    GET_ARTIFACT_CANDIDATE.operation_id,
    APPROVE_ARTIFACT_CANDIDATE.operation_id,
    REJECT_ARTIFACT_CANDIDATE.operation_id,
    REVISE_ARTIFACT_CANDIDATE.operation_id,
    CREATE_SCOPE.operation_id,
    LIST_SCOPES.operation_id,
    GET_SCOPE.operation_id,
    RESOLVE_SCOPE_BINDING.operation_id,
    SET_SCOPE_BINDING.operation_id,
    CLEAR_SCOPE_BINDING.operation_id,
    PUBLISH_ARTIFACT.operation_id,
})
_MCP_READ_ONLY_OPERATION_IDS = frozenset({
    "get_atomic_memory_state",
    "list_atomic_memories",
    "search_atomic_memory",
    "preview_atomic_memory_restoration",
    GET_ARTIFACT.operation_id,
    GET_ARTIFACT_REVISION.operation_id,
    GET_EXPERIENCE.operation_id,
    GET_SKILL.operation_id,
    LIST_MANAGED_SKILLS.operation_id,
    LIST_EXTERNAL_SKILLS.operation_id,
    RESOLVE_EXTERNAL_SKILL.operation_id,
    GET_DREAM_RUN.operation_id,
    LIST_DREAM_RUNS.operation_id,
    CONTINUE_HANDOFF.operation_id,
    PREPARE_HANDOFF_HINT.operation_id,
    SEARCH_MEMORY.operation_id,
    QUERY_CODE.operation_id,
    SEARCH_TOPIC_MEMORY.operation_id,
    GET_TOPIC_MEMORY.operation_id,
    LIST_MEMORY_ENTRIES.operation_id,
    GET_MEMORY_ENTRY.operation_id,
    GET_HANDOFF_REPORT.operation_id,
    LIST_ARTIFACT_CANDIDATES.operation_id,
    GET_ARTIFACT_CANDIDATE.operation_id,
    LIST_SCOPES.operation_id,
    GET_SCOPE.operation_id,
    RESOLVE_SCOPE_BINDING.operation_id,
})
_MCP_CANDIDATE_WRITE_OPERATION_IDS = frozenset({
    GENERATE_EXPERIENCE.operation_id,
    PROPOSE_EXPERIENCE.operation_id,
    GENERATE_SKILL.operation_id,
    PROPOSE_SKILL.operation_id,
    IMPORT_EXTERNAL_SKILL.operation_id,
})
_MCP_EXTERNAL_SKILL_OPERATION_IDS = frozenset({
    SCAN_EXTERNAL_SKILLS.operation_id,
    LIST_EXTERNAL_SKILLS.operation_id,
    RESOLVE_EXTERNAL_SKILL.operation_id,
    IMPORT_EXTERNAL_SKILL.operation_id,
})
_MCP_REVIEW_WRITE_OPERATION_IDS = frozenset({
    APPROVE_ARTIFACT_CANDIDATE.operation_id,
    REJECT_ARTIFACT_CANDIDATE.operation_id,
    REVISE_ARTIFACT_CANDIDATE.operation_id,
})
_MCP_CONTENT_ETAG_OPERATION_IDS = frozenset({GET_ARTIFACT.operation_id, REPLACE_ARTIFACT.operation_id})


@dataclass
class _ArtifactHttpResponse:
    response: httpx.Response | None = None


_artifact_http_response: ContextVar[_ArtifactHttpResponse | None] = ContextVar(
    "powercontext_mcp_artifact_http_response", default=None
)


class _ArtifactContentEtagMiddleware(Middleware):
    """Preserve content validators lost by FastMCP's JSON-only OpenAPI projection."""

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if context.message.name not in _MCP_CONTENT_ETAG_OPERATION_IDS:
            return await call_next(context)
        if (
            context.message.name == REPLACE_ARTIFACT.operation_id
            and (context.message.arguments or {}).get("family") != "atomic-memory"
        ):
            message = "MCP replace_artifact supports family=atomic-memory only."
            raise ToolError(message)
        captured = _ArtifactHttpResponse()
        token = _artifact_http_response.set(captured)
        try:
            try:
                result = await call_next(context)
            except ToolError as error:
                # FastMCP wraps HTTP 304 in ValueError and then ToolError. Only
                # the captured GET's actual conditional response is a success.
                cause: BaseException | None = error
                while cause is not None:
                    if isinstance(cause, httpx.HTTPStatusError):
                        break
                    cause = cause.__cause__
                if not (
                    context.message.name == GET_ARTIFACT.operation_id
                    and isinstance(cause, httpx.HTTPStatusError)
                    and cause.response is captured.response
                    and cause.response.status_code == 304
                ):
                    raise
                result = None
            if result is not None and result.is_error:
                return result
            response = captured.response
            if response is None:
                message = "Artifact tool did not receive its HTTP response."
                raise ValueError(message)
            return ToolResult(
                structured_content={
                    "artifact": None if response.status_code == 304 else response.json(),
                    "etag": response.headers["ETag"],
                    "status_code": response.status_code,
                },
                meta=None if result is None else result.meta,
            )
        finally:
            _artifact_http_response.reset(token)


def _select_mcp_type(route: HTTPRoute, _: MCPType) -> MCPType:
    if route.operation_id in _MCP_OPERATION_IDS:
        return MCPType.TOOL
    return MCPType.EXCLUDE


def _resolve_openapi_schema(original: Mapping[str, Any], definitions: Mapping[str, Any]) -> Mapping[str, Any]:
    source_reference = original.get("$ref")
    if isinstance(source_reference, str) and source_reference.startswith("#/components/schemas/"):
        definition = definitions.get(source_reference.rsplit("/", 1)[-1])
        if isinstance(definition, Mapping):
            return {**definition, **{key: value for key, value in original.items() if key != "$ref"}}
    return original


def _allow_null(projected: dict[str, Any]) -> None:
    projected_type = projected.get("type")
    if isinstance(projected_type, str):
        projected["type"] = [projected_type, "null"]
    elif isinstance(projected_type, list) and "null" not in projected_type:
        projected["type"] = [*projected_type, "null"]
    else:
        for keyword in ("anyOf", "oneOf"):
            branches = projected.get(keyword)
            if isinstance(branches, list) and {"type": "null"} not in branches:
                branches.append({"type": "null"})
                break


def _preserve_nullable_input(
    projected: dict[str, Any],
    original: Mapping[str, Any],
    definitions: Mapping[str, Any],
    projected_definitions: Mapping[str, Any],
    visited: set[tuple[str, str]],
) -> None:
    """Keep OpenAPI 3.0 nullable values valid after FastMCP flattens request schemas."""

    source_reference = original.get("$ref")
    original = _resolve_openapi_schema(original, definitions)

    target_reference = projected.get("$ref")
    if isinstance(target_reference, str) and target_reference.startswith("#/$defs/"):
        if original.get("nullable") is True:
            projected.clear()
            projected["anyOf"] = [{"$ref": target_reference}, {"type": "null"}]
        key = (source_reference or "", target_reference)
        if key not in visited:
            visited.add(key)
            target = projected_definitions.get(target_reference.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                _preserve_nullable_input(
                    target,
                    {key: value for key, value in original.items() if key != "nullable"},
                    definitions,
                    projected_definitions,
                    visited,
                )
        return

    if original.get("nullable") is True:
        _allow_null(projected)

    properties = projected.get("properties")
    if isinstance(properties, dict):
        for name, source in original.get("properties", {}).items():
            target = properties.get(name)
            if isinstance(source, Mapping) and isinstance(target, dict):
                _preserve_nullable_input(target, source, definitions, projected_definitions, visited)

    source_items = original.get("items")
    target_items = projected.get("items")
    if isinstance(source_items, Mapping) and isinstance(target_items, dict):
        _preserve_nullable_input(target_items, source_items, definitions, projected_definitions, visited)


def _describe_artifact_content_tool(route: HTTPRoute, component: OpenAPITool) -> None:
    """Advertise the response envelope used to preserve HTTP content CAS."""

    if route.operation_id in _MCP_CONTENT_ETAG_OPERATION_IDS:
        artifact_schema = component.output_schema or {"type": "object", "additionalProperties": True}
        conditional = route.operation_id == GET_ARTIFACT.operation_id
        if not conditional:
            component.parameters["properties"]["family"] = {"type": "string", "enum": ["atomic-memory"]}
            component.description = "Replace Atomic Memory content. MCP supports family=atomic-memory only."
        component.output_schema = {
            "type": "object",
            "properties": {
                "artifact": {"anyOf": [artifact_schema, {"type": "null"}]} if conditional else artifact_schema,
                "etag": {"type": "string", "description": "Exact HTTP content ETag; pass unchanged as If-Match."},
                "status_code": {"type": "integer", "enum": [200, 304] if conditional else [200]},
            },
            "required": ["artifact", "etag", "status_code"],
            "additionalProperties": False,
        }
        component.description = (component.description or "") + (
            " MCP returns {artifact, etag, status_code}; artifact is the unchanged HTTP response JSON and etag"
            " is the exact HTTP content ETag, separate from Atomic state_version."
        )
        if conditional:
            component.description += (
                " Pass this etag unchanged to replace_artifact's If-Match parameter."
                " A conditional 304 returns artifact null with the current etag."
            )
        else:
            component.description += " Requires the current get_artifact etag as If-Match; stale writes are rejected."
            component.description += " For Atomic Memory submit content schema/kind/text only; do not copy creation, which is system metadata."
    elif route.operation_id == GET_ARTIFACT_REVISION.operation_id:
        component.description = (component.description or "") + (
            " MCP returns the exact HTTP Artifact JSON; this historical read has no current-head ETag."
        )


def _annotate_mcp_component(
    route: HTTPRoute,
    component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
    *,
    openapi_spec: Mapping[str, Any],
) -> None:
    """Describe the side effects that an MCP host should use for approval decisions."""

    if not isinstance(component, OpenAPITool):
        return
    operation = openapi_spec["paths"][route.path][route.method.lower()]
    request_schema = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if isinstance(request_schema, Mapping):
        _preserve_nullable_input(
            component.parameters,
            request_schema,
            openapi_spec["components"]["schemas"],
            component.parameters.get("$defs", {}),
            set(),
        )
    if route.operation_id == GET_HANDOFF_REPORT.operation_id:
        # This operation returns either a JSON object or Markdown text. MCP's
        # object output schema would require structured content for both formats.
        component.output_schema = None
    _describe_artifact_content_tool(route, component)
    if route.operation_id in _MCP_READ_ONLY_OPERATION_IDS:
        component.annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=route.operation_id in _MCP_EXTERNAL_SKILL_OPERATION_IDS,
        )
    elif (
        route.operation_id in _MCP_CANDIDATE_WRITE_OPERATION_IDS
        or route.operation_id == SCAN_EXTERNAL_SKILLS.operation_id
    ):
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=route.operation_id in _MCP_EXTERNAL_SKILL_OPERATION_IDS,
        )
    elif route.operation_id == HANDOFF_CURRENT_WORK.operation_id:
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
    elif route.operation_id in {COMMIT_HANDOFF.operation_id, CREATE_DREAM_RUN.operation_id}:
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    elif route.operation_id == REPLACE_ARTIFACT.operation_id:
        # Replaying the same content validator fails before another revision is
        # published. Hosts still decide whether the explicit replacement is authorized.
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )
    elif route.operation_id in _MCP_REVIEW_WRITE_OPERATION_IDS:
        # Approval and rejection are terminal; a revision replaces the proposal a reviewer last
        # inspected. MCP visibility is not an authorization boundary (RFC 0050), so these hints
        # only let a host apply its own confirmation policy. An exact replay is rejected by the
        # pending-head CAS before anything is written, so repeated identical calls have no
        # additional effect and the tools are idempotent in the MCP sense.
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )


def create_mcp_server(
    server_app: FastAPI,
    *,
    access_log: bool = False,
    metrics: ServerMetrics | None = None,
    tracing: ServerTracing | None = None,
) -> FastMCP:
    """Project the Agent-facing subset of a Server app into MCP components."""

    resolved_tracing = ServerTracing.context_only() if tracing is None else tracing
    client = httpx.AsyncClient(
        transport=_InternalBridgeTransport(app=server_app),
        base_url="http://fastapi",
    )
    openapi_spec = deepcopy(server_app.openapi())
    # MCP exposes Replace only for Atomic Memory. Specialize its input before
    # FastMCP builds the flattened parameter map: the HTTP union has no shared
    # top-level properties, so projecting it loses the request body entirely.
    replace_operation = openapi_spec["paths"][REPLACE_ARTIFACT.path][REPLACE_ARTIFACT.method.lower()]
    replace_operation["requestBody"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/ReplaceAtomicMemoryArtifactRequest"
    }
    provider = OpenAPIProvider(
        openapi_spec=openapi_spec,
        client=client,
        route_map_fn=_select_mcp_type,
        mcp_component_fn=partial(_annotate_mcp_component, openapi_spec=openapi_spec),
        # FastAPI has already validated the response model. A second JSON Schema
        # pass rejects valid OpenAPI 3.0 nullable references in empty results.
        validate_output=False,
    )
    server = FastMCP(name=MCP_SERVER_NAME, instructions=MCP_GUIDANCE, providers=[provider])
    server.add_middleware(McpTracingMiddleware(resolved_tracing))
    if access_log:
        server.add_middleware(McpAccessLogMiddleware())
    if metrics is not None:
        server.add_middleware(McpMetricsMiddleware(metrics))
    # FastMCP's first middleware is outermost. Observe the final MCP outcome
    # after the content adapter has converted a real conditional HTTP 304.
    server.add_middleware(_ArtifactContentEtagMiddleware())
    return server


class _InternalBridgeTransport(httpx.ASGITransport):
    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request_id = current_request_id()
        if request_id is not None:
            request.headers[REQUEST_ID_HEADER] = request_id
        token = bind_internal_bridge()
        try:
            response = await super().handle_async_request(request)
            captured = _artifact_http_response.get()
            if captured is not None:
                captured.response = response
            return response
        finally:
            reset_internal_bridge(token)


def mount_mcp(
    server_app: FastAPI,
    *,
    path: str = MCP_PATH,
    access_log: bool = False,
    metrics: ServerMetrics | None = None,
    tracing: ServerTracing | None = None,
) -> FastAPI:
    """Mount the MCP transport while preserving the Server HTTP contract."""

    mcp_server = create_mcp_server(
        server_app,
        access_log=access_log,
        metrics=metrics,
        tracing=tracing,
    )
    mcp_app = mcp_server.http_app(path="/")

    server_app.router.lifespan_context = combine_lifespans(
        server_app.router.lifespan_context,
        mcp_app.lifespan,
    )
    server_app.mount(path, mcp_app, name="mcp")
    return server_app
