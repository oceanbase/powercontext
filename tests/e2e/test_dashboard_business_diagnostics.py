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

"""Reopen durable processing and recall evidence through independent detail pages."""

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    SOURCE_JOURNAL_HEADS_TABLE,
    TOPIC_MEMORY_WORK_BLOCKS_TABLE,
    TOPIC_MEMORY_WORK_BUDGETS_TABLE,
)
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.sources import SourceCursor
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, DashboardConfig, McpConfig, ServerSettings
from tests.test_dashboard import create_scope
from tests.test_dashboard_context_diagnostics import detail_page, latest_record_id, prepare
from tests.test_dashboard_processing_status import family_detail


def test_source_gap_and_unselected_memory_remain_distinct_after_runtime_reopen(tmp_path):
    settings = ServerSettings(
        workspace=tmp_path,
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/diagnostics.db"),
        dashboard=DashboardConfig(enabled=True),
        access=AccessControlConfig(mode="disabled"),
        mcp=McpConfig(enabled=False),
        runtime=RuntimeConfig(dream_enabled=False, profile_schedule_enabled=False, artifact_processing_families=()),
    )

    def app():
        return create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")

    server = app()
    with TestClient(server) as client:
        scope = create_scope(client, "Durable diagnostic scope")["scope_id"]
        empty = create_scope(client, "Empty diagnostic scope")["scope_id"]
        source = client.post(f"/v1/scopes/{scope}/sources", json={"content": "Input with no proven output."})
        assert source.status_code == 201
        remembered = client.post(
            "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "The budget review is Thursday."}
        )
        assert remembered.status_code == 200

        async def recorded_state():
            async with server.state.application.primary_database.transaction() as connection:
                # Remember also captures provenance; seed the actual frontier,
                # rather than assuming that the explicit Source is its only entry.
                head = await connection.scalar(
                    select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope)
                )
                assert head is not None
                await SourceCursorRepository().save(
                    connection, scope, FAMILY_BINDINGS["memory"], SourceCursor(sequence=head), expected_generation=None
                )
                values = {
                    "scope_id": scope,
                    "binding_name": TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                    "source_after": 0,
                    "source_through": head,
                    "attempt_id": "retained-attempt",
                    "attempts": 3,
                    "requests": 4,
                    "tokens": 120,
                }
                await connection.execute(
                    insert(TOPIC_MEMORY_WORK_BUDGETS_TABLE).values(
                        **values,
                        attempt_source_through=head,
                        failure_code="",
                    )
                )
                await connection.execute(
                    insert(TOPIC_MEMORY_WORK_BLOCKS_TABLE).values(
                        **values,
                        block_id="retained-block",
                        budget_source_through=head,
                        target_source_through=head,
                        error_code="window_attempt_limit",
                        last_error_code="inference_timeout",
                        blocked_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
                    )
                )
                return head

        head = asyncio.run(recorded_state())
        prepare(client, scope)
        from powercontext.builtin.persistence.context_prepare_diagnostics import ContextPrepareDiagnosticsRepository

        async def saved_diagnostic():
            async with server.state.application.primary_database.transaction() as connection:
                rows, _ = await ContextPrepareDiagnosticsRepository().list_page(connection, scope)
                return rows[0]["record_id"]

        assert client.portal is not None
        record_id = client.portal.call(saved_diagnostic)
        inspected = detail_page(client, scope, record_id)
        assert inspected.status_code == 200
        assert "data-selected-reference" in inspected.text
        unselected = prepare(client, scope, query="zxqv_no_matching_memory_349182")
        assert unselected["status"] == "empty"
        assert "Not retrieved" in detail_page(client, scope, latest_record_id(client, scope)).text

    # A fresh application/runtime reopens the same durable SQLite database. No
    # supervisor observations are transferred.
    with TestClient(app()) as client:
        status = client.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
        assert status.status_code == 200
        assert "Input with no proven output." in status.text
        memory = family_detail(status.text, "memory")
        assert f'data-source-cursor="{head}"' in memory
        assert f'data-source-head="{head}"' in memory
        assert "Unobserved" in memory
        topic = family_detail(status.text, "topic-memory")
        assert "Blocked" in topic
        assert "2026-01-01" in topic
        assert "The last model request timed out" in topic
        assert 'data-processing-time="blocked"' in topic
        fresh = client.get("/dashboard/context-diagnostics", params={"scope": scope, "lang": "en"})
        assert fresh.status_code == 200
        assert "data-selected-reference" not in fresh.text
        assert "The budget review is Thursday." not in fresh.text
        automatic = detail_page(client, scope, record_id)
        assert automatic.status_code == 200
        assert "The budget review is Thursday." in automatic.text
        assert "data-selected-reference" in automatic.text
        switched = client.get("/dashboard/processing", params={"scope": empty, "record_type": "sources"})
        assert switched.status_code == 200
        assert 'data-source-head="0"' in family_detail(switched.text, "memory")
        assert "Input with no proven output." not in switched.text
        assert "2026-01-01" not in switched.text
