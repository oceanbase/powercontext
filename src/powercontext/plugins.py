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

"""Interfaces that plugins implement and that PC Server calls when it needs them.

A plugin directory holds ``plugin.py``, which assigns an ``ArtifactPlugin`` declaration
to ``plugin``. The declaration tells core which Sources to deliver and when; its
``setup`` factory returns an object with ``render_artifact(request)``, ``generate(request,
writer)`` when the declaration has a schedule or ``on_sources``, and, optionally,
``search_artifacts(request)``, the user-change methods, and the handlers of the plugin's
own HTTP routes.
Slow work such as model calls runs outside the writer; Artifact writes run inside one
short ``async with writer.transaction()`` block, which core commits together with the
consumption cursor and the invocation acknowledgment.

Callers reach plugins through the existing Source and Artifact APIs, and through the
routes a plugin declares under ``/v1/plugins/{name}/api/``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.sources import SourceProjectionKey, SourceRef

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

ResourceT = TypeVar("ResourceT")
LifecycleState = Literal["active", "deprecated", "retired"]
BUILTIN_FAMILIES = frozenset({
    "memory",
    "atomic-memory",
    "experience",
    "skill",
    "handoff",
    "profile",
    "prompt",
    "topic-memory",
})


class Interval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    seconds: float = Field(gt=0)

    @field_validator("seconds")
    @classmethod
    def representable(cls, value: float) -> float:
        if timedelta(seconds=value).total_seconds() <= 0:
            raise ValueError("seconds must be at least one microsecond")  # noqa: TRY003
        return value


class Cron(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expression: str = Field(min_length=1)
    # An IANA time zone name; None uses the time zone of the machine running the Server.
    timezone: str | None = None


_PROJECTION = r"^[^@\s]+@[^@\s]+$"


class Inputs(BaseModel):
    """The Sources delivered to ``generate`` and the Projection each one arrives in.

    ``projection`` applies to every Source type; ``projections`` names a different one for
    particular types. Every listed type needs one or the other.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_types: tuple[Annotated[str, Field(min_length=1)], ...] = Field(min_length=1)
    projection: str | None = Field(default=None, pattern=_PROJECTION)
    projections: dict[str, Annotated[str, Field(pattern=_PROJECTION)]] = Field(default_factory=dict)
    max_sources: int = Field(default=50, ge=1, le=1000)

    @model_validator(mode="after")
    def every_type_has_a_projection(self) -> Inputs:
        if set(self.projections) - set(self.source_types):
            raise ValueError("projections may only name listed source_types")  # noqa: TRY003
        if self.projection is None and set(self.source_types) - set(self.projections):
            raise ValueError("every source type needs a projection")  # noqa: TRY003
        return self

    def projection_key(self, source_type: str) -> SourceProjectionKey:
        projection = self.projections.get(source_type, self.projection)
        if projection is None:
            raise KeyError(source_type)
        name, version = projection.rsplit("@", 1)
        return SourceProjectionKey(name=name, version=version)


