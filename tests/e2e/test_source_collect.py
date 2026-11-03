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

"""Managed collect instances preserve Scope progress and independent resource lifetimes."""

from __future__ import annotations

import asyncio
import json
import shutil

import yaml

from powercontext.limits import MAX_SOURCE_OBSERVATION_BYTES
from tests.e2e.source_collect_helpers import (
    bind_server,
    cli,
    grant_scope,
    instance_state,
    prepare_environment,
    set_enabled,
    stop_services,
)
from tests.source_helpers import ROOT, create_scope, running_server

EXAMPLE = ROOT / "examples/source_plugins/ticket_chat"


async def checkpoint(client, scope, identifier, *, after=None):
    async with asyncio.timeout(40):
        while True:
            response = await client.post(
                "/v1/connector-checkpoints/get",
                json={
                    "binding": {
                        "scope_id": scope,
                        "binding_id": identifier,
                        "connector_name": "example-ticket-chat",
                        "connector_version": "1",
                    }
                },
            )
            assert response.status_code == 200, response.text
            current = response.json()["checkpoint"]
            if current is not None and current != after:
                return current
            await asyncio.sleep(0.1)


async def received(client, scope, target, count, *, namespace):
    async with asyncio.timeout(40):
        while True:
            response = await client.post("/v1/source-management/status", json={"scope_id": scope, "target_id": target})
            assert response.status_code == 200, response.text
            status = response.json()
            page = await client.get(f"/v1/scopes/{scope}/sources")
            assert page.status_code == 200, page.text
            assert page.json()["next_cursor"] is None
            sources = [
                item for item in page.json()["items"] if item["content"]["payload"]["capture"]["namespace"] == namespace
            ]
            if len(sources) == count:
                latest = max(sources, key=lambda item: item["position"])
                if status["source_refs"] == [{"source_type": latest["source_type"], "source_id": latest["source_id"]}]:
                    return status
            await asyncio.sleep(0.1)


def test_managed_instances_isolate_scopes_and_release_resources(tmp_path):
    asyncio.run(_managed_instances(tmp_path))


