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

from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import RateLimitConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def test_shared_rate_limit_rejects_only_protected_requests(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
            rate_limit=RateLimitConfig(enabled=True, requests=1, window_seconds=60),
        )
    )

    with TestClient(app) as client:
        first = client.get("/v1/capabilities")
        rejected = client.get("/v1/capabilities")
        health = client.get("/health/ready")

    assert first.status_code == 200
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"]
    assert rejected.json()["error"]["code"] == "rate_limited"
    assert health.status_code == 200


def test_shared_rate_limit_skips_unauthenticated_receiver_routes(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
            auth=BearerAuthConfig(enabled=True, token=SecretStr("server-token")),
            rate_limit=RateLimitConfig(enabled=True, requests=1, window_seconds=60),
        )
    )
    paths = (
        "/v1/skill/remote/target/enroll",
        "/v1/skill/remote/reconcile",
        "/v1/skill/remote/package/download",
        "/v1/skill/remote/receipt",
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        responses = [client.post(path, json={}) for path in paths]

    assert {response.status_code for response in responses} == {422}
