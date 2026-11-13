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

"""The plugin host process: core runtime plus one plugin, serving synchronous calls.

Like a Worker, the host builds its own database connection and authorization service
from configuration, so the Server only authenticates callers and forwards requests.
Authorization, version prechecks, plugin calls, and writes all happen here.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.merge_models import ArtifactMergeConflictError
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.plugins.executor import open_plugin_database
from powercontext.builtin.plugins.families import replace_registrations, revision_metadata
from powercontext.builtin.plugins.host_protocol import (
    HostCancel,
    HostFailed,
    HostHeartbeat,
    HostReady,
    HostRegistry,
    HostRequest,
    HostResponse,
    HostShutdown,
    PluginHostSpec,
)
from powercontext.builtin.plugins.manifest import PluginConfigError, PluginManifest, load_plugin
from powercontext.builtin.plugins.storage import open_plugin_resources
from powercontext.builtin.plugins.writer import PluginArtifactWriter, PluginContractError, PluginWriteError, UserChange
from powercontext.builtin.records import ArtifactRevisionPreconditionError, BaseValueConflictError
from powercontext.builtin.runtime.artifact_merge import ArtifactMergeAccess
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.errors import RevisionConflictError
from powercontext.plugins import (
    USER_CHANGE_METHODS,
    AccessDenied,
    ApiError,
    ApiRequest,
    ArtifactPluginSpec,
    BinaryResponse,
    Caller,
    CreateRequest,
    ForgetRequest,
    InvalidContent,
    MergeInput,
    MergeRequest,
    ModelUnavailable,
    PluginContext,
    PluginScope,
    Rendering,
    RenderRequest,
    RestoreRequest,
    SearchRequest,
    SearchResult,
    UpdateRequest,
)
from powercontext.sources import SourceRef

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 1.0
_CHANGE_METHODS = {
    "create_artifact": "create",
    "update_artifact": "update",
    "merge_artifacts": "merge",
    "forget_artifact": "forget",
    "restore_artifact": "restore",
}


@dataclass
class _Call:
    request: HostRequest
    context: ArtifactSearchExecutionContext | None
    contribute_scopes: set[str]


_CURRENT: contextvars.ContextVar[_Call] = contextvars.ContextVar("powercontext_plugin_call")


def _control(message: object, tasks: dict[str, asyncio.Task[None]]) -> None:
    if isinstance(message, HostCancel):
        running = tasks.get(message.id)
        if running is not None:
            running.cancel()
    elif isinstance(message, HostRegistry):
        replace_registrations(message.plugin_families, message.source_bindings)


class _UpgradeFailed(Exception):
    """The version's upgrade raised or ran past upgrade_timeout_seconds."""


def run_plugin_host(connection: Any, spec: PluginHostSpec) -> None:
    """Spawn target: serve requests from the Server until the Pipe closes or a shutdown arrives."""

    asyncio.run(_Host(connection, spec).serve())


def run_plugin_upgrade(spec: PluginHostSpec) -> None:
    """Spawn target on instances without host processes: run only the version's upgrade, then exit.

    The exit code tells the Server whether the new version may be used for background calls.
    """

    async def upgrade() -> None:
        async with AsyncExitStack() as resources:
            await _Host(None, spec)._start(resources, serve=False)

    try:
        asyncio.run(upgrade())
    except Exception as error:
        logger.warning("Plugin upgrade failed (%s)", type(error).__name__)
        raise SystemExit(1) from None


