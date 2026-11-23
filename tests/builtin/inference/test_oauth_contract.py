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

"""Execute the shared wire/storage contract against real auth and temporary files."""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import httpx2
import pytest
from filelock import AsyncFileLock
from pydantic_ai.exceptions import UserError

from powercontext.builtin.inference import grok
from powercontext.builtin.inference.codex import CodexCliAuth

_CONTRACT = json.loads((Path(__file__).parents[2] / "fixtures/oauth/contract.json").read_text())
assert _CONTRACT["schema_version"] == 1
_UPSTREAM = json.loads((Path(__file__).parents[2] / "fixtures/oauth/upstream/republic-grok.json").read_text())
assert _UPSTREAM["schema_version"] == 1
_SCENARIOS = [*_CONTRACT["scenarios"], *_UPSTREAM["scenarios"]]


def _replace(path: Path, credentials: dict[str, Any] | None) -> bytes:
    replacement = path.with_suffix(".new")
    replacement.write_text(json.dumps(credentials) if credentials is not None else "synthetic-secret-error")
    replacement.replace(path)
    return path.read_bytes()


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda scenario: scenario["id"])
def test_shared_oauth_contract(  # noqa: C901 - interpret shared actions and verify wire/storage outcomes
    scenario: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _CONTRACT["profiles"][scenario["profile"]]
    path = tmp_path / "auth.json"
    current = _replace(path, scenario["credentials"])
    resource_tokens = []
    refresh_forms = []
    token_responses = iter(scenario["http"]["token_responses"])

    async def run() -> None:  # noqa: C901 - independent contract actions share one client lifetime
        nonlocal current
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def token_response(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == profile["refresh_url"]
            assert request.method == "POST"
            assert "authorization" not in request.headers
            assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
            refresh_forms.append(dict(parse_qsl(request.content.decode())))
            response = next(token_responses)
            if response.get("wait_for_cancel"):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            return httpx.Response(
                response["status"],
                **{key: response[key] for key in ("headers", "json", "text") if key in response},
            )

        monkeypatch.setattr(
            grok,
            "AsyncOAuth2Client",
            partial(grok.AsyncOAuth2Client, transport=httpx.MockTransport(token_response)),
        )

        def resource_response(request: httpx2.Request) -> httpx2.Response:
            assert str(request.url) == profile["resource_url"]
            resource_tokens.append(request.headers["authorization"].removeprefix("Bearer "))
            if scenario["profile"] == "codex-cli":
                assert request.headers["chatgpt-account-id"] == json.loads(path.read_text())["tokens"]["account_id"]
            response = scenario["http"]["resource_response"]
            return httpx2.Response(response["status"], json=response["json"])

        def auth() -> httpx2.Auth:
            if scenario["profile"] == "codex-cli":
                return CodexCliAuth(path)
            return grok.GrokCliAuth(path, clock=lambda: scenario["now"])

        async def send(client: httpx2.AsyncClient, action: dict[str, Any]) -> None:
            url = action.get("url", profile["resource_url"])
            if action["outcome"] == "auth_error":
                with pytest.raises(UserError) as caught:
                    await client.post(url, json={"input": "synthetic"}, auth=auth())
                error_chain = "".join(traceback.format_exception(caught.value))
                for marker in ("synthetic-secret-error", *scenario.get("secret_markers", [])):
                    assert marker not in error_chain
                assert str(path) not in error_chain
            else:
                response = await client.post(url, json={"input": "synthetic"}, auth=auth())
                if action["outcome"] == "http_error":
                    assert response.status_code == action["status"]
                else:
                    assert action["outcome"] == "success"
                    assert response.status_code == 200

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(resource_response), follow_redirects=False
        ) as client:
            for action in scenario["actions"]:
                if action["type"] == "request":
                    await send(client, action)
                elif action["type"] == "replace_credentials":
                    current = _replace(path, action["credentials"])
                elif action["type"] == "concurrent_requests":
                    await asyncio.gather(*(send(client, action) for _ in range(action["count"])))
                elif action["type"] == "cancel_during_refresh":
                    assert action["outcome"] == "cancelled"
                    task = asyncio.create_task(client.post(profile["resource_url"], auth=auth()))
                    try:
                        async with asyncio.timeout(5):
                            await started.wait()
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                        assert cancelled.is_set()
                        # A different auth instance/process must be able to use the lock again.
                        async with AsyncFileLock(path.with_name("auth.json.lock"), timeout=0, run_in_executor=False):
                            assert path.read_bytes() == current
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                else:
                    pytest.fail(f"Unknown OAuth contract action: {action['type']}")

    asyncio.run(run())
    assert resource_tokens == scenario["expect"]["resource_tokens"]
    assert refresh_forms == scenario["expect"]["refresh_forms"]
    assert next(token_responses, None) is None
    store = scenario["expect"]["store"]
    if store["mode"] == "unchanged":
        assert path.read_bytes() == current
    else:
        assert store["mode"] == "updated"
        saved = json.loads(path.read_text())
        initial = json.loads(current)
        scope = profile["credential_scope"]
        entry = saved[scope]
        assert entry["key"] == store["access_token"]
        assert entry["refresh_token"] == store["refresh_token"]
        assert datetime.fromisoformat(entry["expires_at"]).timestamp() == store["expires_at"]
        for key, value in initial[scope].items():
            if key not in {"key", "refresh_token", "expires_at"}:
                assert entry[key] == value
        assert {key: value for key, value in saved.items() if key != scope} == {
            key: value for key, value in initial.items() if key != scope
        }
        if os.name == "posix":
            assert path.stat().st_mode & 0o777 == 0o600
