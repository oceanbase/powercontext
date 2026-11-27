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

"""Exercise passive consumption evidence through authenticated Dashboard requests."""

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, insert
from sqlalchemy.exc import OperationalError

from powercontext.builtin.artifacts.profile.models import ProfileCandidateProposal, SourceWindow
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import SOURCE_CURSORS_TABLE
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.runtime.scope_reads import ScopeReadSnapshot
from powercontext.builtin.sources import SourceCursor
from powercontext.builtin.statistics.models import ProcessingObservation
from powercontext.server.dashboard.api import DashboardAPI
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.preferences import CATALOGS
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, DashboardConfig, McpConfig, ServerSettings
from powercontext.sources import SourceRef
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_scope_selection import _grant, _revoke


def _runtime(client):
    return cast(FastAPI, client.app).state.application


def _call(client, function):
    assert client.portal is not None
    return client.portal.call(function)


def _capture(client, scope, count=3):
    for index in range(count):
        response = client.post(
            "/v1/sources/content",
            json={"scope_id": scope, "source_id": str(index), "content": "Private source content"},
        )
        assert response.status_code == 202, response.text


def _section(html):
    return html.split("data-source-consumption>", 1)[1].split("</section>", 1)[0]


def _fact(section, name):
    match = re.search(r'data-fact="' + name + r'">(\d+)</dd>', section)
    assert match is not None, section
    return int(match[1])


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("page,family", [("notes", "memory"), ("profile", "profile"), ("methods", "experience")])
def test_consumption_pending_requests_and_caught_up_are_independent(dashboard, language, page, family):
    scope = create_scope(dashboard, "Consumption evidence")["scope_id"]
    _capture(dashboard, scope)
    runtime = _runtime(dashboard)
    binding = FAMILY_BINDINGS[family]

    async def seed():
        async with runtime.primary_database.transaction() as connection:
            await SourceCursorRepository().save(
                connection, scope, binding, SourceCursor(sequence=1), expected_generation=None
            )
            intents = ArtifactProcessingIntentRepository()
            intent = await intents.request(connection, scope, binding)
            await intents.acknowledge(
                connection, scope, binding, intent.requested_generation, clean_generation=intent.dirty_generation
            )

    _call(dashboard, seed)
    params = {"scope": scope, "lang": language}
    first = dashboard.get("/dashboard/" + page, params=params)
    assert first.status_code == 200, first.text
    section = _section(first.text)
    assert binding in section
    assert 'data-input-state="pending"' in section
    assert 'data-invocation-state="acknowledged"' in section
    assert (_fact(section, "source_cursor"), _fact(section, "source_head")) == (1, 3)
    assert CATALOGS[language]["source_consumption_result_unavailable"] in section
    assert CATALOGS[language]["source_consumption_unconfigured"] in section
    assert "Private source content" not in section

    async def request():
        async with runtime.primary_database.transaction() as connection:
            await ArtifactProcessingIntentRepository().request(connection, scope, binding)

    _call(dashboard, request)
    outstanding = _section(dashboard.get("/dashboard/" + page, params=params).text)
    assert 'data-invocation-state="pending"' in outstanding
    assert (_fact(outstanding, "requested_generation"), _fact(outstanding, "handled_generation")) == (2, 1)

    async def catch_up():
        async with runtime.primary_database.transaction() as connection:
            cursor = await SourceCursorRepository().load(connection, scope, binding)
            assert cursor is not None
            await SourceCursorRepository().save(
                connection, scope, binding, SourceCursor(sequence=3), expected_generation=cursor.generation
            )
            intents = ArtifactProcessingIntentRepository()
            intent = await intents.load(connection, scope, binding)
            assert intent is not None
            await intents.acknowledge(
                connection, scope, binding, intent.requested_generation, clean_generation=intent.dirty_generation
            )

    _call(dashboard, catch_up)
    caught = _section(dashboard.get("/dashboard/" + page, params=params, headers={"HX-Request": "true"}).text)
    assert 'data-input-state="caught_up"' in caught
    assert 'data-execution-state="unavailable"' in caught
    assert CATALOGS[language]["source_consumption_result_unavailable"] in caught
    assert _fact(caught, "source_cursor") == 3