class _Host:
    def __init__(self, connection: Any, spec: PluginHostSpec) -> None:
        self.connection = connection
        self.spec = spec
        self.send_lock = asyncio.Lock()
        self.manifest = PluginManifest.model_validate(spec.manifest)
        artifact = self.manifest.artifact
        # Source plugins own no family: they have no Artifact reads, writes, presentation, or search.
        self.family = None if artifact is None else artifact.family
        self.access: Any = None
        self.deployment_id: str | None = None
        self.plugin: Any = None
        self.routes: dict[tuple[str, str], Any] = {}
        self.definitions: Any = None
        self.security: Any = None
        # While upgrade runs, reads and writes act as the plugin itself and no caller exists.
        self.upgrading = False
        self.upgrade_writer: PluginArtifactWriter | None = None

    async def _send(self, message: object) -> None:
        async with self.send_lock:
            await asyncio.to_thread(self.connection.send, message)

    async def serve(self) -> None:
        async with AsyncExitStack() as resources:
            try:
                implemented = await self._start(resources)
            except PluginConfigError as error:
                await self._send(HostFailed(error=str(error), permanent=True))
                return
            except _UpgradeFailed as error:
                await self._send(HostFailed(error=str(error), category="upgrade_failed"))
                return
            except Exception as error:
                await self._send(
                    HostFailed(error=str(error) if isinstance(error, ValueError) else type(error).__name__)
                )
                return
            await self._send(HostReady(implemented=implemented))
            heartbeat = asyncio.create_task(self._beat())
            tasks: dict[str, asyncio.Task[None]] = {}
            while True:
                try:
                    message = await asyncio.to_thread(self.connection.recv)
                except (EOFError, OSError):
                    break
                if isinstance(message, HostShutdown):
                    break
                if not isinstance(message, HostRequest):
                    _control(message, tasks)
                    continue
                task = asyncio.create_task(self._handle(message))
                tasks[message.id] = task
                task.add_done_callback(lambda _, key=message.id: tasks.pop(key, None))
            if tasks:
                await asyncio.wait(tasks.values(), timeout=10)
            heartbeat.cancel()

    async def _beat(self) -> None:
        while True:
            try:
                await self._send(HostHeartbeat())
            except (EOFError, OSError):
                return
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def _start(self, resources: AsyncExitStack, *, serve: bool = True) -> tuple[str, ...]:
        spec = self.spec
        directory = Path(spec.directory)
        loaded, declaration = load_plugin(directory, self.manifest.name)
        if loaded != self.manifest:
            raise PluginConfigError("the plugin changed after the Server checked it; restart the Server")  # noqa: TRY003
        families = {*spec.plugin_families, *(() if self.family is None else (self.family,))}
        replace_registrations(tuple(families), spec.source_bindings)
        self.database = await resources.enter_async_context(open_plugin_database(spec.database))
        from powercontext.builtin.authz.worker import open_worker_security

        security = await resources.enter_async_context(open_worker_security(spec.security, self.database))
        self.security = security
        if security is not None:
            self.access = security.access
        if spec.security is not None:
            self.deployment_id = spec.security.get("deployment_id")
        storage, models = await open_plugin_resources(
            spec.database, spec.storage_root, self.manifest.name, spec.inference, resources
        )
        if declaration.definitions:
            from powercontext.sources.definitions import SourceDefinitionRegistry

            self.definitions = SourceDefinitionRegistry(declaration.definitions)
        if declaration.api is not None:
            self.routes = {(route.method, route.path): route for route in declaration.api.routes}
        context = PluginContext(
            name=self.manifest.name,
            scope_id=None,
            plugin_dir=directory,
            logger=logging.getLogger(f"powercontext.plugin.{self.manifest.name}"),
            resources=resources,
            storage=storage,
            models=models,
            host=self,
        )
        if declaration.upgrade is not None:
            await self._upgrade(declaration.upgrade, context)
        if not serve:
            return ()
        self.plugin = None if declaration.setup is None else await declaration.setup(context)
        declared = self.manifest.artifact
        if declared is None:
            return ()
        background = declared.schedule is not None or declared.on_sources
        required = ("render_artifact", *(("generate",) if background else ()))
        missing = [name for name in required if not callable(getattr(self.plugin, name, None))]
        if missing:
            raise PluginConfigError("setup returned an object without " + ", ".join(missing))
        return tuple(name for name in USER_CHANGE_METHODS if callable(getattr(self.plugin, name, None)))

    async def _upgrade(self, upgrade: Any, context: PluginContext) -> None:
        self.upgrading = True
        try:
            async with asyncio.timeout(self.manifest.spec.upgrade_timeout_seconds):
                await upgrade(context)
        except Exception as error:
            raise _UpgradeFailed(type(error).__name__) from error
        finally:
            self.upgrading = False
            self.upgrade_writer = None

    def _plugin_writer(self, scope_id: str) -> PluginArtifactWriter:
        """A writer acting as the plugin, like a Worker's, with no invocation cursor."""

        spec = self._artifact()
        security = self.security

        async def authorize(connection: Any) -> None:
            if spec.scopes is not None and scope_id not in spec.scopes:
                raise PluginWriteError("the Scope is outside the plugin's scopes")  # noqa: TRY003
            if security is not None:
                await security.authorize_transaction(connection, scope_id)

        return PluginArtifactWriter(
            database=self.database,
            scope_id=scope_id,
            family=spec.family,
            sources=SourceRepository(BUILTIN_SOURCE_REGISTRY),
            metadata=revision_metadata(self.manifest.name, self.manifest.fingerprint),
            begin=authorize,
            transaction_timeout_seconds=spec.transaction_timeout_seconds,
            security=security,
        )

    def _no_caller(self) -> None:
        if self.upgrading:
            raise RuntimeError("upgrade runs as the plugin itself; there is no caller")  # noqa: TRY003

    # Request handling

    async def _handle(self, request: HostRequest) -> None:  # noqa: C901 - maps every plugin outcome to one response
        context = None
        if self.access is not None and request.caller is not None:
            context = ArtifactSearchExecutionContext(principal=request.caller, access=self.access, audit=request.audit)
        token = _CURRENT.set(_Call(request, context, set()))
        try:
            response = await self._dispatch(request)
        except InvalidContent as error:
            response = HostResponse(request.id, "invalid_content", message=error.message, field=error.field)
        except ValidationError as error:
            fields = ", ".join(".".join(str(part) for part in item["loc"]) or "body" for item in error.errors())
            response = HostResponse(request.id, "invalid_content", message=f"invalid fields: {fields}")
        except ApiError as error:
            response = HostResponse(
                request.id, "api_error", status=error.status, field=error.code, message=error.message
            )
        except PluginContractError as error:
            response = HostResponse(request.id, "contract_violation", message=str(error))
        except ArtifactRevisionPreconditionError:
            response = HostResponse(request.id, "precondition_failed")
        except (ArtifactMergeConflictError, BaseValueConflictError, RevisionConflictError):
            response = HostResponse(request.id, "conflict")
        except RepositoryNotFoundError:
            response = HostResponse(request.id, "not_found")
        except (AccessDenied, PermissionError):
            response = HostResponse(request.id, "forbidden")
        except PluginWriteError as error:
            response = HostResponse(request.id, "invalid_content", message=str(error))
        except ModelUnavailable:
            response = HostResponse(request.id, "model_unavailable")
        except Exception as error:
            logger.warning("Plugin %s request failed (%s)", self.manifest.name, type(error).__name__)
            response = HostResponse(request.id, "error", error_type=type(error).__name__)
        finally:
            _CURRENT.reset(token)
        await self._send(response)

    async def _dispatch(self, request: HostRequest) -> HostResponse:
        if request.method == "api":
            return await self._api(request)
        if request.method == "create_source":
            return await self._create_source(request)
        if request.method == "project_source":
            return self._project_source(request)
        if request.method == "render_artifact":
            return await self._render(request)
        if request.method == "search_artifacts":
            return await self._search(request)
        return await self._change(request)

    async def _render(self, request: HostRequest) -> HostResponse:
        scope_id = request.scope_id
        if scope_id is None:
            raise ValueError("rendering needs a Scope")  # noqa: TRY003
        await self.require("scope.read", scope_id)
        payload = request.payload
        snapshot = await self._writer(scope_id).get(payload["artifact_id"], revision=payload.get("revision"))
        if snapshot is None:
            return HostResponse(request.id, "not_found")
        rendering = await self.plugin.render_artifact(
            RenderRequest(scope_id=scope_id, artifact=snapshot, caller=self.caller())
        )
        if not isinstance(rendering, Rendering):
            raise PluginContractError("render_artifact must return a Rendering")  # noqa: TRY003
        return HostResponse(
            request.id, "ok", result={"ref": snapshot.ref.model_dump(mode="json"), **rendering.model_dump(mode="json")}
        )

    async def _search(self, request: HostRequest) -> HostResponse:
        """Pass the whole search to the plugin, then keep only current, active, readable revisions."""

        method = getattr(self.plugin, "search_artifacts", None)
        if method is None:
            return HostResponse(request.id, "not_implemented")
        scope_id = request.scope_id
        if scope_id is None:
            raise ValueError("search needs a Scope")  # noqa: TRY003
        await self.require("scope.read", scope_id)
        payload = request.payload
        result = await method(
            SearchRequest(
                scope_id=scope_id, search=payload["search"], max_bytes=payload.get("max_bytes"), caller=self.caller()
            )
        )
        if not isinstance(result, SearchResult):
            raise PluginContractError("search_artifacts must return a SearchResult")  # noqa: TRY003
        reads = self._writer(scope_id)
        hits = []
        for hit in result.hits:
            if hit.ref.family != self.family:
                raise PluginContractError("search_artifacts returned another family")  # noqa: TRY003
            current = await reads.get(hit.ref.artifact_id)
            if current is not None and current.ref == hit.ref and current.lifecycle_state == "active":
                hits.append(hit.model_dump(mode="json"))
        return HostResponse(request.id, "ok", result={"hits": hits})

    async def _create_source(self, request: HostRequest) -> HostResponse:
        """A generic Source write of one of this plugin's types: validate the input, then resolve and store it."""

        scope_id = request.scope_id
        if scope_id is None or self.definitions is None:
            return HostResponse(request.id, "not_found")
        await self.require("scope.contribute", scope_id)
        selected = self.definitions.definition_for_name(request.payload["source_type"])
        value = selected.input_class.model_validate(request.payload["content"])
        ref = await self.store_source(scope_id, request.payload["source_type"], value)
        return HostResponse(request.id, "ok", result=ref.model_dump(mode="json"))

    def _artifact(self) -> ArtifactPluginSpec:
        spec = self.manifest.artifact
        if spec is None:
            raise RuntimeError("Source plugins have no Artifact family to read or write")  # noqa: TRY003
        return spec

    def _project_source(self, request: HostRequest) -> HostResponse:
        """Recompute a runner's observation with this plugin version; the Server stores the result."""

        from powercontext.builtin.plugins.observations import ObservationRejected, reproject
        from powercontext.sources.observations import SourceObservation

        if self.definitions is None:
            return HostResponse(request.id, "not_found")
        observation = SourceObservation.model_validate(request.payload["observation"])
        try:
            projected = reproject(self.definitions, self.manifest.source_manifests, observation)
        except ObservationRejected as error:
            return HostResponse(request.id, "invalid_content", message=str(error))
        return HostResponse(request.id, "ok", result=projected.model_dump(mode="json"))

    def _writer(self, scope_id: str, *, change: UserChange | None = None) -> PluginArtifactWriter:
        spec = self._artifact()
        return PluginArtifactWriter(
            database=self.database,
            scope_id=scope_id,
            family=spec.family,
            sources=SourceRepository(BUILTIN_SOURCE_REGISTRY),
            metadata=revision_metadata(self.manifest.name, self.manifest.fingerprint),
            transaction_timeout_seconds=spec.transaction_timeout_seconds,
            context=_CURRENT.get().context,
            change=change,
            caller_scoped=True,
        )

    async def _authorize(self, scope_id: str, action: str, artifact_id: str | None) -> None:
        call = _CURRENT.get()
        family = self._artifact().family
        ref = None if artifact_id is None else ArtifactRef(family=family, artifact_id=artifact_id, revision=1)
        async with self.database.transaction() as connection:
            await ArtifactMergeAccess(family).authorize(connection, scope_id, call.context, action, ref)

    async def _change(self, request: HostRequest) -> HostResponse:
        method = getattr(self.plugin, request.method, None)
        if method is None:
            return HostResponse(request.id, "not_implemented")
        scope_id = request.scope_id
        payload = request.payload
        operation = _CHANGE_METHODS[request.method]
        caller = self.caller()
        if scope_id is None:
            raise ValueError("user changes need a Scope")  # noqa: TRY003
        if operation == "create":
            target = payload.get("artifact_id") or uuid4().hex
            await self._authorize(scope_id, "create", None)
            change = UserChange("create", target)
            plugin_request: BaseModel = CreateRequest(
                scope_id=scope_id, artifact_id=target, content=payload["content"], caller=caller
            )
        elif operation == "merge":
            target = payload.get("into") or uuid4().hex
            inputs = tuple(MergeInput.model_validate(item) for item in payload["inputs"])
            await self._authorize(scope_id, "create", None)
            for item in inputs:
                await self._precheck(scope_id, item.artifact_id, item.revision, item.state_version, "write")
            change = UserChange(
                "merge",
                target,
                tuple(item.artifact_id for item in inputs),
                {item.artifact_id: (item.revision, item.state_version) for item in inputs},
            )
            plugin_request = MergeRequest(
                scope_id=scope_id, inputs=inputs, into=target, content=payload["content"], caller=caller
            )
        else:
            target = payload["artifact_id"]
            await self._authorize(scope_id, "write", target)
            if operation == "update":
                await self._precheck(scope_id, target, payload["expected_revision"], None, "precondition")
                change = UserChange("update", target, expected={target: (payload["expected_revision"], None)})
                plugin_request = UpdateRequest(
                    scope_id=scope_id,
                    artifact_id=target,
                    expected_revision=payload["expected_revision"],
                    content=payload["content"],
                    caller=caller,
                )
            elif operation == "forget":
                await self._precheck(
                    scope_id, target, payload["expected_revision"], payload["expected_state_version"], "write"
                )
                change = UserChange(
                    "forget",
                    target,
                    expected={target: (payload["expected_revision"], payload["expected_state_version"])},
                )
                plugin_request = ForgetRequest(
                    scope_id=scope_id,
                    artifact_id=target,
                    expected_revision=payload["expected_revision"],
                    expected_state_version=payload["expected_state_version"],
                    caller=caller,
                )
            else:
                kind = payload.get("operation", "restore")
                change = UserChange("undo_merge" if kind == "undo_merge" else "restore", target)
                plugin_request = RestoreRequest(
                    scope_id=scope_id,
                    artifact_id=target,
                    operation=kind,
                    revision=payload.get("revision"),
                    caller=caller,
                )
        writer = self._writer(scope_id, change=change)
        await method(plugin_request, writer)
        if not writer.committed:
            raise PluginContractError(f"{request.method} returned without its agreed write")  # noqa: TRY003
        snapshot = writer.written if writer.written is not None else await self._writer(scope_id).get(target)
        restored, retired, undone = writer.outcome
        return HostResponse(
            request.id,
            "ok",
            result={
                "artifact": None if snapshot is None else snapshot.model_dump(mode="json"),
                "records": [record.model_dump(mode="json") for record in writer.records],
                "restored": [ref.model_dump(mode="json") for ref in restored],
                "retired": [ref.model_dump(mode="json") for ref in retired],
                "undo_merge_results": list(undone),
            },
        )

    async def _precheck(
        self, scope_id: str, artifact_id: str, revision: int, state_version: int | None, kind: str
    ) -> None:
        current = await self._writer(scope_id).get(artifact_id)
        if current is None:
            raise RepositoryNotFoundError("artifact", (scope_id, self.family, artifact_id))
        if current.ref.revision != revision:
            if kind == "precondition":
                raise ArtifactRevisionPreconditionError(f'"{revision}"', f'"{current.ref.revision}"')
            raise ArtifactMergeConflictError("the Artifact changed after it was read")  # noqa: TRY003
        if state_version is not None and current.governance_generation != state_version:
            raise ArtifactMergeConflictError("the Artifact governance changed after it was read")  # noqa: TRY003

    async def _api(self, request: HostRequest) -> HostResponse:
        payload = request.payload
        route = self.routes.get((payload["method"], payload["route"]))
        if route is None:
            return HostResponse(request.id, "not_found")
        scope_id = None
        if route.access != "authenticated":
            scope_id = payload["path_params"][route.scope_param]
            await self.require(route.access, scope_id)
        body = request.body
        if route.body == "json" and route.request_model is not None:
            body = route.request_model.model_validate(body)
        api_request = ApiRequest(
            scope_id=scope_id,
            route=route.path,
            path_params=payload["path_params"],
            query=payload["query"],
            body=body,
            caller=self.caller(),
        )
        result = await getattr(self.plugin, route.handler)(api_request)
        if result is None:
            return HostResponse(request.id, "ok", result=("none", None))
        if isinstance(result, BinaryResponse):
            return HostResponse(
                request.id, "ok", result=("binary", result.content, result.content_type, result.filename)
            )
        if isinstance(result, BaseModel):
            return HostResponse(request.id, "ok", result=("json", result.model_dump(mode="json")))
        return HostResponse(request.id, "ok", result=("json", result))

    # Request helpers behind PluginContext

    def caller(self) -> Caller:
        self._no_caller()
        principal = _CURRENT.get().request.caller
        return Caller(principal_id="local" if principal is None else f"{principal.type}:{principal.id}")

    async def require(self, action: str, scope_id: str | None) -> None:
        self._no_caller()
        call = _CURRENT.get()
        if action in {"scope.read", "scope.contribute"} and scope_id is None:
            raise ValueError("Scope permissions need a scope_id")  # noqa: TRY003
        if self.access is None or call.request.caller is None:
            if action == "scope.contribute" and scope_id is not None:
                call.contribute_scopes.add(scope_id)
            return
        from powercontext.builtin.authz import AccessAction, AccessDeniedError, ResourceRef

        if action == "server.admin":
            resource = ResourceRef.server() if self.deployment_id is None else ResourceRef.server(self.deployment_id)
        else:
            assert scope_id is not None  # noqa: S101 - checked above for Scope actions
            resource = ResourceRef.scope(scope_id)
            await self.access.bootstrap_static_scope(call.request.caller, scope_id, context=call.request.audit)
        try:
            await self.access.require(call.request.caller, AccessAction(action), resource, context=call.request.audit)
        except AccessDeniedError:
            raise AccessDenied(action) from None
        if action == "scope.contribute" and scope_id is not None:
            call.contribute_scopes.add(scope_id)

    def reads(self, scope_id: str) -> Any:
        if self.upgrading:
            return self._plugin_writer(scope_id)
        return _CheckedReads(self, scope_id)

    async def store_source(self, scope_id: str, definition: str, value: BaseModel) -> SourceRef:
        """Resolve and project an input of one of this plugin's Source types, then store it like a remote observation."""

        if self.definitions is None:
            raise ValueError("the plugin declares no Source Definitions")  # noqa: TRY003
        from powercontext.builtin.plugins.observations import observe

        observation = await observe(self.definitions, self.manifest.source_manifests, definition, value)
        repository = SourceRepository(BUILTIN_SOURCE_REGISTRY)
        async with self.database.transaction() as connection:
            stored = await repository.add(connection, scope_id, observation)
        return stored.ref

    def writer(self, scope_id: str) -> PluginArtifactWriter:
        if self.upgrading:
            # Like a Worker invocation, one upgrade opens at most one write transaction.
            if self.upgrade_writer is None:
                self.upgrade_writer = self._plugin_writer(scope_id)
            elif self.upgrade_writer.scope_id != scope_id:
                raise PluginWriteError("an upgrade opens at most one write transaction")  # noqa: TRY003
            return self.upgrade_writer
        if scope_id not in _CURRENT.get().contribute_scopes:
            raise AccessDenied("call ctx.require('scope.contribute', scope_id) before writing")  # noqa: TRY003
        return self._writer(scope_id)

    def sources(self) -> _Sources:
        self._no_caller()
        return _Sources(self)

    def scopes(self) -> _Scopes:
        self._no_caller()
        return _Scopes(self)


