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

"""Deploy trusted write code through the CLI and use the real public MCP."""

from __future__ import annotations

import asyncio
import base64
import io
import shutil

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from PIL import Image

from tests.source_helpers import ROOT, TOKENS, call, create_scope, discover, grant, load_write_cli, read, running_server

EXAMPLE = ROOT / "examples/source_plugins/tickets"
LOAD = "/v1/source-management/write/load"


def test_server_local_write_mcp_projection_and_restart(tmp_path):
    asyncio.run(_write_and_restart(tmp_path))


async def _write_and_restart(directory):
    deployment = directory / "tickets"
    shutil.copytree(EXAMPLE, deployment, ignore=shutil.ignore_patterns("__pycache__"))
    value = {
        "namespace": "support",
        "project": "powercontext",
        "ticket_id": "PC-123",
        "title": "数据库恢复",
        "body": "重试连接后服务恢复。",
        "labels": ["数据库"],
        "metadata": {"owner": None, "attempts": 2, "nested": {"verified": True}},
    }
    async with running_server(directory) as (admin, url, _process):
        scope = await create_scope(admin, "write-evidence")
        await grant(admin, scope, "deployer", "scope.viewer")
        await grant(admin, scope)
        await grant(admin, scope, "reader", "scope.viewer")
        other_scope = await create_scope(admin, "other-evidence")
        await grant(admin, other_scope, "deployer", "scope.viewer")
        configuration = deployment / "write.yml"
        first = await load_write_cli(configuration, url)
        assert await load_write_cli(configuration, url) == first
        assert first["entry"] == str(deployment)
        args = {"scope_id": scope, "capability": "ticket.write", "capability_version": "1", "input": value}
        async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
            catalog = await discover(host, scope)
            assert catalog["management"] == "not-configured"
            ticket = next(c for c in catalog["capabilities"] if c["name"] == "ticket.write")
            assert ticket["kind"] == "write" and ticket["can_write"] and ticket["instances"] == []
            assert ticket["definition_name"] == "example-ticket"
            a, repeated = await asyncio.gather(call(host, "pc_source_add", args), call(host, "pc_source_add", args))
            assert a == repeated
            assert a["kind"] == "write" and "request_id" not in a and "target_id" not in a
            ref = a["result"]["source_refs"][0]
            stored = await read(admin, scope, ref)
            assert stored["content"]["payload"]["capture"] == value
            details = next(p for p in stored["content"]["projections"] if p["key"]["name"] == "example.ticket-details")
            assert details == {"key": {"name": "example.ticket-details", "version": "1"}, "value": value}
            changed = await call(host, "pc_source_add", {**args, "input": {**value, "body": "服务持续稳定。"}})
            second_ref = changed["result"]["source_refs"][0]
            assert second_ref != ref
            page = await admin.get(f"/v1/scopes/{scope}/sources", params={"limit": 1})
            assert page.status_code == 200, page.text
            assert [s["source_id"] for s in page.json()["items"]] == [ref["source_id"]]
            page = await admin.get(
                f"/v1/scopes/{scope}/sources", params={"limit": 1, "cursor": page.json()["next_cursor"]}
            )
            assert [s["source_id"] for s in page.json()["items"]] == [second_ref["source_id"]]
            assert page.json()["next_cursor"] is None
            with pytest.raises(ToolError):
                await call(host, "pc_source_add", {**args, "scope_id": other_scope})
            with pytest.raises(ToolError):
                await call(host, "pc_source_add", {**args, "instance_id": "unused"})
            with pytest.raises(ToolError, match="capability-unavailable"):
                await call(host, "pc_source_add", {**args, "capability_version": "999"})
        async with Client(url + "/mcp", auth=TOKENS["reader"]) as reader:
            assert not next(c for c in (await discover(reader, scope))["capabilities"] if c["name"] == "ticket.write")[
                "can_write"
            ]
            with pytest.raises(ToolError):
                await call(reader, "pc_source_add", args)
        response = await admin.post(LOAD, json={"id": first["id"], "entry": "does-not-exist.py"})
        assert response.status_code == 409
        await grant(admin, other_scope)
        async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
            isolated = await call(host, "pc_source_add", {**args, "scope_id": other_scope})
            assert isolated["result"]["source_refs"] == [ref]
        assert (await read(admin, other_scope, ref))["content"] == stored["content"]
    async with running_server(directory) as (admin, url, _process):
        async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
            assert any(c["name"] == "ticket.write" for c in (await discover(host, scope))["capabilities"])
            assert (await call(host, "pc_source_add", args))["result"]["source_refs"] == [ref]
        assert (await read(admin, scope, ref))["position"] == stored["position"]
    shutil.move(deployment, directory / "unavailable-tickets")
    async with running_server(directory) as (admin, url, _process):
        async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
            catalog = await discover(host, scope)
            assert [c["name"] for c in catalog["capabilities"]] == ["content.inline"]
            builtin = await call(
                host,
                "pc_source_add",
                {
                    "scope_id": scope,
                    "capability": "content.inline",
                    "capability_version": "2",
                    "input": {"source_id": "still-available", "text": "Server remains usable"},
                },
            )
            assert builtin["result"]["source_refs"][0]["source_type"] == "content"
        # Saved projections remain readable without executable write code.
        assert (await read(admin, scope, ref))["content"] == stored["content"]