class RouteSpec(BaseModel):
    """The data part of one plugin route: what core needs to mount and authorize it."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(pattern=r"^/[A-Za-z0-9._~{}/-]*$", max_length=512)
    handler: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    access: Literal["authenticated", "scope.read", "scope.contribute"]
    scope_param: str | None = None
    body: Literal["none", "json", "binary"] = "none"
    # The route's own body limit; defaults to 1 MiB for JSON and to the deployment's ceiling for binary
    # bodies. The deployment's POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES always caps it.
    max_body_bytes: int | None = Field(default=None, ge=1)
    timeout_seconds: float = Field(default=30, gt=0)
    description: str | None = None
    mcp: bool = False
    request_schema: dict[str, JsonValue] | None = None
    response_schema: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def consistent(self) -> RouteSpec:
        parameters = set(_path_parameters(self.path))
        if self.access == "authenticated":
            if self.scope_param is not None:
                raise ValueError("authenticated routes do not take scope_param")  # noqa: TRY003
        elif self.scope_param not in parameters:
            raise ValueError("Scope routes must name a path parameter as scope_param")  # noqa: TRY003
        if self.max_body_bytes is not None and self.body == "none":
            raise ValueError("max_body_bytes needs a JSON or binary body")  # noqa: TRY003
        if self.mcp and (self.body == "binary" or not self.description):
            raise ValueError("MCP routes need a description and a JSON or empty body")  # noqa: TRY003
        return self


class PluginApiSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: tuple[RouteSpec, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_routes(self) -> PluginApiSpec:
        keys = [(route.method, route.path) for route in self.routes]
        if len(set(keys)) != len(keys):
            raise ValueError("routes must be unique by method and path")  # noqa: TRY003
        return self


class Route(RouteSpec):
    """One HTTP route a plugin declares; its handler is a method of the ``setup`` result."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    request_model: type[BaseModel] | None = Field(default=None, exclude=True)
    response_model: type[BaseModel] | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def describe_models(self) -> Route:
        # Schemas cross process boundaries; the classes themselves stay in the plugin.
        if self.request_model is not None and self.body != "json":
            raise ValueError("request_model requires a JSON body")  # noqa: TRY003
        object.__setattr__(
            self, "request_schema", None if self.request_model is None else self.request_model.model_json_schema()
        )
        object.__setattr__(
            self, "response_schema", None if self.response_model is None else self.response_model.model_json_schema()
        )
        return self


class PluginApi(PluginApiSpec):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    routes: tuple[Route, ...] = Field(min_length=1, max_length=200)


class PluginPages(BaseModel):
    """Static pages the Dashboard shows in a sandboxed frame; they reach only this plugin's routes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: str = Field(default="ui", pattern=r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
    entry: str = Field(default="index.html", pattern=r"^[A-Za-z0-9._-]+\.html$")
    # Navigation title by language; English is required as the fallback.
    title: dict[str, Annotated[str, Field(min_length=1, max_length=64)]]

    @field_validator("directory")
    @classmethod
    def inside_plugin(cls, value: str) -> str:
        if ".." in value.split("/"):
            raise ValueError("directory must stay inside the plugin directory")  # noqa: TRY003
        return value

    @field_validator("title")
    @classmethod
    def has_english(cls, value: dict[str, str]) -> dict[str, str]:
        if "en" not in value:
            raise ValueError("title needs an en entry")  # noqa: TRY003
        return value


class PluginSpec(BaseModel):
    """Declaration fields every kind of plugin shares: its host processes, routes, and pages."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    # Time limit of one user change, presentation, search, or Source write in the host process.
    change_timeout_seconds: float = Field(default=30, gt=0)
    # Suggested ceiling on this plugin's host processes in each Server process; deployers may override it.
    host_processes: int = Field(default=1, ge=1)
    # Time limit of ``upgrade``, which runs every time a version of the plugin loads.
    upgrade_timeout_seconds: float = Field(default=600, gt=0)
    api: PluginApiSpec | None = None
    pages: PluginPages | None = None

    @model_validator(mode="after")
    def pages_need_api(self) -> PluginSpec:
        if self.pages is not None and self.api is None:
            raise ValueError("pages call the plugin's own routes; declare api as well")  # noqa: TRY003
        return self


