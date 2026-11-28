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

"""How a Server process follows its plugin directory from one scan to the next."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.server import plugins as plugin_manager
from powercontext.server.plugins import FAMILY_CONFLICT, PluginManager

PLUGIN = """
from pathlib import Path

from powercontext.plugins import ArtifactPlugin, Rendering

{prelude}


class Notes:
    async def render_artifact(self, request):
        return Rendering(text="")


async def setup(ctx):
    return Notes()


plugin = ArtifactPlugin(family="{family}", setup=setup)
"""


def write(directory, family, prelude=""):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.py").write_text(PLUGIN.format(family=family, prelude=prelude), encoding="utf-8")


async def scenario(tmp_path, steps):
    async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
        manager = PluginManager(
            profile.database,
            SQLiteConfig(),
            None,
            lambda: None,
            lambda _: None,
            tmp_path / "plugins",
            dispatch=False,
            serve_requests=False,
            snapshot_root=tmp_path / "versions",
        )
        async with manager:
            await steps(manager)


def loaded(manager, name):
    plugin = manager.plugins.get(name)
    return plugin is not None and plugin.manifest is not None


def test_a_directory_missing_from_one_scan_stays_loaded_and_a_freed_family_is_retried(tmp_path):
    plugins = tmp_path / "plugins"
    write(plugins / "alpha", "scan-shared")
    write(plugins / "beta", "scan-shared")

    async def steps(manager):
        assert loaded(manager, "alpha")
        assert manager.plugins["beta"].last_check_failure == FAMILY_CONFLICT

        # Replacing a directory by renames can hide it from one scan; that alone does not unload it.
        (plugins / "alpha").rename(tmp_path / "alpha")
        await manager.scan()
        assert loaded(manager, "alpha")
        await manager.scan()
        assert not loaded(manager, "alpha")

        # The plugin that wanted the same family is checked again at the next scan, without waiting.
        await manager.scan()
        assert loaded(manager, "beta")

    asyncio.run(scenario(tmp_path, steps))


def test_a_failed_version_is_checked_again_after_a_growing_wait(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    ready = tmp_path / "ready"
    # The check fails until a file outside the plugin directory exists, as a missing dependency would.
    write(plugins / "gamma", "scan-gamma", prelude=f"Path({str(ready)!r}).read_text()")
    offset = 0.0
    monkeypatch.setattr(plugin_manager.time, "monotonic", lambda: time.perf_counter() + offset)

    async def steps(manager):
        nonlocal offset
        gamma = manager.plugins["gamma"]
        assert not loaded(manager, "gamma") and gamma.retry_seconds == pytest.approx(300)

        await manager.scan()
        assert gamma.retry_seconds == pytest.approx(300)
        offset += 301
        await manager.scan()
        # Still failing: the next wait doubles.
        assert not loaded(manager, "gamma") and gamma.retry_seconds == pytest.approx(600)

        ready.write_text("")
        await manager.scan()
        assert not loaded(manager, "gamma")
        offset += 601
        await manager.scan()
        assert loaded(manager, "gamma")

    asyncio.run(scenario(tmp_path, steps))


def test_a_version_snapshot_holds_copies_of_linked_files_and_refuses_links_outside_the_plugin(tmp_path):
    plugins = tmp_path / "plugins"
    write(plugins / "delta", "scan-delta", prelude="import helper")
    shared = plugins / "delta" / "shared"
    shared.mkdir()
    (shared / "helper.py").write_text("VERSION = 'v1'\n", encoding="utf-8")
    # An absolute link into the live plugin directory, as deployment tools often create.
    (plugins / "delta" / "helper.py").symlink_to(shared / "helper.py")
    outside = tmp_path / "secret.txt"
    outside.write_text("not part of any plugin", encoding="utf-8")
    write(plugins / "epsilon", "scan-epsilon")
    (plugins / "epsilon" / "secret.txt").symlink_to(outside)

    async def steps(manager):
        delta = manager.plugins["delta"]
        assert loaded(manager, "delta")
        copied = delta.snapshot / "helper.py"
        assert not copied.is_symlink() and copied.read_text() == "VERSION = 'v1'\n"

        # Deploying the next version through the link leaves the loaded version's files as they were.
        (shared / "helper.py").write_text("VERSION = 'v2'\n", encoding="utf-8")
        assert copied.read_text() == "VERSION = 'v1'\n"

        epsilon = manager.plugins["epsilon"]
        assert not loaded(manager, "epsilon")
        assert epsilon.last_check_failure == "secret.txt links outside the plugin directory"

    asyncio.run(scenario(tmp_path, steps))


def test_a_plugin_with_a_loop_of_links_fails_alone(tmp_path):
    plugins = tmp_path / "plugins"
    write(plugins / "looped", "scan-looped")
    (plugins / "looped" / "a").symlink_to(plugins / "looped" / "b")
    (plugins / "looped" / "b").symlink_to(plugins / "looped" / "a")
    write(plugins / "sound", "scan-sound")

    async def steps(manager):
        # The Server starts and the other plugin loads; only the broken directory is reported.
        assert loaded(manager, "sound")
        assert not loaded(manager, "looped")
        assert manager.plugins["looped"].last_check_failure == "a is a link that cannot be resolved"

    asyncio.run(scenario(tmp_path, steps))


@pytest.mark.parametrize(
    ("routes", "message"),
    [
        (
            [
                {"method": "GET", "path": "/items", "handler": "items", "mcp": True, "description": "List items."},
                {"method": "POST", "path": "/items", "handler": "items", "mcp": True, "description": "Add an item."},
            ],
            "MCP routes need distinct handlers; each handler is one tool",
        ),
        (
            [{"method": "POST", "path": "/items/{body}", "handler": "put", "mcp": True, "description": "Put an item."}],
            "MCP routes cannot name a path parameter query or body",
        ),
        (
            [{"method": "GET", "path": "/items/{query}", "handler": "find", "mcp": True, "description": "Find items."}],
            "MCP routes cannot name a path parameter query or body",
        ),
    ],
    ids=["shared-handler", "body-parameter", "query-parameter"],
)
def test_mcp_routes_that_cannot_be_called_as_tools_fail_the_check(tmp_path, routes, message):
    from powercontext.builtin.plugins.manifest import PluginConfigError, load_plugin
    from tests.plugin_support import write_plugin

    api = {"routes": [{"access": "authenticated", **route} for route in routes]}
    directory = write_plugin(tmp_path, "tools", "async def setup(ctx):\n    return object()\n", api=api)
    with pytest.raises(PluginConfigError, match=message):
        load_plugin(directory)
    # The same routes are fine when only one of them, or none, is a tool.
    for route in api["routes"][1:] or api["routes"]:
        route["mcp"] = False
    directory = write_plugin(tmp_path, "plain", "async def setup(ctx):\n    return object()\n", api=api)
    assert load_plugin(directory)[0].spec.api is not None


def test_a_cancelled_wait_for_a_retiring_version_still_stops_its_processes(tmp_path):
    """Shutdown cancels the wait for an old version's calls; its host must close before its snapshot is released."""

    events = []

    class Host:
        async def retire(self):
            await asyncio.Event().wait()

        async def close(self):
            # The snapshot is still held while the old version's processes stop.
            events.append(("closed", dict(manager._retiring)))

    async def scenario():
        retiring = asyncio.create_task(PluginManager._retire(cast(Any, manager), cast(Any, Host()), tmp_path))
        await asyncio.sleep(0)
        retiring.cancel()
        with pytest.raises(asyncio.CancelledError):
            await retiring

    manager = SimpleNamespace(_retiring={})
    asyncio.run(scenario())
    assert events == [("closed", {tmp_path: 1})] and manager._retiring == {tmp_path: 0}