BOUNDARY_PLUGIN = """
import asyncio
import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from powercontext.sources import Source, SourceMaterialization
from powercontext.source_management import Capability

Path(__file__).with_name("loaded-pid").write_text(str(os.getpid()))

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    data: str = ""
    repeat: int = Field(default=1, ge=1, le=5000000)
    delay: float = Field(default=0, ge=0, le=30)
    fail: bool = False

class Saved(Source):
    data: str

class Definition:
    name = "boundary-example"
    version = "1"
    input_class = Input
    source_class = Saved
    projections = ()
    async def resolve(self, value, /):
        if value.delay:
            Path(__file__).with_name("resolving").write_text(value.source_id)
            await asyncio.sleep(value.delay)
        if value.fail:
            raise ValueError("private input must not be echoed: " + value.data)
        return Saved(name=value.source_id, materialization=SourceMaterialization.CAPTURED, data=value.data * value.repeat)
    async def read(self, source, /):
        return source.data

definitions = (Definition(),)
capabilities = (Capability(name="boundary.write", version="7", kind="write", description="Capture supplied content",
    definition_name="boundary-example", definition_version="1", input_schema=Input.model_json_schema()),)
"""


def test_write_loading_limits_conflicts_and_live_permissions(tmp_path):
    asyncio.run(_write_boundaries(tmp_path))


