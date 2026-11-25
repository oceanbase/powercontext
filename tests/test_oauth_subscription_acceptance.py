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
from pathlib import Path

import httpx
import pytest

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
            ),
        },
        "copilot": {
            "PC_GATEWAY_UPSTREAM_PROFILE": ("https://api.githubcopilot.com/chat/completions", "openai-chat"),
            "PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES": ("https://api.githubcopilot.com/responses", "openai-responses"),
        },
    }[channel]
    assert set(profiles) == set(expected)
    assert len(set(profiles.values())) == len(profiles)
    assert set(targets) == set(profiles.values())
    for key, (endpoint, protocol) in expected.items():
        assert targets[profiles[key]] == {
            "endpoint": endpoint,
            "model": "gpt-6-luna" if channel == "codex" else "claude-haiku-5.5",
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
