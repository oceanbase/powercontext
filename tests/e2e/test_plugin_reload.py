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

"""A running Server follows changes to its plugin directory without a restart."""

import asyncio
import shutil

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, running_server, wait_for

PLUGIN = """
import os
from pathlib import Path

from pydantic import BaseModel

from powercontext.plugins import ApiRequest, ArtifactPlugin, PluginApi, Rendering, Route


class Version(BaseModel):
    version: str


class Child(BaseModel):
    text: str


class Note(BaseModel):
    child: Child


class Notes:
    async def render_artifact(self, request):
        return Rendering(text="")

    async def version(self, request: ApiRequest) -> Version:
        return Version(version="{version}")

    async def echo(self, request: ApiRequest) -> Note:
        return request.body


async def setup(ctx):
    return Notes()


async def upgrade(ctx):
    {upgrade}


plugin = ArtifactPlugin(
    family="{family}",
    setup=setup,
    upgrade=upgrade,
    upgrade_timeout_seconds=20,
    api=PluginApi(
        routes=(
            Route(
                method="GET",
                path="/version",
                handler="version",
                access="authenticated",
                response_model=Version,
                description="The plugin version that answered.",
                mcp=True,
            ),
            Route(
                method="POST",
                path="/echo",
                handler="echo",
                access="authenticated",
                body="json",
                request_model=Note,
                response_model=Note,
            ),
        )
    ),
)
"""


def write(directory, *, version, upgrade="pass", family="reload-notes"):
    directory.mkdir(parents=True, exist_ok=True)
    source = PLUGIN.replace("{version}", version).replace("{upgrade}", upgrade).replace("{family}", family)
    (directory / "plugin.py").write_text(source, encoding="utf-8")


def record(marker):
    # The upgrade appends to a file the test reads, so each load of a version is visible.
    return f'Path({str(marker)!r}).open("a").write("{{}}\\n".format(os.getpid()))'


def test_server_reloads_added_updated_and_removed_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    notes = plugins / "notes"
    marker = tmp_path / "upgrades.txt"
    write(notes, version="v1", upgrade=record(marker))
    log = tmp_path / "server.log"
    env = {"POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS": "0.5", "POWERCONTEXT_SERVER_MCP_ENABLED": "true"}

    async def scenario():
        async with running_server(tmp_path, plugins, extra_env=env) as client:

            async def version():
                response = await client.get("/v1/plugins/notes/api/version")
                return response.json().get("version") if response.status_code == 200 else response.status_code

            assert await wait_for(version, lambda value: value == "v1", log=log) == "v1"
            first = (await client.get("/v1/plugins/notes")).json()
            assert first["status"] == "loaded"
            assert first["routes"] == [
                {"method": "GET", "path": "/version", "access": "authenticated", "mcp": True},
                {"method": "POST", "path": "/echo", "access": "authenticated", "mcp": False},
            ]
            assert first["version_digest"] and first["activated_at"] and first["last_check_failure"] is None
            # The version's upgrade ran before it served requests.
            assert len(marker.read_text().splitlines()) == 1

            document = (await client.get("/v1/plugins/notes/openapi.json")).json()
            operation = document["paths"]["/v1/plugins/notes/api/version"]["get"]
            assert operation["operationId"] == "notes__version"
            assert operation["responses"]["200"]["content"]["application/json"]["schema"]["title"] == "Version"
            # Nested models are document components, so every reference resolves within the document.
            echo = document["paths"]["/v1/plugins/notes/api/echo"]["post"]
            child = echo["requestBody"]["content"]["application/json"]["schema"]["properties"]["child"]
            assert child == {"$ref": "#/components/schemas/Child"}
            assert all(_resolves(document, reference) for reference in _references(document))

            # A changed directory takes effect without a restart; the new version upgrades first.
            write(notes, version="v2", upgrade=record(marker))
            assert await wait_for(version, lambda value: value == "v2", log=log) == "v2"
            second = (await client.get("/v1/plugins/notes")).json()
            assert second["version_digest"] != first["version_digest"]
            assert len(marker.read_text().splitlines()) == 2

            # A version whose upgrade fails is not used; the previous version keeps serving.
            write(notes, version="v3", upgrade='raise RuntimeError("cannot upgrade")')
            failed = await wait_for(
                lambda: client.get("/v1/plugins/notes"),
                lambda response: response.json()["last_check_failure"] is not None,
                log=log,
            )
            assert failed.json()["last_check_failure"] == "upgrade_failed"
            assert failed.json()["version_digest"] == second["version_digest"]
            assert await version() == "v2"

            # MCP lists the route as a tool of the loaded version and calls it as the caller.
            transport = StreamableHttpTransport(
                f"{client.base_url}/mcp/", headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"}
            )
            async with Client(transport) as mcp:
                tools = {tool.name: tool for tool in await mcp.list_tools()}
                assert tools["notes__version"].annotations.readOnlyHint is True
                result = await mcp.call_tool("notes__version", {})
                assert result.structured_content == {"version": "v2"}

            # Added and removed directories load and unload; a removed plugin's routes and tools disappear.
            write(plugins / "extra", version="x1", family="extra-notes")
            await wait_for(
                lambda: client.get("/v1/plugins/extra/api/version"),
                lambda response: response.status_code == 200,
                log=log,
            )
            shutil.rmtree(plugins / "extra")
            await wait_for(
                lambda: client.get("/v1/plugins/extra"), lambda response: response.status_code == 404, log=log
            )
            assert (await client.get("/v1/plugins/extra/api/version")).status_code == 404
            async with Client(transport) as mcp:
                names = {tool.name for tool in await mcp.list_tools()}
            assert "notes__version" in names
            assert "extra__version" not in names

    asyncio.run(scenario())


