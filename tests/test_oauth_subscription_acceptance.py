# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from tests.e2e import oauth_subscription_acceptance as acceptance
from tests.e2e.oauth_subscription_acceptance import core_environment, create_scope, gateway_profiles


@pytest.mark.parametrize("channel", ["codex", "copilot"])
@pytest.mark.parametrize("via_gateway", [False, True])
def test_subscription_gateway_routes_match_core_approved_targets(
    tmp_path: Path, channel: str, via_gateway: bool
) -> None:
    env = core_environment(tmp_path, channel, "core-token", "gateway-token", 8787, via_gateway)
    targets = json.loads(env["POWERCONTEXT_SERVER_INFERENCE"])["protection"]["targets"]
    profiles = gateway_profiles(channel)
    expected = {
        "codex": {
            "PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES": (
                "https://chatgpt.com/backend-api/codex/responses",
                "openai-responses",
                "gpt-6-luna",
            ),
        },
        "copilot": {
            "PC_GATEWAY_UPSTREAM_PROFILE": (
                "https://api.githubcopilot.com/chat/completions",
                "openai-chat",
                "claude-haiku-5.5",
            ),
            "PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES": (
                "https://api.githubcopilot.com/responses",
                "openai-responses",
                "gpt-6-luna",
            ),
        },
    }[channel]
    assert set(profiles) == set(expected)
    assert len(set(profiles.values())) == len(profiles)
    assert set(targets) == set(profiles.values())
    for key, (endpoint, protocol, model) in expected.items():
        assert targets[profiles[key]] == {
            "endpoint": endpoint,
            "model": model,
            "protocol": protocol,
            "allow_streaming": True,
        }


@pytest.mark.anyio
async def test_live_scope_grants_gateway_invocation() -> None:
    grants = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/scopes":
            return httpx.Response(201, json={"scope_id": "scope-1"})
        grants.append(request.read().decode())
        return httpx.Response(201, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://localhost") as client:
        assert await create_scope(client) == "scope-1"
    assert [json.loads(grant)["role"] for grant in grants] == ["scope.contributor", "gateway.user"]
    assert all(json.loads(grant)["resource"]["scope_id"] == "scope-1" for grant in grants)


@pytest.mark.anyio
async def test_copilot_routes_only_requests_match_approved_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = "copilot"
    targets = {}

    @contextmanager
    def process(command, env, directory, name):
        if name == "core-routes":
            targets.update(json.loads(env["POWERCONTEXT_SERVER_INFERENCE"])["protection"]["targets"])
        yield None

    async def wait_ready(*args):
        pass

    async def create_scope(client):
        return "scope-1"

    async def check_route(sdk, route, model, stream):
        profile = gateway_profiles(channel)[acceptance.PROFILE_ENV[route]]
        assert model == targets[profile]["model"]
        return {"status": "passed", "model": model}

    monkeypatch.setattr(acceptance, "process", process)
    monkeypatch.setattr(acceptance, "wait_ready", wait_ready)
    monkeypatch.setattr(acceptance, "create_scope", create_scope)
    monkeypatch.setattr(acceptance, "check_route", check_route)
    result = {}
    await acceptance.exercise_routes(tmp_path, channel, tmp_path / "credentials.json", "unused", result)

    assert result["routes"] == {
        f"{route}:{wire_format}": {"status": "passed", "model": model}
        for route, model in [("chat/completions", "claude-haiku-5.5"), ("responses", "gpt-6-luna")]
        for wire_format in ("json", "sse")
    }
