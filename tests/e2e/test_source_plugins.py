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

"""Source plugins collect through the Server's own runner and through a device runner that syncs targets."""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

from tests.e2e.plugin_server import create_scope, items, running_server, wait_for

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


async def start_agent(server: str, plugins: Path, *options: str, token: str = ROBOT):
    entrypoint = Path(sys.executable).with_name("powercontext")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(entrypoint),
        "source-agent",
        "--server",
        server,
        "--plugins",
        str(plugins),
        *options,
        "--once",
        env={**os.environ, "POWERCONTEXT_SOURCE_AGENT_TOKEN": token},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 120)
    return process.returncode, stdout.decode(), stderr.decode()


async def run_agent(server: str, plugins: Path, *options: str, token: str = ROBOT) -> dict[str, Any]:
    code, stdout, stderr = await start_agent(server, plugins, *options, token=token)
    assert code == 0, stderr
    return json.loads(stdout.strip().splitlines()[-1])


async def grant(admin, scope: str, *subjects: str) -> None:
    # Server administrators get no Scope access implicitly; every principal that contributes is granted it.
    for subject in subjects:
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


TICKET_NOTES = """
from powercontext.plugins import ArtifactPlugin, Inputs, Rendering


class TicketNotes:
    async def render_artifact(self, request):
        return Rendering(format="text", text=request.artifact.content["title"])

    async def generate(self, request, writer):
        async with writer.transaction() as tx:
            for source in request.sources:
                ticket = source.projection
                await tx.put(
                    writer.stable_id(ticket["ticket_id"]), content={"title": ticket["title"]}, source_refs=[source.ref]
                )


async def setup(ctx):
    return TicketNotes()


plugin = ArtifactPlugin(
    family="ticket-notes",
    inputs=Inputs(source_types=("example-ticket",), projection="example.ticket-details@1"),
    on_sources=True,
    setup=setup,
)
"""


