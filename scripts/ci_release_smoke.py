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

"""Run a public-interface smoke test against an installed PowerContext release."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.client import PowerContextClient
from powercontext.http import MemorySearchMode, RememberMemoryRequest, SearchMemoryRequest


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _console_script(python_executable: str | Path) -> Path:
    name = "powercontext.exe" if os.name == "nt" else "powercontext"
    executable = Path(python_executable).with_name(name)
    if not executable.is_file():
        raise RuntimeError(  # noqa: TRY003 - diagnostics identify the exact verification environment
            f"The powercontext console script is not installed next to {python_executable}"
        )
    return executable


def _wait_until_ready(base_url: str, process: subprocess.Popen[str], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "Server did not respond"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"PowerContext Server exited with status {process.returncode}")  # noqa: TRY003
        try:
            response = httpx.get(f"{base_url}/health/ready", timeout=2)
            if response.status_code == 200 and response.json().get("status") in {"ready", "degraded"}:
                return
            last_error = f"readiness returned HTTP {response.status_code}: {response.text}"
        except (httpx.HTTPError, ValueError) as error:
            last_error = str(error)
        time.sleep(0.25)
    raise RuntimeError(f"PowerContext Server was not ready within {timeout_seconds:g} seconds: {last_error}")  # noqa: TRY003


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as error:
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill is None:
                raise RuntimeError(  # noqa: TRY003
                    "Could not locate taskkill to stop the PowerContext Server"
                ) from error
            subprocess.run(  # noqa: S603 - exact child process tree created by this script
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


async def _exercise_public_interfaces(base_url: str) -> None:
    scope_id = "release-verification"
    memory_text = "PowerContext release verification stores and retrieves this exact memory."
    async with PowerContextClient(base_url) as client:
        readiness = await client.get_readiness()
        if readiness.status.value not in {"ready", "degraded"}:
            raise RuntimeError(f"Unexpected readiness status: {readiness.status.value}")  # noqa: TRY003
        remembered = await client.remember_memory(
            RememberMemoryRequest(scope_id=scope_id, kind="fact", text=memory_text)
        )
        if remembered.entry is None or remembered.entry.text != memory_text:
            raise RuntimeError("Remember operation did not return the stored Memory entry")  # noqa: TRY003
        found = await client.search_memory(
            SearchMemoryRequest(
                scope_id=scope_id,
                query="release verification retrieves",
                mode=MemorySearchMode.FTS,
            )
        )
        if [hit.text for hit in found.hits] != [memory_text]:
            raise RuntimeError(f"Search did not return the expected Memory entry: {found.hits!r}")  # noqa: TRY003

    async with Client(StreamableHttpTransport(f"{base_url}/mcp/")) as mcp:
        tools = {tool.name for tool in await mcp.list_tools()}
    required_tools = {"remember_memory", "search_memory"}
    if missing := required_tools - tools:
        raise RuntimeError(f"MCP endpoint is missing tools: {', '.join(sorted(missing))}")  # noqa: TRY003


def run_smoke(expected_version: str, timeout_seconds: float) -> None:
    installed_version = version("powercontext")
    if installed_version != expected_version:
        raise RuntimeError(  # noqa: TRY003
            f"Installed PowerContext version mismatch: expected {expected_version}, received {installed_version}"
        )

    executable = _console_script(sys.executable)

    with tempfile.TemporaryDirectory(prefix="powercontext-release-") as temporary:
        root = Path(temporary)
        port = _available_port()
        base_url = f"http://127.0.0.1:{port}"
        log_path = root / "server.log"
        environment = os.environ.copy()
        environment["POWERCONTEXT_HOME"] = str(root / "home")
        environment["POWERCONTEXT_SERVER_DATABASE_URL"] = f"sqlite+aiosqlite:///{root / 'runtime.db'}"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(  # noqa: S603 - resolved installed console script and fixed arguments
                [executable, "server", "run", "--host", "127.0.0.1", "--port", str(port)],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            try:
                _wait_until_ready(base_url, process, timeout_seconds)
                asyncio.run(_exercise_public_interfaces(base_url))
            except BaseException:
                log.flush()
                print("PowerContext Server log:")
                print(log_path.read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                _stop_process(process)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60)
    args = parser.parse_args()
    run_smoke(args.version, args.timeout_seconds)
    print(f"Verified PowerContext {args.version} CLI, Server, MCP, and SQLite Memory search.")


if __name__ == "__main__":
    main()