class ArtifactPluginSpec(PluginSpec):
    """The data part of an Artifact plugin's declaration: what core needs before it runs plugin code."""

    kind: Literal["artifact"] = "artifact"
    family: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    inputs: Inputs | None = None
    schedule: Interval | Cron | None = None
    on_sources: bool = False
    scopes: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] | None = Field(
        default=None, min_length=1, max_length=1000
    )
    max_workers: int = Field(default=1, ge=1)
    timeout_seconds: float = Field(default=300, gt=0)
    transaction_timeout_seconds: float = Field(default=30, gt=0)

    @field_validator("family")
    @classmethod
    def not_builtin(cls, value: str) -> str:
        if value in BUILTIN_FAMILIES:
            raise ValueError("family must not reuse a built-in Artifact family")  # noqa: TRY003
        return value

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Scope IDs must be unique")  # noqa: TRY003
        return None if value is None else tuple(sorted(value))

    @model_validator(mode="after")
    def consistent_triggers(self) -> ArtifactPluginSpec:
        # Without a schedule or on_sources the plugin has no background generation.
        if self.inputs is not None and self.schedule is None and not self.on_sources:
            raise ValueError("inputs require a schedule, on_sources, or both")  # noqa: TRY003
        if self.on_sources and self.inputs is None:
            raise ValueError("on_sources requires inputs")  # noqa: TRY003
        if isinstance(self.schedule, Cron) and (self.on_sources or self.inputs is None):
            # The Supervisor admits one interval or one cron per binding, and cron plugins
            # without inputs have no dirty work to admit.
            raise ValueError("cron schedules require inputs and cannot be combined with on_sources")  # noqa: TRY003
        if self.schedule is not None and self.inputs is None and self.scopes is None:
            raise ValueError("scheduled plugins without inputs must list scopes explicitly")  # noqa: TRY003
        if self.transaction_timeout_seconds > self.timeout_seconds:
            raise ValueError("transaction_timeout_seconds must not exceed timeout_seconds")  # noqa: TRY003
        return self


class GenerationSource(BaseModel):
    """One Source of the invocation window with the manifest's input Projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: SourceRef
    position: int = Field(ge=1)
    projection: JsonValue


class GenerateRequest(BaseModel):
    """Inputs of one Scope invocation; the cursor itself is never exposed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    reason: Literal["schedule", "sources"]
    sources: tuple[GenerationSource, ...] = ()


class ArtifactSnapshot(BaseModel):
    """One exact revision with its current governance state and direct lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ArtifactRef
    content: dict[str, JsonValue]
    lineage: ArtifactLineage
    lifecycle_state: LifecycleState
    merged_into_id: str | None = None
    replacement_artifact_id: str | None = None
    governance_generation: int = Field(ge=0)


class ArtifactPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ArtifactSnapshot, ...]
    next_cursor: str | None = None


class UndoMergePreview(BaseModel):
    """The group an undo would restore and retire, computed from current state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    restore: tuple[ArtifactRef, ...]
    retire: tuple[ArtifactRef, ...]
    undo_merge_results: tuple[str, ...]


class ArtifactChange(BaseModel):
    """A lifecycle change and the other Artifacts the shared flow restored or retired with it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: ArtifactSnapshot
    restored: tuple[ArtifactRef, ...] = ()
    retired: tuple[ArtifactRef, ...] = ()


class PluginStorage(Protocol):
    """The plugin's dedicated area on the deployment's database backend, outside core's transactions."""

    @property
    def backend(self) -> Literal["sqlite", "oceanbase", "seekdb"]: ...

    def engine(self) -> AsyncEngine: ...


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ModelUnavailable(Exception):
    """The Server has no model of the kind the plugin called."""


class PluginModels(Protocol):
    """Server's configured models; addresses and credentials stay with core.

    ``ctx.models`` is None when the Server configures neither an embedding nor a generation
    model; a call to a kind that is not configured raises ``ModelUnavailable``.
    """

    @property
    def embedding_available(self) -> bool: ...

    @property
    def embedding_dimension(self) -> int | None: ...

    @property
    def generation_available(self) -> bool: ...

    async def embed(self, texts: Sequence[str], /) -> tuple[tuple[float, ...], ...]: ...

    async def generate(self, messages: Sequence[ChatMessage], /, *, max_tokens: int | None = None) -> str: ...