class _CheckedReads:
    """Reads of one Scope after the caller's ``scope.read`` is confirmed."""

    def __init__(self, host: _Host, scope_id: str) -> None:
        self._host = host
        self._scope_id = scope_id
        self._ready = False

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._host._writer(self._scope_id), name)
        if not callable(target):
            return target

        async def checked(*args: Any, **kwargs: Any) -> Any:
            if not self._ready:
                await self._host.require("scope.read", self._scope_id)
                self._ready = True
            return await target(*args, **kwargs)

        return checked


class _Sources:
    def __init__(self, host: _Host) -> None:
        self._host = host

    async def submit(self, scope_id: str, definition: str, value: BaseModel, /) -> SourceRef:
        if scope_id not in _CURRENT.get().contribute_scopes:
            raise AccessDenied("call ctx.require('scope.contribute', scope_id) before submitting Sources")  # noqa: TRY003
        return await self._host.store_source(scope_id, definition, value)


class _Scopes:
    _KIND = "powercontext.plugin"

    def __init__(self, host: _Host) -> None:
        self._host = host

    def _application(self) -> Any:
        from powercontext.builtin.scope.application import ScopeApplication

        return ScopeApplication(self._host.database)

    def _own(self, descriptor: Any) -> bool:
        return any(
            reference.kind == self._KIND and reference.value == self._host.manifest.name
            for reference in descriptor.external_references
        )

    @staticmethod
    def _scope(descriptor: Any) -> PluginScope:
        return PluginScope(
            scope_id=descriptor.scope_id,
            title=descriptor.title,
            summary=descriptor.summary,
            version=descriptor.version,
        )

    async def create(self, *, title: str, summary: str, idempotency_key: str) -> PluginScope:
        from powercontext.builtin.scope.models import ScopeDraft, ScopeExternalReference

        host = self._host
        await host.require("server.admin", None)
        created = await self._application().create(
            ScopeDraft(
                title=title,
                summary=summary,
                idempotency_key=f"{host.manifest.name}:{idempotency_key}",
                external_references=(ScopeExternalReference(kind=self._KIND, value=host.manifest.name),),
            )
        )
        call = _CURRENT.get()
        if host.access is not None and call.request.caller is not None:
            await host.access.bootstrap_static_scope(call.request.caller, created.scope_id, context=call.request.audit)
        return self._scope(created)

    async def update(self, scope_id: str, /, *, expected_version: int, title: str, summary: str) -> PluginScope:
        from powercontext.builtin.scope.models import ScopeMutation

        host = self._host
        application = self._application()
        (current,) = await application.list(scope_ids=(scope_id,))
        if not self._own(current):
            raise AccessDenied("the Scope was not created by this plugin")  # noqa: TRY003
        await host.require("server.admin", None)
        updated = await application.update(
            scope_id,
            ScopeMutation(
                expected_version=expected_version,
                title=title,
                summary=summary,
                parent_scope_id=current.parent_scope_id,
                context_references=current.context_references,
                external_references=current.external_references,
            ),
        )
        return self._scope(updated)

    async def list(self) -> tuple[PluginScope, ...]:
        host = self._host
        visible = []
        for descriptor in await self._application().list():
            if self._own(descriptor) and await _allowed(host, descriptor.scope_id):
                visible.append(self._scope(descriptor))
        return tuple(visible)


async def _allowed(host: _Host, scope_id: str) -> bool:
    try:
        await host.require("scope.read", scope_id)
    except AccessDenied:
        return False
    return True
