# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Read-only recall details and list-to-detail navigation."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import event, update

from powercontext.builtin.persistence.context_prepare_diagnostics import ContextPrepareDiagnosticsRepository
from powercontext.builtin.persistence.tables import CONTEXT_PREPARE_DIAGNOSTICS_TABLE
from powercontext.server.dashboard.api import DashboardAPI
from powercontext.server.dashboard.errors import ReadError
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


@pytest.fixture(autouse=True)
def capture_enabled(dashboard):
    dashboard.app.state.context_diagnostics_enabled = True


def prepare(client, scope, query="budget review", *, max_bytes=8000):
    response = client.post(
        "/v1/context/prepare",
        json={
            "scope_id": scope,
            "query": query,
            "include_receipt": True,
            "max_bytes": max_bytes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def latest_record_id(client, scope):
    async def read():
        async with client.app.state.application.primary_database.transaction() as connection:
            rows, _ = await ContextPrepareDiagnosticsRepository().list_page(connection, scope)
            return rows[0]["record_id"]

    return client.portal.call(read)


def detail_page(client, scope, record_id, **params):
    return client.get(
        "/dashboard/context-diagnostic-detail",
        params={
            "scope": scope,
            "diagnostic": record_id,
            "lang": "en",
            **params,
        },
    )


@pytest.mark.parametrize("language", ["en", "zh"])
def test_history_opens_separate_detail_and_returns_to_same_filters(dashboard, monkeypatch, language):
    scope = create_scope(dashboard, "Recall navigation")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember",
        json={
            "scope_id": scope,
            "kind": "fact",
            "text": "The budget review is Thursday.",
        },
    )
    assert saved.status_code == 200
    prepared = prepare(dashboard, scope)
    ref = prepared["receipt"]["selected"][0]["artifact_address"]["artifact"]
    artifact = f"/v1/scopes/{scope}/artifacts/atomic-memory/{ref['artifact_id']}"
    current = dashboard.get(artifact)
    assert (
        dashboard.put(
            artifact,
            json={"content": {"kind": "fact", "text": "The budget review moved to Friday."}},
            headers={"If-Match": current.headers["etag"]},
        ).status_code
        == 200
    )
    original = DashboardAPI.send

    async def no_replay(self, method, path, *args, **kwargs):
        assert path != "/v1/context/prepare"
        return await original(self, method, path, *args, **kwargs)

    monkeypatch.setattr(DashboardAPI, "send", no_replay)
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "pc_context_prepare_diagnostics" in statement:
            queries.append(statement)

    engine = dashboard.app.state.application.primary_database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = dashboard.get(
            "/dashboard/context-diagnostics",
            params={
                "scope": scope,
                "lang": language,
                "diagnostic_status": "ready",
                "diagnostic_since": "2020-01-01",
            },
        )
        assert page.status_code == 200, page.text
        assert 'name="response"' not in page.text and 'id="context-result"' not in page.text
        assert "导入准备响应" not in page.text and "Import a prepared response" not in page.text
        assert "The budget review is Thursday." not in page.text
        assert all("prepared_content" not in q and "receipt_payload" not in q for q in queries)
        link = re.search(r'href="([^"]*context-diagnostic-detail[^"]*)"', page.text)
        assert link is not None
        detail = dashboard.get(unescape(link[1]))
        assert detail.status_code == 200, detail.text
        assert "The budget review is Thursday." in detail.text
        assert "The budget review moved to Friday." not in detail.text
        assert 'aria-labelledby="context-history-heading"' not in detail.text
        assert f"artifact={ref['artifact_id']}&amp;revision={ref['revision']}" in detail.text
        assert detail.headers["Cache-Control"] == "no-store"
        assert "返回召回记录" not in detail.text and "Back to recall history" not in detail.text
        params = parse_qs(urlsplit(unescape(link[1])).query)
        assert params["diagnostic_status"] == ["ready"] and params["diagnostic_since"] == ["2020-01-01"]
        assert params["lang"] == [language]
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("damage", ["missing", "invalid", "digest", "bytes"])
def test_invalid_stored_receipt_preserves_final_content(dashboard, damage):
    scope = create_scope(dashboard, "Receipt fallback")["scope_id"]
    dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "The budget review is Thursday."}
    )
    response = prepare(dashboard, scope)
    record_id = latest_record_id(dashboard, scope)
    receipt = response["receipt"]
    if damage == "missing":
        receipt = None
    elif damage == "invalid":
        receipt = {"schema": "private-provider-secret"}
    elif damage == "digest":
        receipt["content_digest"] = "sha256:" + "0" * 64
    else:
        receipt["used_bytes"] += 1

    async def damage_receipt():
        async with dashboard.app.state.application.primary_database.transaction() as connection:
            await connection.execute(
                update(CONTEXT_PREPARE_DIAGNOSTICS_TABLE)
                .where(CONTEXT_PREPARE_DIAGNOSTICS_TABLE.c.record_id == record_id)
                .values(receipt_payload=None if receipt is None else json.dumps(receipt).encode())
            )

    dashboard.portal.call(damage_receipt)
    page = detail_page(dashboard, scope, record_id)
    assert page.status_code == 200 and "The budget review is Thursday." in page.text
    assert "Receipt unavailable" in page.text and "private-provider-secret" not in page.text
    assert "data-selected-reference" not in page.text


def test_reference_denial_redacts_links_in_saved_detail(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Reference authorization")["scope_id"]
    dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "The budget review is Thursday."}
    )
    response = prepare(dashboard, scope)
    artifact = response["receipt"]["selected"][0]["artifact_address"]["artifact"]["artifact_id"]

    async def denied(*args, **kwargs):
        raise ReadError(403, "private-error-secret")

    monkeypatch.setattr(DashboardAPI, "artifact_revision", denied)
    page = detail_page(dashboard, scope, latest_record_id(dashboard, scope))
    assert page.status_code == 200 and "Reference unavailable" in page.text
    assert f"artifact={artifact}" not in page.text and "private-error-secret" not in page.text


def test_import_endpoint_is_removed_and_detail_requires_an_id(dashboard):
    scope = create_scope(dashboard, "No import")["scope_id"]
    assert dashboard.post("/dashboard/context-diagnostics", data={"scope": scope, "response": "{}"}).status_code == 405
    assert dashboard.get("/dashboard/context-diagnostic-detail", params={"scope": scope}).status_code == 422


def test_saved_content_is_escaped_on_detail_page(dashboard):
    scope = create_scope(dashboard, "Escaped context")["scope_id"]
    content = "<script>window.budget = true</script>"
    assert (
        dashboard.post("/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": content}).status_code
        == 200
    )
    prepare(dashboard, scope, query="budget")
    page = detail_page(dashboard, scope, latest_record_id(dashboard, scope))
    assert page.status_code == 200 and content not in page.text
    assert "&lt;script&gt;window.budget" in page.text
