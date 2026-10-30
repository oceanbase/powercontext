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

"""HTTP/MCP integration and fault recovery for the Source management boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sys
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sse_starlette.sse import AppStatus

from examples.source_plugins.tickets.definition import TicketCapture
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig as BuiltinRuntimeConfig
from powercontext.client import PowerContextClient, register_source_definitions
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    MetricsConfig,
    ServerSettings,
    SourceManagementConfig,
)
from powercontext.source_management.config import Endpoint, ManagerConfig, RuntimeConfig, configuration_digest
from powercontext.source_management.loading import load_runtime_plugin
from powercontext.source_management.manager import create_manager_app
from powercontext.source_management.models import InstanceRegistration, ManagementError
from powercontext.source_management.runtime import SourceRuntime
from powercontext.source_management.storage import Store
from tests.source_helpers import protected, wait_until

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/source_plugins"


@pytest.fixture(scope="module")
def scenario_runner():
    # The scenarios share one event loop, as the HTTP and MCP service process does.
    with asyncio.Runner() as runner:
        yield runner


class FixtureServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Several isolated services share pytest's process; its signal handler owns shutdown.
        yield


@asynccontextmanager
async def listen(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    service = FixtureServer(uvicorn.Config(app, log_level="error", lifespan="off"))
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(service.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not service.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield url
        finally:
            service.should_exit = True
            await asyncio.wait_for(task, 10)
            sock.close()


@asynccontextmanager
async def source_server(tmp_path, *, management=True):
    # SSE keeps process-level shutdown state; a fresh test Server represents a fresh service process.
    AppStatus.should_exit = False
    names = ("deployer", "host", "reader", "manager", "worker", "worker-two")
    tokens = {name: f"source-test-{name}" for name in names}
    identities = protected(
        tmp_path / "identities.json",
        json.dumps({
            "identities": [
                {
                    "credential_id": name,
                    "token_digest": hashlib.sha256(tokens[name].encode()).hexdigest(),
                    "subject": {"type": "service", "id": name},
                }
                for name in names
            ]
        }),
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            auth=BearerAuthConfig(identities_file=identities, administrators=("service:deployer",)),
            access=AccessControlConfig(mode="enforced"),
            metrics=MetricsConfig(enabled=False),
            runtime=BuiltinRuntimeConfig(artifact_processing_families=()),
            source_management=SourceManagementConfig(
                manager_subject="service:manager" if management else None,
                lease_seconds=3,
                request_seconds=2,
            ),
        ),
    )
    async with (
        listen(app) as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}) as admin,
    ):
        response = await admin.post(
            "/v1/scopes", json={"title": "Sources", "summary": "Managed", "idempotency_key": "main"}
        )
        assert response.status_code == 201, response.text
        scope = response.json()["scope_id"]
        for name in ("host", "reader", "worker", "worker-two"):
            response = await admin.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": "service", "id": name},
                    "resource": {"type": "scope", "scope_id": scope},
                    "role": "scope.viewer" if name == "reader" else "scope.contributor",
                    "idempotency_key": "bind-" + name,
                },
            )
            assert response.status_code == 201, response.text
        yield app, url, tokens, scope


async def tool(client, name, arguments):
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    return result.data


async def retry_tool(client, name, arguments):
    async with asyncio.timeout(15):
        while True:
            try:
                return await tool(client, name, arguments)
            except ToolError as error:
                if not any(
                    code in str(error)
                    for code in ("result-unconfirmed", "management-unavailable", "instance-unavailable")
                ):
                    raise
                await asyncio.sleep(0.1)


async def status(client, scope, target):
    return await tool(client, "pc_source_status", {"scope_id": scope, **target})


@asynccontextmanager
async def management(tmp_path, url, tokens):
    credential = protected(tmp_path / "manager-token", tokens["manager"])
    app = create_manager_app(
        ManagerConfig(
            server=Endpoint(url=url, credential_file=credential),
            deployment_id="powercontext",
            manager_id="sources",
            state_dir=tmp_path / "management",
            request_seconds=2,
            lease_seconds=3,
        )
    )
    async with listen(app) as manager_url:
        yield app, manager_url


async def configured_runtime(
    tmp_path,
    server_url,
    manager_url,
    tokens,
    scope,
    *,
    identifier="tickets-one",
    worker="worker",
    plugin=str(EXAMPLES / "tickets"),
    config=None,
    default_capabilities=None,
):
    config = config or {"poll_interval": 0.1}
    digest = configuration_digest(plugin, config, {})
    plugin_dir = tmp_path / identifier
    plugin_dir.mkdir()
    (plugin_dir / "tickets.json").write_text(json.dumps([{"id": "A", "body": "Alpha"}, {"id": "B", "body": "Bravo"}]))
    pc_credential = protected(plugin_dir / "pc-token", tokens[worker])
    instance_token = "instance-test-" + identifier
    management_credential = protected(plugin_dir / "instance-token", instance_token)
    async with httpx.AsyncClient(
        base_url=server_url, headers={"Authorization": "Bearer " + tokens["deployer"]}
    ) as client:
        await wait_until(
            lambda: client.post(
                "/v1/source-management/configure",
                json={
                    "registration": InstanceRegistration(
                        instance_id=identifier,
                        credential_digest=hashlib.sha256(instance_token.encode()).hexdigest(),
                        execution_location=str(plugin_dir),
                        scope_ids=(scope,),
                        public_principals=("service:host", "service:reader"),
                        default_capabilities=(("tickets.item",) if identifier == "tickets-one" else ())
                        if default_capabilities is None
                        else default_capabilities,
                        configuration_digest=digest,
                    ).model_dump(mode="json")
                },
            ),
            lambda response: response.status_code == 200,
        )
    runtime_config = RuntimeConfig(
        instance_id=identifier,
        plugin=plugin,
        plugin_dir=plugin_dir,
        management=Endpoint(url=manager_url, credential_file=management_credential),
        server=Endpoint(url=server_url, credential_file=pc_credential),
        state_dir=plugin_dir / "state",
        execution_location=str(plugin_dir),
        configuration_digest=digest,
        config=config,
    )
    async with PowerContextClient(server_url, token=tokens["deployer"]) as deployer:
        await register_source_definitions(client=deployer, registry=load_runtime_plugin(runtime_config).registry)
    return runtime_config


@asynccontextmanager
async def running(config):
    runtime = SourceRuntime(config)
    task = asyncio.create_task(runtime.run())
    try:
        yield runtime
        if task.done():
            await task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def runtime_process(config):
    path = protected(config.plugin_dir / "runtime-process.json", config.model_dump_json(indent=2))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "powercontext.source_management.cli",
        "runtime",
        "run",
        "--config",
        str(path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            _stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        assert process.returncode in (0, -9), stderr.decode()


async def discover_running(host, scope, name="tickets.item"):
    return await wait_until(
        lambda: tool(host, "pc_source_capabilities", {"scope_id": scope}),
        lambda r: any(c["name"] == name and c["instances"][0]["actual"] == "running" for c in r["capabilities"]),
    )


def test_collect_dynamic_targets_and_pc_permissions(tmp_path, scenario_runner):
    async def scenario():
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(tmp_path, url, manager_url, tokens, scope)
            async with running(config), Client(url + "/mcp", auth=tokens["host"]) as host:
                await discover_running(host, scope)
                write_entry = tmp_path / "ticket_write.py"
                write_entry.write_text("""from powercontext.source_management import Capability