def test_scope_switching_and_bindings_keep_independent_positions(dashboard):
    scope = create_scope(dashboard, "Independent consumers")["scope_id"]
    other = create_scope(dashboard, "Empty Scope")["scope_id"]
    _capture(dashboard, scope)
    runtime = _runtime(dashboard)

    async def seed():
        async with runtime.primary_database.transaction() as connection:
            for family, position in [("memory", 1), ("profile", 2), ("experience", 3)]:
                await SourceCursorRepository().save(
                    connection,
                    scope,
                    FAMILY_BINDINGS[family],
                    SourceCursor(sequence=position),
                    expected_generation=None,
                )

    _call(dashboard, seed)
    for page, position in [("notes", 1), ("profile", 2), ("methods", 3)]:
        selected = _section(dashboard.get("/dashboard/" + page, params={"scope": scope}).text)
        assert (_fact(selected, "source_cursor"), _fact(selected, "source_head")) == (position, 3)
        switched = _section(
            dashboard.get("/dashboard/" + page, params={"scope": other}, headers={"HX-Request": "true"}).text
        )
        assert (_fact(switched, "source_cursor"), _fact(switched, "source_head")) == (0, 0)
        assert scope not in switched
        assert 'data-invocation-state="unavailable"' in switched
        assert 'data-fact="requested_generation"' not in switched
    skill = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "skill"})
    assert "data-source-consumption" not in skill.text


