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

"""Check automatic processing evidence through authenticated Dashboard requests."""

import asyncio
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, insert

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    ARTIFACTS_TABLE,
    SOURCES_TABLE,
    TOPIC_MEMORY_WORK_BLOCKS_TABLE,
    TOPIC_MEMORY_WORK_BUDGETS_TABLE,
)
from powercontext.builtin.processing_status import ScopedProcessingStatusApplication
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


def family_detail(html, family):
    match = re.search(rf'<details[^>]*data-auto-family="{family}"[^>]*>(.*?)</details>', html, re.S)
    assert match is not None, html
    return match[1]


@pytest.mark.parametrize("language", ["en", "zh"])
def test_automatic_processing_shows_consumption_separately_from_outputs(dashboard, language):
    scope = create_scope(dashboard, "Consumption evidence")["scope_id"]
    saved = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Saved input remains readable."})
    assert saved.status_code == 201
    writes = []
    engine = dashboard.app.state.application.primary_database.engine.sync_engine

    def capture(connection, cursor, statement, parameters, context, executemany):
        if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", statement, re.I):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = dashboard.get(
            "/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": language}
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    # Audit records are permitted; inspecting status must not mutate business data.
    for table in (SOURCES_TABLE, ARTIFACTS_TABLE, ARTIFACT_PROCESSING_INTENTS_TABLE, TOPIC_MEMORY_WORK_BUDGETS_TABLE):
        assert not any(table.name in statement for statement in writes)
    assert page.status_code == 200
    for family in ("memory", "topic-memory", "profile", "experience"):
        detail = family_detail(page.text, family)
        assert 'data-source-cursor="0"' in detail
        assert 'data-source-head="1"' in detail
        assert 'action="/dashboard/processing/retry' not in detail
        visible_detail = detail.split('<details class="automatic-evidence-panel">')[0]
        assert 'data-source-cursor="0"' in visible_detail
        assert 'data-processing-time="unknown"' in visible_detail
    assert "Saved input remains readable." in page.text
    assert ("Related artifacts" if language == "en" else "关联产物") in page.text
    detail = dashboard.get(
        "/dashboard/processing",
        params={"scope": scope, "record_type": "sources", "source_id": saved.json()["source_id"]},
    )
    assert detail.status_code == 200
    assert "Saved input remains readable." in detail.text
    assert "data-auto-family" not in detail.text


def test_one_unavailable_processing_family_keeps_sources_and_other_statuses(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Partial observation")["scope_id"]
    dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Independent source content."})
    original = ScopedProcessingStatusApplication.status

    async def unavailable(self, family, **kwargs):
        if family == "profile":
            raise RuntimeError("private database connection details")  # noqa: TRY003 - intentional privacy sentinel
        return await original(self, family, **kwargs)

    monkeypatch.setattr(ScopedProcessingStatusApplication, "status", unavailable)
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
    assert page.status_code == 200
    assert "Independent source content." in page.text
    assert 'data-source-head="1"' in family_detail(page.text, "memory")
    assert "Unavailable" in family_detail(page.text, "profile")
    assert "private database connection details" not in page.text


@pytest.mark.parametrize(
    ("persisted", "last_error"),
    [(False, None), (True, None), (True, "inference_timeout"), (True, "private-error-secret")],
)
def test_normal_automatic_entry_explains_topic_block_without_recovery_actions(dashboard, persisted, last_error):
    scope = create_scope(dashboard, "Retained block")["scope_id"]
    dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Input behind a retained block."})

    async def retain_block():
        async with dashboard.app.state.application.primary_database.transaction() as connection:
            await connection.execute(
                insert(TOPIC_MEMORY_WORK_BUDGETS_TABLE).values(
                    scope_id=scope,
                    binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                    source_after=0,
                    source_through=1,
                    attempt_id="attempt-1",
                    attempt_source_through=1,
                    attempts=3,
                    requests=4,
                    tokens=120,
                    failure_code="",
                )
            )
            if persisted:
                await connection.execute(
                    insert(TOPIC_MEMORY_WORK_BLOCKS_TABLE).values(
                        scope_id=scope,
                        binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        block_id="block-1",
                        attempt_id="attempt-1",
                        source_after=0,
                        source_through=1,
                        budget_source_through=1,
                        target_source_through=1,
                        attempts=3,
                        requests=4,
                        tokens=120,
                        error_code="window_attempt_limit",
                        last_error_code=last_error,
                        blocked_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
                    )
                )

    asyncio.run(retain_block())
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
    assert page.status_code == 200
    detail = family_detail(page.text, "topic-memory")
    assert "Blocked" in detail
    assert "Generation" in detail
    assert ("2026-01-01" in detail) is persisted
    visible_detail = detail.split('<details class="automatic-evidence-panel">')[0]
    if persisted:
        assert 'data-processing-time="blocked"' in visible_detail
        assert 'datetime="2026-01-01T00:00:00+00:00"' in visible_detail
    else:
        assert 'data-processing-time="unknown"' in visible_detail
    assert ("The last model request timed out" in detail) == (last_error == "inference_timeout")
    assert "private-error-secret" not in page.text
    assert "Input behind a retained block." in page.text
    assert "/dashboard/processing/retry" not in page.text


def test_non_builtin_runtime_keeps_sources_when_status_is_unsupported(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Public reads")["scope_id"]
    dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Public source remains readable."})
    monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
    assert page.status_code == 200
    assert "Public source remains readable." in page.text
    assert "Unavailable" in family_detail(page.text, "memory")


def test_inspection_enforces_scope_read_before_returning_evidence(dashboard, monkeypatch):
    from powercontext.builtin.authz import AccessDeniedError
    from powercontext.builtin.runtime.atomic_memory import AtomicMemoryAccess

    scope = create_scope(dashboard, "Restricted inspection")["scope_id"]
    dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Independently authorized source."})
    original = AtomicMemoryAccess.authorize

    async def denied(self, connection, scope_id, context, action, ref=None):
        if context is not None and context.audit.operation == "dashboard_processing_status":
            raise AccessDeniedError
        return await original(self, connection, scope_id, context, action, ref)

    monkeypatch.setattr(AtomicMemoryAccess, "authorize", denied)
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
    assert page.status_code == 200
    assert "Independently authorized source." in page.text
    assert "Unavailable" in family_detail(page.text, "memory")
    assert "data-source-cursor" not in page.text


def test_processing_evidence_is_scoped_and_audit_failure_reveals_nothing(dashboard, monkeypatch):
    first = create_scope(dashboard, "First observation")["scope_id"]
    second = create_scope(dashboard, "Second observation")["scope_id"]
    dashboard.post(f"/v1/scopes/{first}/sources", json={"content": "Only first scope."})
    for scope, head in ((first, 1), (second, 0)):
        page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources"})
        assert page.status_code == 200
        assert f'data-source-head="{head}"' in family_detail(page.text, "memory")
        assert ("Only first scope." in page.text) == (scope == first)

    async def unavailable(events):
        raise RuntimeError

    monkeypatch.setattr(dashboard.app.state.access_control.audit, "append_audits", unavailable)
    page = dashboard.get("/dashboard/processing", params={"scope": first, "record_type": "sources"})
    assert page.status_code == 503
    assert "Only first scope." not in page.text
    assert "data-auto-family" not in page.text