from examples.source_plugins.tickets.definition import TicketCapture, TicketDefinition
definitions = (TicketDefinition(),)
capabilities = (Capability(name="tickets.item", version="2", kind="write", description="Write a ticket snapshot",
    definition_name="example-ticket", definition_version="1", input_schema=TicketCapture.model_json_schema()),)
""")
                async with httpx.AsyncClient(
                    base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}
                ) as admin:
                    loaded = await admin.post(
                        "/v1/source-management/write/load", json={"id": "ticket-write", "entry": str(write_entry)}
                    )
                    assert loaded.status_code == 200, loaded.text
                written = await tool(
                    host,
                    "pc_source_add",
                    {
                        "scope_id": scope,
                        "capability": "tickets.item",
                        "capability_version": "2",
                        "input": {
                            "namespace": "host",
                            "project": "test",
                            "ticket_id": "W",
                            "title": "",
                            "body": "Write",
                        },
                    },
                )
                assert written["kind"] == "write" and written["result"]["source_refs"]
                args = {
                    "scope_id": scope,
                    "instance_id": config.instance_id,
                    "capability": "tickets.item",
                    "capability_version": "1",
                    "input": {"id": "A"},
                }
                a, repeated = await asyncio.gather(tool(host, "pc_source_add", args), tool(host, "pc_source_add", args))
                assert a["target_id"] == repeated["target_id"]
                saved = await wait_until(
                    lambda: status(host, scope, {"target_id": a["target_id"]}), lambda r: bool(r["source_refs"])
                )
                assert saved["last_submitted_at"]
                b = await tool(host, "pc_source_add", {**args, "input": {"id": "B"}})
                await wait_until(
                    lambda: status(host, scope, {"target_id": b["target_id"]}), lambda r: bool(r["source_refs"])
                )
                stop = {"scope_id": scope, "target_id": b["target_id"], "enabled": False}
                await tool(host, "pc_source_target_set_enabled", stop)
                await tool(host, "pc_source_target_set_enabled", stop)
                await wait_until(
                    lambda: status(host, scope, {"target_id": b["target_id"]}), lambda r: r["tracking"] == "stopped"
                )
                repeated = await tool(host, "pc_source_add", {**args, "input": {"id": "B"}})
                assert repeated["tracking"] == "stopped"
                async with Client(url + "/mcp", auth=tokens["worker-two"]) as other:
                    # Another contributor can manage the same Scope's target without a manager ACL.
                    resumed = await tool(other, "pc_source_target_set_enabled", {**stop, "enabled": True})
                    assert resumed["tracking"] == "enabled"
                async with Client(url + "/mcp", auth=tokens["reader"]) as reader:
                    assert (await status(reader, scope, {"target_id": a["target_id"]}))["source_refs"]
                    with pytest.raises(ToolError):
                        await tool(reader, "pc_source_target_set_enabled", stop)
                with pytest.raises(ToolError):
                    await tool(
                        host, "pc_source_instance_set_enabled", {"instance_id": config.instance_id, "enabled": False}
                    )

    scenario_runner.run(scenario())


def test_collect_status_keeps_only_the_last_received_submission(tmp_path, scenario_runner):
    async def scenario():
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(tmp_path, url, manager_url, tokens, scope)
            async with (
                running(config) as runtime,
                Client(url + "/mcp", auth=tokens["host"]) as host,
                httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["host"]}) as http,
            ):
                await discover_running(host, scope)
                arguments = {
                    "scope_id": scope,
                    "instance_id": config.instance_id,
                    "capability": "tickets.item",
                    "capability_version": "1",
                    "input": {"id": "missing"},
                }
                added = await tool(host, "pc_source_add", arguments)
                target_id = added["target_id"]
                assert "source_refs" not in added and "last_submitted_at" not in added
                initial = await status(host, scope, {"target_id": target_id})
                assert initial["source_refs"] == [] and initial.get("last_submitted_at") is None
                references = [
                    {"source_type": "example-ticket", "source_id": "a" * 256},
                    {"source_type": "example-chat", "source_id": '界\\"\n' * 63 + "xxxx"},
                ]
                # Each report confirms one item. Arrival order, not the Runtime clock, selects the state.
                # Reporting an earlier Source again is still a new confirmation, not a history union.
                reports = [
                    {
                        "target_id": target_id,
                        "source_refs": [reference],
                        "submitted_at": f"2026-10-26T00:00:0{3 - index}+00:00",
                    }
                    for index, reference in enumerate([*references, references[0]])
                ]
                for report in reports:
                    assert await runtime.post("results", report) == {"confirmed": True}
                    response = await http.post(
                        "/v1/source-management/status", json={"scope_id": scope, "target_id": target_id}
                    )
                    assert response.status_code == 200, response.text
                    repeated = await tool(host, "pc_source_add", arguments)
                    assert repeated == added
                    receipt = response.json()
                    assert receipt["target_id"] == target_id
                    assert receipt["source_refs"] == report["source_refs"]
                    assert receipt["last_submitted_at"] == report["submitted_at"]
                    assert receipt["tracking"] == added["tracking"]
                repeated_http = await http.post("/v1/source-management/add", json=arguments)
                assert repeated_http.status_code == 200, repeated_http.text
                assert repeated_http.json() == added
                with pytest.raises(ManagementError, match="instance request is invalid"):
                    await runtime.post("results", {**reports[-1], "source_refs": references})
                unchanged = await status(host, scope, {"target_id": target_id})
                assert unchanged["source_refs"] == reports[-1]["source_refs"]
                assert unchanged["last_submitted_at"] == reports[-1]["submitted_at"]
        store = Store(tmp_path / "management")
        try:
            persisted = store.get("target", target_id)
            assert persisted is not None
            assert persisted["source_refs"] == reports[-1]["source_refs"]
            assert persisted["last_submitted_at"] == reports[-1]["submitted_at"]
        finally:
            store.close()

    scenario_runner.run(scenario())


def test_discovery_uses_scope_and_keeps_control_permissions(tmp_path, scenario_runner):
    async def scenario():
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
            httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}) as admin,
        ):
            config = await configured_runtime(tmp_path, url, manager_url, tokens, scope)
            async with (
                running(config),
                Client(url + "/mcp", auth=tokens["host"]) as host,
                Client(url + "/mcp", auth=tokens["reader"]) as reader,
                Client(url + "/mcp", auth=tokens["deployer"]) as deployer,
            ):
                await discover_running(host, scope)
                listed = await tool(host, "pc_source_instances", {"scope_id": scope})
                assert [i["id"] for i in listed["instances"]] == [config.instance_id]
                filtered = await tool(
                    host, "pc_source_capabilities", {"scope_id": scope, "instance_id": config.instance_id}
                )
                assert [c["name"] for c in filtered["capabilities"]] == ["tickets.item"]
                query = {"scope_id": scope, "capability": "tickets.item", "instance_id": config.instance_id}
                capabilities = await tool(host, "pc_source_capabilities", query)
                assert [c["name"] for c in capabilities["capabilities"]] == ["tickets.item"]
                assert capabilities["capabilities"][0]["kind"] == "collect"
                instances = await tool(host, "pc_source_instances", query)
                assert [i["id"] for i in instances["instances"]] == [config.instance_id]
                assert instances["instances"][0]["can_write"]
                for name in ("pc_source_capabilities", "pc_source_instances"):
                    visible = await tool(reader, name, query)
                    assert visible["capabilities"] and not visible["capabilities"][0]["can_write"]
                    definition = next(t for t in await host.list_tools() if t.name == name)
                    assert "operation" not in definition.inputSchema["properties"]
                for endpoint in ("capabilities", "instances"):
                    response = await admin.post(
                        "/v1/source-management/" + endpoint, json={"scope_id": scope, "operation": "add"}
                    )
                    assert response.status_code == 422

                hidden_scopes = []
                for key in ("unserved", "unauthorized"):
                    response = await admin.post(
                        "/v1/scopes", json={"title": key, "summary": "Discovery boundary", "idempotency_key": key}
                    )
                    assert response.status_code == 201, response.text
                    hidden_scopes.append(response.json()["scope_id"])
                unserved, unauthorized = hidden_scopes
                response = await admin.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": "service", "id": "host"},
                        "resource": {"type": "scope", "scope_id": unserved},
                        "role": "scope.contributor",
                        "idempotency_key": "unserved-host",
                    },
                )
                assert response.status_code == 201, response.text
                for name in ("pc_source_capabilities", "pc_source_instances"):
                    empty = await tool(host, name, {**query, "scope_id": unserved})
                    assert empty["capabilities"] == []
                    with pytest.raises(ToolError):
                        await tool(host, name, {**query, "scope_id": unauthorized})

                control = {"instance_id": config.instance_id, "view": "control"}
                state = await tool(deployer, "pc_source_instances", control)
                assert state["instances"] == [
                    {"instance_id": config.instance_id, "enabled": True, "actual": "running", "availability": "online"}
                ]
                for client in (host, reader):
                    with pytest.raises(ToolError):
                        await tool(client, "pc_source_instances", control)
                assert (await admin.post("/v1/source-management/instances", json={})).status_code == 400
                assert (
                    await admin.post("/v1/source-management/instances", json={**control, "scope_id": scope})
                ).status_code == 400

    scenario_runner.run(scenario())


def test_builtin_write_uses_source_identity_without_management(tmp_path, scenario_runner):
    async def scenario():
        async with (
            source_server(tmp_path, management=False) as (_app, url, tokens, scope),
            Client(url + "/mcp", auth=tokens["host"]) as host,
        ):
            args = {
                "scope_id": scope,
                "capability": "content.inline",
                "capability_version": "2",
                "input": {"source_id": "doc-a", "text": "Alpha"},
            }
            first = await tool(host, "pc_source_add", args)
            second = await tool(host, "pc_source_add", args)
            assert first["result"]["source_refs"] == second["result"]["source_refs"]
            assert "request_id" not in first and first["kind"] == "write"
            with pytest.raises(ToolError):
                await tool(host, "pc_source_add", {**args, "input": {"source_id": "doc-a", "text": "Different"}})

    scenario_runner.run(scenario())


def test_collect_continues_offline_and_reconnects_without_restarting(tmp_path, scenario_runner, monkeypatch):
    async def scenario():
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(tmp_path, url, manager_url, tokens, scope)
            async with (
                Client(url + "/mcp", auth=tokens["host"]) as host,
                httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["worker"]}) as worker,
            ):
                args = {
                    "scope_id": scope,
                    "instance_id": config.instance_id,
                    "capability": "tickets.item",
                    "capability_version": "1",
                    "input": {"id": "A"},
                }
                checkpoint_args = {
                    "binding": {
                        "scope_id": scope,
                        "binding_id": config.instance_id,
                        "connector_name": "tickets",
                        "connector_version": "1",
                    }
                }
                async with running(config) as runtime:
                    await discover_running(host, scope)
                    target = (await tool(host, "pc_source_add", args))["target_id"]
                    await wait_until(
                        lambda: status(host, scope, {"target_id": target}), lambda r: bool(r["source_refs"])
                    )
                    original = runtime.post
                    offline = False

                    async def exchange(operation, value):
                        if offline:
                            raise httpx.ConnectError("simulated management outage")  # noqa: TRY003
                        return await original(operation, value)

                    monkeypatch.setattr(runtime, "post", exchange)
                    offline = True
                    await asyncio.sleep(0.2)
                    (config.plugin_dir / "tickets.json").write_text('[{"id":"A","body":"Changed while disconnected"}]')
                    expected = TicketCapture(
                        namespace="tickets",
                        project="default",
                        ticket_id="A",
                        title="",
                        body="Changed while disconnected",
                    ).snapshot_id()
                    await wait_until(
                        lambda: worker.post("/v1/connector-checkpoints/get", json=checkpoint_args),
                        lambda r: (
                            r.status_code == 200
                            and (r.json().get("checkpoint") or {}).get("seen", {}).get(target) == expected
                        ),
                    )
                    stopped = await tool(
                        host, "pc_source_target_set_enabled", {"scope_id": scope, "target_id": target, "enabled": False}
                    )
                    assert stopped["tracking"] == "stopping"
                    offline = False
                    await wait_until(
                        lambda: status(host, scope, {"target_id": target}), lambda r: r["tracking"] == "stopped"
                    )

    scenario_runner.run(scenario())


def test_runtime_process_restart_reruns_enabled_apply(tmp_path, scenario_runner):
    async def scenario():
        plugin = tmp_path / "single.py"
        plugin.write_text("""from examples.source_plugins.tickets import Config, capabilities, connector_name, connector_version, definitions, identify_target, plugin_api_version, TicketConnector