def test_server_and_device_runners_collect_assigned_targets(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(EXAMPLES / "tickets", plugins / "tickets")
    (plugins / "ticket-notes").mkdir()
    (plugins / "ticket-notes" / "plugin.py").write_text(TICKET_NOTES, encoding="utf-8")
    shutil.copytree(EXAMPLES / "ticket-chat", plugins / "ticket-chat")
    device = tmp_path / "device"
    shutil.copytree(EXAMPLES / "ticket-chat", device / "ticket-chat")
    tickets = tmp_path / "tickets.json"
    tickets.write_text(json.dumps([{"id": "A", "title": "Disk full", "body": "db-1 is out of space"}]))
    log = tmp_path / "server.log"

    async def scenario():
        async with running_server(tmp_path, plugins, identities={"robot": ROBOT}) as admin:
            scope = await create_scope(admin, "source-plugins-e2e")
            await grant(admin, scope, "admin", "robot")
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
            # What the Server's runner collects reaches the Artifact plugins that take it as input.
            await wait_for(lambda: items(admin, scope, "ticket-notes"), lambda value: len(value) == 1, log=log)

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
            chat_target = {"runner": "robot-7", "capability": "ticket-chat", "parameters": {"file": str(chats)}}
            # Targets go only to registered runners; the runner's first start registers it.
            unregistered = await robot.post(base, json=chat_target)
            assert (unregistered.status_code, unregistered.json()["error"]["code"]) == (422, "runner_not_registered")
            started = await run_agent(str(admin.base_url), device, "--name", "robot-7")
            assert started == {"runner": "robot-7", "outcomes": []}
            assigned = await robot.post(base, json=chat_target)
            assert assigned.status_code == 201, assigned.text
            first = await run_agent(str(admin.base_url), device, "--name", "robot-7")
            assert [len(item["sources"]) for item in first["outcomes"]] == [1]
            assert len(await sources(admin, scope, "example-chat")) == 1
            device_target = (await targets(admin, scope))[assigned.json()["target_id"]]
            assert device_target["status"]["last_success_at"] is not None
            # The committed checkpoint means nothing new is submitted the next time.
            second = await run_agent(str(admin.base_url), device, "--name", "robot-7")
            assert [item["sources"] for item in second["outcomes"]] == [[]]
            runners = {item["runner"]: item for item in (await admin.get("/v1/source-runners")).json()["items"]}
            assert runners["robot-7"]["capabilities"][0]["compatible"] is True

            # Another credential cannot sync as this runner.
            hijack = await admin.post("/v1/source-runners/robot-7/sync", json={"plugins": []})
            assert hijack.status_code == 403, hijack.text

            # A device plugin newer than the Server's is rejected until the Server upgrades.
            source = (device / "ticket-chat" / "plugin.py").read_text()
            (device / "ticket-chat" / "plugin.py").write_text(source.replace('version="1.0.0"', 'version="2.0.0"'))
            newer = await run_agent(str(admin.base_url), device, "--name", "robot-7")
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


OTHER = "other-token"


def test_runner_registration_ownership_and_target_fencing(tmp_path):
    import sqlite3

    from powercontext.sources.collection import TARGET_CHECKPOINT_VERSION

    plugins = tmp_path / "plugins"
    shutil.copytree(EXAMPLES / "tickets", plugins / "tickets")
    shutil.copytree(EXAMPLES / "ticket-chat", plugins / "ticket-chat")
    chats = tmp_path / "chats.json"
    shutil.copy(EXAMPLES / "ticket-chat" / "chats.json", chats)
    plugin = {"name": "ticket-chat", "version": "1.0.0"}
    report = {"plugins": [{**plugin, "error": None}]}

    async def scenario():
        async with running_server(tmp_path, plugins, identities={"robot": ROBOT, "other": OTHER}) as admin:
            scope = await create_scope(admin, "source-runners-e2e")
            await grant(admin, scope, "admin", "robot", "other")
            robot = httpx.AsyncClient(base_url=admin.base_url, headers={"Authorization": f"Bearer {ROBOT}"})
            other = httpx.AsyncClient(base_url=admin.base_url, headers={"Authorization": f"Bearer {OTHER}"})
            base = f"/v1/scopes/{scope}/source-targets"
            runners = "/v1/source-runners"

            async def synced(client, runner):
                response = await client.post(f"{runners}/{runner}/sync", json=report)
                assert response.status_code == 200, response.text
                return {item["target_id"]: item for item in response.json()["targets"]}

            def commit(target, checkpoint, *, expected=None, version=None, **fields):
                return {
                    "binding": {
                        "scope_id": scope,
                        "binding_id": target,
                        "connector_name": "ticket-chat",
                        "connector_version": TARGET_CHECKPOINT_VERSION,
                    },
                    "expected": expected,
                    "checkpoint": checkpoint,
                    **({} if version is None else {"config_version": version}),
                    **fields,
                }

            # The Server decides names: a suggestion is used once, and never takes over an existing name.
            first = await robot.post(runners, json={"registration_id": "r" * 24, "suggested_name": "edge box"})
            assert (first.status_code, first.json()) == (201, {"runner": "edge-box", "display_name": "edge box"})
            again = await robot.post(runners, json={"registration_id": "r" * 24, "suggested_name": "renamed"})
            assert (again.status_code, again.json()["runner"]) == (200, "edge-box")
            second = await other.post(runners, json={"registration_id": "r" * 24, "suggested_name": "edge box"})
            assert (second.status_code, second.json()["runner"]) == (201, "edge-box-2")

            # A fixed name is created, reused by its own principal, and refused to any other.
            fixed = await robot.post(runners, json={"name": "robot-7"})
            assert (fixed.status_code, fixed.json()["runner"]) == (201, "robot-7")
            assert (await robot.post(runners, json={"name": "robot-7"})).status_code == 200
            taken = await other.post(runners, json={"name": "robot-7"})
            assert (taken.status_code, taken.json()["error"]["code"]) == (409, "runner_name_taken")
            assert (await robot.post(runners, json={"name": "server"})).status_code == 403
            assert (await robot.post(runners, json={})).status_code == 422

            # A sync never creates a runner, and only the registering principal syncs under a name.
            unknown = await robot.post(f"{runners}/never-registered/sync", json=report)
            assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "runner_not_registered")
            for client in (other, admin):
                assert (await client.post(f"{runners}/robot-7/sync", json=report)).status_code == 403

            target = {"runner": "robot-7", "capability": "ticket-chat", "parameters": {"file": str(chats)}}
            created = await robot.post(base, json=target)
            assert created.status_code == 201, created.text
            target_id = created.json()["target_id"]
            assert created.json()["config_version"] == 1
            assert (await synced(robot, "robot-7"))[target_id]["config_version"] == 1

            # Only the target's runner commits its checkpoint, whichever optional fields a request carries.
            for fields in ({}, {"plugin": plugin}, {"plugin": plugin, "status": {"succeeded": True}}):
                for client in (other, admin):
                    refused = await client.post(
                        "/v1/connector-checkpoints/commit", json=commit(target_id, {"cursor": 9}, version=1, **fields)
                    )
                    assert refused.status_code == 403, refused.text
            assert (await synced(robot, "robot-7"))[target_id]["checkpoint"] is None

            # The accepted plugin version must be that of the target's own plugin.
            foreign = await robot.post(
                "/v1/connector-checkpoints/commit",
                json=commit(target_id, {"cursor": 1}, version=1, plugin={"name": "tickets", "version": "1.0.0"}),
            )
            assert (foreign.status_code, foreign.json()["error"]["code"]) == (422, "invalid_request")
            newer = await robot.post(
                "/v1/connector-checkpoints/commit",
                json=commit(target_id, {"cursor": 1}, version=1, plugin={**plugin, "version": "2.0.0"}),
            )
            assert (newer.status_code, newer.json()["error"]["code"]) == (409, "plugin_version_incompatible")
            assert (await synced(robot, "robot-7"))[target_id]["checkpoint"] is None

            # The runner commits the checkpoint and the status together: a status write that fails takes the
            # checkpoint with it, so the same commit can be repeated. That holds for a target's first
            # checkpoint, which is inserted, and for later ones, which replace it.
            database = sqlite3.connect(tmp_path / "server.db")
            succeeded = {"plugin": plugin, "status": {"succeeded": True}}
            steps = (
                (None, commit(target_id, {"cursor": 1}, version=1, **succeeded)),
                ({"cursor": 1}, commit(target_id, {"cursor": 2}, expected={"cursor": 1}, version=1, **succeeded)),
            )
            for before, step in steps:
                database.execute(
                    "CREATE TRIGGER reject_status BEFORE UPDATE OF last_attempt_at ON pc_source_collect_targets "
                    "BEGIN SELECT RAISE(ABORT, 'rejected'); END"
                )
                database.commit()
                attempted = (await targets(admin, scope))[target_id]["status"]["last_attempt_at"]
                # A request that ends in an unexpected error can leave its connection unusable, so it gets its own.
                async with httpx.AsyncClient(base_url=admin.base_url, headers=robot.headers) as failing:
                    assert (await failing.post("/v1/connector-checkpoints/commit", json=step)).status_code == 500
                assert (await synced(robot, "robot-7"))[target_id]["checkpoint"] == before
                assert (await targets(admin, scope))[target_id]["status"]["last_attempt_at"] == attempted
                database.execute("DROP TRIGGER reject_status")
                database.commit()
                assert (await robot.post("/v1/connector-checkpoints/commit", json=step)).status_code == 200
                assert (await targets(admin, scope))[target_id]["status"]["last_success_at"] != attempted
            database.close()
            # A checkpoint that lost the comparison leaves the reported status unwritten too.
            status = (await targets(admin, scope))[target_id]["status"]
            lost = await robot.post(
                "/v1/connector-checkpoints/commit",
                json=commit(target_id, {"cursor": 9}, expected={"cursor": 1}, version=1, **succeeded),
            )
            assert lost.status_code == 409, lost.text
            assert (await targets(admin, scope))[target_id]["status"] == status

            # A changed target refuses what was collected under its previous configuration.
            changed = await robot.patch(f"{base}/{target_id}", json={"parameters": {"file": str(chats), "room": "ops"}})
            assert changed.json()["config_version"] == 2
            stale = await robot.post(
                "/v1/connector-checkpoints/commit",
                json=commit(target_id, {"cursor": 3}, expected={"cursor": 2}, version=1, plugin=plugin),
            )
            assert (stale.status_code, stale.json()["error"]["code"]) == (409, "target_changed")
            assert (await synced(robot, "robot-7"))[target_id]["checkpoint"] == {"cursor": 2}
            # So does a disabled one, and the same parameters again change nothing.
            assert (await robot.patch(f"{base}/{target_id}", json={"enabled": False})).json()["config_version"] == 3
            paused = await robot.post(
                "/v1/connector-checkpoints/commit",
                json=commit(target_id, {"cursor": 3}, expected={"cursor": 2}, version=3, plugin=plugin),
            )
            assert (paused.status_code, paused.json()["error"]["code"]) == (409, "target_changed")
            assert (await robot.patch(f"{base}/{target_id}", json={"enabled": True})).json()["config_version"] == 4
            assert (await robot.patch(f"{base}/{target_id}", json={"enabled": True})).json()["config_version"] == 4

            # A transfer keeps the target and its checkpoint, and fences the previous runner.
            assert (await robot.post(runners, json={"name": "robot-8"})).status_code == 201
            unregistered = await robot.post(f"{base}/{target_id}/transfer", json={"runner": "nobody"})
            assert (unregistered.status_code, unregistered.json()["error"]["code"]) == (422, "runner_not_registered")
            # Transferring to the Server's runner is adding a target there, which is an operator decision.
            assert (await robot.post(f"{base}/{target_id}/transfer", json={"runner": "server"})).status_code == 403
            moved = await robot.post(f"{base}/{target_id}/transfer", json={"runner": "robot-8"})
            assert (moved.status_code, moved.json()["runner"], moved.json()["config_version"]) == (200, "robot-8", 5)
            assert target_id not in await synced(robot, "robot-7")
            assert (await synced(robot, "robot-8"))[target_id]["checkpoint"] == {"cursor": 2}
            late = await robot.post(
                "/v1/connector-checkpoints/commit",
                json=commit(target_id, {"cursor": 3}, expected={"cursor": 2}, version=4, plugin=plugin),
            )
            assert (late.status_code, late.json()["error"]["code"]) == (409, "target_changed")
            # A runner whose data differs starts from the beginning.
            back = await robot.post(
                f"{base}/{target_id}/transfer", json={"runner": "robot-7", "reset_checkpoint": True}
            )
            assert back.json()["config_version"] == 6
            assert (await synced(robot, "robot-7"))[target_id]["checkpoint"] is None
            # The destination must not already collect the same object.
            twin = await robot.post(base, json={**target, "runner": "robot-8"})
            assert twin.status_code == 201, twin.text
            clash = await robot.post(f"{base}/{target_id}/transfer", json={"runner": "robot-8"})
            assert (clash.status_code, clash.json()["error"]["code"]) == (409, "target_conflict")
            # Moving a target away from the Server's runner needs what changing it there needs.
            tickets = tmp_path / "tickets.json"
            tickets.write_text("[]")
            owned = await admin.post(
                base, json={"runner": "server", "capability": "tickets", "parameters": {"file": str(tickets)}}
            )
            assert owned.status_code == 201, owned.text
            away = await robot.post(f"{base}/{owned.json()['target_id']}/transfer", json={"runner": "robot-7"})
            assert away.status_code == 403, away.text

            # A deleted target's checkpoint is not recreated by a commit that arrives late.
            assert (await robot.delete(f"{base}/{target_id}")).status_code == 204
            ghost = await robot.post(
                "/v1/connector-checkpoints/commit", json=commit(target_id, {"cursor": 1}, version=6, plugin=plugin)
            )
            assert (ghost.status_code, ghost.json()["error"]["code"]) == (409, "target_changed")
            # Nor by one that names a target's checkpoint without the fields a runner sends.
            bare = await robot.post("/v1/connector-checkpoints/commit", json=commit(target_id, {"cursor": 1}))
            assert (bare.status_code, bare.json()["error"]["code"]) == (409, "target_changed")
            readded = await robot.post(base, json=target)
            assert readded.status_code == 201, readded.text
            assert (await synced(robot, "robot-7"))[readded.json()["target_id"]]["checkpoint"] is None
            assert (await robot.delete(f"{base}/{readded.json()['target_id']}")).status_code == 204

            # Records are deleted by their owner or an administrator, once no target is left.
            held = await robot.delete(f"{runners}/robot-8")
            assert (held.status_code, held.json()["error"]["code"]) == (409, "conflict")
            assert (await other.delete(f"{runners}/robot-7")).status_code == 403
            assert (await robot.delete(f"{runners}/robot-7")).status_code == 204
            assert (await admin.delete(f"{runners}/edge-box-2")).status_code == 204
            assert (await admin.delete(f"{runners}/edge-box-2")).status_code == 404
            # The display name is the only part of a runner that changes.
            assert (await robot.patch(f"{runners}/edge-box", json={"display_name": "Lab"})).status_code == 403
            renamed = await admin.patch(f"{runners}/edge-box", json={"display_name": "Lab"})
            assert renamed.json() == {"runner": "edge-box", "display_name": "Lab"}
            listed = {item["runner"]: item for item in (await admin.get(runners)).json()["items"]}
            assert set(listed) == {"server", "edge-box", "robot-8"} and listed["edge-box"]["display_name"] == "Lab"
            await robot.aclose()
            await other.aclose()

    asyncio.run(scenario())


