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

"""Use wizard-generated configuration to save through MCP and read in Dashboard."""

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from typer.testing import CliRunner

from powercontext.cli.config import app as config_app
from powercontext.cli.env_file import parse_environment
from powercontext.server.configuration import server_settings_context
from powercontext.server.factory import create_server_app


@pytest.mark.parametrize("require_authentication", [False, True], ids=["anonymous-default", "authenticated"])
def test_local_wizard_connects_dashboard_api_and_mcp(tmp_path: Path, require_authentication: bool) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    output = tmp_path / "server.env"
    authentication_answer = "y" if require_authentication else ""
    result = CliRunner().invoke(
        config_app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'memory.db'}\nlocal\nbase\ny\n{port}\n{authentication_answer}\n"
            "codex\ndefault\nnone\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    if not require_authentication:
        assert "POWERCONTEXT_SERVER_AUTH_TOKEN" not in values
        assert "POWERCONTEXT_CLIENT_API_TOKEN" not in values
        assert "POWERCONTEXT_CODEX_AUTHORIZATION" not in values
    base_url = values["POWERCONTEXT_CLIENT_SERVER_URL"]
    headers = {"Authorization": values["POWERCONTEXT_CODEX_AUTHORIZATION"]} if require_authentication else {}
    with server_settings_context(env_file=output) as settings:
        app = create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")

    def create_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def exercise() -> None:
        transport = StreamableHttpTransport(
            f"{base_url}/mcp/", headers=headers, httpx_client_factory=create_http_client
        )
        async with app.router.lifespan_context(app), create_http_client() as browser:
            home = await browser.get("/dashboard/home")
            assert home.status_code == (401 if require_authentication else 200)
            if require_authentication:
                assert (await browser.get("/v1/scopes/default")).status_code == 401
                assert (
                    await browser.get("/v1/scopes/default", headers={"Authorization": "Bearer invalid"})
                ).status_code == 401
                login = await browser.post(
                    "/dashboard/session",
                    headers={"Origin": base_url},
                    data={"token": values["POWERCONTEXT_SERVER_AUTH_TOKEN"]},
                )
                assert login.status_code == 200
            scope = await browser.get("/v1/scopes/default", headers=headers)
            assert scope.status_code == 200
            text = "Local Dashboard authentication is optional."
            async with Client(transport) as mcp:
                saved = await mcp.call_tool(
                    "remember_memory", {"scope_id": scope.json()["scope_id"], "kind": "decision", "text": text}
                )
                assert not saved.is_error
            notes = await browser.get("/dashboard/notes")
            assert notes.status_code == 200
            assert text in notes.text

    asyncio.run(exercise())
