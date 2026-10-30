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

# ruff: noqa: TRY301

"""Independent runtime owner: control stays responsive while apply owns scheduling."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from typing import Any
from uuid import uuid4

import httpx

from powercontext.client import PowerContextClient
from powercontext.source_management.config import RuntimeConfig, configuration_digest, secret
from powercontext.source_management.context import PluginContext
from powercontext.source_management.durable import InstanceOwner, RuntimeOwner
from powercontext.source_management.loading import load_runtime_plugin
from powercontext.source_management.models import (
    PROTOCOL_VERSION,
    ManagementError,
    TargetIdentity,
    TargetIdentityRequest,
)
from powercontext.source_management.targets import ManagedTargets

logger = logging.getLogger(__name__)
TERMINAL = {"complete", "failed", "stopped", "cleanup-failed"}


class SourceRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        if configuration_digest(config.plugin, config.config, config.secrets) != config.configuration_digest:
            raise ManagementError(
                "configuration-conflict", "Runtime business parameters do not match the approved digest", 409
            )
        self.plugin = load_runtime_plugin(config)
        self.owner = RuntimeOwner(config.state_dir)
        try:
            self.identity_owner = InstanceOwner(config.server.url, config.instance_id)
        except BaseException:
            self.owner.close()
            raise
        self.local: dict[str, Any] | None = None
        self.generation: str | None = None
        self.task: asyncio.Task[None] | None = None
        self._started_run: str | None = None
        self.context: PluginContext | None = None
        self.targets: ManagedTargets | None = None
        self._client: PowerContextClient | None = None
        self._http: httpx.AsyncClient | None = None
        self._stopping = False

    async def post(self, operation: str, value: dict[str, Any]) -> dict[str, Any]:
        if self._http is None:
            raise ManagementError("runtime-unavailable", "The management transport is not open", 503)
        self._http.headers["Authorization"] = "Bearer " + secret(self.config.management.credential_file)
        response = await self._http.post(
            "/v1/source-management/instances/" + self.config.instance_id + "/" + operation,
            json={**value, **({"generation": self.generation} if self.generation is not None else {})},
        )
        if response.status_code >= 400:
            error = response.json().get("error", {})
            raise ManagementError(
                error.get("code", "management-unavailable"),
                error.get("message", "Source management rejected the request"),
                response.status_code,
            )
        return response.json()

    async def run(self, *, ready=None) -> None:
        try:
            token = secret(self.config.server.credential_file)
            async with (
                PowerContextClient(self.config.server.url, token=token) as client,
                httpx.AsyncClient(base_url=self.config.management.url, timeout=20) as http,
            ):
                try:
                    self._client, self._http = client, http
                    await client.get_access_principal()
                    # This credential is deliberately unusable for registering output Definitions.
                    from powercontext.http import AccessCheckRequest

                    result = await client.check_access(
                        AccessCheckRequest.model_validate({
                            "match": "all",
                            "requirements": [
                                {
                                    "action": "server.admin",
                                    "resource": {"type": "server", "deployment_id": self.config.deployment_id},
                                }
                            ],
                        })
                    )
                    if result.allowed:
                        raise ManagementError(
                            "execution-credential-privileged", "Runtime credentials must not have server.admin", 403
                        )
                    while True:
                        try:
                            previous = (await self.post("owner", {})).get("owner")
                            proof = {"owner": self.owner.identity()}
                            if previous is not None and previous != self.owner.identity():
                                proof.update(
                                    previous_owner=previous, owner_exited=self.owner.exited({"owner": previous})
                                )
                            connected = await self.post(
                                "connect",
                                {
                                    **proof,
                                    "protocol": PROTOCOL_VERSION,
                                    "execution_location": self.config.execution_location,
                                    "configuration_digest": self.config.configuration_digest,
                                    "capabilities": [value.declaration() for value in self.plugin.capabilities],
                                },
                            )
                            self.generation = connected["generation"]
                            if ready is not None:
                                ready()
                                ready = None
                            await self.connected()
                        except (httpx.HTTPError, ManagementError):
                            await asyncio.sleep(0.2)
                        finally:
                            current_task = asyncio.current_task()
                            if self.generation is not None and (current_task is None or not current_task.cancelling()):
                                with suppress(httpx.HTTPError, ManagementError):
                                    await self.post("close", {})
                                self.generation = None
                finally:
                    await self.shutdown_business()
        finally:
            self.owner.close()
            self.identity_owner.close()

    async def shutdown_business(self) -> None:
        """Clean up while the data client remains available to registered resources."""
        self._stopping = True
        if self.context is not None:
            self.context.stop()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.generation is not None:
            if self.local is not None and self.local["status"] in TERMINAL:
                with suppress(httpx.HTTPError, ManagementError):
                    await self.post("execution", self.local)
            with suppress(httpx.HTTPError, ManagementError):
                await self.post("close", {})
            self.generation = None

    async def connected(self) -> None:  # noqa: C901 - explicit protocol state transitions
        async def respond(message):
            try:
                if message["operation"] == "identify":
                    request = TargetIdentityRequest.model_validate(message["arguments"])
                    capability = next(
                        (
                            c
                            for c in self.plugin.capabilities
                            if c.name == request.capability
                            and c.version == request.capability_version
                            and c.kind == "collect"
                        ),
                        None,
                    )
                    if capability is None:
                        raise ManagementError("capability-unavailable", "The collect capability is unavailable")
                    capability.validate_input(request.input)
                    async with asyncio.timeout(10):
                        identity = TargetIdentity.model_validate(
                            await self.plugin.module.identify_target(request, self.plugin.config)
                        )
                    capability.validate_input(identity.normalized_input)
                    result = identity.model_dump(mode="json")
                else:
                    raise ManagementError("unknown-operation", "Unknown Runtime operation")
                payload = {"result": result}
            except ManagementError as error:
                payload = {"error": {"code": error.code, "message": error.message, "status": error.status}}
            except Exception:
                payload = {
                    "error": {"code": "plugin-input-invalid", "message": "The plugin could not process the input"}
                }
            await self.post("reply", {"message_id": message["message_id"], "response": payload})

        async def poll():
            capacity = asyncio.Semaphore(32)

            async def limited(message):
                try:
                    await respond(message)
                finally:
                    capacity.release()

            async with asyncio.TaskGroup() as handlers:
                while True:
                    response = await self.post("poll", {})
                    for message in response["messages"]:
                        if message["generation"] == self.generation:
                            await capacity.acquire()
                            handlers.create_task(limited(message))

        async def synchronize():
            while True:
                state = await self.post("sync", {})
                while state.get("next_cursor"):
                    page = await self.post("sync", {"cursor": state["next_cursor"]})
                    if (page["run_id"], page["enabled"]) != (state["run_id"], state["enabled"]):
                        state = await self.post("sync", {})
                        continue
                    state["targets"].extend(page["targets"])
                    state["stopping"].extend(page["stopping"])
                    state["next_cursor"] = page.get("next_cursor")
                await self.synchronize(state)
                await asyncio.sleep(0.1)

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(poll())
                tasks.create_task(synchronize())
        except* (httpx.HTTPError, ManagementError):
            pass

    async def report_submission(self, report):
        if self.generation is None:
            return
        try:
            async with asyncio.timeout(2):
                await self.post("results", report)
        except (httpx.HTTPError, ManagementError, TimeoutError):
            logger.warning("Source submission report was not confirmed for instance %s", self.config.instance_id)

    async def synchronize(self, state):  # noqa: C901 - explicit protocol state transitions
        if self._client is None:
            raise ManagementError("runtime-unavailable", "Data client is not connected", 503)
        if self.targets is not None:
            self.targets.synchronize(state["targets"], state["stopping"])
        if not state["enabled"]:
            if self.context is not None:
                self.context.stop()
            if self.task is not None and not self.task.done():
                self.task.cancel()
                await self.task
            if self.local is None or self.local["run_id"] != state["run_id"]:
                self.local = {
                    "run_id": state["run_id"],
                    "execution": state["execution"] or uuid4().hex,
                    "status": "stopped",
                }
            if self.local["status"] != "cleanup-failed":
                self.local["status"] = "stopped"
            if state["actual"] != self.local["status"] or state["execution"] != self.local["execution"]:
                await self.post("execution", self.local)
            await self.ack_stops(state)
            return
        if (
            self.local is not None
            and self.local["run_id"] == state["run_id"]
            and (self._started_run == state["run_id"] or self.local["status"] != "running")
        ):
            if state["actual"] != self.local["status"] or state["execution"] != self.local["execution"]:
                await self.post("execution", self.local)
            await self.ack_stops(state)
            return
        if self.task is not None and not self.task.done():
            raise ManagementError("execution-owned", "The old Context is still running", 409)
        if self.local is None or self.local["run_id"] != state["run_id"]:
            self.local = {"run_id": state["run_id"], "execution": uuid4().hex, "status": "running"}
        await self.post("execution", self.local)
        self.targets = ManagedTargets(instance_id=self.config.instance_id, report=self.report_submission)
        self.targets.synchronize(state["targets"], state["stopping"])
        self.context = PluginContext(
            plugin_id=self.config.instance_id,
            connector_name=self.plugin.connector_name,
            connector_version=self.plugin.connector_version,
            plugin_dir=self.config.plugin_dir,
            secrets=self.config.secrets,
            client=self._client,
            registry=self.plugin.registry,
            targets=self.targets,
        )
        self._started_run = state["run_id"]
        self.task = asyncio.create_task(self.business(self.context))
        await self.ack_stops(state)

    async def business(self, ctx):
        status = "complete"
        try:
            await self.plugin.module.apply(ctx, self.plugin.config)
        except asyncio.CancelledError:
            status = "stopped"
        except Exception:
            status = "failed"
        finally:
            try:
                await ctx.close()
            except BaseException:
                status = "cleanup-failed"
            if self.local is not None:
                self.local["status"] = status
            logger.info("Source instance %s: %s", self.config.instance_id, status)

    async def ack_stops(self, state):
        if self.local is None or self.local["status"] == "cleanup-failed":
            return
        active = set() if self.targets is None else self.targets.active_target_ids()
        stops = [s for s in state["stopping"] if s["target_id"] not in active]
        for offset in range(0, len(stops), 256):
            await self.post(
                "stopped-targets",
                {
                    "targets": stops[offset : offset + 256],
                    "run_id": self.local["run_id"],
                    "execution": self.local["execution"],
                },
            )


async def run_runtime(config: RuntimeConfig, *, ready=None) -> None:
    """Run until cancelled or signalled, waiting for all business cleanup before exit."""
    runtime = SourceRuntime(config)
    task = asyncio.create_task(runtime.run(ready=ready))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