@pytest.mark.parametrize("failure", ["corrupt_cursor", "database"])
def test_status_failure_preserves_readable_memory(dashboard, failure):
    scope = create_scope(dashboard, "Readable content")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Readable despite unavailable status."}
    )
    assert saved.status_code == 200
    runtime = _runtime(dashboard)

    async def corrupt():
        async with runtime.primary_database.transaction() as connection:
            await connection.execute(
                insert(SOURCE_CURSORS_TABLE).values(
                    scope_id=scope,
                    binding_name=FAMILY_BINDINGS["memory"],
                    cursor=b"private invalid cursor",
                    generation=1,
                )
            )

    def broken(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "pc_source_cursors" in statement:
            raise OperationalError("private query", {}, RuntimeError("private error"))  # noqa: TRY003

    engine = runtime.primary_database.engine.sync_engine
    if failure == "corrupt_cursor":
        _call(dashboard, corrupt)
    else:
        event.listen(engine, "before_cursor_execute", broken)
    try:
        response = dashboard.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
    finally:
        if failure == "database":
            event.remove(engine, "before_cursor_execute", broken)
    assert response.status_code == 200, response.text
    assert "Readable despite unavailable status." in response.text
    section = _section(response.text)
    assert CATALOGS["en"]["source_consumption_unavailable"] in section
    assert "data-fact=" not in section
    assert "private" not in section


@pytest.mark.parametrize(
    "observation", ["running", "retry_wait", "stale", "other_binding", "unobserved", "failed", "external"]
)
def test_execution_requires_a_fresh_scope_and_binding_sample(dashboard, monkeypatch, observation):
    scope = create_scope(dashboard, "Local execution")["scope_id"]
    other = create_scope(dashboard, "Other execution")["scope_id"]
    runtime = _runtime(dashboard)

    class LocalScheduler:
        def observe_scope(self, selected):
            if observation == "failed":
                raise RuntimeError("private observer error")  # noqa: TRY003
            if selected != scope:
                return ()
            return (
                ProcessingObservation(
                    binding=FAMILY_BINDINGS["profile" if observation == "other_binding" else "memory"],
                    family="profile" if observation == "other_binding" else "memory",
                    state="retry_wait" if observation == "retry_wait" else "running",
                    observed_at=datetime.now(UTC) - timedelta(minutes=1 if observation == "stale" else 0),
                    scheduler_observed=observation != "unobserved",
                    reason="retry_backoff" if observation == "retry_wait" else "executing",
                    retry_failures=2 if observation == "retry_wait" else None,
                ),
            )

    monkeypatch.setattr(
        runtime, "artifact_processing_supervisor", None if observation == "external" else LocalScheduler()
    )
    response = dashboard.get("/dashboard/notes", params={"scope": scope})
    section = _section(response.text)
    expected = observation if observation in {"running", "retry_wait"} else "unavailable"
    assert f'data-execution-state="{expected}"' in section
    assert _fact(section, "source_cursor") == 0
    switched = _section(dashboard.get("/dashboard/notes", params={"scope": other}).text)
    assert 'data-execution-state="unavailable"' in switched
    monkeypatch.setattr(runtime, "artifact_processing_supervisor", None)
    restarted = _section(dashboard.get("/dashboard/notes", params={"scope": scope}).text)
    assert 'data-execution-state="unavailable"' in restarted
    assert _fact(restarted, "source_head") == 0


def test_profile_review_does_not_imply_consumption_or_publication(dashboard):
    scope = create_scope(dashboard, "Pending Profile")["scope_id"]
    _capture(dashboard, scope, 1)
    runtime = _runtime(dashboard)

    async def seed():
        async with runtime.primary_database.transaction() as connection:
            policies = ProfilePolicyRepository()
            policy = await policies.create(connection, scope, enabled=True, activation_mode="review_required")
            await CandidateRepository({"profile": ProfileCandidateProposal}).create(
                connection,
                scope,
                "private-candidate-id",
                "profile",
                ProfileCandidateProposal(
                    content="# Candidate",
                    source_window=SourceWindow(after=0, through=1),
                    generator_id=FAMILY_BINDINGS["profile"],
                    generator_version="1",
                    created_at=datetime.now(UTC),
                ),
                sources=(SourceRef(source_type="content", source_id="0"),),
                artifacts=(),
                target=None,
                reason=None,
            )
            await policies.update(connection, policy, pending_candidate_id="private-candidate-id")
            intents = ArtifactProcessingIntentRepository()
            intent = await intents.request(connection, scope, FAMILY_BINDINGS["profile"])
            await intents.acknowledge(connection, scope, FAMILY_BINDINGS["profile"], intent.requested_generation)

    _call(dashboard, seed)
    response = dashboard.get("/dashboard/profile", params={"scope": scope, "lang": "en"})
    section = _section(response.text)
    assert CATALOGS["en"]["source_consumption_review_pending"] in section
    assert CATALOGS["en"]["source_consumption_result_unavailable"] in section
    assert 'data-invocation-state="dirty"' in section
    assert (_fact(section, "source_cursor"), _fact(section, "handled_generation")) == (0, 1)
    assert "private-candidate-id" not in section


def test_exact_artifact_share_does_not_grant_scope_progress_and_revocation_applies(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Private processing")["scope_id"]
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={"family": "atomic-memory", "content": {"kind": "fact", "text": "Shared exact content"}},
    ).json()
    reader = {"type": "user", "id": "source-status-reader"}
    scope_grant = _grant(dashboard, reader, {"type": "scope", "scope_id": scope}, "scope.viewer")
    _grant(
        dashboard,
        reader,
        {
            "type": "artifact",
            "scope_id": scope,
            "identity": {"family": "atomic-memory", "artifact_id": created["artifact_id"]},
        },
        "artifact.viewer",
    )
    authentication = cast(FastAPI, dashboard.app).state.authentication_provider
    params = {"scope": scope, "artifact": created["artifact_id"], "revision": 1}
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        assert "data-source-consumption" in dashboard.get("/dashboard/notes", params=params).text
    _revoke(dashboard, scope_grant)
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        shared = dashboard.get("/dashboard/notes", params=params)
        assert shared.status_code == 200
        assert "Shared exact content" in shared.text
        assert "data-source-consumption" not in shared.text
        denied = dashboard.get("/dashboard/notes", params={"scope": scope})
        assert denied.status_code == 403
        assert "data-source-consumption" not in denied.text


def test_refreshes_keep_consumption_separate_from_bounded_dream_history(dashboard):
    scope = create_scope(dashboard, "Passive inspection")["scope_id"]
    _capture(dashboard, scope, 1)
    runtime = _runtime(dashboard)
    prohibited = []
    dream_reads = []

    async def topic_flush():
        async with runtime.primary_database.transaction() as connection:
            pending = await ArtifactProcessingPendingRepository().request_flush(
                connection, scope, TOPIC_MEMORY_SOURCE_WINDOW_BINDING
            )
            assert pending is not None and pending.flush_generation > pending.handled_flush_generation

    _call(dashboard, topic_flush)

    def record(connection, cursor, statement, parameters, context, executemany):
        normalized = statement.lstrip().upper()
        tables = (
            "pc_source_cursors",
            "pc_artifact_processing_",
            "pc_memory_source_windows",
            "pc_topic_memory_work_",
            "pc_candidates",
            "pc_artifacts",
            "pc_sources",
            "pc_profile_policies",
            "pc_dream_runs",
        )
        if normalized.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) and any(
            name in statement for name in tables
        ):
            prohibited.append(statement)
        if normalized.startswith("SELECT") and "pc_dream_runs" in statement:
            dream_reads.append(statement)

    engine = runtime.primary_database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        for _ in range(2):
            for page in ("notes", "profile", "methods"):
                before = len(dream_reads)
                response = dashboard.get("/dashboard/" + page, params={"scope": scope}, headers={"HX-Request": "true"})
                assert response.status_code == 200, response.text
                assert "data-source-consumption" in response.text
                assert CATALOGS["zh"]["source_consumption_not_applicable"] in _section(response.text)
                assert "data-operation-outcomes" in response.text
                # The separate outcome window may read one bounded page; it cannot walk history.
                assert len(dream_reads) - before == 1
                assert " LIMIT " in dream_reads[-1].upper()
                assert "pc_dream_runs.operation =" in dream_reads[-1]
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not prohibited