class Caller(BaseModel):
    """The authenticated caller of a user change or plugin route; carries no credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str


class AccessDenied(Exception):
    """``ctx.require`` found the caller without the permission; the route returns 403."""


class PluginContext:
    """Context of one Worker invocation, or of a host process for every Scope it serves.

    In a Worker ``scope_id`` is the invocation Scope. In the host process it is None,
    each request carries its Scope, and the request helpers below act as that
    request's caller. Resources registered here close when the call or process ends.
    """

    def __init__(
        self,
        *,
        name: str,
        scope_id: str | None,
        plugin_dir: Path,
        logger: logging.Logger,
        resources: AsyncExitStack,
        storage: PluginStorage | None = None,
        models: PluginModels | None = None,
        host: Any = None,
        center: PluginCenter | None = None,
    ) -> None:
        self.name = name
        # Set only on a device runner: the plugin's own routes on the central Server.
        self.center = center
        self.scope_id = scope_id
        self.plugin_dir = plugin_dir
        self.logger = logger
        self.models = models
        self._storage = storage
        self._resources = resources
        self._host = host

    @property
    def storage(self) -> PluginStorage:
        if self._storage is None:
            raise RuntimeError("dedicated plugin storage is unavailable")  # noqa: TRY003
        return self._storage

    async def enter_async_context(self, manager: AbstractAsyncContextManager[ResourceT]) -> ResourceT:
        return await self._resources.enter_async_context(manager)

    def _request_host(self) -> Any:
        if self._host is None:
            raise RuntimeError("request helpers are available only while the host process serves a request")  # noqa: TRY003
        return self._host

    @property
    def caller(self) -> Caller:
        return self._request_host().caller()

    async def require(
        self, action: Literal["scope.read", "scope.contribute", "server.admin"], scope_id: str | None = None
    ) -> None:
        await self._request_host().require(action, scope_id)

    async def allowed(
        self, action: Literal["scope.read", "scope.contribute", "server.admin"], scope_id: str | None = None
    ) -> bool:
        try:
            await self.require(action, scope_id)
        except AccessDenied:
            return False
        return True

    def reads(self, scope_id: str) -> ArtifactReads:
        return self._request_host().reads(scope_id)

    def writer(self, scope_id: str) -> ArtifactWriter:
        return self._request_host().writer(scope_id)

    @property
    def sources(self) -> PluginSources:
        return self._request_host().sources()

    @property
    def scopes(self) -> PluginScopes:
        return self._request_host().scopes()


class PluginSources(Protocol):
    async def submit(self, scope_id: str, definition: str, value: BaseModel, /) -> SourceRef: ...


class PluginScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    title: str
    summary: str
    version: int


class PluginScopes(Protocol):
    async def create(self, *, title: str, summary: str, idempotency_key: str) -> PluginScope: ...

    async def update(self, scope_id: str, /, *, expected_version: int, title: str, summary: str) -> PluginScope: ...

    async def list(self) -> tuple[PluginScope, ...]: ...


class ArtifactReads(Protocol):
    """Reads limited to the invocation Scope and the plugin's own Family."""

    async def get(self, artifact_id: str, /, *, revision: int | None = None) -> ArtifactSnapshot | None: ...

    async def revisions(self, artifact_id: str, /, *, cursor: str | None = None, limit: int = 50) -> ArtifactPage: ...

    async def list(
        self,
        *,
        states: Collection[LifecycleState] = ("active",),
        tags: Collection[str] = (),
        cursor: str | None = None,
        limit: int = 50,
    ) -> ArtifactPage: ...

    async def by_source(self, ref: SourceRef, /) -> tuple[ArtifactSnapshot, ...]: ...

    async def merge_inputs(self, artifact_id: str, /) -> tuple[ArtifactRef, ...]: ...

    async def tags(self, artifact_id: str, /) -> tuple[str, ...]: ...

    async def resolve_handle(self, handle_key: str, /) -> ArtifactRef | None: ...


class ArtifactTransaction(ArtifactReads, Protocol):
    """Writes execute immediately in the open transaction and return exact revisions."""

    async def put(
        self,
        artifact_id: str,
        /,
        *,
        content: dict[str, JsonValue],
        source_refs: Sequence[SourceRef] = (),
        artifact_refs: Sequence[ArtifactRef] = (),
        expected_revision: int | None = None,
    ) -> ArtifactSnapshot: ...

    async def revert(self, artifact_id: str, /, *, to_revision: int) -> ArtifactSnapshot: ...

    async def set_lifecycle(
        self, artifact_id: str, state: LifecycleState, /, *, replacement_artifact_id: str | None = None
    ) -> ArtifactSnapshot: ...

    async def merge(
        self,
        inputs: Sequence[str],
        /,
        *,
        into: str,
        content: dict[str, JsonValue],
        source_refs: Sequence[SourceRef] = (),
        artifact_refs: Sequence[ArtifactRef] = (),
    ) -> ArtifactSnapshot: ...

    async def preview_undo_merge(self, artifact_id: str, /) -> UndoMergePreview: ...

    async def undo_merge(self, artifact_id: str, /) -> UndoMergePreview: ...

    async def bind_handle(self, handle_key: str, ref: ArtifactRef, /) -> ArtifactRef: ...

    async def set_tags(self, artifact_id: str, tags: Sequence[str], /) -> tuple[str, ...]: ...