async def _managed_instances(directory):
    envfile, state = prepare_environment(directory)
    environment = {
        "POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR": str(directory / "source-plugins"),
        "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": json.dumps({"state_dir": str(state)}),
    }
    async with running_server(directory, environment=environment) as (admin, url, _process):
        bind_server(envfile, url)
        scopes = [await create_scope(admin, name) for name in ("first", "second")]
        for scope in scopes:
            await grant_scope(admin, scope)
        first, second = directory / "first", directory / "second"
        shutil.copytree(EXAMPLE, first, ignore=shutil.ignore_patterns("__pycache__"))
        second.mkdir()
        shutil.copy(first / "events.json", second / "events.json")
        entry = first / "__init__.py"
        entry.write_text(
            entry.read_text()
            + """

_collect = apply

async def apply(ctx, config):
    def closed():
        (ctx.plugin_dir / "closed").touch()
    ctx.on_dispose(closed)
    await _collect(ctx, config)
"""
        )
        targets = {}
        try:
            for instance, selected in ((first, scopes), (second, scopes[1:])):
                manifest = {
                    "id": instance.name,
                    "plugin": str(first),
                    "scope_ids": selected,
                    "config": {"namespace": instance.name, "interval_seconds": 0.1},
                    "targets": [
                        {"scope_id": scope, "capability": "ticket-chat.dataset", "capability_version": "1", "input": {}}
                        for scope in selected
                    ],
                }
                path = instance / "powercontext.yml"
                path.write_text(yaml.safe_dump(manifest))
                await cli(directory, "source", "load", "--config", str(path), "--env-file", str(envfile))
                await instance_state(admin, instance.name, "running")
                for scope in selected:
                    added = await admin.post(
                        "/v1/source-management/add",
                        json={
                            "scope_id": scope,
                            "instance_id": instance.name,
                            "capability": "ticket-chat.dataset",
                            "capability_version": "1",
                            "input": {},
                        },
                    )
                    assert added.status_code == 200, added.text
                    target = added.json()["target_id"]
                    targets[instance.name, scope] = target
                    await received(admin, scope, target, 2, namespace=instance.name)
            previous = await checkpoint(admin, scopes[1], "first")
            stopped_target = await admin.post(
                "/v1/source-management/target-set-enabled",
                json={
                    "scope_id": scopes[1],
                    "target_id": targets["first", scopes[1]],
                    "enabled": False,
                },
            )
            assert stopped_target.status_code == 200, stopped_target.text
            async with asyncio.timeout(40):
                while True:
                    status = await admin.post(
                        "/v1/source-management/status",
                        json={
                            "scope_id": scopes[1],
                            "target_id": targets["first", scopes[1]],
                        },
                    )
                    if status.json()["tracking"] == "stopped":
                        break
                    await asyncio.sleep(0.1)
            fixture = json.loads((first / "events.json").read_text())
            fixture["tickets"][0]["body"] = "Changed in the first Scope only"
            replacement = first / "events.next.json"
            replacement.write_text(json.dumps(fixture))
            replacement.replace(first / "events.json")
            await received(admin, scopes[0], targets["first", scopes[0]], 3, namespace="first")
            assert await checkpoint(admin, scopes[1], "first") == previous
            await set_enabled(admin, "first", False)
            assert (first / "closed").exists()
            assert not (second / "closed").exists()
            assert (await instance_state(admin, "second"))["actual"] == "running"
            previous = await checkpoint(admin, scopes[1], "second")
            fixture["tickets"][0]["body"] = "Second instance continues after the first stops"
            replacement = second / "events.next.json"
            replacement.write_text(json.dumps(fixture))
            replacement.replace(second / "events.json")
            last_success = await received(admin, scopes[1], targets["second", scopes[1]], 3, namespace="second")
            # Submission reports can precede checkpoint persistence. Wait for both.
            previous = await checkpoint(admin, scopes[1], "second", after=previous)
            # A rejected observation must not advance the Scope's checkpoint.
            fixture["tickets"][0]["body"] = "x" * MAX_SOURCE_OBSERVATION_BYTES
            replacement.write_text(json.dumps(fixture))
            replacement.replace(second / "events.json")
            await instance_state(admin, "second", "failed")
            assert (second / "closed").exists()
            assert await checkpoint(admin, scopes[1], "second") == previous
            failed = await received(admin, scopes[1], targets["second", scopes[1]], 3, namespace="second")
            assert failed["source_refs"] == last_success["source_refs"]
            assert failed["last_submitted_at"] == last_success["last_submitted_at"]
        finally:
            await stop_services(directory, state)


def test_scope_shrink_preserves_progress_and_offline_discovery(tmp_path):
    asyncio.run(_scope_shrink_and_discovery(tmp_path))


