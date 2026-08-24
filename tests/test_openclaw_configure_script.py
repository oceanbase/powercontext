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

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "integrations"
    / "openclaw"
    / "plugins"
    / "memory-powercontext"
    / "scripts"
    / "configure-openclaw.py"
)
SPEC = importlib.util.spec_from_file_location("configure_openclaw", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
configure_openclaw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_openclaw)


def test_allowlist_update_is_idempotent_and_preserves_unrelated_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    state: list[object] = ["custom_tool", "powercontext_memory_get"]

    def fake_run(command: list[str], *, timeout: int, check: bool = True) -> CompletedProcess[str]:
        del timeout, check
        if command[1:4] == ["config", "get", "tools.alsoAllow"]:
            return CompletedProcess(command, 0, json.dumps(state), "")
        if command[1:3] == ["config", "set"] and command[3] == "tools.alsoAllow":
            state[:] = json.loads(command[4])
            return CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(configure_openclaw, "run_command", fake_run)

    configure_openclaw.set_tools_allowlist("openclaw", add=True)
    first = list(state)
    configure_openclaw.set_tools_allowlist("openclaw", add=True)
    assert state == first
    assert state[0:2] == ["custom_tool", "powercontext_memory_get"]
    assert set(state[2:]) == set(configure_openclaw.POWERCONTEXT_TOOLS) - {"powercontext_memory_get"}

    configure_openclaw.set_tools_allowlist("openclaw", add=False)
    assert state == ["custom_tool"]


def test_enable_initializes_local_gateway_when_mode_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: int, check: bool = True) -> CompletedProcess[str]:
        del timeout, check
        commands.append(command)
        if command[1:4] == ["config", "get", "gateway.mode"]:
            return CompletedProcess(command, 1, "", "Config path not found")
        if command[1:4] == ["config", "get", "tools.alsoAllow"]:
            return CompletedProcess(command, 0, "[]", "")
        if command[1:3] == ["config", "set"] and command[3] == "tools.alsoAllow":
            return CompletedProcess(command, 0, "", "")
        if command[1:4] == ["config", "set", "--batch-json"]:
            settings = json.loads(command[4])
            assert settings[0] == {"path": "gateway.mode", "value": "local"}
            return CompletedProcess(command, 0, "", "")
        if command[1:3] == ["gateway", "restart"]:
            return CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(configure_openclaw, "run_command", fake_run)
    monkeypatch.setenv("OPENCLAW_RESTART", "1")

    configure_openclaw.enable("openclaw", "http://127.0.0.1:8765", "agent")

    assert commands[0][1:4] == ["config", "get", "gateway.mode"]


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@example.com",
        "http://127.0.0.1:8765?token=secret",
        "file:///tmp/powercontext",
    ],
)
def test_normalize_endpoint_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(configure_openclaw.ConfigurationError):
        configure_openclaw.normalize_endpoint(value)


def test_normalize_endpoint_strips_trailing_slashes() -> None:
    assert configure_openclaw.normalize_endpoint("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
