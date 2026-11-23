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

"""The real CLI fixture retains its selected TCP endpoint through startup and cleanup."""

from __future__ import annotations

import asyncio
import errno
import json
import socket
from contextlib import suppress

import pytest

from tests.e2e.plugin_server import running_server


def test_running_server_reserves_its_endpoint_before_cli_startup(tmp_path, monkeypatch):
    port_file = tmp_path / "server-port.json"
    original_spawn = asyncio.create_subprocess_exec
    processes = []

    async def record_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_spawn)

    async def scenario():
        with socket.socket() as competitor:

            async def compete():
                async with asyncio.timeout(60):
                    while not port_file.exists():  # noqa: ASYNC110 - child process publishes through this file
                        await asyncio.sleep(0.01)
                port = json.loads(port_file.read_text())["port"]
                try:
                    competitor.bind(("127.0.0.1", port))
                except OSError as error:
                    assert error.errno == errno.EADDRINUSE
                    return port
                pytest.fail("another process can acquire the Server endpoint during startup")

            competition = asyncio.create_task(compete())
            try:
                async with running_server(tmp_path) as client:
                    port = await competition
                    assert client.base_url.port == port
                    assert (await client.get("/health/ready")).status_code == 200
            finally:
                competition.cancel()
                with suppress(asyncio.CancelledError):
                    await competition
        assert processes and all(process.returncode is not None for process in processes)

    asyncio.run(scenario())


def test_running_server_startup_failure_releases_its_endpoint(tmp_path):
    async def scenario():
        with pytest.raises(AssertionError, match="bearer token"):
            async with running_server(tmp_path, extra_env={"POWERCONTEXT_SERVER_AUTH_TOKEN": ""}):
                pytest.fail("the invalid CLI configuration unexpectedly became ready")
        port = json.loads((tmp_path / "server-port.json").read_text())["port"]
        with socket.socket() as after_exit:
            after_exit.bind(("127.0.0.1", port))

    asyncio.run(scenario())
