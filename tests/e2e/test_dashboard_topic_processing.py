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

"""Selected-Scope processing evidence through real Dashboard requests and SQLite."""

import asyncio
import re
from datetime import UTC, datetime
from html import unescape
from time import monotonic
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, event, insert, update

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING as BINDING
from powercontext.builtin.authz import AccessDeniedError, PrincipalRef
from powercontext.builtin.authz.service import AccessControlService
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_PROCESSING_TARGETS_TABLE as TARGETS
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BLOCKS_TABLE as BLOCKS
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BUDGETS_TABLE as BUDGETS
from powercontext.builtin.runtime import scope_reads
from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
from powercontext.builtin.runtime.scope_reads import ScopeReadSnapshot
from powercontext.builtin.runtime.topic_memory_recovery import TopicMemoryRecoveryService
from powercontext.builtin.sources import SourceCursor
from powercontext.server.dashboard.preferences import CATALOGS
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    ServerSettings,
)
from tests.test_dashboard import create_scope, page_link, record_links
from tests.test_dashboard import dashboard as dashboard


def _pending(client: TestClient, title="Pending topics") -> str:
    scope = create_scope(client, title)["scope_id"]
    result = client.post(
        "/v1/sources/content", json={"scope_id": scope, "source_id": "evidence", "content": "Retained input."}
    )
    assert result.status_code == 202, result.text
    return scope


def _seed(client: TestClient, scope: str, *, block=True, binding=BINDING, active=False) -> None:
    runtime = cast(FastAPI, client.app).state.application

    async def seed():
        async with runtime.primary_database.transaction() as connection:
            await connection.execute(
                insert(BUDGETS).values(
                    scope_id=scope,
                    binding_name=binding,
                    source_after=0,
                    source_through=1,
                    attempt_id="fixture-attempt",
                    attempts=3,
                    requests=17,
                    tokens=4321,
                    failure_code="",
                    started_at=datetime(2026, 1, 1, tzinfo=UTC) if active else None,
                )
            )
            if block:
                await connection.execute(
                    insert(BLOCKS).values(
                        block_id=f"fixture-{scope}-{binding}",
                        attempt_id="fixture-attempt",
                        scope_id=scope,
                        binding_name=binding,
                        source_after=0,
                        source_through=None,
                        budget_source_through=1,
                        target_source_through=1,
                        attempts=3,
                        requests=17,
                        tokens=4321,
                        error_code="window_attempt_limit",
                        last_error_code="model_timeout",
                        blocked_at=datetime(2026, 1, 2, tzinfo=UTC),
                    )
                )

    assert client.portal is not None
    client.portal.call(seed)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_processing_progress_and_scope_switching(dashboard: TestClient, language):
    pending = _pending(dashboard)
    caught_up = create_scope(dashboard, "Empty topic scope")["scope_id"]
    expected = (
        ("Pending inputs or retained target", "Inputs caught up")
        if language == "en"
        else ("有待消费输入或保留目标", "输入已追平")
    )
    for scope, state in ((pending, expected[0]), (caught_up, expected[1]), (pending, expected[0])):
        response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": language})
        assert response.status_code == 200, response.text
        assert state in response.text
        assert ("Current execution state is unknown" if language == "en" else "当前执行状态未知") in response.text
        assert ("does not prove publication" if language == "en" else "不能证明主题已发布") in response.text
        assert 'aria-controls="topic-processing-details"' in response.text
        assert 'data-bs-dismiss="offcanvas"' in response.text


@pytest.mark.parametrize("active", [False, True], ids=["legacy-missing-diagnostic", "final-attempt-active"])
def test_page_reads_do_not_materialize_blocks_or_activate_work(dashboard: TestClient, monkeypatch, active):
    scope = _pending(dashboard)
    _seed(dashboard, scope, block=False, active=active)
    runtime = cast(FastAPI, dashboard.app).state.application

    async def forbidden(*args, **kwargs):
        pytest.fail("Dashboard inspection must not call the recovery observation operation")

    monkeypatch.setattr(TopicMemoryRecoveryService, "status", forbidden)
    starts = []
    start = SpawnArtifactProcessingWorkerLauncher.start

    async def record_start(launcher, assignment):
        starts.append(assignment)
        return await start(launcher, assignment)

    monkeypatch.setattr(SpawnArtifactProcessingWorkerLauncher, "start", record_start)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = runtime.primary_database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        for _ in range(2):
            response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
            assert response.status_code == 200, response.text
            assert "Pending inputs or retained target" in response.text
            assert "No applicable retained block diagnostic" in response.text
            assert "Reserved token capacity units</dt><dd>4321" in response.text
            assert "Exact attempt end position</dt><dd>Unknown" in response.text
            assert "Current execution state is unknown" in response.text
    finally:
        event.remove(engine, "before_cursor_execute", record)
    protected_tables = ("pc_topic_memory_", "pc_source_", "pc_artifact_processing_", "pc_artifacts")
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        and any(table in statement for table in protected_tables)
    ]
    assert all("FOR UPDATE" not in statement.upper() for statement in statements)
    assert not starts, "Passive page reads must not launch processing workers"


