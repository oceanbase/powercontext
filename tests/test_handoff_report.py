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

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import HandoffReportConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_handoff_report_uses_common_scope_selection(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'report.db'}"),
            handoff_report=HandoffReportConfig(enabled=True),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        default_scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        root_scope_id = _create_scope(client, title="Feature", key="feature")
        child_scope_id = _create_scope(client, title="Agent validation", key="validation", parent=root_scope_id)
        other_scope_id = _create_scope(client, title="Incident", key="incident")
        root_handoff = _commit_handoff(client, root_scope_id, disposition="continuable")
        other_handoff = _commit_handoff(client, other_scope_id, disposition="blocked")

        subtree = client.post(
            "/v1/handoff-reports/get",
            json={"selection": {"mode": "subtree", "root_scope_id": root_scope_id}, "format": "json"},
        )
        exact = client.post(
            "/v1/handoff-reports/get",
            json={"selection": {"mode": "exact", "scope_ids": [child_scope_id]}, "format": "json"},
        )
        all_scopes = client.post(
            "/v1/handoff-reports/get",
            json={"selection": {"mode": "all"}, "format": "json"},
        )
    assert subtree.status_code == 200
    subtree_report = subtree.json()["report"]
    assert subtree_report["selection"] == {"mode": "subtree", "root_scope_id": root_scope_id}
    assert subtree_report["scope_ids"] == [root_scope_id, child_scope_id]
    assert [entry["status"] for entry in subtree_report["scopes"]] == ["continuable", "no_handoff"]
    assert subtree_report["scopes"][0]["handoff"] == {
        "scope_id": root_scope_id,
        "artifact": root_handoff,
    }
    assert subtree_report["scopes"][1]["scope"]["parent_scope_id"] == root_scope_id
    assert subtree_report["summary"] == {
        "continuable_count": 1,
        "blocked_count": 0,
        "complete_count": 0,
        "no_handoff_count": 1,
    }

    assert exact.status_code == 200
    assert exact.json()["report"]["scope_ids"] == [child_scope_id]
    assert exact.json()["report"]["scopes"][0]["status"] == "no_handoff"

    assert all_scopes.status_code == 200
    all_report = all_scopes.json()["report"]
    assert set(all_report["scope_ids"]) == {default_scope_id, root_scope_id, child_scope_id, other_scope_id}
    assert all_report["summary"] == {
        "continuable_count": 1,
        "blocked_count": 1,
        "complete_count": 0,
        "no_handoff_count": 2,
    }
    other_entry = next(entry for entry in all_report["scopes"] if entry["scope"]["scope_id"] == other_scope_id)
    assert other_entry["handoff"]["artifact"] == other_handoff


def test_handoff_report_markdown_preserves_scope_organization_and_exact_addresses(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'markdown.db'}"),
            handoff_report=HandoffReportConfig(enabled=True),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        scope_id = _create_scope(client, title="Release review", key="release")
        handoff = _commit_handoff(client, scope_id, disposition="complete")
        response = client.post(
            "/v1/handoff-reports/get",
            json={"selection": {"mode": "exact", "scope_ids": [scope_id]}, "format": "markdown"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "# Handoff Report" in response.text
    assert "## Release review" in response.text
    assert f"{scope_id}/handoff/{handoff['artifact_id']}@{handoff['revision']}" in response.text


def _create_scope(client: TestClient, *, title: str, key: str, parent: str | None = None) -> str:
    response = client.post(
        "/v1/scopes",
        json={
            "title": title,
            "summary": f"{title} context",
            "parent_scope_id": parent,
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()["scope_id"]


def _commit_handoff(client: TestClient, scope_id: str, *, disposition: str) -> dict[str, object]:
    source = client.post(
        "/v1/sources/content",
        json={"scope_id": scope_id, "source_id": "handoff-state", "content": f"State is {disposition}."},
    )
    assert source.status_code == 202
    citation = {"kind": "source", "source_ref": source.json()["source"]}
    prepared = client.post(
        "/v1/handoff/finalize",
        json={
            "scope_id": scope_id,
            "draft": {
                "objective": f"Finish {scope_id}.",
                "state": [{"text": f"Current state is {disposition}.", "citations": [citation]}],
                "disposition": disposition,
                "next_action": {"text": "Verify the exact result.", "citations": [citation]},
                "omissions": [],
            },
        },
    )
    assert prepared.status_code == 200
    committed = client.post(
        "/v1/handoff/commit",
        json={"scope_id": scope_id, "handoff": prepared.json()},
    )
    assert committed.status_code == 200
    return committed.json()["reference"]