async def apply(ctx, config):
    ctx.connect(TicketConnector(ctx, config))
    path = ctx.plugin_dir / "starts.txt"
    count = int(path.read_text()) if path.exists() else 0
    path.write_text(str(count + 1))
""")
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(tmp_path, url, manager_url, tokens, scope, plugin=str(plugin))
            async with httpx.AsyncClient(
                base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}
            ) as admin:

                async def control():
                    return await admin.post(
                        "/v1/source-management/instances", json={"instance_id": config.instance_id, "view": "control"}
                    )

                async with runtime_process(config):
                    await wait_until(
                        control, lambda r: r.status_code == 200 and r.json()["instances"][0]["actual"] == "complete"
                    )
                    assert (config.plugin_dir / "starts.txt").read_text() == "1"
                    await admin.post(
                        "/v1/source-management/instance-set-enabled",
                        json={"instance_id": config.instance_id, "enabled": True},
                    )
                    await asyncio.sleep(0.3)
                    assert (config.plugin_dir / "starts.txt").read_text() == "1"
                async with runtime_process(config):
                    await wait_until(
                        control,
                        lambda r: (
                            r.status_code == 200
                            and r.json()["instances"][0]["actual"] == "complete"
                            and (config.plugin_dir / "starts.txt").read_text() == "2"
                        ),
                    )

    scenario_runner.run(scenario())


def test_reenable_survives_lost_start_confirmation(tmp_path, scenario_runner, monkeypatch):
    async def scenario():
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(tmp_path, url, manager_url, tokens, scope)
            async with (
                running(config) as runtime,
                httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}) as admin,
            ):

                async def control():
                    return await admin.post(
                        "/v1/source-management/instances", json={"instance_id": config.instance_id, "view": "control"}
                    )

                await wait_until(
                    control, lambda r: r.status_code == 200 and r.json()["instances"][0]["actual"] == "running"
                )
                response = await admin.post(
                    "/v1/source-management/instance-set-enabled",
                    json={"instance_id": config.instance_id, "enabled": False},
                )
                assert response.status_code == 200
                await wait_until(control, lambda r: r.json()["instances"][0]["actual"] == "stopped")
                original = runtime.post
                lose = True

                async def exchange(operation, value):
                    nonlocal lose
                    result = await original(operation, value)
                    if operation == "execution" and value.get("status") == "running" and lose:
                        lose = False
                        raise httpx.ConnectError("lost start confirmation")  # noqa: TRY003
                    return result

                monkeypatch.setattr(runtime, "post", exchange)
                response = await admin.post(
                    "/v1/source-management/instance-set-enabled",
                    json={"instance_id": config.instance_id, "enabled": True},
                )
                assert response.status_code == 200
                await wait_until(
                    control, lambda r: r.status_code == 200 and r.json()["instances"][0]["actual"] == "running"
                )
                async with Client(url + "/mcp", auth=tokens["host"]) as host:
                    added = await retry_tool(
                        host,
                        "pc_source_add",
                        {
                            "scope_id": scope,
                            "instance_id": config.instance_id,
                            "capability": "tickets.item",
                            "capability_version": "1",
                            "input": {"id": "A"},
                        },
                    )
                    await wait_until(
                        lambda: status(host, scope, {"target_id": added["target_id"]}),
                        lambda result: bool(result["source_refs"]),
                    )

    scenario_runner.run(scenario())


def test_managed_plugin_can_collect_autonomously_without_targets(tmp_path, scenario_runner):
    async def scenario():
        plugin = tmp_path / "autonomous.py"
        plugin.write_text("""from examples.source_plugins.tickets import Config, capabilities, connector_name, connector_version, definitions, identify_target, plugin_api_version, TicketCapture, ticket_definition
