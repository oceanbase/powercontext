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

from __future__ import annotations

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import RateLimitConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def _settings(tmp_path, *, enabled: bool):
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
        rate_limit=RateLimitConfig(enabled=enabled, requests=1, window_seconds=60),
        mcp=McpConfig(enabled=False),
    )


def test_enabled_rate_limit_charges_a_principal_budget(tmp_path) -> None:
    app = create_server_app(settings=_settings(tmp_path, enabled=True))
    with TestClient(app) as client:
        first = client.get("/v1/operations")
        second = client.get("/v1/operations")
        probe = client.get("/health/live")

    assert first.status_code != 429
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert int(second.headers["retry-after"]) > 0
    # Liveness probes never consume the caller's window.
    assert probe.status_code == 200


def test_disabled_rate_limit_leaves_requests_unlimited(tmp_path) -> None:
    app = create_server_app(settings=_settings(tmp_path, enabled=False))
    with TestClient(app) as client:
        statuses = {client.get("/v1/operations").status_code for _ in range(3)}

    assert 429 not in statuses