def _references(value):
    if isinstance(value, dict):
        if isinstance(value.get("$ref"), str):
            yield value["$ref"]
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def _resolves(document, reference):
    target = document
    for part in reference.removeprefix("#/").split("/"):
        if not isinstance(target, dict) or part not in target:
            return False
        target = target[part]
    return True


PHASES = """
import asyncio
import time

from pydantic import BaseModel

from powercontext.plugins import ApiRequest, ArtifactPlugin, PluginApi, Rendering, Route


class Version(BaseModel):
    version: str


class Slow:
    async def render_artifact(self, request):
        return Rendering(text="")

    async def version(self, request: ApiRequest) -> Version:
        return Version(version="{version}")


async def setup(ctx):
    {setup}
    return Slow()


async def upgrade(ctx):
    {upgrade}


plugin = ArtifactPlugin(
    family="slow-notes",
    setup=setup,
    upgrade={upgrade_ref},
    upgrade_timeout_seconds={upgrade_seconds},
    setup_timeout_seconds={setup_seconds},
    api=PluginApi(routes=(Route(method="GET", path="/version", handler="version", access="authenticated"),)),
)
"""


def write_phases(directory, *, version, upgrade=None, upgrade_seconds=20, setup="pass", setup_seconds=60):
    directory.mkdir(parents=True, exist_ok=True)
    source = (
        PHASES
        .replace("{version}", version)
        .replace("{upgrade_ref}", "None" if upgrade is None else "upgrade")
        .replace("{upgrade}", upgrade or "pass")
        .replace("{upgrade_seconds}", str(upgrade_seconds))
        .replace("{setup}", setup)
        .replace("{setup_seconds}", str(setup_seconds))
    )
    (directory / "plugin.py").write_text(source, encoding="utf-8")


def test_each_host_startup_phase_has_its_own_time_limit(tmp_path):
    plugins = tmp_path / "plugins"
    slow = plugins / "slow"
    # The upgrade takes longer than the setup limit; setup is timed only from the end of the upgrade.
    write_phases(slow, version="v1", upgrade="await asyncio.sleep(4)", setup="await asyncio.sleep(1)", setup_seconds=2)
    log = tmp_path / "server.log"
    env = {"POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS": "0.5"}

    async def scenario():
        # Startup waits for the first version's upgrade, which adds to a Server start on a loaded machine.
        async with running_server(tmp_path, plugins, extra_env=env, startup_seconds=180) as client:

            async def version():
                response = await client.get("/v1/plugins/slow/api/version")
                return response.json().get("version") if response.status_code == 200 else response.status_code

            async def failure_after(previous):
                response = await wait_for(
                    lambda: client.get("/v1/plugins/slow"),
                    lambda response: response.json()["last_check_failure"] not in {None, previous},
                    log=log,
                )
                return response.json()["last_check_failure"]

            assert await wait_for(version, lambda value: value == "v1", log=log) == "v1"

            # An upgrade that blocks its event loop past its limit is stopped by the Server, which names the phase.
            write_phases(slow, version="v2", upgrade="time.sleep(30)", upgrade_seconds=2)
            assert await failure_after(None) == "upgrade_timeout"
            assert await version() == "v1"

            # So is a setup that runs past its limit, with or without an upgrade before it.
            write_phases(slow, version="v3", setup="time.sleep(30)", setup_seconds=2)
            assert await failure_after("upgrade_timeout") == "setup_timeout"
            assert await version() == "v1"

            # A plugin that declares a longer setup limit gets it.
            write_phases(slow, version="v4", setup="await asyncio.sleep(3)", setup_seconds=10)
            assert await wait_for(version, lambda value: value == "v4", log=log) == "v4"

    asyncio.run(scenario())