from powercontext.sources import ConnectorRunCompletion, ConnectorRunStatus
class Connector:
    name = connector_name
    version = connector_version
    source_definitions = frozenset({ticket_definition.name})
    async def run(self, session):
        await session.submit("ticket", ticket_definition.name, TicketCapture(namespace="tickets", project="default", ticket_id="A", title="", body="Autonomous"))
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint={"done": True})
async def apply(ctx, config):
    ctx.connect(Connector())
    await ctx.run_batch(scope_id=config.file, targets=())
""")
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(
                tmp_path, url, manager_url, tokens, scope, plugin=str(plugin), config={"file": scope}
            )
            async with (
                running(config),
                httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["worker"]}) as worker,
            ):
                binding = {
                    "scope_id": scope,
                    "binding_id": config.instance_id,
                    "connector_name": "tickets",
                    "connector_version": "1",
                }
                result = await wait_until(
                    lambda: worker.post("/v1/connector-checkpoints/get", json={"binding": binding}),
                    lambda r: r.status_code == 200 and r.json()["checkpoint"] == {"done": True},
                )
                assert result.status_code == 200

    scenario_runner.run(scenario())


@pytest.mark.parametrize("initially_enabled", [False, True])
def test_explicit_update_requires_stopped_state_and_never_enables(tmp_path, scenario_runner, initially_enabled):
    async def scenario():
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens),
            httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}) as admin,
        ):
            registration = {
                "instance_id": "update-test",
                "credential_digest": hashlib.sha256(b"instance-token").hexdigest(),
                "execution_location": "test",
                "scope_ids": [scope],
                "configuration_digest": "original",
                "enabled": initially_enabled,
            }
            await wait_until(
                lambda: admin.post("/v1/source-management/configure", json={"registration": registration}),
                lambda r: r.status_code == 200,
            )
            updated = {k: v for k, v in registration.items() if k != "enabled"}
            if not initially_enabled:
                updated["configuration_digest"] = "replacement"
            response = await admin.post(
                "/v1/source-management/configure", json={"registration": updated, "update": True}
            )
            assert response.status_code == (409 if initially_enabled else 200), response.text
            state = await admin.post(
                "/v1/source-management/instances", json={"instance_id": "update-test", "view": "control"}
            )
            assert state.status_code == 200, state.text
            assert state.json()["instances"][0]["enabled"] is initially_enabled
            if not initially_enabled:
                assert state.json()["instances"][0]["actual"] == "stopped"

    scenario_runner.run(scenario())


def test_reconnect_preserves_capabilities_required_by_stopped_targets(tmp_path, scenario_runner):
    async def scenario():
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(tmp_path, url, manager_url, tokens, scope)
            async with (
                running(config),
                Client(url + "/mcp", auth=tokens["host"]) as host,
                httpx.AsyncClient(
                    base_url=manager_url,
                    headers={"Authorization": "Bearer " + config.management.credential_file.read_text()},
                ) as instance,
            ):
                await discover_running(host, scope)
                added = await tool(
                    host,
                    "pc_source_add",
                    {
                        "scope_id": scope,
                        "instance_id": config.instance_id,
                        "capability": "tickets.item",
                        "capability_version": "1",
                        "input": {"id": "A"},
                    },
                )
                target = {"target_id": added["target_id"]}
                await tool(host, "pc_source_target_set_enabled", {"scope_id": scope, **target, "enabled": False})
                await wait_until(lambda: status(host, scope, target), lambda r: r["tracking"] == "stopped")
                prefix = f"/v1/source-management/instances/{config.instance_id}"
                owner = await instance.post(prefix + "/owner", json={})
                assert owner.status_code == 200, owner.text
                response = await instance.post(
                    prefix + "/connect",
                    json={
                        "protocol": "3",
                        "execution_location": config.execution_location,
                        "configuration_digest": config.configuration_digest,
                        "owner": owner.json()["owner"],
                        "capabilities": [],
                    },
                )
                assert response.status_code == 409, response.text
                assert response.json()["error"]["code"] == "capability-in-use"
                assert (await status(host, scope, target))["tracking"] == "stopped"

    scenario_runner.run(scenario())


def test_oversized_normalized_target_is_rejected_without_blocking_sync(tmp_path, scenario_runner):
    async def scenario():
        plugin = tmp_path / "expanded.py"
        plugin.write_text("""from pathlib import Path
