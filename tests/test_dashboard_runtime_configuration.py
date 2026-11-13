# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Display loaded safe settings without reloading deployment files or exposing secrets."""

import re
from secrets import token_urlsafe

from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    ServerSettings,
)
from tests.test_dashboard import dashboard as dashboard


def test_runtime_page_reports_loaded_settings_and_ignores_later_environment_changes(tmp_path, monkeypatch):
    token = token_urlsafe(24)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/runtime.db"),
            mcp=McpConfig(enabled=False),
            dashboard=DashboardConfig(enabled=True),
            access=AccessControlConfig(mode="enforced"),
            auth=BearerAuthConfig(token=SecretStr(token)),
            runtime=RuntimeConfig(
                artifact_processing_role="all",
                dream_enabled=False,
                profile_schedule_enabled=False,
                profile_cron="15 3 * * *",
                profile_timezone="UTC",
                generation_concurrency=2,
                artifact_processing_max_workers=7,
                artifact_processing_worker_timeout_seconds=150,
            ),
        ),
        scheduler_path=tmp_path / "scheduler.db",
    )
    with TestClient(app) as client:
        monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_PROFILE_CRON", "0 9 * * *")
        client.headers["Authorization"] = f"Bearer {token}"
        page = client.get("/dashboard/runtime", params={"lang": "en"})
        assert page.status_code == 200, page.text
        config = page.text.split('aria-labelledby="runtime-configuration-heading"', 1)[1]
        assert "15 3 * * *" in config and "Daily at 03:15" in config and "0 9 * * *" not in config
        assert "API and background processing" in config
        html_match = re.search(r'<tr data-runtime-config="dream">(.*?)</tr>', config, re.S)
        assert html_match is not None
        dream = html_match[1]
        assert "Disabled" in dream and "On demand" in dream
        assert "Generation concurrency limit" in config and "<dd>2</dd>" in config
        assert "<dd>7</dd>" in config and "150 seconds" in config
        assert token not in page.text and str(tmp_path) not in page.text
        client.headers.pop("Authorization")
        denied = client.get("/dashboard/runtime")
        assert denied.status_code == 401 and "runtime-configuration-heading" not in denied.text


def test_runtime_without_loaded_configuration_does_not_invent_defaults(dashboard, monkeypatch):
    monkeypatch.delattr(dashboard.app.state, "dashboard_runtime_configuration")
    page = dashboard.get("/dashboard/runtime", params={"lang": "en"})
    assert page.status_code == 200
    assert "This service instance did not provide runtime configuration." in page.text
    assert 'data-runtime-config="dream"' not in page.text
    assert "Process is responding" in page.text


def test_runtime_page_formats_configured_intervals_without_claiming_live_execution(dashboard, monkeypatch):
    from powercontext.builtin.runtime.config import BuiltinConfig
    from powercontext.server.dashboard.runtime import runtime_configuration

    settings = BuiltinConfig(
        runtime=RuntimeConfig(
            memory_schedule_seconds=60,
            topic_memory_schedule_seconds=300,
            experience_schedule_seconds=900,
            profile_schedule_enabled=True,
            profile_cron="0 2 * * *",
            profile_timezone="Asia/Shanghai",
        )
    )
    monkeypatch.setattr(dashboard.app.state, "dashboard_runtime_configuration", runtime_configuration(settings))
    page = dashboard.get("/dashboard/runtime", params={"lang": "en"})
    assert page.status_code == 200
    for family, interval in [
        ("memory", "Every 1 minute"),
        ("topic-memory", "Every 5 minutes"),
        ("experience", "Every 15 minutes"),
    ]:
        html_match = re.search(r'<tr data-runtime-config="' + family + r'">(.*?)</tr>', page.text, re.S)
        assert html_match is not None
        row = html_match[1]
        assert "Enabled" in row and interval in row
    assert "Daily at 02:00" in page.text and "Asia/Shanghai" in page.text
    assert "An enabled setting does not mean a task is running." in page.text