async def _scope_shrink_and_discovery(directory):
    from fastmcp import Client

    from tests.source_helpers import TOKENS, call

    envfile, state = prepare_environment(directory)
    environment = {
        "POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR": str(directory / "source-plugins"),
        "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": json.dumps({"state_dir": str(state)}),
    }
    async with running_server(directory, environment=environment) as (admin, url, _process):
        bind_server(envfile, url)
        scopes = [await create_scope(admin, name) for name in ("kept", "removed")]
        for scope in scopes:
            await grant_scope(admin, scope)
        plugin = directory / "collector"
        shutil.copytree(EXAMPLE, plugin, ignore=shutil.ignore_patterns("__pycache__"))
        manifest = {
            "id": "scope-review",
            "plugin": str(plugin),
            "scope_ids": scopes,
            "config": {"namespace": "scope-review", "interval_seconds": 0.1},
            "targets": [
                {"scope_id": s, "capability": "ticket-chat.dataset", "capability_version": "1", "input": {}}
                for s in scopes
            ],
        }
        path = plugin / "powercontext.yml"
        path.write_text(yaml.safe_dump(manifest))
        try:
            await cli(directory, "source", "load", "--config", str(path), "--env-file", str(envfile))
            await instance_state(admin, "scope-review", "running")
            targets = {}
            for scope in scopes:
                response = await admin.post(
                    "/v1/source-management/add",
                    json={
                        "scope_id": scope,
                        "instance_id": "scope-review",
                        "capability": "ticket-chat.dataset",
                        "capability_version": "1",
                        "input": {},
                    },
                )
                assert response.status_code == 200, response.text
                targets[scope] = response.json()["target_id"]
                await received(admin, scope, targets[scope], 2, namespace="scope-review")
            previous = await checkpoint(admin, scopes[1], "scope-review")
            await set_enabled(admin, "scope-review", False)
            runtime_config = state / "instances/scope-review/runtime-config.json"
            await cli(directory, "source", "runtime", "stop", "--config", str(runtime_config))
            manifest["scope_ids"] = scopes[:1]
            manifest["targets"] = manifest["targets"][:1]
            path.write_text(yaml.safe_dump(manifest))
            await cli(directory, "source", "load", "--config", str(path), "--env-file", str(envfile), "--update")
            await instance_state(admin, "scope-review", "stopped")
            fixture = json.loads((plugin / "events.json").read_text())
            fixture["tickets"][0]["body"] = "Changed after removing a Scope"
            (plugin / "events.json").write_text(json.dumps(fixture))
            await set_enabled(admin, "scope-review", True)
            await received(admin, scopes[0], targets[scopes[0]], 3, namespace="scope-review")
            # Observe another collection cycle before draining, regardless of target iteration order.
            fixture["tickets"][0]["body"] = "Another change while the Scope remains excluded"
            replacement = plugin / "events.next.json"
            replacement.write_text(json.dumps(fixture))
            replacement.replace(plugin / "events.json")
            await received(admin, scopes[0], targets[scopes[0]], 4, namespace="scope-review")
            # Stop and drain actual work before checking the removed Scope's stored progress.
            await set_enabled(admin, "scope-review", False)
            assert await checkpoint(admin, scopes[1], "scope-review") == previous
            await received(admin, scopes[1], targets[scopes[1]], 2, namespace="scope-review")
            page = await admin.get(f"/v1/scopes/{scopes[1]}/sources")
            assert page.status_code == 200 and len(page.json()["items"]) == 2

            # Re-admit the Scope through the public management API, without recreating its target.
            registration = json.loads((state / "instances/scope-review/registration.json").read_text())
            registration["scope_ids"] = scopes
            response = await admin.post(
                "/v1/source-management/configure", json={"registration": registration, "update": True}
            )
            assert response.status_code == 200, response.text
            await set_enabled(admin, "scope-review", True)
            restored = await received(admin, scopes[1], targets[scopes[1]], 3, namespace="scope-review")
            assert restored["target_id"] == targets[scopes[1]]
            receipts = [json.loads(line) for line in (plugin / "receipts.jsonl").read_text().splitlines()]
            assert any(r["scope_id"] == scopes[1] and r["source"] in restored["source_refs"] for r in receipts)

            async with Client(url + "/mcp", auth=TOKENS["deployer"]) as host:
                query = {"scope_id": scopes[0], "instance_id": "scope-review"}
                async with asyncio.timeout(40):
                    while True:
                        result = await call(host, "pc_source_instances", query)
                        if result["instances"] and result["instances"][0]["availability"] == "online":
                            break
                        await asyncio.sleep(0.1)
                await cli(directory, "source", "manager", "stop", "--config", str(state / "manager.json"))
                async with asyncio.timeout(40):
                    while True:
                        result = await call(host, "pc_source_instances", query)
                        if result["management"] == "unavailable":
                            break
                        await asyncio.sleep(0.1)
                assert result["instances"][0]["availability"] == "unavailable"
                capabilities = await call(host, "pc_source_capabilities", query)
                assert capabilities["capabilities"][0]["instances"][0]["availability"] == "unavailable"
                written = await call(
                    host,
                    "pc_source_add",
                    {
                        "scope_id": scopes[0],
                        "capability": "content.inline",
                        "capability_version": "2",
                        "input": {"source_id": "offline-write", "text": "write remains available"},
                    },
                )
                assert written["result"]["source_refs"] == [{"source_type": "content", "source_id": "offline-write"}]
        finally:
            await stop_services(directory, state)