def _variant(directory: Path, name: str, collect: str) -> None:
    """A copy of the chat example under another name and Source type, with a replaced start of ``collect``."""

    source = (EXAMPLES / "ticket-chat" / "plugin.py").read_text(encoding="utf-8")
    source = source.replace('"example-chat"', f'"{name}-room"').replace("example.chat-messages", f"{name}.messages")
    marker = "        result = CollectResult()\n"
    assert marker in source
    (directory / name).mkdir(parents=True)
    (directory / name / "plugin.py").write_text(source.replace(marker, marker + collect), encoding="utf-8")


def test_device_runner_keeps_its_name_and_a_failing_plugin_does_not_stop_the_others(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(EXAMPLES / "ticket-chat", plugins / "ticket-chat")
    # One plugin raises from collect, with a message that must not leave the device; one returns a wrong item.
    _variant(plugins, "broken", '        raise RuntimeError("cannot open /srv/private/token.txt")\n')
    _variant(
        plugins,
        "odd",
        "        for target in request.targets:\n"
        '            result.add(target, [CollectedItem(definition="odd-room", value=ChatMessage(author="a", text="b"))],'
        " checkpoint={})\n"
        "        return result\n",
    )
    device = tmp_path / "device"
    shutil.copytree(plugins, device)
    chats = tmp_path / "chats.json"
    shutil.copy(EXAMPLES / "ticket-chat" / "chats.json", chats)
    state = tmp_path / "state"

    async def scenario():
        async with running_server(tmp_path, plugins, identities={"robot": ROBOT, "other": OTHER}) as admin:
            server = str(admin.base_url)
            scope = await create_scope(admin, "source-agent-e2e")
            await grant(admin, scope, "admin", "robot")
            robot = httpx.AsyncClient(base_url=admin.base_url, headers={"Authorization": f"Bearer {ROBOT}"})
            other = httpx.AsyncClient(base_url=admin.base_url, headers={"Authorization": f"Bearer {OTHER}"})
            base = f"/v1/scopes/{scope}/source-targets"

            # The first start registers; later starts reuse the name kept in the state directory.
            options = ("--state-dir", str(state), "--suggest-name", "lab robot")
            first = await run_agent(server, device, *options)
            assert first == {"runner": "lab-robot", "outcomes": []}
            assert len(list(state.iterdir())) == 1
            assert (await run_agent(server, device, "--state-dir", str(state)))["runner"] == "lab-robot"
            # Without the state, the same suggestion yields another runner.
            assert (
                await run_agent(server, device, "--state-dir", str(tmp_path / "lost"), "--suggest-name", "lab robot")
            )["runner"] == "lab-robot-2"

            # A state directory that cannot be written stops a suggested-name start, and a fixed name needs none.
            blocked = tmp_path / "blocked"
            blocked.write_text("not a directory")
            code, _, stderr = await start_agent(server, device, "--state-dir", str(blocked / "state"))
            assert code != 0, stderr
            assert (await run_agent(server, device, "--name", "fixed-robot"))["runner"] == "fixed-robot"
            # A fixed name another principal registered fails the start.
            assert (await other.post("/v1/source-runners", json={"name": "taken"})).status_code == 201
            code, _, stderr = await start_agent(server, device, "--name", "taken")
            assert code != 0 and "taken" in stderr, stderr

            created = {}
            for capability in ("broken", "odd", "ticket-chat"):
                response = await robot.post(
                    base, json={"runner": "lab-robot", "capability": capability, "parameters": {"file": str(chats)}}
                )
                assert response.status_code == 201, response.text
                created[capability] = response.json()["target_id"]
            collected = await run_agent(server, device, "--state-dir", str(state))
            outcomes = {item["target_id"]: item for item in collected["outcomes"]}
            assert outcomes[created["broken"]]["error"] == "collect_failed"
            assert outcomes[created["odd"]]["error"] == "submit_failed"
            assert len(outcomes[created["ticket-chat"]]["sources"]) == 1
            assert len(await sources(admin, scope, "example-chat")) == 1
            # The Server sees fixed codes and the error type, never the error's own text.
            status = {key: (await targets(admin, scope))[value]["status"] for key, value in created.items()}
            assert (status["broken"]["last_error_code"], status["broken"]["last_error_message"]) == (
                "collect_failed",
                "RuntimeError",
            )
            assert (status["odd"]["last_error_code"], status["odd"]["last_error_message"]) == (
                "submit_failed",
                "TypeError",
            )
            assert status["ticket-chat"]["last_success_at"] is not None

            # The Server's own runner records a collection that failed as a whole on every target of the call.
            failing = []
            for number in (1, 2):
                response = await admin.post(
                    base, json={"runner": "server", "capability": "broken", "parameters": {"file": f"/data/{number}"}}
                )
                assert response.status_code == 201, response.text
                failing.append(response.json()["target_id"])
            reported = await wait_for(
                lambda: targets(admin, scope),
                lambda value: all(value[target]["status"]["consecutive_failures"] >= 1 for target in failing),
                log=tmp_path / "server.log",
            )
            assert {
                (reported[target]["status"]["last_error_code"], reported[target]["status"]["last_error_message"])
                for target in failing
            } == {("collect_failed", "RuntimeError")}
            await robot.aclose()
            await other.aclose()

    asyncio.run(scenario())