def test_a_server_schedule_failure_keeps_the_collection_loop_alive(monkeypatch, caplog):
    import logging
    from zoneinfo import ZoneInfoNotFoundError

    import tzlocal

    from powercontext.plugins import Cron

    requested = []
    collected = asyncio.Event()
    parked = asyncio.Event()

    async def request_collection(name):
        requested.append(name)
        collected.set()
        # One collection only: the scenario cancels the task while it waits here.
        await parked.wait()

    manager = SimpleNamespace(request_collection=request_collection)
    manifest = SimpleNamespace(
        name="ticket-chat",
        source=SimpleNamespace(collect=SimpleNamespace(schedule=Cron(expression="*/5 * * * *"))),
    )
    # The machine's zone name cannot be resolved on the first attempt, then resolves.
    state = {"failed": False}

    def local_zone_name():
        if not state["failed"]:
            state["failed"] = True
            raise ZoneInfoNotFoundError()
        return "UTC"

    monkeypatch.setattr(tzlocal, "get_localzone_name", local_zone_name)

    real_sleep = asyncio.sleep

    async def instant(seconds):
        # Yield like the real sleep so the waiting scenario and a cancel are delivered.
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", instant)

    async def scenario():
        task = asyncio.create_task(PluginManager._collect_due(cast(Any, manager), cast(Any, manifest)))
        # A loop that dies on the scheduling failure never collects; fail instead of hanging.
        await asyncio.wait_for(collected.wait(), 5)
        # The task is parked inside request_collection after its first call, so canceling there
        # keeps the count below exact regardless of how the recovered iterations interleave.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    # The failed iteration warned and collected nothing off-schedule; the recovered one collected.
    assert requested == ["ticket-chat"]
    assert any("ticket-chat schedule failed" in record.getMessage() for record in caplog.records)
