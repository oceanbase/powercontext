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