@pytest.mark.parametrize("obsolete", ["retried", "replacement-attempt", "advanced-cursor", "other-binding"])
def test_only_current_blocks_are_displayed(dashboard: TestClient, obsolete):
    scope = _pending(dashboard)
    _seed(dashboard, scope, binding="another-binding" if obsolete == "other-binding" else BINDING)
    runtime = cast(FastAPI, dashboard.app).state.application
    if obsolete != "other-binding":
        current = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
        assert current.status_code == 200, current.text
        assert "Known processing block" in current.text
        assert "window_attempt_limit" in current.text
        assert "2026-01-02T00:00:00Z" in current.text

    async def change():
        async with runtime.primary_database.transaction() as connection:
            if obsolete == "retried":
                await connection.execute(
                    update(BLOCKS)
                    .where(BLOCKS.c.scope_id == scope)
                    .values(retried_at=datetime(2026, 1, 3, tzinfo=UTC), retry_request_generation=2)
                )
            elif obsolete == "replacement-attempt":
                await connection.execute(
                    update(BUDGETS).where(BUDGETS.c.scope_id == scope).values(attempt_id="replacement")
                )
            elif obsolete == "advanced-cursor":
                await SourceCursorRepository().save(
                    connection, scope, BINDING, SourceCursor(sequence=1), expected_generation=None
                )

    assert dashboard.portal is not None
    dashboard.portal.call(change)
    response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    assert "window_attempt_limit" not in response.text and "model_timeout" not in response.text
    assert "No applicable retained block diagnostic" in response.text
    assert ("Inputs caught up" if obsolete == "advanced-cursor" else "Pending inputs") in response.text


def test_explicit_recovery_and_success_preserve_historical_evidence(dashboard: TestClient):
    scope = _pending(dashboard)
    _seed(dashboard, scope)
    runtime = cast(FastAPI, dashboard.app).state.application
    service = TopicMemoryRecoveryService(runtime.primary_database)
    assert dashboard.portal is not None
    # Exercise the supported recovery service, then observe only through Dashboard.
    accepted = dashboard.portal.call(service.retry, scope, f"fixture-{scope}-{BINDING}")
    assert accepted.block.retried_at is not None
    assert dashboard.portal.call(service.retry, scope, accepted.block.block_id) == accepted
    pending = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert pending.status_code == 200, pending.text
    assert "Pending inputs" in pending.text and "window_attempt_limit" not in pending.text

    async def consume():
        async with runtime.primary_database.transaction() as connection:
            await SourceCursorRepository().save(
                connection, scope, BINDING, SourceCursor(sequence=1), expected_generation=None
            )
            await connection.execute(delete(TARGETS).where(TARGETS.c.scope_id == scope))

    dashboard.portal.call(consume)
    caught_up = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert caught_up.status_code == 200, caught_up.text
    assert "Inputs caught up" in caught_up.text
    assert "does not prove publication or a recorded no-change outcome" in caught_up.text
    assert dashboard.portal.call(service.get_block, scope, accepted.block.block_id) == accepted.block


def test_retained_target_remains_pending_after_cursor_catches_up(dashboard: TestClient):
    scope = _pending(dashboard)
    runtime = cast(FastAPI, dashboard.app).state.application

    async def retain_target():
        async with runtime.primary_database.transaction() as connection:
            await SourceCursorRepository().save(
                connection, scope, BINDING, SourceCursor(sequence=1), expected_generation=None
            )
            await connection.execute(
                insert(TARGETS).values(
                    binding_name=BINDING,
                    scope_id=scope,
                    target_request_generation=1,
                    source_through=1,
                    captured_flush_generation=0,
                    observed_dirty_generation=1,
                )
            )

    assert dashboard.portal is not None
    dashboard.portal.call(retain_target)
    response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    assert "Pending inputs or retained target" in response.text
    assert "Retained target position</dt><dd>1" in response.text


