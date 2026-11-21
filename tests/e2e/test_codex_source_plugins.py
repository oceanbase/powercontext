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

"""Codex Source collectors use the current Server runner and SourcePlugin APIs."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from tests.e2e.plugin_server import create_scope, running_server, wait_for

ROOT = Path(__file__).resolve().parents[2]
ROBOT = "codex-source-runner-token"


async def grant_scope_contribution(admin, scope: str) -> None:
    response = await admin.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": "service", "id": "admin"},
            "resource": {"type": "scope", "scope_id": scope},
            "role": "scope.contributor",
            "idempotency_key": f"{scope}-admin",
        },
    )
    assert response.status_code == 201, response.text


async def add_target(admin, scope: str, capability: str, parameters: dict[str, str]) -> str:
    response = await admin.post(
        f"/v1/scopes/{scope}/source-targets",
        json={"runner": "codex-device", "capability": capability, "parameters": parameters},
    )
    assert response.status_code == 201, response.text
    return response.json()["target_id"]


async def run_agent(admin, plugins: Path, codex_home: Path) -> dict[str, object]:
    executable = Path(sys.executable).with_name("powercontext")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(executable),
        "source-agent",
        "--server",
        str(admin.base_url),
        "--plugins",
        str(plugins),
        "--name",
        "codex-device",
        "--once",
        env={**os.environ, "POWERCONTEXT_SOURCE_AGENT_TOKEN": ROBOT, "CODEX_HOME": str(codex_home)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 120)
    assert process.returncode == 0, stderr.decode()
    return json.loads(stdout.decode().strip().splitlines()[-1])


async def source_types(admin, scope: str) -> set[str]:
    response = await admin.get(f"/v1/scopes/{scope}/sources", params={"limit": 100})
    assert response.status_code == 200, response.text
    return {item["source_type"] for item in response.json()["items"]}


def test_codex_session_and_memory_plugins_collect_with_server_runner(tmp_path: Path) -> None:
    asyncio.run(_collect(tmp_path))


async def _collect(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    session = home / "sessions" / "2026" / "rollout.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        "\n".join([
            json.dumps({"type": "session_meta", "payload": {"session_id": "s1", "cwd": "/project"}}),
            json.dumps({"type": "turn_context", "payload": {"turn_id": "t1"}}),
            json.dumps({
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "keep this decision"},
            }),
        ])
        + "\n",
        encoding="utf-8",
    )
    memories = home / "memories"
    memories.mkdir(parents=True)
    (memories / "MEMORY.md").write_text("A derived memory note.\n", encoding="utf-8")

    plugins = tmp_path / "plugins"
    shutil.copytree(ROOT / "integrations/codex/source_plugin", plugins / "codex-history")
    shutil.copytree(ROOT / "integrations/codex/memory_source_plugin", plugins / "codex-memory-history")
    async with running_server(
        tmp_path, plugins, extra_env={"CODEX_HOME": str(home)}, identities={"robot": ROBOT}
    ) as admin:
        scope = await create_scope(admin, "codex-source-plugins")
        await grant_scope_contribution(admin, scope)
        robot_grant = await admin.post(
            "/v1/access/bindings/create",
            json={
                "subject": {"type": "service", "id": "robot"},
                "resource": {"type": "scope", "scope_id": scope},
                "role": "scope.contributor",
                "idempotency_key": f"{scope}-robot",
            },
        )
        assert robot_grant.status_code == 201, robot_grant.text
        await run_agent(admin, plugins, home)  # Register the device runner before assigning targets.
        history_id = await add_target(admin, scope, "codex-history", {"session_file": "2026/rollout.jsonl"})
        memory_id = await add_target(admin, scope, "codex-memory-history", {"path": "MEMORY.md"})
        await run_agent(admin, plugins, home)

        await wait_for(
            lambda: source_types(admin, scope),
            lambda types: {"codex-session-prompt", "codex-memory-snapshot"} <= types,
            log=tmp_path / "server.log",
        )
        response = await admin.get(f"/v1/scopes/{scope}/source-targets")
        targets = {item["target_id"]: item for item in response.json()["items"]}
        assert targets[history_id]["status"]["last_success_at"] is not None
        assert targets[memory_id]["status"]["last_success_at"] is not None