async def _write_boundaries(directory):
    entry = directory / "plugin.py"
    entry.write_text(BOUNDARY_PLUGIN)
    async with running_server(directory, write_timeout=1) as (admin, url, process):
        scope = await create_scope(admin, "write-limits")
        await grant(admin, scope, "deployer", "scope.viewer")
        binding = await grant(admin, scope)
        config = {"id": "boundary", "entry": str(entry)}
        response = await admin.post(LOAD, json=config)
        assert response.status_code == 200, response.text
        assert int((directory / "loaded-pid").read_text()) == process.pid
        response = await admin.post(LOAD, json={**config, "id": "conflicting-capability"})
        assert response.status_code == 409 and response.json()["error"]["code"] == "capability-conflict"
        invalid = directory / "invalid.py"
        invalid.write_text(BOUNDARY_PLUGIN.replace('definition_version="1"', 'definition_version="wrong"'))
        response = await admin.post(LOAD, json={"id": "invalid", "entry": str(invalid)})
        assert response.status_code == 400
        invalid.write_text(BOUNDARY_PLUGIN.replace("resolve(self, value, /)", "resolve(self, value, required, /)"))
        response = await admin.post(LOAD, json={"id": "invalid", "entry": str(invalid)})
        assert response.status_code == 400
        conflict = directory / "definition_conflict.py"
        conflict.write_text(
            BOUNDARY_PLUGIN.replace('name="boundary.write"', 'name="conflicting-definition.write"').replace(
                "class Saved(Source):\n    data: str", "class Saved(Source):\n    data: str\n    extra: str = ''"
            )
        )
        response = await admin.post(LOAD, json={"id": "definition-conflict", "entry": str(conflict)})
        assert response.status_code == 409 and response.json()["error"]["code"] == "definition-conflict"
        async with httpx.AsyncClient(base_url=url, headers={"Authorization": "Bearer " + TOKENS["host"]}) as http_host:
            assert (await http_host.post(LOAD, json=config)).status_code == 403
        response = await admin.post(LOAD, json={"id": "images", "entry": str(ROOT / "examples/source_plugins/images")})
        assert response.status_code == 200, response.text
        args = {
            "scope_id": scope,
            "capability": "boundary.write",
            "capability_version": "7",
            "input": {"source_id": "one", "data": "original"},
        }
        async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
            names = {c["name"] for c in (await discover(host, scope))["capabilities"]}
            assert names == {"content.inline", "boundary.write", "images.write"}
            output = io.BytesIO()
            Image.new("RGB", (2, 2), "red").save(output, format="PNG")
            image_args = {
                "scope_id": scope,
                "capability": "images.write",
                "capability_version": "1",
                "input": {"media_type": "image/png", "data_base64": base64.b64encode(output.getvalue()).decode()},
            }
            image = await call(host, "pc_source_add", image_args)
            assert (await call(host, "pc_source_add", image_args)) == image
            assert "request_id" not in image
            stored_image = await read(admin, scope, image["result"]["source_refs"][0])
            assert stored_image["content"]["payload"]["capture"] == {**image_args["input"], "title": ""}
            saved = await call(host, "pc_source_add", args)
            ref = saved["result"]["source_refs"][0]
            assert (await call(host, "pc_source_add", args)) == saved
            with pytest.raises(ToolError):
                await call(host, "pc_source_add", {**args, "input": {"source_id": "one", "data": "different"}})
            assert (await read(admin, scope, ref))["content"]["payload"]["data"] == "original"
            with pytest.raises(ToolError, match="invalid-input"):
                await call(host, "pc_source_add", {**args, "input": {"source_id": "bad", "unknown": True}})
            with pytest.raises(ToolError, match="input-too-large"):
                await call(
                    host, "pc_source_add", {**args, "input": {"source_id": "oversize", "data": "x" * (1024 * 1024)}}
                )
            with pytest.raises(ToolError, match="input-too-large"):
                await call(
                    host,
                    "pc_source_add",
                    {
                        "scope_id": scope,
                        "capability": "images.write",
                        "capability_version": "1",
                        "input": {"media_type": "image/png", "data_base64": "A" * (1024 * 1024)},
                    },
                )
            with pytest.raises(ToolError, match="observation-too-large"):
                await call(
                    host,
                    "pc_source_add",
                    {**args, "input": {"source_id": "expanded", "data": "x", "repeat": 4 * 1024 * 1024}},
                )
            with pytest.raises(ToolError, match="invalid-input") as failure:
                await call(
                    host,
                    "pc_source_add",
                    {**args, "input": {"source_id": "failure", "data": "sensitive-marker", "fail": True}},
                )
            assert "sensitive-marker" not in str(failure.value)
            before_timeout = (await admin.get(f"/v1/scopes/{scope}/sources")).json()["items"]
            assert len(before_timeout) == 2
            with pytest.raises(ToolError, match="result-unconfirmed"):
                await call(host, "pc_source_add", {**args, "input": {"source_id": "timed-out", "delay": 10}})
            page = await admin.get(f"/v1/scopes/{scope}/sources")
            assert page.status_code == 200 and page.json()["items"] == before_timeout
            # A timed-out invocation must not prevent subsequent synchronous writes.
            recovered = await call(host, "pc_source_add", {**args, "input": {"source_id": "after-timeout"}})
            assert "request_id" not in recovered
            assert (await read(admin, scope, recovered["result"]["source_refs"][0]))["content"]["payload"]["data"] == ""
            page = await admin.get(f"/v1/scopes/{scope}/sources")
            assert page.status_code == 200
            (directory / "resolving").unlink(missing_ok=True)
            pending = asyncio.create_task(
                call(host, "pc_source_add", {**args, "input": {"source_id": "revoked", "delay": 0.6}})
            )
            async with asyncio.timeout(5):
                while True:
                    if (directory / "resolving").exists():
                        break
                    await asyncio.sleep(0.01)
            revoked = await admin.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": binding["binding_id"],
                    "expected_version": binding["version"],
                    "idempotency_key": "revoke-host",
                },
            )
            assert revoked.status_code == 200, revoked.text
            with pytest.raises(ToolError):
                await pending
            assert (await admin.get(f"/v1/scopes/{scope}/sources")).json()["items"] == page.json()["items"]
        assert "sensitive-marker" not in (directory / "server.log").read_text()