def test_status_audit_failure_does_not_publish_status_or_hide_content(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Audited status")["scope_id"]
    response = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Independently audited content"}
    )
    assert response.status_code == 200
    audit = cast(FastAPI, dashboard.app).state.access_control.audit
    original = audit.append_audits

    async def fail_status(events):
        if any(event.operation == "dashboard_source_consumption" for event in events):
            raise RuntimeError
        return await original(events)

    monkeypatch.setattr(audit, "append_audits", fail_status)
    page = dashboard.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
    assert page.status_code == 200, page.text
    assert "Independently audited content" in page.text
    section = _section(page.text)
    assert CATALOGS["en"]["source_consumption_unavailable"] in section
    assert "data-fact=" not in section


def test_runtime_restart_retains_progress_without_local_execution(tmp_path, monkeypatch):
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"),
        dashboard=DashboardConfig(enabled=True),
        mcp=McpConfig(enabled=False),
        access=AccessControlConfig(mode="disabled"),
    )
    with TestClient(create_server_app(settings=settings)) as first:
        scope = create_scope(first, "Restart progress")["scope_id"]
        _capture(first, scope, 2)
        runtime = _runtime(first)

        async def seed():
            async with runtime.primary_database.transaction() as connection:
                await SourceCursorRepository().save(
                    connection, scope, FAMILY_BINDINGS["memory"], SourceCursor(sequence=1), expected_generation=None
                )
                await ArtifactProcessingIntentRepository().request(connection, scope, FAMILY_BINDINGS["memory"])

        _call(first, seed)

        class LocalScheduler:
            def observe_scope(self, selected):
                return (
                    (
                        ProcessingObservation(
                            binding=FAMILY_BINDINGS["memory"],
                            family="memory",
                            state="running",
                            scheduler_observed=True,
                            observed_at=datetime.now(UTC),
                        ),
                    )
                    if selected == scope
                    else ()
                )

        with monkeypatch.context() as patch:
            patch.setattr(runtime, "artifact_processing_supervisor", LocalScheduler())
            before = _section(first.get("/dashboard/notes", params={"scope": scope}).text)
            assert 'data-execution-state="running"' in before
    with TestClient(create_server_app(settings=settings)) as second:
        after = _section(second.get("/dashboard/notes", params={"scope": scope}).text)
        assert (_fact(after, "source_cursor"), _fact(after, "source_head")) == (1, 2)
        assert 'data-invocation-state="pending"' in after
        assert 'data-execution-state="unavailable"' in after


