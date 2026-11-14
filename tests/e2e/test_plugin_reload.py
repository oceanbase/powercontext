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


class Notes:
    async def render_artifact(self, request):
        return Rendering(text="")

    async def version(self, request: ApiRequest) -> Version:
        return Version(version="{version}")


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
            assert first["routes"] == [{"method": "GET", "path": "/version", "access": "authenticated", "mcp": True}]
            assert first["version_digest"] and first["activated_at"] and first["last_check_failure"] is None
            # The version's upgrade ran before it served requests.
            assert len(marker.read_text().splitlines()) == 1

            document = (await client.get("/v1/plugins/notes/openapi.json")).json()
            operation = document["paths"]["/v1/plugins/notes/api/version"]["get"]
            assert operation["operationId"] == "notes__version"
            assert operation["responses"]["200"]["content"]["application/json"]["schema"]["title"] == "Version"

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
