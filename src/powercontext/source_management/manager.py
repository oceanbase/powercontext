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

"""Standalone management authority, reached through its outbound Server channel."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from secrets import compare_digest
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from powercontext.http._generated.models import SourceInstanceExchangeRequest
from powercontext.source_management.channel import ReverseChannel
from powercontext.source_management.config import ManagerConfig, secret
from powercontext.source_management.durable import RuntimeOwner
from powercontext.source_management.models import (
    MAX_ADD_BYTES,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    Caller,
    Capability,
    InstanceRegistration,
    ManagementError,
    SourceTarget,
    TargetIdentity,
    TargetIdentityRequest,
    TargetSubmissionReport,
)
from powercontext.source_management.storage import Store, canonical


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def error_payload(error: ManagementError) -> dict[str, Any]:
    return {"error": {"code": error.code, "message": error.message, "status": error.status}}


def _sync_target_size(snapshot: dict[str, Any]) -> int:
    length = len(canonical(snapshot).encode())
    if length > MAX_MESSAGE_BYTES - 3072:
        raise ManagementError("response-too-large", "One target exceeds the sync limit")
    return length


class SourceManager:
    def __init__(self, config: ManagerConfig) -> None:
        self.config = config
        self.owner = RuntimeOwner(config.state_dir)
        self.store = Store(config.state_dir)
        binding = {
            "server_url": config.server.url,
            "deployment_id": config.deployment_id,
            "manager_id": config.manager_id,
        }
        existing = self.store.get("binding", "deployment")
        if existing is not None and existing != binding:
            self.store.close()
            self.owner.close()
            raise ManagementError("state-binding-conflict", "This management state belongs to another Server", 409)
        self.store.put("binding", "deployment", binding)
        self.channels: dict[str, ReverseChannel] = {}
        self._lock = asyncio.Lock()
        self._generation: str | None = None
        self._catalog_revision = 0
        self._catalog_event = asyncio.Event()
        self._initialize_locks: dict[str, asyncio.Lock] = {}

    def channel(self, instance_id: str) -> ReverseChannel:
        return self.channels.setdefault(
            instance_id,
            ReverseChannel(lease_seconds=self.config.lease_seconds, request_seconds=self.config.request_seconds),
        )

    def instance(self, identifier: str) -> dict[str, Any]:
        instance = self.store.get("instance", identifier)
        if instance is None:
            raise ManagementError("instance-not-found", "The instance is not registered", 404)
        return instance

    def authorize_instance(self, request: Request, identifier: str) -> dict[str, Any]:
        instance = self.instance(identifier)
        supplied = request.headers.get("authorization", "")
        scheme, _, token = supplied.partition(" ")
        digest = hashlib.sha256(token.encode()).hexdigest()
        if scheme.lower() != "bearer" or not compare_digest(digest, instance["credential_digest"]):
            raise ManagementError("unauthorized", "A valid instance credential is required", 401)
        return instance

    def runnable(self, instance: dict[str, Any]) -> bool:
        return self.channel(instance["instance_id"]).online and instance["enabled"] and instance["actual"] == "running"

    def catalog(self) -> dict[str, Any]:
        capabilities: dict[tuple[str, str], dict[str, Any]] = {}
        instances = []
        for instance in self.store.all("instance"):
            availability = "online" if self.runnable(instance) else "unavailable"
            public = {
                "id": instance["instance_id"],
                "availability": availability,
                "scope_ids": instance["scope_ids"],
                "public_principals": instance["public_principals"],
                "desired": "enabled" if instance["enabled"] else "disabled",
                "actual": instance["actual"],
                "run_id": instance["run_id"],
                "updated_at": instance["updated_at"],
            }
            instances.append(public)
            for old_declaration in instance.get("capabilities", []):
                declaration = {k: v for k, v in old_declaration.items() if k in Capability.model_fields}
                key = declaration["name"], declaration["version"]
                item = capabilities.setdefault(key, {**declaration, "instances": []})
                item["instances"].append(public)
                if declaration["name"] in instance["default_capabilities"]:
                    if item.get("default_instance_id") not in (None, instance["instance_id"]):
                        raise ManagementError("default-conflict", "Multiple default instances were declared", 409)
                    item["default_instance_id"] = instance["instance_id"]
        return {"revision": self._catalog_revision, "capabilities": list(capabilities.values()), "instances": instances}

    def changed(self) -> None:
        self._catalog_revision += 1
        self._catalog_event.set()

    async def handle(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        caller = Caller.model_validate(arguments["caller"])
        parameters = arguments["parameters"]
        if (
            caller.deployment != self.config.deployment_id
            or caller.operation != operation
            or caller.scope_id != parameters.get("scope_id")
            or not caller.operation_authorized
        ):
            raise ManagementError(
                "untrusted-context", "The operation is not bound to an authorized Server context", 403
            )
        if operation == "configure":
            return await self.configure(caller, parameters)
        if operation == "set_enabled":
            return await self.set_enabled(caller, parameters)
        if operation == "instances" and parameters.get("view") == "control":
            self.require_admin(caller)
            return {"instances": [self.instance_control(self.instance(parameters["instance_id"]))]}
        if operation == "add":
            return await self.add(caller, parameters)
        if operation == "status":
            target = self.target(parameters["target_id"])
            self.require_target(caller, target)
            return self.target_control(target) if parameters.get("view") == "control" else self.receipt(target)
        if operation == "target_set_enabled":
            return await self.target_set_enabled(caller, parameters)
        raise ManagementError("unknown-operation", "Unknown Source management operation")

    @staticmethod
    def require_admin(caller):
        if not caller.administrator:
            raise ManagementError("forbidden", "This operation requires server.admin", 403)

    async def configure(self, caller, parameters):  # noqa: C901 - explicit protocol state transitions
        self.require_admin(caller)
        data = parameters["registration"]
        if data.get("action") == "initialize_targets":
            identifier = data["instance_id"]
            async with self._initialize_locks.setdefault(identifier, asyncio.Lock()):
                instance = self.instance(identifier)
                if instance.get("targets_initialized"):
                    return {"instance_id": identifier, "reused": True}
                for entry in data.get("targets", []):
                    if entry["scope_id"] not in instance["scope_ids"]:
                        raise ManagementError("scope-mismatch", "Initial target lies outside instance Scopes", 403)
                    await self.add(
                        caller.model_copy(update={"scope_id": entry["scope_id"], "operation": "add"}),
                        {**entry, "instance_id": identifier},
                    )
                # A lost response can safely repeat add; an empty initialized list stays initialized.
                instance = self.instance(identifier)
                instance["targets_initialized"] = True
                self.store.put("instance", identifier, instance)
                return {"instance_id": identifier, "reused": False}
        registration = InstanceRegistration.model_validate(data)
        value = registration.model_dump(mode="json")
        async with self._lock:
            current = self.store.get("instance", registration.instance_id)
            for other in self.store.all("instance"):
                if other["instance_id"] != registration.instance_id and set(other["default_capabilities"]) & set(
                    registration.default_capabilities
                ):
                    raise ManagementError("default-conflict", "This capability already has a default instance", 409)
            if current is not None:
                configured = current.get("registration", {})
                configured = {k: v for k, v in configured.items() if k in InstanceRegistration.model_fields}
                same_deployment = configured == value or (
                    current.get("targets_initialized")
                    and not parameters.get("update")
                    and {k: v for k, v in configured.items() if k != "scope_ids"}
                    == {k: v for k, v in value.items() if k != "scope_ids"}
                )
                if same_deployment and not parameters.get("update"):
                    return {
                        "instance_id": registration.instance_id,
                        "reused": True,
                        "targets_initialized": current.get("targets_initialized", False),
                    }
                extending = (
                    parameters.get("update")
                    and {k: v for k, v in configured.items() if k != "scope_ids"}
                    == {k: v for k, v in value.items() if k != "scope_ids"}
                    and set(current["scope_ids"]) < set(value["scope_ids"])
                )
                if not extending and not (
                    parameters.get("update")
                    and not current["enabled"]
                    and current["actual"] == "stopped"
                    and not self.channel(registration.instance_id).online
                ):
                    raise ManagementError(
                        "instance-conflict", "Stop the instance before changing its configuration", 409
                    )
                desired = current["enabled"]
                current.update(value)
                current["registration"] = value
                current["enabled"] = desired if extending else False
                if not extending:
                    current.update(
                        run_id=uuid4().hex,
                        execution=None,
                        owner=None,
                        actual="stopped",
                    )
                self.store.put("instance", registration.instance_id, current)
            else:
                value.update(
                    registration=registration.model_dump(mode="json"),
                    run_id=uuid4().hex,
                    execution=None,
                    owner=None,
                    actual="pending" if registration.enabled else "stopped",
                    capabilities=[],
                    updated_at=timestamp(),
                    targets_initialized=False,
                )
                self.store.put("instance", registration.instance_id, value)
            self.changed()
            return {
                "instance_id": registration.instance_id,
                "reused": False,
                "targets_initialized": False if current is None else current.get("targets_initialized", False),
            }

    async def set_enabled(self, caller, parameters):
        self.require_admin(caller)
        async with self._lock:
            instance = self.instance(parameters["instance_id"])
            if instance["enabled"] == parameters["enabled"]:
                return self.instance_control(instance)
            if parameters["enabled"]:
                if instance["actual"] != "stopped":
                    raise ManagementError("stop-unconfirmed", "Wait for the previous Context to finish cleanup", 409)
                instance.update(run_id=uuid4().hex, execution=None, actual="pending")
            else:
                instance["actual"] = "stopping"
            instance.update(enabled=parameters["enabled"], updated_at=timestamp())
            self.store.put("instance", instance["instance_id"], instance)
            self.changed()
            return self.instance_control(instance)

    def instance_control(self, instance):
        return {
            "instance_id": instance["instance_id"],
            "enabled": instance["enabled"],
            "actual": instance["actual"],
            "availability": "online" if self.channel(instance["instance_id"]).online else "offline",
        }

    def target(self, identifier):
        target = self.store.get("target", identifier)
        if target is None:
            raise ManagementError("target-not-found", "The target does not exist", 404)
        return target

    def save_work(self, target):
        self.store.put("target", target["target_id"], target)

    @staticmethod
    def require_target(caller, target):
        if caller.scope_id != target["scope_id"]:
            raise ManagementError("scope-mismatch", "The target does not belong to the authorized Scope", 403)

    @staticmethod
    def target_control(target):
        return {
            "kind": "collect",
            "target_id": target["target_id"],
            "instance_id": target["instance_id"],
            "enabled": target["tracking"] == "enabled",
            "tracking": target["tracking"],
        }

    def receipt(self, target):
        instance = self.instance(target["instance_id"])
        return {
            **self.target_control(target),
            "capability": target["capability"],
            "capability_version": target["capability_version"],
            "source_refs": target.get("source_refs", []),
            "last_submitted_at": target.get("last_submitted_at"),
            "availability": "online" if self.runnable(instance) else "unavailable",
        }

    async def identify(self, instance, capability, value):
        request = TargetIdentityRequest(capability=capability.name, capability_version=capability.version, input=value)
        result = TargetIdentity.model_validate(
            await self.channel(instance["instance_id"]).request("identify", request.model_dump(mode="json"))
        )
        capability.validate_input(result.normalized_input)
        return result

    @staticmethod
    def identity(scope, instance, capability, key):
        return canonical([scope, instance, capability.name, capability.version, key])

    async def add(self, caller, parameters):
        if len(canonical(parameters).encode()) > MAX_ADD_BYTES:
            raise ManagementError("input-too-large", "Source add exceeds 1 MiB")
        if not parameters.get("instance_id"):
            raise ManagementError("instance-selection-required", "Select an explicit instance_id")
        instance = self.instance(parameters["instance_id"])
        if caller.scope_id not in instance["scope_ids"]:
            raise ManagementError("scope-mismatch", "The instance cannot serve this Scope", 403)
        declaration = next(
            (
                c
                for c in instance["capabilities"]
                if c["name"] == parameters["capability"] and c["version"] == parameters["capability_version"]
            ),
            None,
        )
        if declaration is None:
            raise ManagementError("capability-unavailable", "The capability version is unavailable", 503)
        capability = Capability.model_validate({k: v for k, v in declaration.items() if k in Capability.model_fields})
        capability.validate_input(parameters["input"])
        if capability.kind != "collect":
            raise ManagementError("capability-unavailable", "write must be loaded and executed by the PC Server")
        identified = await self.identify(instance, capability, parameters["input"])
        identity = self.identity(caller.scope_id, instance["instance_id"], capability, identified.target_key)
        async with self._lock:
            matches = [t for t in self.store.all("target") if t.get("identity") == identity]
            if len(matches) > 1:
                raise ManagementError("target-identity-conflict", "Conflicting collect target identities", 409)
            if matches:
                target = matches[0]
                if target["input"] != identified.normalized_input:
                    raise ManagementError("target-config-conflict", "The target already has different settings", 409)
                self.store.claim_identity(identity, target["target_id"])
                return self.target_control(target)
            instance = self.instance(instance["instance_id"])
            if not self.runnable(instance):
                raise ManagementError("instance-unavailable", "The plugin is not running", 503)
            target: dict[str, Any] = {
                "target_id": "tgt_" + uuid4().hex,
                "instance_id": instance["instance_id"],
                "scope_id": caller.scope_id,
                "capability": capability.name,
                "capability_version": capability.version,
                "input": identified.normalized_input,
                "identity": identity,
                "tracking": "enabled",
                "creator": caller.subject,
                "source_refs": [],
                "updated_at": timestamp(),
            }
            snapshot = {k: target[k] for k in SourceTarget.model_fields if k in target}
            _sync_target_size(snapshot)
            with self.store.transaction():
                self.store.claim_identity(identity, target["target_id"])
                self.save_work(target)
            return self.target_control(target)

    async def target_set_enabled(self, caller, parameters):
        async with self._lock:
            target = self.target(parameters["target_id"])
            self.require_target(caller, target)
            if (target["tracking"] == "enabled") == parameters["enabled"]:
                return self.target_control(target)
            if parameters["enabled"]:
                if target["tracking"] != "stopped":
                    raise ManagementError("stop-unconfirmed", "Wait for target cleanup", 409)
                if caller.scope_id not in self.instance(target["instance_id"])["scope_ids"]:
                    raise ManagementError("scope-mismatch", "The instance cannot serve this Scope", 403)
                declarations = self.instance(target["instance_id"])["capabilities"]
                if not any(
                    c["name"] == target["capability"] and c["version"] == target["capability_version"]
                    for c in declarations
                ):
                    raise ManagementError(
                        "capability-unavailable", "The target's capability version is unavailable", 409
                    )
                target["tracking"] = "enabled"
            else:
                target["tracking"] = "stopping"
            self.save_work(target)
            return self.target_control(target)

    async def connect_instance(self, instance, data):  # noqa: C901 - ownership and contract checks
        declarations = [Capability.model_validate(c).declaration() for c in data["capabilities"]]
        if any(c["kind"] != "collect" for c in declarations):
            raise ManagementError("capability-conflict", "Managed instances may publish only collect capabilities", 409)
        if len({(c["name"], c["version"]) for c in declarations}) != len(declarations):
            raise ManagementError("capability-conflict", "Duplicate capability declarations", 409)
        async with self._lock:
            instance = self.instance(instance["instance_id"])
            if (
                data["protocol"] != PROTOCOL_VERSION
                or data["execution_location"] != instance["execution_location"]
                or data["configuration_digest"] != instance["configuration_digest"]
            ):
                raise ManagementError("instance-binding", "The runtime does not match registration", 409)
            for c in declarations:
                key = canonical([c["name"], c["version"]])
                known = self.store.get("capability", key)
                if known is not None:
                    known = {k: v for k, v in known.items() if k in Capability.model_fields}
                if c["name"] == "content.inline" or (known is not None and known != c):
                    raise ManagementError("capability-conflict", "A capability version cannot change its contract", 409)
            owner = data["owner"]
            old_owner = instance.get("owner")
            # Lease expiry alone never authorizes a second execution process.
            if (
                old_owner is not None
                and old_owner != owner
                and (not data.get("owner_exited") or data.get("previous_owner") != old_owner)
            ):
                raise ManagementError("execution-owned", "The previous process has not confirmed exit", 409)
            if old_owner is None and instance.get("execution") and instance["actual"] != "stopped":
                raise ManagementError("execution-owned", "The existing execution has not confirmed exit", 409)
            offered = {(c["name"], c["version"]) for c in declarations}
            if any(
                t["instance_id"] == instance["instance_id"]
                and (t["capability"], t["capability_version"]) not in offered
                for t in self.store.all("target")
            ):
                raise ManagementError("capability-in-use", "Keep capability versions required by retained targets", 409)
            generation = self.channel(instance["instance_id"]).open(instance["instance_id"])
            try:
                with self.store.transaction():
                    for c in declarations:
                        self.store.put("capability", canonical([c["name"], c["version"]]), c)
                    if owner != old_owner:
                        instance.update(
                            run_id=uuid4().hex, execution=None, actual="pending" if instance["enabled"] else "stopped"
                        )
                    instance.update(owner=owner, capabilities=declarations)
                    self.store.put("instance", instance["instance_id"], instance)
            except BaseException:
                self.channel(instance["instance_id"]).close()
                raise
            self.changed()
            return {"generation": generation, "protocol": PROTOCOL_VERSION}

    async def sync_instance(self, instance, generation, cursor=""):
        self.channel(instance["instance_id"]).check(instance["instance_id"], generation)
        targets, stopping, size, next_cursor, last_id = [], [], 1024, None, cursor
        for target in sorted(self.store.all("target"), key=lambda t: t["target_id"]):
            if target["instance_id"] != instance["instance_id"] or target["target_id"] <= cursor:
                continue
            if target["tracking"] == "stopping":
                snapshot = {"target_id": target["target_id"]}
                destination = stopping
            elif target["tracking"] == "enabled" and target["scope_id"] in instance["scope_ids"]:
                snapshot = {k: target[k] for k in SourceTarget.model_fields if k in target}
                destination = targets
            else:
                continue
            length = _sync_target_size(snapshot)
            if size + length > MAX_MESSAGE_BYTES - 2048:
                next_cursor = last_id
                break
            destination.append(snapshot)
            size += length
            last_id = target["target_id"]
        return {
            "enabled": instance["enabled"],
            "run_id": instance["run_id"],
            "actual": instance["actual"],
            "execution": instance["execution"],
            "targets": targets,
            "stopping": stopping,
            "next_cursor": next_cursor,
        }

    async def execution(self, instance, data):
        async with self._lock:
            instance = self.instance(instance["instance_id"])
            if data["run_id"] != instance["run_id"]:
                raise ManagementError("stale-run", "This report belongs to an old execution", 409)
            if instance["execution"] not in (None, data["execution"]):
                raise ManagementError("execution-owned", "Another Context owns this run", 409)
            if data["status"] == "running" and (
                not instance["enabled"] or instance["actual"] in {"complete", "failed", "stopped", "cleanup-failed"}
            ):
                raise ManagementError("run-terminal", "This business run cannot be restarted", 409)
            if instance["actual"] == "cleanup-failed" and data["status"] != "cleanup-failed":
                raise ManagementError("cleanup-unconfirmed", "Previous resources have not been cleaned up", 409)
            instance.update(execution=data["execution"], actual=data["status"], updated_at=timestamp())
            self.store.put("instance", instance["instance_id"], instance)
            self.changed()
            return {"confirmed": True}

    async def report_result(self, instance, data):
        report = TargetSubmissionReport.model_validate(data)
        target = self.target(report.target_id)
        if target["instance_id"] != instance["instance_id"]:
            raise ManagementError("forbidden", "The target belongs to another instance", 403)
        # This is the last received submission report, not a history or a completed batch.
        target["source_refs"] = [report.source_refs[0].model_dump(mode="json")]
        target["last_submitted_at"] = report.submitted_at
        self.save_work(target)
        return {"confirmed": True}

    async def stopped_targets(self, instance, data):
        async with self._lock:
            instance = self.instance(instance["instance_id"])
            if data["run_id"] != instance["run_id"] or data.get("execution") != instance["execution"]:
                raise ManagementError("execution-owned", "Only the current Context can confirm cleanup", 409)
            if instance["actual"] == "cleanup-failed":
                raise ManagementError("cleanup-unconfirmed", "Resource cleanup failed", 409)
            with self.store.transaction():
                for stop in data["targets"]:
                    target = self.target(stop["target_id"])
                    if target["instance_id"] != instance["instance_id"]:
                        raise ManagementError("forbidden", "The target belongs to another instance", 403)
                    if target["tracking"] == "stopping":
                        target["tracking"] = "stopped"
                        self.save_work(target)
            return {"confirmed": True}

    async def serve_channel(self) -> None:
        headers = {"Authorization": "Bearer " + secret(self.config.server.credential_file)}
        async with httpx.AsyncClient(base_url=self.config.server.url, headers=headers, timeout=20) as client:
            while True:
                try:
                    # Refresh credentials on reconnect; every transport operation is reauthenticated by Server.
                    client.headers["Authorization"] = "Bearer " + secret(self.config.server.credential_file)
                    response = await client.post(
                        "/v1/source-management/channel/open",
                        json={
                            "protocol": PROTOCOL_VERSION,
                            "deployment_id": self.config.deployment_id,
                            "manager_id": self.config.manager_id,
                        },
                    )
                    response.raise_for_status()
                    self._generation = response.json()["generation"]
                    await self._connected(client)
                except (httpx.HTTPError, ManagementError):
                    await asyncio.sleep(0.2)
                finally:
                    if self._generation is not None:
                        with suppress(httpx.HTTPError):
                            await client.post(
                                "/v1/source-management/channel/close", json={"generation": self._generation}
                            )
                        self._generation = None

    async def _connected(self, client: httpx.AsyncClient) -> None:  # noqa: C901 - bounded channel tasks and their cleanup
        generation = self._generation

        async def synchronize() -> None:
            while True:
                self._catalog_event.clear()
                response = await client.post(
                    "/v1/source-management/channel/catalog", json={"generation": generation, "catalog": self.catalog()}
                )
                response.raise_for_status()
                try:
                    async with asyncio.timeout(1):
                        await self._catalog_event.wait()
                except TimeoutError:
                    pass

        async def respond(message: dict[str, Any]) -> None:
            try:
                result = {"result": await self.handle(message["operation"], message["arguments"])}
            except ManagementError as error:
                result = error_payload(error)
            except (ValidationError, KeyError, TypeError, ValueError):
                result = error_payload(ManagementError("invalid-request", "The management request is invalid"))
            response = await client.post(
                "/v1/source-management/channel/reply",
                json={"generation": generation, "message_id": message["message_id"], "response": result},
            )
            response.raise_for_status()

        async def poll() -> None:
            additions, controls = asyncio.Semaphore(16), asyncio.Semaphore(8)

            async def limited_respond(message: dict[str, Any], capacity) -> None:
                try:
                    await respond(message)
                finally:
                    capacity.release()

            async with asyncio.TaskGroup() as requests:
                while True:
                    response = await client.post("/v1/source-management/channel/poll", json={"generation": generation})
                    response.raise_for_status()
                    for message in response.json()["messages"]:
                        if message["generation"] == generation:
                            capacity = additions if message["operation"] == "add" else controls
                            if capacity.locked():
                                response = await client.post(
                                    "/v1/source-management/channel/reply",
                                    json={
                                        "generation": generation,
                                        "message_id": message["message_id"],
                                        "response": error_payload(
                                            ManagementError(
                                                "management-busy", "Too many concurrent Source operations", 503
                                            )
                                        ),
                                    },
                                )
                                response.raise_for_status()
                                continue
                            await capacity.acquire()
                            requests.create_task(limited_respond(message, capacity))

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(synchronize())
                tasks.create_task(poll())
        except* (httpx.HTTPError, ManagementError):
            pass


def create_manager_app(config: ManagerConfig) -> FastAPI:  # noqa: C901 - lifecycle and nested protocol dispatcher
    manager = SourceManager(config)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(manager.serve_channel())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            for channel in manager.channels.values():
                channel.close()
            manager.store.close()
            manager.owner.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.source_manager = manager

    @app.exception_handler(ManagementError)
    async def management_error(request, error):
        return JSONResponse(error_payload(error), status_code=error.status)

    @app.post("/v1/source-management/instances/{instance_id}/{operation}")
    async def instance_endpoint(  # noqa: C901 - one branch per authenticated protocol operation
        instance_id: str, operation: str, request: Request
    ):
        instance = manager.authorize_instance(request, instance_id)
        try:
            content = await request.body()
            from powercontext.limits import MAX_SOURCE_OBSERVATION_BYTES

            if len(content) > MAX_SOURCE_OBSERVATION_BYTES:
                raise ManagementError("request-too-large", "The instance exchange exceeds its size limit")
            data = SourceInstanceExchangeRequest.model_validate_json(content).model_dump(mode="json", exclude_none=True)
            if operation == "owner":
                return {"owner": instance.get("owner")}
            if operation == "connect":
                return await manager.connect_instance(instance, data)
            generation = data["generation"]
            channel = manager.channel(instance_id)
            channel.check(instance_id, generation)
            if operation == "poll":
                return {"messages": await channel.poll(instance_id, generation)}
            if operation == "reply":
                channel.reply(instance_id, generation, data["message_id"], data["response"])
                return {"confirmed": True}
            if operation == "sync":
                return await manager.sync_instance(instance, generation, data.get("cursor", ""))
            if operation == "execution":
                return await manager.execution(instance, data)
            if operation == "results":
                return await manager.report_result(instance, data)
            if operation == "stopped-targets":
                return await manager.stopped_targets(instance, data)
            if operation == "close":
                channel.close(instance_id, generation)
                manager.changed()
                return {"confirmed": True}
        except (KeyError, TypeError, ValidationError) as error:
            raise ManagementError("invalid-request", "The instance request is invalid") from error
        raise ManagementError("unknown-operation", "Unknown instance operation", 404)

    return app