def test_experience_historical_read_keeps_current_scope_status_separate(dashboard):
    scope = create_scope(dashboard, "Experience status")["scope_id"]
    content = {
        "situation": "Reading history",
        "action": "Keep exact references",
        "outcome": "The saved revision remains readable",
        "lesson": "Original lesson",
    }
    created = dashboard.post(f"/v1/scopes/{scope}/artifacts", json={"family": "experience", "content": content})
    assert created.status_code == 201, created.text
    artifact = created.json()["artifact_id"]
    path = f"/v1/scopes/{scope}/artifacts/experience/{artifact}"
    head = dashboard.get(path)
    changed = dashboard.put(
        path, json={"content": {**content, "lesson": "Later lesson"}}, headers={"If-Match": head.headers["etag"]}
    )
    assert changed.status_code == 200, changed.text
    page = dashboard.get(
        "/dashboard/experience", params={"scope": scope, "artifact": artifact, "revision": 1, "lang": "en"}
    )
    assert page.status_code == 200, page.text
    assert "Original lesson" in page.text
    section = _section(page.text)
    assert FAMILY_BINDINGS["experience"] in section
    assert CATALOGS["en"]["source_consumption_scope_notice"] in section
    assert "Original lesson" not in section and "Later lesson" not in section
    assert CATALOGS["en"]["source_consumption_candidate_notice"] in section


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("page,family", [("notes", "memory"), ("profile", "profile"), ("methods", "experience")])
def test_cursor_ahead_of_head_is_unavailable(dashboard, language, page, family):
    scope = create_scope(dashboard, "Inconsistent progress")["scope_id"]
    _capture(dashboard, scope, 1)
    runtime = _runtime(dashboard)

    async def seed():
        async with runtime.primary_database.transaction() as connection:
            await SourceCursorRepository().save(
                connection, scope, FAMILY_BINDINGS[family], SourceCursor(sequence=2), expected_generation=None
            )

    _call(dashboard, seed)
    response = dashboard.get("/dashboard/" + page, params={"scope": scope, "lang": language})
    assert response.status_code == 200, response.text
    section = _section(response.text)
    assert 'data-input-state="unavailable"' in section
    assert (_fact(section, "source_cursor"), _fact(section, "source_head")) == (2, 1)
    assert CATALOGS[language]["source_consumption_input_unavailable"] in section
    assert CATALOGS[language]["source_consumption_caught_up"] not in section
    assert CATALOGS[language]["source_consumption_pending"] not in section


@pytest.mark.parametrize("language", ["en", "zh"])
def test_configured_profile_without_local_executor_stays_distinct_from_unconfigured(tmp_path, language):
    class PassiveGenerator:
        async def generate(self, value):
            pytest.fail("Dashboard reads must not invoke the configured generator")

    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'configured.db'}"),
        dashboard=DashboardConfig(enabled=True),
        mcp=McpConfig(enabled=False),
        access=AccessControlConfig(mode="disabled"),
    )
    with TestClient(create_server_app(settings=settings, profile_generator=PassiveGenerator())) as client:
        scope = create_scope(client, "Configured Profile")["scope_id"]
        _capture(client, scope, 1)
        for _ in range(2):
            response = client.get("/dashboard/profile", params={"scope": scope, "lang": language})
            assert response.status_code == 200, response.text
            section = _section(response.text)
            assert CATALOGS[language]["source_consumption_unconfigured"] not in section
            assert CATALOGS[language]["source_consumption_configuration_unknown"] not in section
            assert CATALOGS[language]["source_consumption_profile_disabled"] in section
            assert 'data-execution-state="unavailable"' in section
            assert 'data-input-state="pending"' in section