from examples.source_plugins.tickets import Config, capabilities, connector_name, connector_version, definitions, plugin_api_version
from powercontext.source_management import TargetIdentity
import asyncio
async def apply(ctx, config):
    await asyncio.Event().wait()
async def identify_target(request, config):
    return TargetIdentity(target_key="stable", normalized_input={"id": Path(config.file).read_text()})
""")
        normalized = tmp_path / "normalized.txt"
        normalized.write_text("x" * 2_094_552)
        async with (
            source_server(tmp_path) as (_app, url, tokens, scope),
            management(tmp_path, url, tokens) as (_manager, manager_url),
        ):
            config = await configured_runtime(
                tmp_path,
                url,
                manager_url,
                tokens,
                scope,
                plugin=str(plugin),
                config={"file": str(normalized), "poll_interval": 0.1},
            )
            async with (
                running(config),
                Client(url + "/mcp", auth=tokens["host"]) as host,
                httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + tokens["deployer"]}) as admin,
            ):
                await discover_running(host, scope)
                args = {
                    "scope_id": scope,
                    "instance_id": config.instance_id,
                    "capability": "tickets.item",
                    "capability_version": "1",
                    "input": {"id": "A"},
                }
                with pytest.raises(ToolError, match="response-too-large"):
                    await tool(host, "pc_source_add", args)
                normalized.write_text("A")
                added = await tool(host, "pc_source_add", args)
                assert added["tracking"] == "enabled"
                stopped = await admin.post(
                    "/v1/source-management/instance-set-enabled",
                    json={"instance_id": config.instance_id, "enabled": False},
                )
                assert stopped.status_code == 200, stopped.text
                await wait_until(
                    lambda: admin.post(
                        "/v1/source-management/instances", json={"instance_id": config.instance_id, "view": "control"}
                    ),
                    lambda r: r.status_code == 200 and r.json()["instances"][0]["actual"] == "stopped",
                )

    scenario_runner.run(scenario())
