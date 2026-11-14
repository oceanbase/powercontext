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

"""Capabilities list only the plugin families this Server loaded."""

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.plugins.families import register_plugin_family
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_families_registered_elsewhere_in_the_process_are_not_capabilities(tmp_path) -> None:
    # Another plugin manager or a plugin test in the same process registered this family.
    register_plugin_family("stray-notes")
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:
        response = client.get("/v1/capabilities")

    assert response.status_code == 200, response.text
    assert "stray-notes" not in response.json()["artifact_families"]
