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

"""Client transport consent, precedence, and endpoint binding."""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from powercontext.client import PowerContextClient
from powercontext.client.settings import ClientSettings


@pytest.fixture(autouse=True)
def isolated_client_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "clients.json"
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(path))
    for prefix in ("CLIENT", "LANGCHAIN", "LANGGRAPH", "PYDANTIC_AI", "BUB"):
        for suffix in ("SERVER_URL", "BASE_URL", "ALLOW_INSECURE_HTTP"):
            monkeypatch.delenv(f"POWERCONTEXT_{prefix}_{suffix}", raising=False)
    return path


def test_client_common_environment_allows_plaintext_but_explicit_false_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", "true")
    client = PowerContextClient("http://memory.example")
    asyncio.run(client.aclose())
    with pytest.raises(ValueError, match="non-loopback"):
        PowerContextClient("http://memory.example", allow_insecure_http=False)


def test_client_settings_accept_explicit_plaintext_consent() -> None:
    settings = ClientSettings(server_url="http://memory.example/", allow_insecure_http=True)
    assert settings.server_url == "http://memory.example"
    assert settings.allow_insecure_http is True


@pytest.mark.parametrize("value", ["perhaps", "", "2"])
def test_invalid_common_boolean_fails_even_on_https(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", value)
    with pytest.raises(ValueError, match="boolean"):
        PowerContextClient("https://memory.example")


def test_saved_consent_is_bound_to_the_selected_endpoint(
    isolated_client_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from powercontext.client.transport_policy import resolve_client_transport

    isolated_client_config.write_text(
        json.dumps({
            "version": 1,
            "hosts": {
                "langchain": {
                    "server_url": "http://memory.example/mcp/",
                    "allow_insecure_http": True,
                }
            },
        }),
        encoding="utf-8",
    )
    assert resolve_client_transport("langchain", server_url="http://memory.example/") == (
        "http://memory.example",
        True,
    )
    assert resolve_client_transport("langchain", server_url="http://other.example") == (
        "http://other.example",
        False,
    )
    monkeypatch.setenv("POWERCONTEXT_LANGCHAIN_BASE_URL", "http://other.example")
    assert resolve_client_transport("langchain") == ("http://other.example", False)


def test_host_false_overrides_common_true_and_explicit_constructor_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from powercontext.client.transport_policy import resolve_client_transport

    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("POWERCONTEXT_LANGGRAPH_ALLOW_INSECURE_HTTP", "false")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://common.example")
    monkeypatch.setenv("POWERCONTEXT_LANGGRAPH_BASE_URL", "http://graph.example")
    assert resolve_client_transport("langgraph") == ("http://graph.example", False)
    assert resolve_client_transport("langgraph", server_url="http://explicit.example", allow_insecure_http=True) == (
        "http://explicit.example",
        True,
    )


@pytest.mark.parametrize("host", ["dsh", "pi", "opencode", "openclaw"])
def test_typescript_host_endpoint_alias_precedes_common_url(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from powercontext.client.transport_policy import resolve_client_transport

    prefix = "POWERCONTEXT_" + host.upper()
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://common.example")
    monkeypatch.setenv(prefix + "_ENDPOINT", "https://host.example")
    assert resolve_client_transport(host) == ("https://host.example", False)
    monkeypatch.setenv(prefix + "_SERVER_URL", "https://server.example")
    assert resolve_client_transport(host) == ("https://server.example", False)
    monkeypatch.setenv(prefix + "_BASE_URL", "https://base.example")
    assert resolve_client_transport(host) == ("https://base.example", False)


@pytest.mark.parametrize("allow", ["true", 1, None])
def test_saved_consent_must_be_a_json_boolean(isolated_client_config: Path, allow: object) -> None:
    from powercontext.client.transport_policy import resolve_client_transport

    isolated_client_config.write_text(
        json.dumps({
            "version": 1,
            "hosts": {
                "client": {
                    "server_url": "http://memory.example",
                    "allow_insecure_http": allow,
                }
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="boolean"):
        resolve_client_transport("client")


def test_saved_client_url_and_consent_work_without_environment(isolated_client_config: Path) -> None:
    isolated_client_config.write_text(
        json.dumps({
            "version": 1,
            "hosts": {
                "client": {
                    "server_url": "http://memory.example",
                    "allow_insecure_http": True,
                }
            },
        }),
        encoding="utf-8",
    )
    settings = ClientSettings()
    assert (settings.server_url, settings.allow_insecure_http) == ("http://memory.example", True)
    with pytest.raises(ValidationError):
        ClientSettings(server_url="http://other.example")


def test_https_certificate_validation_stays_enabled_with_http_consent() -> None:
    async def scenario() -> None:
        async def reject_certificate(request: httpx.Request) -> httpx.Response:
            message = "certificate verify failed"
            raise httpx.ConnectError(message, request=request)

        from powercontext.client import TransportError

        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(reject_certificate)) as http_client,
            PowerContextClient(
                "https://memory.example",
                allow_insecure_http=True,
                http_client=http_client,
            ) as client,
        ):
            with pytest.raises(TransportError):
                await client.get_liveness()

    asyncio.run(scenario())


@pytest.mark.parametrize("host", ["langchain", "langgraph", "pydantic-ai"])
def test_framework_settings_resolve_common_and_host_http_consent(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    classes = {
        "langchain": ("powercontext_langchain.settings", "PowerContextLangChainSettings"),
        "langgraph": ("powercontext_langgraph.settings", "PowerContextLangGraphSettings"),
        "pydantic-ai": ("powercontext_pydantic_ai.settings", "PowerContextSettings"),
    }
    module, name = classes[host]
    settings_class = getattr(importlib.import_module(module), name)
    prefix = "POWERCONTEXT_" + host.upper().replace("-", "_")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://memory.example")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", "true")
    settings = settings_class()
    assert settings.base_url == "http://memory.example"
    assert settings.allow_insecure_http is True
    monkeypatch.setenv(prefix + "_ALLOW_INSECURE_HTTP", "false")
    assert settings_class().allow_insecure_http is False
    assert settings_class(allow_insecure_http=True).allow_insecure_http is True


@pytest.mark.parametrize("host", ["langchain", "langgraph"])
def test_invocation_url_does_not_inherit_saved_http_consent(host: str, isolated_client_config: Path) -> None:
    module = importlib.import_module("powercontext_" + host)
    client_module = importlib.import_module("powercontext_" + host + ".client")
    settings_class = getattr(
        module, "PowerContextLangChainSettings" if host == "langchain" else "PowerContextLangGraphSettings"
    )
    isolated_client_config.write_text(
        json.dumps({
            "version": 1,
            "hosts": {
                host: {
                    "server_url": "http://memory.example",
                    "allow_insecure_http": True,
                }
            },
        }),
        encoding="utf-8",
    )
    settings = settings_class()
    original = client_module.resolve_config(settings=settings)
    assert (original.base_url, original.allow_insecure_http) == ("http://memory.example", True)
    client = client_module.open_client(original)
    asyncio.run(client.aclose())
    changed = client_module.resolve_config(module.PowerContextScope(base_url="http://other.example"), settings=settings)
    with pytest.raises(ValueError, match="non-loopback"):
        client_module.open_client(changed)
    explicit = client_module.resolve_config(
        module.PowerContextScope(base_url="http://other.example", allow_insecure_http=True),
        settings=settings,
    )
    client = client_module.open_client(explicit)
    asyncio.run(client.aclose())


@pytest.mark.parametrize("host", ["langchain", "langgraph"])
def test_framework_shared_transport_applies_explicit_http_opt_in(host: str) -> None:
    module = importlib.import_module("powercontext_" + host)
    client_module = importlib.import_module("powercontext_" + host + ".client")
    config = client_module.resolve_config(
        module.PowerContextScope(base_url="http://memory.example", allow_insecure_http=True)
    )

    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "ok"}))
        async with httpx.AsyncClient(transport=transport) as http_client:
            with client_module.shared_http_client(http_client):
                async with client_module.open_client(config) as client:
                    assert (await client.get_liveness()).status == "ok"

    asyncio.run(scenario())


def test_pydantic_ai_does_not_transfer_saved_consent_when_copying_settings(
    isolated_client_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import powercontext_pydantic_ai.toolset as toolset_module
    from powercontext_pydantic_ai import PowerContext, PowerContextSettings
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from tests.pydantic_ai_adapter.fakes import RecordingClient

    isolated_client_config.write_text(
        json.dumps({
            "version": 1,
            "hosts": {
                "pydantic-ai": {
                    "server_url": "http://memory.example",
                    "allow_insecure_http": True,
                }
            },
        }),
        encoding="utf-8",
    )
    settings = PowerContextSettings().model_copy(update={"base_url": "http://other.example"})
    RecordingClient.reset()
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)

    async def respond(_messages, _info):
        return ModelResponse(parts=[TextPart("complete")])

    agent = Agent(FunctionModel(respond), capabilities=[PowerContext(settings=settings)])
    asyncio.run(agent.run("use the configured Server"))
    assert RecordingClient.instances[0].base_url == "http://other.example"
    assert RecordingClient.instances[0].allow_insecure_http is False