def test_injected_runtime_without_capability_declaration_reports_configuration_unknown(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Unknown configuration")["scope_id"]
    runtime = _runtime(dashboard)
    injected = BuiltinRuntime(
        provider=runtime._provider,
        capabilities=runtime._capabilities,
        primary_database=runtime.primary_database,
        atomic_memory_application=runtime.atomic_memory._application,
        statistics_service=runtime._statistics_service,
        record_service=runtime._records(),
        scope_application=runtime.scopes,
    )
    try:
        app = cast(FastAPI, dashboard.app)
        with monkeypatch.context() as patch:
            patch.setattr(app.state, "application", injected)
            patch.setattr(app.state, "dashboard_runtime", injected)
            response = dashboard.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
            assert response.status_code == 200, response.text
            section = _section(response.text)
            assert CATALOGS["en"]["source_consumption_configuration_unknown"] in section
            assert CATALOGS["en"]["source_consumption_unconfigured"] not in section
            assert CATALOGS["en"]["source_consumption_unsupported"] not in section
            assert _fact(section, "source_head") == 0
    finally:
        _call(dashboard, injected.close)


def test_status_authentication_failure_returns_login_instead_of_unavailable(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Expired status authentication")["scope_id"]
    original = DashboardAPI.project

    async def expired(self, selected, operation, **params):
        if operation == "source_consumption":
            raise ReadError(401, "unauthorized")
        return await original(self, selected, operation, **params)

    monkeypatch.setattr(DashboardAPI, "project", expired)
    response = dashboard.get("/dashboard/notes", params={"scope": scope}, follow_redirects=False)
    assert response.status_code == 401
    assert 'name="token"' in response.text
    assert "data-source-consumption" not in response.text


def test_status_timeout_cancels_read_and_preserves_content(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Bounded status read")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Readable after status timeout."}
    )
    assert saved.status_code == 200, saved.text
    cancelled = Event()
    original = ScopeReadSnapshot.source_consumption

    async def slow(self, family, *, context=None):
        try:
            await asyncio.sleep(6)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return await original(self, family, context=context)

    monkeypatch.setattr(ScopeReadSnapshot, "source_consumption", slow)
    response = dashboard.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
    assert response.status_code == 200, response.text
    assert cancelled.is_set()
    assert "Readable after status timeout." in response.text
    section = _section(response.text)
    assert CATALOGS["en"]["source_consumption_unavailable"] in section
    assert CATALOGS["en"]["source_consumption_unsupported"] not in section
    assert "data-fact=" not in section


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("page", ["notes", "profile", "methods", "experience"])
def test_public_read_fallback_reports_unsupported_status_without_hiding_content(dashboard, monkeypatch, language, page):
    scope = create_scope(dashboard, "Public read fallback")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Content through public reads."}
    )
    assert saved.status_code == 200, saved.text
    experience = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "experience",
            "content": {"situation": "Fallback", "action": "Read", "outcome": "Visible", "lesson": "Saved lesson"},
        },
    )
    assert experience.status_code == 201, experience.text
    monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    params = {"scope": scope, "lang": language}
    if page == "experience":
        params.update(artifact=experience.json()["artifact_id"], revision="1")
    response = dashboard.get("/dashboard/" + page, params=params)
    assert response.status_code == 200, response.text
    section = _section(response.text)
    assert CATALOGS[language]["source_consumption_unsupported"] in section
    assert CATALOGS[language]["source_consumption_unavailable"] not in section
    assert "data-fact=" not in section
    if page == "notes":
        assert "Content through public reads." in response.text
    elif page in {"methods", "experience"}:
        assert "Saved lesson" in response.text


def test_empty_experience_collection_has_status_and_missing_detail_is_not_a_saved_experience(dashboard):
    scope = create_scope(dashboard, "Empty Experience")["scope_id"]
    collection = dashboard.get("/dashboard/methods", params={"scope": scope, "lang": "en"})
    assert collection.status_code == 200, collection.text
    section = _section(collection.text)
    assert FAMILY_BINDINGS["experience"] in section
    assert 'data-invocation-state="unavailable"' in section
    assert CATALOGS["en"]["source_consumption_candidate_notice"] in section
    detail = dashboard.get("/dashboard/experience", params={"scope": scope, "lang": "en"})
    assert detail.status_code == 404, detail.text
    assert "data-source-consumption" not in detail.text


def test_oceanbase_api_role_reads_durable_progress_without_observing_external_workers():
    url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not url:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated test database")
    settings = ServerSettings(
        database=OceanBaseConfig(url=SecretStr(url)),
        runtime=RuntimeConfig(artifact_processing_role="api", artifact_processing_families=("memory",)),
        dashboard=DashboardConfig(enabled=True),
        mcp=McpConfig(enabled=False),
        access=AccessControlConfig(mode="disabled"),
    )
    with TestClient(create_server_app(settings=settings)) as client:
        scope = create_scope(client, f"External processing capability {uuid4().hex}")["scope_id"]
        _capture(client, scope, 2)
        runtime = _runtime(client)

        async def seed():
            async with runtime.primary_database.transaction() as connection:
                await SourceCursorRepository().save(
                    connection, scope, FAMILY_BINDINGS["memory"], SourceCursor(sequence=1), expected_generation=None
                )
                await ArtifactProcessingIntentRepository().request(connection, scope, FAMILY_BINDINGS["memory"])

        _call(client, seed)
        response = client.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
        assert response.status_code == 200, response.text
        section = _section(response.text)
        assert (_fact(section, "source_cursor"), _fact(section, "source_head")) == (1, 2)
        assert 'data-input-state="pending"' in section
        assert 'data-invocation-state="pending"' in section
        assert 'data-execution-state="unavailable"' in section
        assert CATALOGS["en"]["source_consumption_unconfigured"] not in section
        assert CATALOGS["en"]["source_consumption_unsupported"] not in section