@pytest.mark.parametrize("failure", ["immediate", "timeout", "connection"])
def test_inspection_failure_keeps_saved_topics_readable(dashboard: TestClient, monkeypatch, failure):
    scope = create_scope(dashboard, "Readable topic scope")["scope_id"]
    saved = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "topic-memory",
            "content": {"title": "Saved topic", "summary": "Summary", "detail": "Exact body"},
        },
    )
    assert saved.status_code == 201, saved.text

    async def unavailable(connection, _scope, _binding):
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "connection":
            await connection.invalidate()
        message = "private storage failure"
        raise RuntimeError(message)

    monkeypatch.setattr(scope_reads, "read_topic_memory_processing_status", unavailable)
    response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    assert "Saved topic" in response.text and "Processing status could not be loaded" in response.text
    assert "Request ID" in response.text and "private storage failure" not in response.text

    async def forbidden(*args, **kwargs):
        pytest.fail("An exact revision must not depend on processing inspection")

    monkeypatch.setattr(ScopeReadSnapshot, "topic_memory_processing_status", forbidden)
    detail = dashboard.get(
        "/dashboard/topics",
        params={"scope": scope, "topic_artifact": saved.json()["artifact_id"], "topic_revision": 1},
    )
    assert detail.status_code == 200, detail.text
    assert "Exact body" in detail.text


def test_inspection_authorization_failure_does_not_disclose_evidence(dashboard: TestClient, monkeypatch):
    scope = _pending(dashboard)
    _seed(dashboard, scope)
    authorize = AccessControlService.require_scope_read

    async def denied(service, principal, scope_id, *, connection, context):
        if context.operation == "dashboard_topic_memory_processing_status":
            raise AccessDeniedError
        return await authorize(service, principal, scope_id, connection=connection, context=context)

    monkeypatch.setattr(AccessControlService, "require_scope_read", denied)
    response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    assert "Processing status could not be loaded" in response.text
    assert "window_attempt_limit" not in response.text
    assert "Consumed through position" not in response.text


def test_inspection_audit_failure_discards_projection_and_keeps_topics(dashboard: TestClient, monkeypatch):
    scope = _pending(dashboard)
    _seed(dashboard, scope)
    saved = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={"family": "topic-memory", "content": {"title": "Readable topic", "summary": "Summary", "detail": "Body"}},
    )
    assert saved.status_code == 201, saved.text
    audit = cast(FastAPI, dashboard.app).state.access_control.audit
    append = audit.append_audits

    async def unavailable(events):
        if any(event.operation == "dashboard_topic_memory_processing_status" for event in events):
            message = "private inspection audit failure"
            raise RuntimeError(message)
        return await append(events)

    monkeypatch.setattr(audit, "append_audits", unavailable)
    response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    assert "Readable topic" in response.text
    assert "Processing status could not be loaded" in response.text
    assert "window_attempt_limit" not in response.text
    assert "Consumed through position" not in response.text
    assert "private inspection audit failure" not in response.text


def test_scope_denial_and_audit_failure_disclose_no_processing_facts(dashboard: TestClient, monkeypatch):
    scope = _pending(dashboard, "Private topic scope")
    _seed(dashboard, scope)
    app = cast(FastAPI, dashboard.app)
    with monkeypatch.context() as patch:
        patch.setattr(app.state.authentication_provider, "_principal", PrincipalRef(type="user", id="stranger"))
        denied = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
        assert denied.status_code == 403, denied.text
        assert "Private topic scope" not in denied.text and "window_attempt_limit" not in denied.text
        assert "Consumed through position" not in denied.text

    async def unavailable(_events):
        message = "private audit failure"
        raise RuntimeError(message)

    monkeypatch.setattr(app.state.access_control.audit, "append_audits", unavailable)
    response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert response.status_code == 503, response.text
    assert "window_attempt_limit" not in response.text and "Consumed through position" not in response.text


def test_reopened_runtime_keeps_durable_blocks_and_unknown_execution(dashboard: TestClient):
    scope = _pending(dashboard)
    _seed(dashboard, scope)
    app = cast(FastAPI, dashboard.app)
    first = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    assert first.status_code == 200, first.text
    # A second application against the same SQLite database has no local history.
    restarted = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=str(app.state.application.primary_database.engine.url)),
            mcp=McpConfig(enabled=False),
            dashboard=DashboardConfig(enabled=True),
            access=AccessControlConfig(mode="enforced"),
            auth=BearerAuthConfig(token=SecretStr(dashboard.headers["Authorization"].removeprefix("Bearer "))),
        )
    )
    with TestClient(restarted) as client:
        client.headers.update(dashboard.headers)
        response = client.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
        assert response.status_code == 200, response.text
        assert "Known processing block" in response.text and "Current execution state is unknown" in response.text
        assert "2026-01-02T00:00:00Z" in response.text