def test_tickets_refreshes_targets_when_a_later_scope_is_disabled(tmp_path):
    asyncio.run(_tickets_target_refresh(tmp_path))


async def _tickets_target_refresh(directory):
    envfile, state = prepare_environment(directory)
    environment = {
        "POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR": str(directory / "source-plugins"),
        "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": json.dumps({"state_dir": str(state)}),
    }
    async with running_server(directory, environment=environment) as (admin, url, _process):
        bind_server(envfile, url)
        scopes = sorted([await create_scope(admin, name) for name in ("first", "second")])
        for scope in scopes:
            await grant_scope(admin, scope)
        plugin = directory / "tickets"
        shutil.copytree(ROOT / "examples/source_plugins/tickets", plugin, ignore=shutil.ignore_patterns("__pycache__"))
        entry = plugin / "__init__.py"
        # Pause real connector I/O so a later Scope's saved target becomes stale deterministically.
        entry.write_text(
            entry.read_text()
            + f"""

_run = TicketConnector.run
async def paused_run(self, session):
    if self.targets[0].scope_id == {scopes[0]!r} and (self.ctx.plugin_dir / "pause").exists():
        (self.ctx.plugin_dir / "paused").touch()
        while not (self.ctx.plugin_dir / "resume").exists():
            await asyncio.sleep(0.05)
    result = await _run(self, session)
    if (self.ctx.plugin_dir / "paused").exists():
        (self.ctx.plugin_dir / "finished").touch()
    return result
TicketConnector.run = paused_run
"""
        )
        records = plugin / "tickets.json"
        records.write_text('[{"id":"A","body":"Initial"}]')
        manifest = plugin / "powercontext.yml"
        manifest.write_text(
            yaml.safe_dump({
                "id": "tickets-refresh",
                "plugin": str(plugin),
                "scope_ids": scopes,
                "config": {"namespace": "refresh", "poll_interval": 0.1},
                "targets": [
                    {"scope_id": scope, "capability": "tickets.item", "capability_version": "1", "input": {"id": "A"}}
                    for scope in scopes
                ],
            })
        )
        try:
            await cli(directory, "source", "load", "--config", str(manifest), "--env-file", str(envfile))
            targets = []
            for scope in scopes:
                response = await admin.post(
                    "/v1/source-management/add",
                    json={
                        "scope_id": scope,
                        "instance_id": "tickets-refresh",
                        "capability": "tickets.item",
                        "capability_version": "1",
                        "input": {"id": "A"},
                    },
                )
                assert response.status_code == 200, response.text
                targets.append(response.json()["target_id"])
                await received(admin, scope, targets[-1], 1, namespace="refresh")
            (plugin / "pause").touch()
            async with asyncio.timeout(40):
                while not (plugin / "paused").exists():  # noqa: ASYNC110 - subprocess rendezvous
                    await asyncio.sleep(0.1)
            response = await admin.post(
                "/v1/source-management/target-set-enabled",
                json={
                    "scope_id": scopes[1],
                    "target_id": targets[1],
                    "enabled": False,
                },
            )
            assert response.status_code == 200, response.text
            async with asyncio.timeout(40):
                while True:
                    response = await admin.post(
                        "/v1/source-management/status",
                        json={
                            "scope_id": scopes[1],
                            "target_id": targets[1],
                        },
                    )
                    assert response.status_code == 200, response.text
                    if response.json()["tracking"] == "stopped":
                        break
                    await asyncio.sleep(0.1)
            (plugin / "resume").touch()
            # The paused batch may finish; require another batch to consume a new snapshot.
            async with asyncio.timeout(40):
                while not (plugin / "finished").exists():  # noqa: ASYNC110 - subprocess rendezvous
                    await asyncio.sleep(0.1)
            replacement = plugin / "tickets.next.json"
            replacement.write_text('[{"id":"A","body":"After target stop"}]')
            replacement.replace(records)
            await received(admin, scopes[0], targets[0], 2, namespace="refresh")
            assert (await instance_state(admin, "tickets-refresh"))["actual"] == "running"
            await received(admin, scopes[1], targets[1], 1, namespace="refresh")
        finally:
            (plugin / "resume").touch()
            await stop_services(directory, state)
