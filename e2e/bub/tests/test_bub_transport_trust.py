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

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from powercontext_bub import plugin as plugin_module
from powercontext_bub import tools as tools_module
from powercontext_bub.plugin import STATE_KEY, PowerContextPlugin, PowerContextSettings


def _plugin_with(settings: PowerContextSettings, monkeypatch, tmp_path: Path) -> PowerContextPlugin:
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    return PowerContextPlugin(SimpleNamespace(workspace=tmp_path))


def _tool_settings(plugin: PowerContextPlugin) -> tools_module.ToolSettings:
    return plugin.load_state(message=None, session_id="session-1")[STATE_KEY]


def test_plugin_refuses_a_plaintext_non_loopback_server_by_default(monkeypatch, tmp_path: Path) -> None:
    settings = PowerContextSettings(base_url="http://host-gateway:8000", scope_id="test:scope")
    plugin = _plugin_with(settings, monkeypatch, tmp_path)

    async def prepare() -> None:
        async with plugin._client():
            pass

    with pytest.raises(ValueError, match="non-loopback"):
        asyncio.run(prepare())


class RecordingClient:
    constructions: ClassVar[list[dict[str, Any]]] = []

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        trust_transport_security: bool = False,
        timeout: float | None = None,
    ) -> None:
        RecordingClient.constructions.append({
            "base_url": base_url,
            "http_client": http_client,
            "trust_transport_security": trust_transport_security,
            "timeout": timeout,
        })

    async def __aenter__(self) -> RecordingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info


@pytest.fixture
def constructions() -> Any:
    RecordingClient.constructions.clear()
    return RecordingClient.constructions


def test_trusting_the_transport_supplies_the_client_an_explicit_vouched_transport(
    monkeypatch,
    tmp_path: Path,
    constructions: list[dict[str, Any]],
) -> None:
    settings = PowerContextSettings(
        base_url="http://host-gateway:8000",
        scope_id="test:scope",
        trust_transport_security=True,
    )
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    plugin = _plugin_with(settings, monkeypatch, tmp_path)

    async def open_client() -> None:
        async with plugin._client():
            pass

    asyncio.run(open_client())

    assert len(constructions) == 1
    construction = constructions[0]
    assert construction["base_url"] == "http://host-gateway:8000"
    assert isinstance(construction["http_client"], httpx.AsyncClient)
    assert construction["trust_transport_security"] is True


def test_load_state_carries_the_transport_vouch_to_tool_settings(monkeypatch, tmp_path: Path) -> None:
    refusing = _plugin_with(
        PowerContextSettings(base_url="http://host-gateway:8000", scope_id="test:scope"),
        monkeypatch,
        tmp_path,
    )
    assert _tool_settings(refusing)["trust_transport_security"] is False

    vouched = _plugin_with(
        PowerContextSettings(
            base_url="http://host-gateway:8000",
            scope_id="test:scope",
            trust_transport_security=True,
        ),
        monkeypatch,
        tmp_path,
    )
    assert _tool_settings(vouched)["trust_transport_security"] is True


def test_tools_refuse_a_plaintext_non_loopback_server_by_default(monkeypatch, tmp_path: Path) -> None:
    settings = PowerContextSettings(base_url="http://host-gateway:8000", scope_id="test:scope")
    tool_settings = _tool_settings(_plugin_with(settings, monkeypatch, tmp_path))

    async def open_tool_client() -> None:
        async with tools_module._client(tool_settings):
            pass

    with pytest.raises(ValueError, match="non-loopback"):
        asyncio.run(open_tool_client())


def test_tools_honour_the_operator_transport_vouch(
    monkeypatch,
    tmp_path: Path,
    constructions: list[dict[str, Any]],
) -> None:
    settings = PowerContextSettings(
        base_url="http://host-gateway:8000",
        scope_id="test:scope",
        trust_transport_security=True,
    )
    tool_settings = _tool_settings(_plugin_with(settings, monkeypatch, tmp_path))
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)

    async def open_tool_client() -> None:
        async with tools_module._client(tool_settings):
            pass

    asyncio.run(open_tool_client())

    assert len(constructions) == 1
    construction = constructions[0]
    assert construction["base_url"] == "http://host-gateway:8000"
    assert isinstance(construction["http_client"], httpx.AsyncClient)
    assert construction["trust_transport_security"] is True