class ArtifactWriter(ArtifactReads, Protocol):
    """Committed reads outside the transaction, plus the single write transaction."""

    @property
    def family(self) -> str: ...

    def stable_id(self, *parts: str) -> str: ...

    def transaction(self) -> AbstractAsyncContextManager[ArtifactTransaction]: ...


class RenderRequest(BaseModel):
    """Present one exact revision; the result may be cached by family, ID, revision, and plugin version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    artifact: ArtifactSnapshot
    caller: Caller | None = None


class Rendering(BaseModel):
    """The default presentation of one revision, shown to people and placed into agent context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["markdown", "text"] = "markdown"
    text: str


class SearchRequest(BaseModel):
    """A generic search of the plugin's family, passed on with every field the caller sent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    # The complete body of POST .../artifacts/{family}/search as JSON; Prepared Context sends its
    # query, the section's limit, and the section's own search fields. Reject what you do not
    # support with InvalidContent.
    search: dict[str, JsonValue]
    # Bytes still available to this family when the search comes from Prepared Context.
    max_bytes: int | None = Field(default=None, ge=0)
    caller: Caller | None = None


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    ref: ArtifactRef
    # Text for agent context: a passage of the revision or its rendering.
    text: str
    score: float | None = None
    locator: dict[str, JsonValue] | None = None


class SearchResult(BaseModel):
    """Hits in the plugin's order; core drops any the caller cannot read or that are not current."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hits: tuple[SearchHit, ...] = ()


class ArtifactGenerator(Protocol):
    """What ``setup`` returns; core checks the required methods after calling it."""

    async def render_artifact(self, request: RenderRequest, /) -> Rendering: ...

    # Required when the declaration has a schedule or on_sources.
    async def generate(self, request: GenerateRequest, writer: ArtifactWriter, /) -> None: ...


class CreateRequest(BaseModel):
    """A user creates an Artifact; core allocated ``artifact_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    artifact_id: str
    content: dict[str, JsonValue]
    caller: Caller | None = None


class UpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    artifact_id: str
    expected_revision: int
    content: dict[str, JsonValue]
    caller: Caller | None = None


class MergeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    revision: int
    state_version: int


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    inputs: tuple[MergeInput, ...]
    into: str
    content: dict[str, JsonValue]
    caller: Caller | None = None


class ForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    artifact_id: str
    expected_revision: int
    expected_state_version: int
    caller: Caller | None = None


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    artifact_id: str
    operation: Literal["restore", "undo_merge"] = "restore"
    revision: int | None = None
    caller: Caller | None = None


class InvalidContent(Exception):
    """The plugin rejects content a user submitted; the API returns 422 with this message."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class ApiRequest(BaseModel):
    """One call to a plugin route, after core authenticated the caller and read the body."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    scope_id: str | None
    route: str
    path_params: dict[str, str]
    query: dict[str, list[str]]
    body: Any = None
    caller: Caller


class BinaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes
    content_type: str
    filename: str | None = None


class ApiError(Exception):
    """A handler's 4xx answer; core decides 401 and 403 itself."""

    def __init__(self, status: int, code: str, message: str) -> None:
        if not 400 <= status <= 499 or status in {401, 403}:
            raise ValueError("ApiError status must be a 4xx other than 401 and 403")  # noqa: TRY003
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


USER_CHANGE_METHODS = ("create_artifact", "update_artifact", "merge_artifacts", "forget_artifact", "restore_artifact")


