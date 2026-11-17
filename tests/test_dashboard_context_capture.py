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

"""Actual HTTP prepare -> durable record -> authorized read-only Dashboard acceptance."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from sqlalchemy import event

from powercontext.builtin.persistence.context_prepare_diagnostics import ContextPrepareDiagnosticsRepository
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.application import ScopedContextApplication
from powercontext.builtin.runtime.receipt_builder import ReceiptEvidence
from powercontext.server.context_diagnostics import diagnostic_sink
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


def records(client, scope):
    async def read():
        async with client.app.state.application.primary_database.transaction() as connection:
            return await ContextPrepareDiagnosticsRepository().list_page(connection, scope)

    return client.portal.call(read)[0]


def detail_record(client, scope, record_id):
    async def read():
        async with client.app.state.application.primary_database.transaction() as connection:
            return await ContextPrepareDiagnosticsRepository().get(connection, scope, record_id)

    return client.portal.call(read)


def prepare(client, scope, **kwargs):
    return client.post("/v1/context/prepare", json={"scope_id": scope, "query": "budget", **kwargs})


@pytest.mark.parametrize("reuse", [False, True])
def test_real_prepare_capture_preserves_wire_and_reads_without_replay(dashboard, monkeypatch, reuse):
    scope = create_scope(dashboard, "Captured recall")["scope_id"]
    assert (
        dashboard.post(
            "/v1/memory/remember",
            json={"scope_id": scope, "kind": "fact", "text": "Budget decision uses the Thursday review."},
        ).status_code
        == 200
    )
    ordinary = prepare(dashboard, scope, include_reuse=reuse)
    assert ordinary.status_code == 200, ordinary.text
    response = prepare(dashboard, scope, include_reuse=reuse)
    assert response.status_code == 200, response.text
    body = response.json()["context"] if reuse else response.json()
    baseline = ordinary.json()["context"] if reuse else ordinary.json()
    assert set(body) == {"schema", "status", "content", "content_bytes"}
    assert body == baseline
    summaries = records(dashboard, scope)
    assert len(summaries) == 2
    assert "prepared_content" not in summaries[0] and "receipt_payload" not in summaries[0]
    record = detail_record(dashboard, scope, summaries[0]["record_id"])
    assert record["prepared_content"] == body["content"]
    assert record["receipt_payload"]["used_bytes"] == body["content_bytes"]
    assert record["selected_count"] > 0
    assert record["principal_id"] and record["request_id"]
    payload = record["diagnostics_payload"]
    assert {s["family"] for s in payload["searches"]} == {"atomic-memory", "experience", "topic-memory"}
    assert any(s["result_count"] > 0 for s in payload["searches"] if s["outcome"] == "completed")
    assert "query" not in payload
    assert datetime.fromisoformat(str(record["started_at"])).year >= 2026

    async def no_prepare(*args, **kwargs):
        pytest.fail("Dashboard must not prepare or search again")

    monkeypatch.setattr(ScopedContextApplication, "prepare", no_prepare)
    queries = []
    engine = dashboard.app.state.application.primary_database.engine.sync_engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "pc_context_prepare_diagnostics" in statement:
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = dashboard.get("/dashboard/context-diagnostics", params={"scope": scope, "lang": "en"})
        assert page.status_code == 200, page.text
        assert "Recall history" in page.text and "Budget decision" not in page.text
        assert queries and all("prepared_content" not in query and "receipt_payload" not in query for query in queries)
        for lang in ("en", "zh"):
            selected = dashboard.get(
                "/dashboard/context-diagnostic-detail",
                params={
                    "scope": scope,
                    "lang": lang,
                    "diagnostic": record["record_id"],
                },
            )
            assert selected.status_code == 200, selected.text
            assert "Budget decision uses the Thursday review." in selected.text
            search_table = re.search(r"<table[^>]*data-search-summary[^>]*>(.*?)</table>", selected.text, re.S)
            assert search_table is not None
            assert "Budget decision uses the Thursday review." in search_table.group(1)
            assert "data-candidate-text" in search_table.group(1)
            assert 'href="/dashboard/notes?' in search_table.group(1)
            assert "&amp;revision=" in search_table.group(1)
            assert 'id="context-text-search"' not in selected.text
            assembly_table = re.search(r"<table[^>]*data-assembly-summary[^>]*>(.*?)</table>", selected.text, re.S)
            assert assembly_table is not None
            assert "Budget decision uses the Thursday review." in assembly_table.group(1)
            selected_table = re.search(r"<table[^>]*data-selected-summary[^>]*>(.*?)</table>", selected.text, re.S)
            assert selected_table is not None
            assert "Budget decision uses the Thursday review." in selected_table.group(1)
            assert "data-selected-text" in selected_table.group(1)
            for stage in ("rerank-input", "rerank-output", "assembly"):
                assert f'id="context-text-{stage}"' not in selected.text

            assert "返回召回记录" not in selected.text and "Back to recall history" not in selected.text
            assert "data-context-bytes=" in selected.text
            assert "data-context-bytes-hint" in selected.text
            assert "UTF-8" in selected.text
            assert selected.headers["Cache-Control"] == "no-store"
        other = create_scope(dashboard, "Another capture scope")["scope_id"]
        denied = dashboard.get(
            "/dashboard/context-diagnostic-detail", params={"scope": other, "diagnostic": record["record_id"]}
        )
        assert denied.status_code == 404 and "Budget decision" not in denied.text
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_capture_failure_does_not_change_success_and_receipt_failure_keeps_record(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Fail open capture")["scope_id"]
    original = ContextPrepareDiagnosticsRepository.append

    async def unavailable(*args, **kwargs):
        raise RuntimeError("sensitive database error")  # noqa: TRY003

    monkeypatch.setattr(ContextPrepareDiagnosticsRepository, "append", unavailable)
    response = prepare(dashboard, scope)
    assert response.status_code == 200 and set(response.json()) == {"schema", "status", "content", "content_bytes"}
    monkeypatch.setattr(ContextPrepareDiagnosticsRepository, "append", original)

    def no_receipt(*args, **kwargs):
        raise ValueError("receipt failure")  # noqa: TRY003

    monkeypatch.setattr(ReceiptEvidence, "build", no_receipt)
    response = prepare(dashboard, scope)
    assert response.status_code == 200
    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    assert record["receipt_payload"] is None
    assert record["status"] == "empty" and record["content_bytes"] == 0 and record["selected_count"] == 0
    page = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": record["record_id"]}
    )
    assert page.status_code == 200


@pytest.mark.parametrize("failure", ["unknown", "cancelled", "timeout"])
def test_failed_and_cancelled_prepare_remain_distinct(dashboard, monkeypatch, failure):
    from powercontext.builtin.inference import InferenceTimeoutError

    cancelled = failure == "cancelled"
    expected = asyncio.CancelledError if cancelled else InferenceTimeoutError if failure == "timeout" else RuntimeError
    scope = create_scope(dashboard, "Terminal result")["scope_id"]
    runtime = dashboard.app.state.application

    async def fail(*args, **kwargs):
        if cancelled:
            raise asyncio.CancelledError
        if failure == "timeout":
            raise InferenceTimeoutError("private-operation-content", 1)
        raise RuntimeError("do not store this error body")  # noqa: TRY003

    monkeypatch.setattr(ScopedContextApplication, "_prepare_build", fail)

    async def scenario():
        with pytest.raises(expected):
            await runtime.context.for_scope(scope).prepare(
                PrepareContextRequest(query="secret query"), diagnostic_sink=diagnostic_sink(runtime)
            )

    dashboard.portal.call(scenario)
    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    assert record["status"] == ("cancelled" if cancelled else "failed")
    assert record["content_bytes"] is None and record["selected_count"] is None
    assert "do not store" not in json.dumps(record, default=str)
    assert "private-operation-content" not in json.dumps(record, default=str)
    if failure == "timeout":
        assert record["error_code"] == "inference_timeout"
    page = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": record["record_id"], "lang": "en"}
    )
    assert page.status_code == 200, page.text
    assert ("Cancelled" if cancelled else "Failed") in page.text


def test_history_pages_tied_timestamps_and_binds_filters(dashboard):
    scope = create_scope(dashboard, "Paged capture")["scope_id"]
    assert prepare(dashboard, scope).status_code == 200
    seed = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])

    # Give all fixture records the same timestamp, exercising the record-id tie break.
    now = datetime.now(UTC) + timedelta(days=1)

    async def tied_more():
        async with dashboard.app.state.application.primary_database.transaction() as connection:
            for index in range(26):
                await ContextPrepareDiagnosticsRepository().append(
                    connection, {**seed, "record_id": f"pcd_paged_{index:02}", "finished_at": now}
                )

    dashboard.portal.call(tied_more)
    first = dashboard.get(
        "/dashboard/context-diagnostics", params={"scope": scope, "lang": "en", "diagnostic_status": "empty"}
    )
    assert first.status_code == 200, first.text
    ids = re.findall(r'data-context-record="([^"]+)"', first.text)
    assert len(ids) == 25
    match = re.search(r'href="([^"]+)">Next page', first.text)
    assert match is not None
    next_url = unescape(match[1])
    second = dashboard.get(next_url)
    assert second.status_code == 200
    following = re.findall(r'data-context-record="([^"]+)"', second.text)
    assert len(following) == 2 and not set(ids).intersection(following)
    tampered = dashboard.get(next_url.replace("diagnostic_status=empty", "diagnostic_status=ready"))
    assert tampered.status_code == 400
    bad_date = dashboard.get(
        "/dashboard/context-diagnostics", params={"scope": scope, "diagnostic_since": "not-a-date"}
    )
    assert bad_date.status_code == 422


def test_capture_records_all_five_families_with_code_failure(dashboard):
    scope = create_scope(dashboard, "Multiple artifact families")["scope_id"]
    assert (
        dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={
                "family": "profile",
                "content": {"content": "Prefer concise answers."},
            },
        ).status_code
        == 201
    )
    assert (
        dashboard.post(
            "/v1/memory/remember",
            json={
                "scope_id": scope,
                "kind": "fact",
                "text": "Budget review is Thursday.",
            },
        ).status_code
        == 200
    )
    assembly = {
        "sections": [
            {"family": "profile", "limit": 1},
            {"family": "memory", "limit": 2},
            {"family": "experience", "limit": 2},
            {"family": "topic-memory", "limit": 2},
        ]
    }
    baseline = prepare(dashboard, scope, assembly=assembly, include_code=True)
    captured = prepare(dashboard, scope, assembly=assembly, include_code=True)
    assert captured.status_code == 200 and captured.json() == baseline.json()
    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    assert record["receipt_payload"] is not None
    assert record["selected_count"] == 2
    assert record["error_code"] == "code_disabled"
    evidence = record["diagnostics_payload"]
    assert evidence["selected_by_family"]["profile"] == 1
    assert evidence["selected_by_family"]["atomic-memory"] == 1
    searches = {item["family"]: item for item in evidence["searches"]}
    assert searches.keys() == {"atomic-memory", "topic-memory", "experience", "profile", "code"}
    assert searches["profile"]["result_count"] == 1
    assert searches["experience"]["result_count"] == 0
    assert searches["code"]["outcome"] == "failed" and searches["code"]["result_count"] is None
    assert all(item["duration_ms"] >= 0 for item in searches.values())
    page = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": record["record_id"], "lang": "en"}
    )
    assert page.status_code == 200 and "Prefer concise answers." in page.text
    assert "Partial degradation" in page.text and "Code retrieval is disabled" in page.text


def test_profile_only_search_keeps_its_phase_duration(dashboard):
    scope = create_scope(dashboard, "Profile search duration")["scope_id"]
    assert (
        dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={"family": "profile", "content": {"content": "Prefer concise answers."}},
        ).status_code
        == 201
    )
    response = prepare(dashboard, scope, assembly={"sections": [{"family": "profile", "limit": 1}]})
    assert response.status_code == 200
    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    search = record["diagnostics_payload"]["searches"][0]
    page = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": record["record_id"], "lang": "en"}
    )
    assert page.status_code == 200
    heading = re.search(r'data-context-phase="context_searches".*?</h2>', page.text, re.S)
    assert heading is not None
    assert f"{search['duration_ms']:.2f} ms · 1" in heading[0]


def test_retained_body_reauthorizes_referenced_scope_and_completed_audit(dashboard, monkeypatch):
    from powercontext.builtin.authz import PrincipalRef
    from tests.test_dashboard_scope_selection import _grant, _revoke

    shared = create_scope(dashboard, "Referenced private scope")["scope_id"]
    response = dashboard.post(
        "/v1/scopes",
        json={
            "title": "Recall caller",
            "summary": "Reference scope",
            "idempotency_key": "caller",
            "context_references": [shared],
        },
    )
    assert response.status_code == 201
    scope = response.json()["scope_id"]
    assert (
        dashboard.post(
            "/v1/memory/remember",
            json={
                "scope_id": shared,
                "kind": "fact",
                "text": "Budget contains private reference content.",
            },
        ).status_code
        == 200
    )
    assert prepare(dashboard, scope).status_code == 200
    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    assert "private reference content" in record["prepared_content"]
    reader = {"type": "user", "id": "retained-context-reader"}
    _grant(dashboard, reader, {"type": "scope", "scope_id": scope}, "scope.viewer")
    authentication = dashboard.app.state.authentication_provider
    params = {"scope": scope, "diagnostic": record["record_id"]}
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        denied = dashboard.get("/dashboard/context-diagnostic-detail", params=params)
        assert denied.status_code == 403 and "private reference content" not in denied.text
        refused_prepare = prepare(dashboard, scope)
        assert refused_prepare.status_code == 403
    assert len(records(dashboard, scope)) == 1
    created = dashboard.post(
        "/v1/access/bindings/create",
        json={
            "subject": reader,
            "resource": {"type": "scope", "scope_id": shared},
            "role": "scope.viewer",
            "idempotency_key": "shared-reader-grant",
        },
    )
    assert created.status_code == 201
    grant = created.json()
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        allowed = dashboard.get("/dashboard/context-diagnostic-detail", params=params)
        assert allowed.status_code == 200 and "private reference content" in allowed.text
    _revoke(dashboard, grant)
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        denied = dashboard.get("/dashboard/context-diagnostic-detail", params=params)
        assert denied.status_code == 403 and "private reference content" not in denied.text

    async def unavailable(events):
        raise RuntimeError

    with monkeypatch.context() as patch:
        patch.setattr(dashboard.app.state.access_control.audit, "append_audits", unavailable)
        patch.setattr(dashboard.app.state.access_control.audit, "append_audit", unavailable)
        failed = dashboard.get("/dashboard/context-diagnostic-detail", params=params)
        assert failed.status_code == 503 and "private reference content" not in failed.text


def test_actual_task_cancellation_is_recorded_and_propagates(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Cancelled request task")["scope_id"]
    runtime = dashboard.app.state.application

    async def scenario():
        entered = asyncio.Event()

        async def pending(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(ScopedContextApplication, "_prepare_build", pending)
        task = asyncio.create_task(
            runtime.context.for_scope(scope).prepare(
                PrepareContextRequest(query="cancelled query"),
                diagnostic_sink=diagnostic_sink(runtime),
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    dashboard.portal.call(scenario)
    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    assert record["status"] == "cancelled" and record["content_bytes"] is None


def test_original_query_round_trips_without_loading_stage_payload_in_list(dashboard):
    from markupsafe import escape

    scope = create_scope(dashboard, "Original query")["scope_id"]
    query = "  预算原文\n<诊断> & \"quote\" '单引号' 😀\t"
    response = dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": query})
    assert response.status_code == 200 and set(response.json()) == {"schema", "status", "content", "content_bytes"}
    saved = records(dashboard, scope)[0]
    assert saved["query_text"] == query
    assert detail_record(dashboard, scope, saved["record_id"])["query_text"] == query
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "pc_context_prepare_diagnostics" in statement:
            statements.append(statement)

    engine = dashboard.app.state.application.primary_database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = dashboard.get("/dashboard/context-diagnostics", params={"scope": scope, "lang": "en"})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert page.status_code == 200 and str(escape(query)) in page.text
    assert statements and all(
        "query_text" in sql and "diagnostics_payload" not in sql and "prepared_content" not in sql for sql in statements
    )
    detail = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": saved["record_id"], "lang": "en"}
    )
    assert detail.status_code == 200 and str(escape(query)) in detail.text
    assert query not in detail.text


def test_long_query_can_expand_and_legacy_query_stays_unknown(dashboard):
    scope = create_scope(dashboard, "Long original query")["scope_id"]
    query = "预算原文 " * 100
    assert dashboard.post("/v1/context/prepare", json={"scope_id": scope, "query": query}).status_code == 200
    saved = records(dashboard, scope)[0]
    page = dashboard.get("/dashboard/context-diagnostics", params={"scope": scope, "lang": "en"})
    assert page.status_code == 200 and query in page.text and "<summary>" in page.text

    async def legacy_record():
        original = detail_record_value
        async with dashboard.app.state.application.primary_database.transaction() as connection:
            await ContextPrepareDiagnosticsRepository().append(
                connection, {**original, "record_id": "pcd_legacy_query", "query_text": None}
            )

    detail_record_value = detail_record(dashboard, scope, saved["record_id"])
    dashboard.portal.call(legacy_record)
    detail = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": "pcd_legacy_query", "lang": "en"}
    )
    assert detail.status_code == 200 and "Query text not recorded" in detail.text


def test_search_table_counts_candidates_before_reranking(dashboard):
    from tests.builtin.runtime.test_context_diagnostic_text import SelectLast

    scope = create_scope(dashboard, "Raw search stage count")["scope_id"]
    for text in ("Budget alpha is reviewed Thursday.", "Budget beta is reviewed Friday."):
        assert (
            dashboard.post("/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": text}).status_code
            == 200
        )
    dashboard.app.state.application.atomic_memory._application.reranker = SelectLast()
    assert prepare(dashboard, scope).status_code == 200
    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    memory_search = next(row for row in record["diagnostics_payload"]["searches"] if row["family"] == "atomic-memory")
    assert memory_search["result_count"] == 1  # The complete search API returns the reranked result.
    page = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": record["record_id"], "lang": "en"}
    )
    assert page.status_code == 200
    table = re.search(r"<table[^>]*data-search-summary[^>]*>(.*?)</table>", page.text, re.S)
    assert table is not None
    row = re.search(r"<tbody>\s*<tr>(.*?)</tr>", table[1], re.S)
    assert row is not None
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row[1], re.S)
    assert cells[4].strip() == "2"  # Retrieval precedes the two-input/one-output rerank stage.
    assert "Budget alpha" in cells[-1] and "Budget beta" in cells[-1]


def test_prepare_capture_counts_plugin_families_like_builtin_families(dashboard, monkeypatch):
    from types import SimpleNamespace

    from powercontext.builtin.plugins.families import register_plugin_family
    from powercontext.builtin.plugins.host_protocol import HostResponse

    register_plugin_family("notes")
    manifest = SimpleNamespace(spec=SimpleNamespace(family="notes", change_timeout_seconds=30))

    class FakeHost:
        async def call(self, method, **kwargs):
            assert method == "search_artifacts"
            return HostResponse(
                id="fake",
                outcome="ok",
                result={
                    "hits": [
                        {
                            "ref": {"family": "notes", "artifact_id": "note-alpha", "revision": 1},
                            "text": "Project Alpha runs its deploy from the east region.",
                        }
                    ]
                },
            )

    class FakeManager:
        def __init__(self):
            self.plugins: dict[str, object] = {}

        def host_for_family(self, family):
            return (manifest, FakeHost()) if family == "notes" else None

    monkeypatch.setattr(dashboard.app.state, "plugin_manager", FakeManager())
    scope = create_scope(dashboard, "Plugin family capture")["scope_id"]

    from powercontext.builtin.persistence.sources import SourceRepository
    from powercontext.builtin.plugins.writer import PluginArtifactWriter
    from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY

    application = dashboard.app.state.application

    async def write_note():
        writer = PluginArtifactWriter(
            database=application.primary_database,
            scope_id=scope,
            family="notes",
            sources=SourceRepository(BUILTIN_SOURCE_REGISTRY),
        )
        async with writer.transaction() as tx:
            await tx.put("note-alpha", content={"text": "Project Alpha runs its deploy from the east region."})

    dashboard.portal.call(write_note)

    dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Budget review is Thursday."}
    )
    assembly = {"sections": [{"family": "notes", "limit": 2}, {"family": "memory", "limit": 2}]}
    response = prepare(dashboard, scope, assembly=assembly)
    assert response.status_code == 200, response.text
    assert "Project Alpha runs its deploy from the east region." in response.json()["content"]

    record = detail_record(dashboard, scope, records(dashboard, scope)[0]["record_id"])
    evidence = record["diagnostics_payload"]
    assert evidence["selected_by_family"]["notes"] == 1
    assert evidence["selected_by_family"]["atomic-memory"] == 1
    assert evidence["final_candidates"]["notes"] == 1
    assert evidence["final_candidates"]["atomic-memory"] >= 0
    snapshot = next(item for item in evidence["text_snapshots"] if item["family"] == "notes")
    assert snapshot["stage"] == "assembly"
    assert snapshot["items"][0]["artifact"] == {
        "family": "notes",
        "artifact_id": "note-alpha",
        "revision": 1,
    }
    assert snapshot["items"][0]["text"].startswith("Project Alpha")

    page = dashboard.get(
        "/dashboard/context-diagnostic-detail", params={"scope": scope, "diagnostic": record["record_id"], "lang": "en"}
    )
    assert page.status_code == 200 and "Project Alpha runs its deploy" in page.text