SOURCE_DEFINITIONS = """
from pydantic import BaseModel, Field

from powercontext.plugins import ApiRequest, ArtifactPlugin, PluginApi, Rendering, Route
from powercontext.sources import Source, SourceMaterialization


class Version(BaseModel):
    version: str


class TicketInput(BaseModel):
    key: str
    title: str = Field(min_length={min_length})


class TicketSource(Source):
    title: str


class TicketProjection:
    name = "tickets.ticket"
    version = "1"
    source_class = TicketSource
    output_class = TicketInput

    def project(self, source, /):
        return TicketInput(key=source.name, title=source.title)


class TicketDefinition:
    input_class = TicketInput
    name = "ticket"
    source_class = TicketSource
    version = "{definition}"
    projections = (TicketProjection(),)

    async def resolve(self, value, /):
        return TicketSource(
            name=value.key, materialization=SourceMaterialization.CAPTURED, description="A ticket", title=value.title
        )

    async def read(self, source, /):
        return TicketProjection().project(source)


class Tickets:
    async def render_artifact(self, request):
        return Rendering(text="")

    async def version(self, request: ApiRequest) -> Version:
        return Version(version="{version}")


async def setup(ctx):
    return Tickets()


plugin = ArtifactPlugin(
    family="tickets",
    definitions=(TicketDefinition(),),
    setup=setup,
    api=PluginApi(routes=(Route(method="GET", path="/version", handler="version", access="authenticated"),)),
)
"""


def write_release(directory, *, version, definition="1", min_length=1):
    directory.mkdir(parents=True, exist_ok=True)
    source = (
        SOURCE_DEFINITIONS
        .replace("{version}", version)
        .replace("{definition}", definition)
        .replace("{min_length}", str(min_length))
    )
    (directory / "plugin.py").write_text(source, encoding="utf-8")


def point(link, target):
    # Deployers switch releases by replacing a symbolic link in one rename.
    staging = link.with_name(f".{link.name}.next")
    staging.symlink_to(target, target_is_directory=True)
    staging.replace(link)


def test_symbolic_link_switches_and_rejected_definitions_keep_the_loaded_version(tmp_path):
    plugins = tmp_path / "plugins"
    releases = tmp_path / "releases"
    plugins.mkdir()
    for version in ("v1", "v2"):
        write_release(releases / version, version=version)
    # The same Source Definition name and version with another schema cannot be registered.
    write_release(releases / "v3", version="v3", min_length=2)
    write_release(releases / "v4", version="v4", definition="2", min_length=2)
    point(plugins / "tickets", releases / "v1")
    log = tmp_path / "server.log"
    env = {"POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS": "0.5"}

    async def scenario():
        async with running_server(tmp_path, plugins, extra_env=env) as client:

            async def version():
                response = await client.get("/v1/plugins/tickets/api/version")
                return response.json().get("version") if response.status_code == 200 else response.status_code

            assert await wait_for(version, lambda value: value == "v1", log=log) == "v1"
            point(plugins / "tickets", releases / "v2")
            assert await wait_for(version, lambda value: value == "v2", log=log) == "v2"

            point(plugins / "tickets", releases / "v3")
            failed = await wait_for(
                lambda: client.get("/v1/plugins/tickets"),
                lambda response: response.json()["last_check_failure"] is not None,
                log=log,
            )
            assert failed.json()["status"] == "loaded"
            assert await version() == "v2"

            point(plugins / "tickets", releases / "v4")
            assert await wait_for(version, lambda value: value == "v4", log=log) == "v4"
            assert (await client.get("/v1/plugins/tickets")).json()["last_check_failure"] is None

    asyncio.run(scenario())


def test_servers_sharing_a_data_directory_keep_each_others_version_snapshots(tmp_path):
    plugins = tmp_path / "plugins"
    notes = plugins / "notes"
    home = tmp_path / "home"
    versions = home / "plugin-versions"
    write(notes, version="v1")
    # Left by a process that was killed: nobody holds its lock.
    abandoned = versions / "1-abandoned"
    (abandoned / "notes").mkdir(parents=True)
    (versions / "1-abandoned.lock").touch()
    shared = {"POWERCONTEXT_HOME": str(home), "POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS": "0"}

    async def version(client):
        response = await client.get("/v1/plugins/notes/api/version")
        return response.json().get("version") if response.status_code == 200 else response.status_code

    def snapshots():
        return {path.parent.parent.name for path in versions.glob("*/notes/*")}

    async def scenario():
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        async with running_server(tmp_path / "a", plugins, extra_env=shared) as first:
            log = tmp_path / "a" / "server.log"
            assert await wait_for(lambda: version(first), lambda value: value == "v1", log=log) == "v1"
            (first_directory,) = snapshots()
            assert not abandoned.exists()

            # A second process on the same data directory starts with another version and its own snapshots.
            write(notes, version="v2")
            async with running_server(tmp_path / "b", plugins, extra_env=shared) as second:
                log = tmp_path / "b" / "server.log"
                assert await wait_for(lambda: version(second), lambda value: value == "v2", log=log) == "v2"
                assert first_directory in snapshots() and len(snapshots()) == 2
                assert await version(first) == "v1"
            # The second process removed only its own snapshots when it stopped.
            assert snapshots() == {first_directory}
            assert await version(first) == "v1"

    asyncio.run(scenario())
