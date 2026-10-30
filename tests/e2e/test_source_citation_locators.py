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

"""Source citations survive real Server startup, persistence and both transports."""

import json
import sqlite3

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings


def _handoff(refs):
    return {
        "family": "handoff",
        "content": {
            "schema": "powercontext.handoff.v1",
            "objective": "核实项目约束",
            "state": [
                {"text": "项目使用 Python。", "citations": [{"kind": "source", "source_ref": ref} for ref in refs]}
            ],
            "disposition": "complete",
            "next_action": None,
            "omissions": [],
        },
    }


def _settings(database):
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        auth=BearerAuthConfig(enabled=False),
    )


def _app(database):
    return create_server_app(settings=_settings(database))


def test_locators_survive_upgrade_reopen_http_and_mcp(tmp_path):
    database = tmp_path / "locators.db"
    with TestClient(_app(database)) as client:
        scope_response = client.post("/v1/scope-bindings/resolve", json={"binding_keys": []})
        assert scope_response.is_success, scope_response.text
        scope = scope_response.json()["scope_id"]
        response = client.post(f"/v1/scopes/{scope}/sources", json={"content": "项目使用 Python。"})
        assert response.is_success, response.text
        source = response.json()
        bare = {"name": "content", "source_id": source["source_id"]}
        legacy = client.post(f"/v1/scopes/{scope}/artifacts", json=_handoff([bare]))
        assert legacy.is_success, legacy.text
        legacy_id = legacy.json()["artifact_id"]

    # Reconstruct the previous registered layout, preserving its real historical evidence.
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE pc_artifact_source_locators")
        connection.execute("UPDATE pc_schema_revision SET version_num = 'v1_4_1_005'")

    runner = deployment_runner(_settings(database))
    plan = runner.plan()
    assert plan.revisions == ("v1_4_1_006",)
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)

    locators = [
        {"kind": "sequence", "sequence": source["position"]},
        {"kind": "line_range", "start_line": 40, "end_line": 55, "heading_path": "设计 / 约束"},
        {"kind": "line_range", "start_line": 60, "end_line": 65},
    ]
    references = [bare | {"locator": locator} for locator in locators] + [bare]
    expected = [
        {
            "source_type": "content",
            "source_id": bare["source_id"],
            **({"locator": ref["locator"]} if "locator" in ref else {}),
        }
        for ref in references
    ]
    with TestClient(_app(database)) as client:
        old = client.get(f"/v1/scopes/{scope}/artifacts/handoff/{legacy_id}")
        assert old.is_success, old.text
        assert [ref for ref in old.json()["sources"] if ref["source_id"] == bare["source_id"]] == [expected[-1]]
        created = client.put(
            f"/v1/scopes/{scope}/artifacts/handoff/{legacy_id}",
            json={"content": _handoff(references)["content"]},
            headers={"If-Match": '"revision:1"'},
        )
        assert created.is_success, created.text
        assert [ref for ref in created.json()["sources"] if ref["source_id"] == bare["source_id"]] == expected
        artifact_id = created.json()["artifact_id"]
        invalid = client.post(
            f"/v1/scopes/{scope}/artifacts",
            json=_handoff([bare | {"locator": {"kind": "line_range", "start_line": 1, "end_line": 201}}]),
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_request"
        wrong_position = client.put(
            f"/v1/scopes/{scope}/artifacts/handoff/{legacy_id}",
            json={
                "content": _handoff([
                    bare | {"locator": {"kind": "sequence", "sequence": source["position"] + 100}},
                ])["content"]
            },
            headers={"If-Match": '"revision:2"'},
        )
        assert wrong_position.status_code == 422, wrong_position.text
        assert wrong_position.json()["error"]["code"] == "invalid_request"

    with TestClient(_app(database)) as client:
        path = f"/v1/scopes/{scope}/artifacts/handoff/{artifact_id}"
        read = client.get(path)
        assert read.is_success, read.text
        assert [ref for ref in read.json()["sources"] if ref["source_id"] == bare["source_id"]] == expected
        assert read.json()["content"]["state"][0]["citations"] == [
            {"kind": "source", "source_ref": ref} for ref in expected
        ]
        result = client.post(
            "/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_artifact",
                    "arguments": {
                        "scope_id": scope,
                        "family": "handoff",
                        "artifact_id": artifact_id,
                    },
                },
            },
        )
        assert result.is_success, result.text
        message = next(
            json.loads(line.removeprefix("data:")) for line in result.text.splitlines() if line.startswith("data:")
        )
        assert "error" not in message, message
        payload = message["result"]
        assert not payload.get("isError"), payload
        assert payload["structuredContent"]["artifact"] == read.json()
        historical = client.get(path + "/revisions/1")
        assert historical.is_success, historical.text
        assert historical.json()["content"] == old.json()["content"]
        assert historical.json()["sources"] == old.json()["sources"]