class ArtifactPlugin(ArtifactPluginSpec):
    """The declaration ``plugin.py`` assigns to ``plugin``; the plugin name is its directory name."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Called once per Worker invocation, and once per host process; read deployment settings from the environment here.
    setup: Callable[[PluginContext], Awaitable[ArtifactGenerator]] = Field(exclude=True)
    # Source Definitions this plugin's own routes submit; their manifests register when the plugin loads.
    definitions: tuple[Any, ...] = Field(default=(), exclude=True)
    api: PluginApi | None = None
    # Called every time a version of the plugin loads, before setup; it must be fast and safe to repeat.
    upgrade: Callable[[PluginContext], Awaitable[None]] | None = Field(default=None, exclude=True)


Normalizer = Annotated[str, Field(pattern=r"^(strip|lower|collapse_spaces|remove_prefix:.+|remove_suffix:.+)$")]


class TargetDeclaration(BaseModel):
    """The data part of a collection target: its parameter schema and which parameters identify it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1, max_length=2048)
    # Parameters that form the target's unique key, each with its normalization steps.
    identity: dict[str, tuple[Normalizer, ...]] = Field(min_length=1)
    parameters_schema: dict[str, JsonValue] = Field(default_factory=dict)


class TargetSpec(TargetDeclaration):
    """What a collection target takes: a parameters model, and the parameters that make a target unique."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    parameters: type[BaseModel] = Field(exclude=True)

    @model_validator(mode="after")
    def describe_parameters(self) -> TargetSpec:
        fields = self.parameters.model_fields
        for name in self.identity:
            if name not in fields or fields[name].annotation is not str:
                raise ValueError(f"identity parameter {name} must be a string field of parameters")  # noqa: TRY003
        # Devices may run older plugin versions; parameters from a newer center must not be rejected.
        if self.parameters.model_config.get("extra") == "forbid":
            raise ValueError("target parameters must ignore unknown fields")  # noqa: TRY003
        object.__setattr__(self, "parameters_schema", self.parameters.model_json_schema())
        return self


class Collect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schedule: Interval | Cron
    # Enabled targets of one Scope given to one call; more rotate into later calls.
    max_targets: int = Field(default=50, ge=1, le=1000)
    max_items: int = Field(default=200, ge=1, le=10000)
    timeout_seconds: float = Field(default=120, gt=0)
    max_workers: int = Field(default=1, ge=1)


class SourcePluginSpec(PluginSpec):
    """The data part of a Source plugin's declaration; the plugin name is also its collection capability."""

    kind: Literal["source"] = "source"
    version: str
    # The oldest device version this center version still accepts; defaults to ``version``.
    min_compatible_version: str | None = None
    collect: Collect | None = None
    target: TargetDeclaration | None = None
    # Scopes the Server's own runner may collect into; None allows every Scope.
    scopes: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] | None = Field(
        default=None, min_length=1, max_length=1000
    )

    @model_validator(mode="after")
    def consistent(self) -> SourcePluginSpec:
        from packaging.version import InvalidVersion, Version

        try:
            version = Version(self.version)
            oldest = Version(self.min_compatible_version or self.version)
        except InvalidVersion:
            raise ValueError("version and min_compatible_version must be PEP 440 versions") from None  # noqa: TRY003
        if oldest > version:
            raise ValueError("min_compatible_version must not exceed version")  # noqa: TRY003
        if self.min_compatible_version is None:
            object.__setattr__(self, "min_compatible_version", self.version)
        if (self.collect is None) != (self.target is None):
            raise ValueError("collect and target go together")  # noqa: TRY003
        return self

    def accepts(self, device_version: str) -> bool:
        """Whether this center version accepts a device running ``device_version``."""

        from packaging.version import InvalidVersion, Version

        try:
            device = Version(device_version)
        except InvalidVersion:
            return False
        return Version(self.min_compatible_version or self.version) <= device <= Version(self.version)


class CollectTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str
    parameters: dict[str, JsonValue]
    # The target's last committed checkpoint; None the first time; read-only.
    checkpoint: JsonValue | None = None


class CollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    reason: Literal["schedule", "target_added"] = "schedule"
    targets: tuple[CollectTarget, ...]
    max_items: int


class CollectedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    # The name of one of the plugin's Source Definitions, and an instance of its input class.
    definition: str
    value: BaseModel


class TargetResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    items: list[CollectedItem] = Field(default_factory=list)
    checkpoint: JsonValue | None = None
    has_more: bool = False
    # Set by ``fail``: this target keeps its checkpoint and records the error; others still commit.
    error_code: str | None = None
    error_message: str | None = None


class CollectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    targets: dict[str, TargetResult] = Field(default_factory=dict)

    def add(
        self,
        target: CollectTarget,
        items: Sequence[CollectedItem],
        *,
        checkpoint: JsonValue | None,
        has_more: bool = False,
    ) -> None:
        self.targets[target.target_id] = TargetResult(items=list(items), checkpoint=checkpoint, has_more=has_more)

    def fail(self, target: CollectTarget, *, code: str, message: str) -> None:
        """Report that only this target failed."""

        self.targets[target.target_id] = TargetResult(error_code=code[:128], error_message=message[:1024])


class SourceCollector(Protocol):
    async def collect(self, request: CollectRequest, /) -> CollectResult: ...


class PluginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: int
    content_type: str | None = None
    json_body: JsonValue | None = None
    content: bytes | None = None


class PluginCenter(Protocol):
    """A device runner's calls to its own plugin's routes on the central Server, with the runner's credential."""

    async def call_plugin(
        self,
        method: str,
        path: str,
        /,
        *,
        json: JsonValue | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> PluginResponse: ...


class SourcePlugin(SourcePluginSpec):
    """The declaration of a plugin that provides Source types and, with ``collect``, collects into them."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    definitions: tuple[Any, ...] = Field(min_length=1, exclude=True)
    target: TargetSpec | None = None
    # Returns the collector; plugin routes are methods of its result too. Required with collect or api.
    setup: Callable[[PluginContext], Awaitable[Any]] | None = Field(default=None, exclude=True)
    api: PluginApi | None = None
    upgrade: Callable[[PluginContext], Awaitable[None]] | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def setup_when_needed(self) -> SourcePlugin:
        if (self.collect is not None or self.api is not None) and self.setup is None:
            raise ValueError("collect and api need setup")  # noqa: TRY003
        return self


def _path_parameters(path: str) -> tuple[str, ...]:
    import re

    return tuple(re.findall(r"{([A-Za-z_][A-Za-z0-9_]*)}", path))


__all__ = [
    "USER_CHANGE_METHODS",
    "AccessDenied",
    "ApiError",
    "ApiRequest",
    "ArtifactChange",
    "ArtifactGenerator",
    "ArtifactPage",
    "ArtifactPlugin",
    "ArtifactPluginSpec",
    "ArtifactReads",
    "ArtifactSnapshot",
    "ArtifactTransaction",
    "ArtifactWriter",
    "BinaryResponse",
    "Caller",
    "ChatMessage",
    "Collect",
    "CollectRequest",
    "CollectResult",
    "CollectTarget",
    "CollectedItem",
    "CreateRequest",
    "Cron",
    "ForgetRequest",
    "GenerateRequest",
    "GenerationSource",
    "Inputs",
    "Interval",
    "InvalidContent",
    "LifecycleState",
    "MergeInput",
    "MergeRequest",
    "ModelUnavailable",
    "PluginApi",
    "PluginApiSpec",
    "PluginCenter",
    "PluginContext",
    "PluginModels",
    "PluginPages",
    "PluginResponse",
    "PluginScope",
    "PluginScopes",
    "PluginSources",
    "PluginSpec",
    "PluginStorage",
    "RenderRequest",
    "Rendering",
    "RestoreRequest",
    "Route",
    "RouteSpec",
    "SearchHit",
    "SearchRequest",
    "SearchResult",
    "SourceCollector",
    "SourcePlugin",
    "SourcePluginSpec",
    "TargetDeclaration",
    "TargetResult",
    "TargetSpec",
    "UndoMergePreview",
    "UpdateRequest",
]