def _topic_directory(client, scope):
    topics = {}
    for index in range(51):
        content = {
            "title": f"{'Database' if index < 7 else 'Cache'} topic {index:02}",
            "summary": "Explicitly saved navigation example.",
            "detail": f"Exact original body {index:02}.",
        }
        response = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "topic-memory", "content": content})
        assert response.status_code == 201, response.text
        topics[response.json()["artifact_id"]] = content
    return topics


def _topic_link(html, artifact):
    return next(
        unescape(link)
        for link in re.findall(r'href="([^"]+)"', html)
        if parse_qs(urlsplit(unescape(link)).query).get("topic_artifact") == [artifact]
    )


@pytest.mark.parametrize("language", ["en", "zh"])
def test_processing_survives_search_paging_and_exact_revision_navigation(dashboard, monkeypatch, language):
    scope = create_scope(dashboard, "Searchable topic directory")["scope_id"]
    topics = _topic_directory(dashboard, scope)
    labels = CATALOGS[language]
    params = {"scope": scope, "lang": language}
    first = dashboard.get("/dashboard/topics", params=params)
    assert first.status_code == 200, first.text
    first_ids = record_links(first.text, "/dashboard/topics", "topic_artifact")
    second = dashboard.get(page_link(first.text, labels["next_page"]))
    assert second.status_code == 200, second.text
    second_ids = record_links(second.text, "/dashboard/topics", "topic_artifact")
    assert first_ids and second_ids and first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == set(topics)
    previous = dashboard.get(page_link(second.text, labels["previous_page"]))
    assert record_links(previous.text, "/dashboard/topics", "topic_artifact") == first_ids

    searched = dashboard.get("/dashboard/topics", params={**params, "topic_q": "Database"})
    assert searched.status_code == 200, searched.text
    expected = {artifact for artifact, content in topics.items() if content["title"].startswith("Database")}
    assert record_links(searched.text, "/dashboard/topics", "topic_artifact") == expected
    assert labels["topic_processing_pending"] in searched.text
    missing = dashboard.get("/dashboard/topics", params={**params, "topic_q": "nonexistent-zqxv"})
    assert missing.status_code == 200, missing.text
    assert not record_links(missing.text, "/dashboard/topics", "topic_artifact")
    assert labels["topic_processing_pending"] in missing.text

    artifact = next(iter(expected & first_ids))
    exact_url = _topic_link(searched.text, artifact)
    current = dashboard.get(f"/v1/scopes/{scope}/artifacts/topic-memory/{artifact}")
    replaced = dashboard.put(
        f"/v1/scopes/{scope}/artifacts/topic-memory/{artifact}",
        json={"content": {**topics[artifact], "detail": "Later revision body."}},
        headers={"If-Match": current.headers["etag"]},
    )
    assert replaced.status_code == 200, replaced.text

    async def forbidden(*args, **kwargs):
        pytest.fail("Exact historical revision navigation must not inspect processing")

    with monkeypatch.context() as patch:
        patch.setattr(ScopeReadSnapshot, "topic_memory_processing_status", forbidden)
        detail = dashboard.get(exact_url)
        assert detail.status_code == 200, detail.text
        assert topics[artifact]["detail"] in detail.text
        assert "Later revision body." not in detail.text
        assert 'id="topic-processing-heading"' not in detail.text
        paged_detail = dashboard.get(_topic_link(second.text, next(iter(second_ids))))
        assert paged_detail.status_code == 200, paged_detail.text
    restored = dashboard.get(page_link(detail.text, "← " + labels["topic_inventory"]))
    assert restored.status_code == 200, restored.text
    assert restored.url.params["topic_q"] == "Database"
    assert restored.url.params["scope"] == scope and restored.url.params["lang"] == language
    assert record_links(restored.text, "/dashboard/topics", "topic_artifact") == expected
    restored_page = dashboard.get(page_link(paged_detail.text, "← " + labels["topic_inventory"]))
    assert record_links(restored_page.text, "/dashboard/topics", "topic_artifact") == second_ids
    cleared = dashboard.get("/dashboard/topics", params=params)
    assert record_links(cleared.text, "/dashboard/topics", "topic_artifact") == first_ids


