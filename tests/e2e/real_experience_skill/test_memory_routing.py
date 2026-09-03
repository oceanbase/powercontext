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

"""Opt-in real-Codex acceptance tests for explicit Memory routing."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    ListMemoryEntriesRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CODEX_PLUGIN = PROJECT_ROOT / "integrations" / "codex"
SCOPE_ID = "project:codex-memory-routing"

pytestmark = pytest.mark.real_e2e


@pytest.fixture(autouse=True)
def _require_real_e2e(pytestconfig: pytest.Config) -> None:
    if not pytestconfig.getoption("run_real_e2e"):
        pytest.skip("requires --run-real-e2e")


class _RunningServer:
    def __init__(self, tmp_path: Path) -> None:
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        host, port = self._listener.getsockname()
        self.base_url = f"http://{host}:{port}"
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory-routing.db'}"),
                mcp=McpConfig(enabled=True),
            )
        )
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
        self._thread = threading.Thread(
            target=self._server.run,
            kwargs={"sockets": [self._listener]},
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started and self._thread.is_alive():
            if time.monotonic() >= deadline:
                break
            threading.Event().wait(0.01)
        assert self._server.started, "PowerContext Server did not start"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)
        self._listener.close()
        assert not self._thread.is_alive()


def _codex_executable() -> Path:
    configured = shutil.which("codex")
    if configured:
        return Path(configured)
    bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    if bundled.is_file():
        return bundled
    pytest.skip("real Codex CLI is not installed")


def _auth_file() -> Path:
    configured_home = os.environ.get("CODEX_HOME")
    path = Path(configured_home) if configured_home else Path.home() / ".codex"
    auth = path / "auth.json"
    if not auth.is_file():
        pytest.skip(f"real Codex auth is unavailable at {auth}")
    return auth


def _config_file() -> Path | None:
    configured_home = os.environ.get("CODEX_HOME")
    path = Path(configured_home) if configured_home else Path.home() / ".codex"
    config = path / "config.toml"
    return config if config.is_file() else None


def _prepare_codex_home(root: Path, *, mcp_url: str, timeout: int) -> Path:
    home = root / "codex-home"
    home.mkdir()
    shutil.copyfile(_auth_file(), home / "auth.json")
    (home / "auth.json").chmod(0o600)
    if config := _config_file():
        # Preserve the caller's model provider (including a configured relay) in the isolated home.
        shutil.copyfile(config, home / "config.toml")

    marketplace = root / "marketplace"
    shutil.copytree(CODEX_PLUGIN, marketplace)
    configuration_path = marketplace / "plugins" / "powercontext" / ".mcp.json"
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    configuration["mcpServers"]["powercontext"]["url"] = mcp_url
    configuration_path.write_text(json.dumps(configuration), encoding="utf-8")

    environment = {**os.environ, "CODEX_HOME": str(home)}
    _run_codex(
        ["plugin", "marketplace", "add", str(marketplace), "--json"],
        environment=environment,
        timeout=timeout,
    )
    _run_codex(
        ["plugin", "add", "powercontext@powercontext-local", "--json"],
        environment=environment,
        timeout=timeout,
    )
    return home


def _run_codex(arguments: list[str], *, environment: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [str(_codex_executable()), *arguments],
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"real Codex timed out after {timeout}s: {error}")
    assert result.returncode == 0, result.stderr
    return result


def _run_prompt(
    home: Path,
    repository: Path,
    prompt: str,
    output_path: Path,
    *,
    timeout: int,
) -> tuple[list[dict[str, Any]], str]:
    environment = {
        **os.environ,
        "CODEX_HOME": str(home),
        "POWERCONTEXT_CODEX_SCOPE_ID": SCOPE_ID,
        "NO_COLOR": "1",
    }
    result = _run_codex(
        [
            "exec",
            "--ephemeral",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--json",
            "--skip-git-repo-check",
            "-C",
            str(repository),
            "-o",
            str(output_path),
            prompt,
        ],
        environment=environment,
        timeout=timeout,
    )
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return events, output_path.read_text(encoding="utf-8")


def _walk(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield cast(dict[str, Any], value)
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _tool_calls(events: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in events:
        for item in _walk(event):
            name = item.get("name") or item.get("tool_name") or item.get("tool")
            if isinstance(name, str) and (
                name == tool_name
                or name.endswith(f"__{tool_name}")
                or name.endswith(f".{tool_name}")
                or name.endswith(f"/{tool_name}")
            ):
                calls.append(item)
    return calls


def _has_tool_name(events: list[dict[str, Any]], tool_name: str) -> bool:
    return bool(_tool_calls(events, tool_name))


def _list_entries(server_url: str) -> list[Any]:
    async def read() -> list[Any]:
        async with PowerContextClient(server_url) as client:
            result = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=SCOPE_ID))
            return result.entries

    return asyncio.run(read())


def test_real_codex_routes_explicit_save_and_search_without_handoff_selector(
    tmp_path: Path, pytestconfig: pytest.Config
) -> None:
    _codex_executable()  # Resolve and validate the executable before allocating server state.
    timeout = pytestconfig.getoption("real_codex_timeout")
    server = _RunningServer(tmp_path)
    server.start()
    try:
        with tempfile.TemporaryDirectory(prefix="codex-memory-routing-") as root_name:
            root = Path(root_name)
            home = _prepare_codex_home(root, mcp_url=f"{server.base_url}/mcp", timeout=timeout)
            repository = root / "repository"
            repository.mkdir()

            save_events, save_message = _run_prompt(
                home,
                repository,
                "remember I prefer uv for Python",
                root / "save.last.json",
                timeout=timeout,
            )
            save_calls = _tool_calls(save_events, "remember_memory")
            assert save_calls, save_message
            assert not _has_tool_name(save_events, "select_handoff_workstream")
            entries = _list_entries(server.base_url)
            assert any("uv" in entry.text and "Python" in entry.text for entry in entries)
            assert any(entry.kind == "preference" for entry in entries)
            lowered_save_message = save_message.lower()
            assert any(token in lowered_save_message for token in ("saved", "save", "remember", "保存", "记住"))

            search_events, search_message = _run_prompt(
                home,
                repository,
                "What do you remember about Python tooling?",
                root / "search.last.json",
                timeout=timeout,
            )
            search_calls = _tool_calls(search_events, "search_memory")
            assert search_calls, search_message
            assert not _has_tool_name(search_events, "select_handoff_workstream")
            arguments = search_calls[0].get("arguments", search_calls[0].get("input"))
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            assert isinstance(arguments, dict)
            assert arguments.get("mode", "auto") == "auto"
            assert int(arguments.get("limit", 8)) <= 8
            assert "uv" in search_message.lower() or "python" in search_message.lower()
    finally:
        server.stop()


def test_real_codex_does_not_claim_save_success_when_mcp_is_unavailable(
    tmp_path: Path, pytestconfig: pytest.Config
) -> None:
    timeout = pytestconfig.getoption("real_codex_timeout")
    with tempfile.TemporaryDirectory(prefix="codex-memory-unavailable-") as root_name:
        root = Path(root_name)
        home = _prepare_codex_home(root, mcp_url="http://127.0.0.1:1/mcp", timeout=timeout)
        repository = root / "repository"
        repository.mkdir()
        events, message = _run_prompt(
            home,
            repository,
            "remember I prefer uv for Python",
            root / "unavailable.last.json",
            timeout=timeout,
        )
        assert not _has_tool_name(events, "select_handoff_workstream")
        lowered = message.lower()
        assert not any(
            token in lowered
            for token in ("memory was saved", "memory saved", "successfully saved", "记忆已保存", "已成功保存")
        )
        assert any(token in lowered for token in ("unavailable", "could not", "failed", "not saved", "未保存"))
