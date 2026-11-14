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

"""Delivered Codex Memory plugin: real Linux load, MCP targets, and Runtime restart."""

from __future__ import annotations

import asyncio
import json
import shutil

import yaml
from fastmcp import Client

from tests.e2e.source_collect_helpers import (
    bind_server,
    cli,
    grant_scope,
    instance_state,
    prepare_environment,
    stop_services,
)
from tests.source_helpers import ROOT, TOKENS, call, create_scope, grant, requires_linux, running_server


async def wait_sources(admin, scope, count):
    async with asyncio.timeout(45):
        while True:
            response = await admin.get(f"/v1/scopes/{scope}/sources")
            assert response.status_code == 200, response.text
            rows = response.json()["items"]
            if len(rows) == count:
                return rows
            await asyncio.sleep(0.1)


@requires_linux
def test_memory_load_mcp_collection_restart_and_target_stop(tmp_path):
    asyncio.run(_managed(tmp_path))


async def _managed(directory):
    envfile, state = prepare_environment(directory)
    plugins = directory / "source-plugins"
    plugins.mkdir()
    home = directory / "codex"
    (home / "memories").mkdir(parents=True)
    memory = home / "memories/MEMORY.md"
    memory.write_text("# Derived preferences\nKeep provenance.\n")
    plugin = directory / "memory-plugin"
    shutil.copytree(
        ROOT / "integrations/codex/memory_source_plugin", plugin, ignore=shutil.ignore_patterns("__pycache__")
    )
    environment = {
        "POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR": str(plugins),
        "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": json.dumps({"state_dir": str(state)}),
    }
    async with running_server(directory, environment=environment) as (admin, url, _):
        bind_server(envfile, url)
        scope = await create_scope(admin, "managed-codex-memory")
        await grant_scope(admin, scope)
        await grant(admin, scope)
        manifest = plugin / "powercontext.yml"
        manifest.write_text(
            yaml.safe_dump({
                "id": "codex-memory",
                "plugin": ".",
                "scope_ids": [scope],
                "config": {"codex_home": str(home), "namespace": "managed-memory", "poll_interval": 0.2},
            })
        )
        try:
            await cli(directory, "source", "load", "--config", str(manifest), "--env-file", str(envfile))
            await instance_state(admin, "codex-memory", "running")
            assert await wait_sources(admin, scope, 0) == []
            async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
                arguments = {
                    "scope_id": scope,
                    "instance_id": "codex-memory",
                    "capability": "codex.memory-history",
                    "capability_version": "1",
                    "input": {"path": "."},
                }
                added = await call(host, "pc_source_add", arguments)
                target_id = added["target_id"]
                assert (await call(host, "pc_source_add", arguments))["target_id"] == target_id
                initial = await wait_sources(admin, scope, 1)
                assert initial[0]["content"]["payload"]["capture"]["evidence_kind"] == "derived_memory"
                # Stop and start the actual independent Runtime using its deployed config.
                runtime = state / "instances/codex-memory/runtime-config.json"
                await cli(directory, "source", "runtime", "stop", "--config", str(runtime))
                memory.write_text("# Derived preferences\nPreserve the updated snapshot.\n")
                await cli(directory, "source", "runtime", "start", "--config", str(runtime))
                updated = await wait_sources(admin, scope, 2)
                assert initial[0] in updated
                await call(
                    host, "pc_source_target_set_enabled", {"scope_id": scope, "target_id": target_id, "enabled": False}
                )
                async with asyncio.timeout(30):
                    while True:
                        status = await call(
                            host, "pc_source_status", {"scope_id": scope, "target_id": target_id, "view": "control"}
                        )
                        if status["tracking"] == "stopped":
                            break
                        await asyncio.sleep(0.1)
                # A stopped target preserves evidence and does not capture a changed file.
                memory.write_text("# Must wait for explicit re-enable.\n")
                await cli(directory, "source", "runtime", "stop", "--config", str(runtime))
                await cli(directory, "source", "runtime", "start", "--config", str(runtime))
                await instance_state(admin, "codex-memory", "running")
                assert len(await wait_sources(admin, scope, 2)) == 2
                await call(
                    host, "pc_source_target_set_enabled", {"scope_id": scope, "target_id": target_id, "enabled": True}
                )
                await wait_sources(admin, scope, 3)
        finally:
            await stop_services(directory, state)