@pytest.mark.parametrize("failure", ["immediate", "timeout"])
def test_processing_failure_preserves_directory_pagination_and_search(dashboard, monkeypatch, failure):
    scope = create_scope(dashboard, "Failure with multiple topic pages")["scope_id"]
    topics = _topic_directory(dashboard, scope)
    cancellations = []

    async def unavailable(*args, **kwargs):
        if failure == "timeout":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations.append(monotonic())
                raise
        message = "private inspection failure"
        raise RuntimeError(message)

    monkeypatch.setattr(scope_reads, "read_topic_memory_processing_status", unavailable)
    started = monotonic()
    first = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
    if failure == "timeout":
        assert cancellations and 1.5 <= cancellations[0] - started < 10
    assert first.status_code == 200, first.text
    second = dashboard.get(page_link(first.text, CATALOGS["en"]["next_page"]))
    assert second.status_code == 200, second.text
    first_ids = record_links(first.text, "/dashboard/topics", "topic_artifact")
    second_ids = record_links(second.text, "/dashboard/topics", "topic_artifact")
    assert first_ids.isdisjoint(second_ids) and first_ids | second_ids == set(topics)
    previous = dashboard.get(page_link(second.text, CATALOGS["en"]["previous_page"]))
    assert record_links(previous.text, "/dashboard/topics", "topic_artifact") == first_ids
    searched = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en", "topic_q": "Database"})
    assert record_links(searched.text, "/dashboard/topics", "topic_artifact") == {
        artifact for artifact, content in topics.items() if content["title"].startswith("Database")
    }
    for response in (first, second, previous, searched):
        assert "Processing status could not be loaded" in response.text
        assert "private inspection failure" not in response.text


def test_scope_switch_keeps_all_processing_evidence_with_its_scope(dashboard):
    pending = _pending(dashboard, "Distinct pending evidence")
    blocked = _pending(dashboard, "Distinct blocked evidence")
    _seed(dashboard, pending, block=False)
    _seed(dashboard, blocked)
    runtime = cast(FastAPI, dashboard.app).state.application

    async def distinguish():
        async with runtime.primary_database.transaction() as connection:
            await connection.execute(
                update(BUDGETS).where(BUDGETS.c.scope_id == pending).values(attempts=1, requests=7, tokens=222)
            )
            await SourceCursorRepository().save(
                connection, blocked, BINDING, SourceCursor(sequence=1), expected_generation=None
            )
            await connection.execute(
                insert(TARGETS).values(
                    scope_id=blocked,
                    binding_name=BINDING,
                    source_through=4,
                    target_request_generation=1,
                    captured_flush_generation=0,
                    observed_dirty_generation=1,
                )
            )
            await connection.execute(
                update(BUDGETS)
                .where(BUDGETS.c.scope_id == blocked)
                .values(source_after=1, source_through=3, attempt_source_through=2, requests=29, tokens=8765)
            )
            await connection.execute(
                update(BLOCKS)
                .where(BLOCKS.c.scope_id == blocked)
                .values(
                    source_after=1,
                    source_through=2,
                    budget_source_through=3,
                    target_source_through=4,
                    requests=29,
                    tokens=8765,
                )
            )

    assert dashboard.portal is not None
    for index in range(3):
        result = dashboard.post(
            "/v1/sources/content", json={"scope_id": blocked, "source_id": f"extra-{index}", "content": "Input."}
        )
        assert result.status_code == 202, result.text
    dashboard.portal.call(distinguish)
    for scope, after, head, target, attempts, requests, tokens in (
        (pending, 0, 1, None, 1, 7, 222),
        (blocked, 1, 4, 4, 3, 29, 8765),
        (pending, 0, 1, None, 1, 7, 222),
    ):
        response = dashboard.get("/dashboard/topics", params={"scope": scope, "lang": "en"})
        assert response.status_code == 200, response.text
        assert f"Consumed through position: {after} · Source head position: {head}" in response.text
        for label, value in (
            ("Retained target position", "No retained target" if target is None else target),
            ("Window starts after position", after),
            ("Exact attempt end position", "Unknown" if scope == pending else 2),
            ("Cumulative allowance end position", 1 if scope == pending else 3),
            ("Recorded processing attempts", attempts),
            ("Reserved provider request allowance", requests),
            ("Reserved token capacity units", tokens),
        ):
            assert f"{label}</dt><dd>{value}</dd>" in response.text
        assert ("model_timeout" in response.text) == (scope == blocked)
