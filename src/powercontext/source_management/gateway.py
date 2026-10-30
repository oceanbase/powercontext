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

"""Server public Source facade, without a custom-target database or delivery queue."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from powercontext.builtin.authz import AccessAction, AccessAuditContext, ResourceRef, access_control_for_mode
from powercontext.builtin.runtime.models import CaptureSource
from powercontext.http._generated import operations as operations
from powercontext.server.authentication import AuthenticationRequest
from powercontext.server.context import current_principal
from powercontext.source_management.channel import ReverseChannel
from powercontext.source_management.models import (
    BUILTIN_CAPABILITY,
    MAX_ADD_BYTES,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    Caller,
    Capability,
    ManagementError,
)
from powercontext.source_management.storage import canonical


class SourceGateway:
    def __init__(self, settings, write_plugins=None):
        self.settings = settings
        self.write_plugins = write_plugins
        self.channel = ReverseChannel(
            lease_seconds=settings.source_management.lease_seconds,
            request_seconds=settings.source_management.request_seconds,
        )
        self.ready = False
        self._has_catalog = False
        self._contracts = {}
        self._manager_headers = {}
        self._writes: set[asyncio.Task[dict[str, Any]]] = set()
        self._catalog_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.channel.close()
        for task in self._writes:
            task.cancel()
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)

    async def available(self, request: Request) -> bool:
        if not self.ready or not self.channel.online:
            return False
        provider = request.app.state.authentication_provider
        if provider is None:
            return False
        try:
            result = await provider.authenticate(
                AuthenticationRequest(transport="http", headers=self._manager_headers, client_host=None)
            )
        except Exception:
            self.channel.close()
            self.ready = False
            return False
        return f"{result.subject.type}:{result.subject.id}" == self.settings.source_management.manager_subject

    def synchronize(self, catalog: dict[str, Any]) -> None:
        declarations = catalog["capabilities"]
        contracts = dict(self._contracts)
        keys = set()
        for value in declarations:
            declared = {key: value[key] for key in Capability.model_fields if key in value}
            capability = Capability.model_validate(declared)
            if capability.kind != "collect":
                raise ManagementError(
                    "capability-conflict", "The management catalog accepts only collect capabilities", 409
                )
            key = capability.name, capability.version
            if (
                capability.name == BUILTIN_CAPABILITY.name
                or key in keys
                or (self.write_plugins is not None and self.write_plugins.find(*key) is not None)
            ):
                raise ManagementError("capability-conflict", "The capability snapshot has conflicting identities", 409)
            keys.add(key)
            contract = canonical(capability.declaration())
            if contracts.get(key, contract) != contract:
                raise ManagementError("capability-conflict", "A capability version cannot change its contract", 409)
            contracts[key] = contract
        if self.ready and catalog["revision"] < self.channel.catalog["revision"]:
            raise ManagementError("stale-catalog", "The capability snapshot is stale", 409)
        self.channel.catalog = catalog
        self._contracts = contracts
        self._has_catalog = True
        self.ready = True

    async def call(self, request: Request, operation: str, parameters: dict[str, Any]) -> dict[str, Any]:  # noqa: C901 - explicit protocol state transitions
        if operation == "add":
            try:
                size = len(canonical(parameters).encode())
            except (ValueError, UnicodeError) as error:
                raise ManagementError("invalid-input", "Source add parameters must be valid JSON") from error
            if size > MAX_ADD_BYTES:
                raise ManagementError("input-too-large", "Source add parameters exceed 1 MiB")
        control = parameters.get("view") == "control"
        admin = operation in {"configure", "set_enabled", "write_load"} or (operation == "instances" and control)
        if operation == "instances":
            if control and (not parameters.get("instance_id") or set(parameters) - {"instance_id", "view"}):
                raise ManagementError("invalid-query", "Control requires only instance_id and view")
            if not control and not parameters.get("scope_id"):
                raise ManagementError("invalid-query", "Discovery requires scope_id")
        principal = current_principal()
        access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
        scope = parameters.get("scope_id")
        action = (
            AccessAction.SERVER_ADMIN
            if admin
            else (
                AccessAction.SCOPE_CONTRIBUTE
                if operation in {"add", "target_set_enabled"} or (operation == "status" and control)
                else AccessAction.SCOPE_READ
            )
        )
        if access is not None:
            await access.require(
                principal,
                action,
                ResourceRef.server(access.deployment_id) if admin else ResourceRef.scope(scope or ""),
                context=AccessAuditContext(transport="http", operation="pc_source_" + operation),
            )
            if operation == "add":
                await access.require(
                    principal,
                    AccessAction.SCOPE_READ,
                    ResourceRef.scope(scope or ""),
                    context=AccessAuditContext(transport="http", operation="pc_source_" + operation),
                )
        if (
            operation == "configure"
            and parameters["registration"].get("action") == "initialize_targets"
            and access is not None
        ):
            for target in parameters["registration"].get("targets", []):
                for target_action in (AccessAction.SCOPE_READ, AccessAction.SCOPE_CONTRIBUTE):
                    await access.require(
                        principal,
                        target_action,
                        ResourceRef.scope(target["scope_id"]),
                        context=AccessAuditContext(transport="http", operation="source_initialize_targets"),
                    )
        can_write = True
        if operation in {"capabilities", "instances"} and not control and access is not None:
            can_write = (
                await access.check(
                    principal,
                    AccessAction.SCOPE_CONTRIBUTE,
                    ResourceRef.scope(scope or ""),
                    context=AccessAuditContext(transport="http", operation="pc_source_" + operation),
                )
            ).allowed
        caller = Caller(
            deployment=self.settings.access.deployment_id,
            subject="anonymous" if principal is None else f"{principal.type}:{principal.id}",
            scope_id=scope,
            operation=operation,
            administrator=admin,
        )
        if operation == "write_load":
            if self.write_plugins is None:
                raise ManagementError("server-unavailable", "Source write storage is not configured", 503)
            from powercontext.source_management.write import WritePluginConfig

            async with self._catalog_lock:
                return await self.write_plugins.load(
                    WritePluginConfig.model_validate(parameters), reserved_capabilities=set(self._contracts)
                )
        if operation in {"capabilities", "instances"} and not control:
            return self.discover(parameters, caller, can_write, await self.available(request), operation)
        if operation == "add" and (
            parameters["capability"] == BUILTIN_CAPABILITY.name
            or (
                self.write_plugins is not None
                and (
                    self.write_plugins.find(parameters["capability"], parameters["capability_version"]) is not None
                    or (
                        parameters.get("instance_id") is None
                        and any(c.name == parameters["capability"] for c in self.write_plugins.capabilities())
                    )
                )
            )
        ):
            if parameters.get("instance_id") is not None:
                raise ManagementError("invalid-input", "write does not accept instance_id")
            result = await self.write_add(request, caller, parameters)
        else:
            if not await self.available(request):
                raise ManagementError("management-unavailable", "Source management is unavailable", 503)
            call = self.channel.request(
                operation,
                {"caller": caller.model_dump(mode="json"), "parameters": parameters},
                wait_seconds=self.settings.source_management.write_wait_timeout_seconds if operation == "add" else None,
            )
            if operation == "add":
                pending = asyncio.create_task(call)
                try:
                    while not pending.done():
                        await asyncio.wait({pending}, timeout=1)
                        await self.require_result_read(request, caller)
                    result = await pending
                finally:
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            else:
                result = await call
        if operation == "add" or (operation == "status" and not control):
            await self.require_result_read(request, caller)
        return result

    def discover(self, parameters, caller, can_write, online, operation):  # noqa: C901 - explicit protocol state transitions
        capabilities = []
        instances = {}
        if parameters.get("instance_id") is None and parameters.get("capability") in (None, BUILTIN_CAPABILITY.name):
            capabilities.append({**BUILTIN_CAPABILITY.declaration(), "instances": [], "can_write": can_write})
        if self.write_plugins is not None and parameters.get("instance_id") is None:
            for capability in self.write_plugins.capabilities():
                if parameters.get("capability") in (None, capability.name):
                    capabilities.append({**capability.declaration(), "instances": [], "can_write": can_write})
        if self._has_catalog:
            for capability in self.channel.catalog["capabilities"]:
                if parameters.get("capability") not in (None, capability["name"]):
                    continue
                visible = []
                for instance in capability["instances"]:
                    if caller.scope_id not in instance["scope_ids"]:
                        continue
                    if caller.subject not in instance["public_principals"] and "*" not in instance["public_principals"]:
                        continue
                    if parameters.get("instance_id") not in (None, instance["id"]):
                        continue
                    public = {
                        k: v for k, v in instance.items() if k not in {"scope_ids", "public_principals", "run_id"}
                    }
                    public["can_write"] = can_write
                    if not online:
                        public["availability"] = "unavailable"
                    visible.append(public)
                    instances[instance["id"]] = public
                if visible:
                    item = {**capability, "instances": visible, "can_write": can_write}
                    if item.get("default_instance_id") not in {v["id"] for v in visible}:
                        item.pop("default_instance_id", None)
                    capabilities.append(item)
        return {
            "capabilities": capabilities,
            "management": "online"
            if online
            else ("not-configured" if self.settings.source_management.manager_subject is None else "unavailable"),
            **({"instances": list(instances.values())} if operation == "instances" else {}),
        }

    async def builtin_add(self, request, caller, parameters):
        if parameters["capability_version"] != BUILTIN_CAPABILITY.version or parameters.get("instance_id") is not None:
            raise ManagementError("capability-unavailable", "Use the discovered built-in text capability")
        value = parameters["input"]
        BUILTIN_CAPABILITY.validate_input(value)
        if not value["text"].strip():
            raise ManagementError("invalid-input", "Text must contain non-whitespace content")
        from powercontext.server.app import _reject_reserved_handoff_receipt_content

        _reject_reserved_handoff_receipt_content(value["text"])
        receipt = await request.app.state.application.sources.for_scope(caller.scope_id).capture(
            CaptureSource(source_id=value["source_id"], content=value["text"], metadata=value.get("metadata") or {})
        )
        return {
            "kind": "write",
            "result": {"outcome": "saved", "source_refs": [receipt.source_ref.model_dump(mode="json")]},
        }

    async def write_add(self, request, caller, parameters):
        if len(self._writes) >= 64:
            raise ManagementError("server-busy", "Too many writes are awaiting completion", 503)

        async def authorize():
            await self.require_result_read(request, caller)
            access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
            if access is not None:
                await access.require(
                    current_principal(),
                    AccessAction.SCOPE_CONTRIBUTE,
                    ResourceRef.scope(caller.scope_id),
                    context=AccessAuditContext(transport="http", operation="pc_source_add"),
                )

        async def capture():
            if parameters["capability"] == BUILTIN_CAPABILITY.name:
                await authorize()
                return await self.builtin_add(request, caller, parameters)
            if self.write_plugins is None:
                raise ManagementError("capability-unavailable", "The write plugin is not loaded", 503)
            return await self.write_plugins.write(
                request.app.state.application,
                caller.scope_id,
                parameters["capability"],
                parameters["capability_version"],
                parameters["input"],
                authorize,
            )

        task = asyncio.create_task(capture())
        self._writes.add(task)
        try:
            async with asyncio.timeout(self.settings.source_management.write_wait_timeout_seconds):
                while not task.done():
                    await asyncio.wait({task}, timeout=1)
                    await self.require_result_read(request, caller)
                return await task
        except TimeoutError as error:
            raise ManagementError(
                "result-unconfirmed", "The write result was not confirmed; the Source may already be saved", 503
            ) from error
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._writes.discard(task)

    async def require_result_read(self, request, caller):
        access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
        if access is not None:
            await access.require(
                current_principal(),
                AccessAction.SCOPE_READ,
                ResourceRef.scope(caller.scope_id or ""),
                context=AccessAuditContext(transport="http", operation="pc_source_result"),
            )

    async def channel_operation(self, request: Request, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        if len(canonical(data).encode()) > MAX_MESSAGE_BYTES:
            raise ManagementError("request-too-large", "The management exchange exceeds its size limit")
        principal = current_principal()
        if principal is None or f"{principal.type}:{principal.id}" != self.settings.source_management.manager_subject:
            raise ManagementError("forbidden", "This identity is not bound to the Source management channel", 403)
        owner = self.settings.source_management.manager_subject
        if operation == "open":
            if (
                data["protocol"] != PROTOCOL_VERSION
                or data["deployment_id"] != self.settings.access.deployment_id
                or data["manager_id"] != self.settings.source_management.manager_id
            ):
                raise ManagementError("manager-binding", "The management binding or protocol does not match", 403)
            generation = self.channel.open(owner)
            self._manager_headers = {"authorization": request.headers.get("authorization", "")}
            self.ready = False
            return {"generation": generation, "protocol": PROTOCOL_VERSION}
        generation = data["generation"]
        self.channel.check(owner, generation)
        if operation == "catalog":
            async with self._catalog_lock:
                self.synchronize(data["catalog"])
        elif operation == "poll":
            return {"messages": await self.channel.poll(owner, generation)}
        elif operation == "reply":
            self.channel.reply(owner, generation, data["message_id"], data["response"])
        elif operation == "close":
            self.channel.close(owner, generation)
            self.ready = False
        return {"confirmed": True}


def mount_source_routes(app: FastAPI) -> None:
    app.state.source_gateway = None

    @app.exception_handler(ManagementError)
    async def source_error(request, error):
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message, "details": None}}, status_code=error.status
        )

    def endpoint(operation, model, channel=False):
        async def call(body, request: Request):
            if operation == "add" and len(await request.body()) > MAX_ADD_BYTES:
                raise ManagementError("input-too-large", "Source add parameters exceed 1 MiB")
            gateway = request.app.state.source_gateway
            if gateway is None:
                raise ManagementError("server-unavailable", "The Server is not ready", 503)
            data = body.model_dump(mode="json", exclude_none=True)
            return await (
                gateway.channel_operation(request, operation, data)
                if channel
                else gateway.call(request, operation, data)
            )

        call.__annotations__["body"] = model
        return call

    specs = [
        ("capabilities", operations.PC_SOURCE_CAPABILITIES),
        ("add", operations.PC_SOURCE_ADD),
        ("status", operations.PC_SOURCE_STATUS),
        ("target_set_enabled", operations.PC_SOURCE_TARGET_SET_ENABLED),
        ("instances", operations.PC_SOURCE_INSTANCES),
        ("set_enabled", operations.PC_SOURCE_INSTANCE_SET_ENABLED),
        ("configure", operations.SOURCE_CONFIGURE),
        ("write_load", operations.LOAD_SOURCE_WRITE_PLUGIN),
    ]
    specs += [
        (name, getattr(operations, "SOURCE_CHANNEL_" + name.upper()))
        for name in ("open", "catalog", "poll", "reply", "close")
    ]
    for name, operation in specs:
        app.add_api_route(
            operation.path,
            endpoint(name, operation.request_type, name in {"open", "catalog", "poll", "reply", "close"}),
            methods=[operation.method],
            operation_id=operation.operation_id,
            response_model=operation.response_type,
            response_model_exclude_none=True,
            status_code=operation.success_status,
        )
