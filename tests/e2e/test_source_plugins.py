"""Source plugins collect through the Server's own runner and through a device runner that syncs targets."""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

from tests.e2e.plugin_server import create_scope, running_server, wait_for

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "source_plugins"
ROBOT = "robot-token"


async def sources(client, scope, source_type):
    response = await client.get(f"/v1/scopes/{scope}/sources", params={"limit": 100})
    assert response.status_code == 200, response.text
    return [item for item in response.json()["items"] if item["source_type"] == source_type]


async def targets(client, scope):
    response = await client.get(f"/v1/scopes/{scope}/source-targets")
    assert response.status_code == 200, response.text
    return {item["target_id"]: item for item in response.json()["items"]}


async def run_agent(server: str, plugins: Path, *, name: str) -> dict[str, Any]:
    entrypoint = Path(sys.executable).with_name("powercontext")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(entrypoint),
        "source-agent",
        "--server",
        server,
        "--plugins",
        str(plugins),
        "--name",
        name,
        "--once",
        env={**os.environ, "POWERCONTEXT_SOURCE_AGENT_TOKEN": ROBOT},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 120)
    assert process.returncode == 0, stderr.decode()
    return json.loads(stdout.decode().strip().splitlines()[-1])


def test_server_and_device_runners_collect_assigned_targets(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(EXAMPLES / "tickets", plugins / "tickets")
    shutil.copytree(EXAMPLES / "ticket-chat", plugins / "ticket-chat")
    device = tmp_path / "device"
    shutil.copytree(EXAMPLES / "ticket-chat", device / "ticket-chat")
    tickets = tmp_path / "tickets.json"
    tickets.write_text(json.dumps([{"id": "A", "title": "Disk full", "body": "db-1 is out of space"}]))
    log = tmp_path / "server.log"

    async def scenario():
        async with running_server(tmp_path, plugins, identities={"robot": ROBOT}) as admin:
            scope = await create_scope(admin, "source-plugins-e2e")
            # Server administrators get no Scope access implicitly; both principals contribute to this Scope.
            for subject in ("admin", "robot"):
                granted = await admin.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": "service", "id": subject},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": "scope.contributor",
                        "idempotency_key": f"{scope}-{subject}",
                    },
                )
                assert granted.status_code == 201, granted.text
            robot = httpx.AsyncClient(base_url=admin.base_url, headers={"Authorization": f"Bearer {ROBOT}"})
            base = f"/v1/scopes/{scope}/source-targets"

            runners = (await admin.get("/v1/source-runners")).json()["items"]
            server = next(item for item in runners if item["runner"] == "server")
            assert {item["name"]: item["compatible"] for item in server["capabilities"]} == {
                "ticket-chat": True,
                "tickets": True,
            }

            # Choosing what the Server's own runner collects is an operator decision.
            target = {"runner": "server", "capability": "tickets", "parameters": {"file": str(tickets)}}
            denied = await robot.post(base, json=target)
            assert denied.status_code == 403, denied.text
            created = await admin.post(base, json=target)
            assert created.status_code == 201, created.text
            assert (await admin.post(base, json=target)).status_code == 200
            other = {**target, "parameters": {"file": f" {tickets} ", "namespace": "other"}}
            assert (await admin.post(base, json=other)).json()["error"]["code"] == "target_conflict"
            invalid = await admin.post(base, json={**target, "parameters": {"namespace": "x"}})
            assert (invalid.status_code, invalid.json()["error"]["code"]) == (422, "invalid_parameters")

            # Adding a target collects right away; the Source and the status commit together.
            await wait_for(lambda: sources(admin, scope, "example-ticket"), lambda items: len(items) == 1, log=log)
            good = created.json()["target_id"]
            assert (await targets(admin, scope))[good]["status"]["last_success_at"] is not None

            # A target the plugin reports as failed keeps its checkpoint; the others in the call still commit.
            tickets.write_text(
                json.dumps([
                    {"id": "A", "title": "Disk full", "body": "db-1 is out of space"},
                    {"id": "B", "title": "Slow query", "body": "orders scan"},
                ])
            )
            broken = await admin.post(
                base, json={"runner": "server", "capability": "tickets", "parameters": {"file": "/missing.json"}}
            )
            assert broken.status_code == 201, broken.text
            status = await wait_for(
                lambda: targets(admin, scope),
                lambda value: value[broken.json()["target_id"]]["status"]["consecutive_failures"] >= 1,
                log=log,
            )
            assert status[broken.json()["target_id"]]["status"]["last_error_code"] == "file_unreadable"
            await wait_for(lambda: sources(admin, scope, "example-ticket"), lambda items: len(items) == 2, log=log)

            # Identity parameters cannot change; the other parameters and enablement can.
            changed = await admin.patch(f"{base}/{good}", json={"parameters": {"file": "/elsewhere.json"}})
            assert (changed.status_code, changed.json()["error"]["code"]) == (422, "identity_immutable")
            renamed = await admin.patch(f"{base}/{good}", json={"parameters": {"file": str(tickets), "project": "ops"}})
            assert renamed.status_code == 200 and renamed.json()["parameters"]["project"] == "ops"

            # A device runner collects the targets assigned to it, submitting Sources and then checkpoints.
            chats = tmp_path / "chats.json"
            shutil.copy(EXAMPLES / "ticket-chat" / "chats.json", chats)
            assigned = await robot.post(
                base, json={"runner": "robot-7", "capability": "ticket-chat", "parameters": {"file": str(chats)}}
            )
            assert assigned.status_code == 201, assigned.text
            first = await run_agent(str(admin.base_url), device, name="robot-7")
            assert [len(item["sources"]) for item in first["outcomes"]] == [1]
            assert len(await sources(admin, scope, "example-chat")) == 1
            device_target = (await targets(admin, scope))[assigned.json()["target_id"]]
            assert device_target["status"]["last_success_at"] is not None
            # The committed checkpoint means nothing new is submitted the next time.
            second = await run_agent(str(admin.base_url), device, name="robot-7")
            assert [item["sources"] for item in second["outcomes"]] == [[]]
            runners = {item["runner"]: item for item in (await admin.get("/v1/source-runners")).json()["items"]}
            assert runners["robot-7"]["capabilities"][0]["compatible"] is True

            # Another credential cannot sync as this runner.
            hijack = await admin.post("/v1/source-runners/robot-7/sync", json={"plugins": []})
            assert hijack.status_code == 403, hijack.text

            # A device plugin newer than the Server's is rejected until the Server upgrades.
            source = (device / "ticket-chat" / "plugin.py").read_text()
            (device / "ticket-chat" / "plugin.py").write_text(source.replace('version="1.0.0"', 'version="2.0.0"'))
            newer = await run_agent(str(admin.base_url), device, name="robot-7")
            assert newer["outcomes"] == []
            sync = await robot.post(
                "/v1/source-runners/robot-7/sync",
                json={"plugins": [{"name": "ticket-chat", "version": "2.0.0", "error": None}]},
            )
            assert sync.json() == {
                "targets": [],
                "rejected": [{"name": "ticket-chat", "code": "plugin_version_incompatible"}],
            }

            # Disabled targets are not synced; deleting one removes its checkpoint but keeps its Sources.
            paused = await robot.patch(f"{base}/{assigned.json()['target_id']}", json={"enabled": False})
            assert paused.status_code == 200 and paused.json()["enabled"] is False
            current = await robot.post(
                "/v1/source-runners/robot-7/sync",
                json={"plugins": [{"name": "ticket-chat", "version": "1.0.0", "error": None}]},
            )
            assert current.json()["targets"] == []
            deleted = await robot.delete(f"{base}/{assigned.json()['target_id']}")
            assert deleted.status_code == 204, deleted.text
            assert len(await sources(admin, scope, "example-chat")) == 1
            await robot.aclose()

    asyncio.run(scenario())
