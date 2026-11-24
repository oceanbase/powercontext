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

"""Exercise passive inspection and Dashboard disclosure through public HTTP reads."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, insert
from starlette.middleware import Middleware

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.inference import InferenceTimeoutError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BLOCKS_TABLE
from powercontext.builtin.processing_status import ScopedProcessingStatusApplication
from powercontext.builtin.runtime import CaptureSource
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
)
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.statistics.models import ProcessingObservation
from powercontext.client import PowerContextClient
from powercontext.http import ArtifactProcessingStatusResponse, GetArtifactProcessingStatusRequest
from powercontext.server.app import create_app
from powercontext.server.dashboard.api import DashboardAPI, ReadError
from powercontext.server.dashboard.routes import router
from powercontext.server.middleware import StaticBearerMiddleware
from tests.builtin.runtime.test_processing_scheduler import _Launcher, _request, _wait
from tests.builtin.test_processing_status import _run_record, _runtime, _topic_budget
from tests.e2e.test_access_control_regressions import _grant, _resource, _scope, _server
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_profile_handoff import _profile


@pytest.mark.parametrize(
    ("page", "params", "family"),
    [
        ("notes", {}, "atomic-memory"),
        ("topics", {}, "topic-memory"),
        ("methods", {"kind": "experience"}, "experience"),
        ("methods", {"kind": "skill"}, "skill"),
        ("profile", {}, "profile"),
        ("handoff", {}, "handoff"),
        ("prompts", {}, "prompt"),
    ],
)
def test_empty_artifact_pages_offer_independent_status(dashboard, page, params, family):
    scope = create_scope(dashboard, "Processing scope")["scope_id"]
    response = dashboard.get(f"/dashboard/{page}", params={"scope": scope, "lang": "en", **params})
    assert response.status_code == 200, response.text
    assert f'data-family="{family}"' in response.text
    assert "data-artifact-processing" in response.text
    status = dashboard.get(
        "/dashboard/artifact-processing-status", params={"scope": scope, "family": family, "lang": "en"}
    )
    assert status.status_code == 200, status.text
    assert status.headers["cache-control"] == "no-store"
    assert "Current execution state is unavailable" in status.text
    assert "No associated operation records found" in status.text
    assert "data-processing-drawer" in status.text
    assert "Dream operations: " in status.text


@pytest.mark.parametrize(
    ("family", "page", "lang", "label"),
    [
        ("topic-memory", "topics", "zh", "主题记忆"),
        ("topic-memory", "topics", "en", "Topic Memory"),
        ("prompt", "prompts", "zh", "提示词"),
        ("prompt", "prompts", "en", "Prompts"),
        ("memory", "notes", "zh", "记忆"),
    ],
)
def test_processing_summary_and_drawer_localize_family(dashboard, family, page, lang, label):
    scope = create_scope(dashboard, "Localized status")["scope_id"]
    for path, extra in [(f"/dashboard/{page}", {}), ("/dashboard/artifact-processing-status", {"family": family})]:
        response = dashboard.get(path, params={"scope": scope, "lang": lang, **extra})
        assert response.status_code == 200
        assert f"{scope} · {label}<br>" in response.text


@pytest.mark.parametrize(
    ("stored_code", "public_reason", "en_reason", "zh_reason"),
    [
        ("window_attempt_limit", "usage_limit", "Processing usage limit reached", "处理用量达到限制"),
        (
            "source_complexity_limit",
            "source_complexity_limit",
            "Source complexity limit reached",
            "Source 复杂度达到上限",
        ),
        ("related_history_limit", "related_history_limit", "Related history limit reached", "关联历史记录达到上限"),
        ("private-provider-detail", None, "No safe reason available", "未提供安全原因"),
    ],
)
def test_retained_block_preserves_its_reason_and_window(tmp_path, stored_code, public_reason, en_reason, zh_reason):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, (scope, _)):
            sources = runtime.sources.for_scope(scope)
            for number in [1, 2]:
                await sources.capture(CaptureSource(source_id=str(number), content="Before block", metadata={}))
            async with runtime.primary_database.transaction() as connection:
                await _topic_budget(connection, scope)
                await connection.execute(
                    insert(TOPIC_MEMORY_WORK_BLOCKS_TABLE).values(
                        block_id="retained",
                        attempt_id="attempt-1",
                        scope_id=scope,
                        binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        source_after=0,
                        source_through=1,
                        budget_source_through=1,
                        target_source_through=2,
                        attempts=3,
                        requests=4,
                        tokens=120,
                        error_code=stored_code,
                        blocked_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
            await sources.capture(CaptureSource(source_id="3", content="After block", metadata={}))
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                params = {"family": "topic-memory"}
                result = await client.get(f"/v1/scopes/{scope}/processing-status", params=params)
                result.raise_for_status()
                status = result.json()
                assert status["progress"]["target_source_through"] is None and status["progress"]["source_head"] == 3
                block = status["block"]
                assert block["reason"] == public_reason
                assert status["evidence"]["block"]["binding"] == TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                assert "private-provider-detail" not in result.text
                assert block["persisted"] and (block["source_after"], block["source_through"]) == (0, 1)
                assert (block["budget_source_through"], block["target_source_through"]) == (1, 2)
                html = await client.get(
                    "/dashboard/artifact-processing-status", params={**params, "scope": scope, "lang": "en"}
                )
                html.raise_for_status()
                zh = await client.get(
                    "/dashboard/artifact-processing-status", params={**params, "scope": scope, "lang": "zh"}
                )
                zh.raise_for_status()
                assert en_reason in html.text and zh_reason in zh.text
                assert "private-provider-detail" not in html.text + zh.text
                assert "after 0, through 1 (inclusive)" in html.text
                assert (
                    "Target for this block: 2" in html.text
                    and "Budget accounted through Source position 1" in html.text
                )

    asyncio.run(scenario())


def test_unsupported_family_explains_missing_evidence(dashboard):
    scope = create_scope(dashboard, "Unsupported family")["scope_id"]
    response = dashboard.get(
        "/dashboard/artifact-processing-status", params={"scope": scope, "family": "unknown-plugin", "lang": "en"}
    )
    assert response.status_code == 200
    section = response.text.split("Evidence origin</h3>", 1)[1].split("</section>")[0]
    assert "<p>Unavailable</p>" in section


def test_empty_topic_scope_has_no_block_provenance(dashboard):
    scope = create_scope(dashboard, "No topic work")["scope_id"]
    response = dashboard.get(f"/v1/scopes/{scope}/processing-status", params={"family": "topic-memory"})
    assert response.status_code == 200
    value = response.json()
    assert value["block"] is None and value["availability"]["block"] == "available"
    assert "block" not in value["evidence"]
    assert value["invocation"] is None
    for name in ("progress", "dream"):
        assert value["evidence"][name]["binding"] == TOPIC_MEMORY_SOURCE_WINDOW_BINDING
    html = dashboard.get(
        "/dashboard/artifact-processing-status", params={"scope": scope, "family": "topic-memory", "lang": "en"}
    )
    assert html.status_code == 200
    assert "Read-only budget projection" not in html.text


def test_source_changes_and_requested_invocations_have_distinct_labels(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, (scope, _)):
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                capture = await client.post(f"/v1/scopes/{scope}/sources", json={"content": "New input"})
                capture.raise_for_status()
                for state in ("dirty", "pending"):
                    if state == "pending":
                        async with runtime.primary_database.transaction() as connection:
                            await ArtifactProcessingIntentRepository().request(
                                connection, scope, FAMILY_BINDINGS["memory"]
                            )
                    status = await client.get(
                        f"/v1/scopes/{scope}/processing-status", params={"family": "atomic-memory"}
                    )
                    status.raise_for_status()
                    assert status.json()["invocation"]["state"] == state
                    for lang, dirty, pending in [
                        (
                            "en",
                            "Input changes are waiting to be processed.",
                            "A processing request is awaiting acknowledgement.",
                        ),
                        ("zh", "有尚未处理的输入变更。", "处理请求正在等待确认。"),
                    ]:
                        fragment = await client.get(
                            "/dashboard/artifact-processing-status",
                            params={"scope": scope, "family": "atomic-memory", "lang": lang},
                        )
                        fragment.raise_for_status()
                        summary, drawer = fragment.text.split("data-processing-drawer", 1)
                        expected, other = (dirty, pending) if state == "dirty" else (pending, dirty)
                        assert expected in summary and expected in drawer
                        assert other not in fragment.text

    asyncio.run(scenario())


def test_dream_failure_diagnostics_reach_the_api_and_drawer(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, (scope, _)):
            async with runtime.primary_database.transaction() as connection:
                await _run_record(connection, scope, 1, status="failed", error="budget_exceeded")
                await _run_record(connection, scope, 2, status="failed")
                await _run_record(connection, scope, 3, status="failed", error="artifact_conflict")
                await _run_record(connection, scope, 4, status="failed", error="access_revoked")
                await _run_record(connection, scope, 5, status="failed", error="evidence_unavailable")
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/v1/scopes/{scope}/processing-status", params={"family": "skill"})
                assert response.status_code == 200
                evidence, revoked, conflict, unknown, budget = response.json()["dream_operations"]
                assert (evidence["reason"], evidence["stage"]) == ("evidence_unavailable", "business_validation")
                assert (revoked["reason"], revoked["stage"]) == ("access_revoked", None)
                assert (conflict["reason"], conflict["stage"]) == ("artifact_conflict", None)
                assert (budget["reason"], budget["stage"]) == ("usage_limit", "generation")
                assert (unknown["reason"], unknown["stage"]) == (None, None)
                html = await client.get(
                    "/dashboard/artifact-processing-status", params={"scope": scope, "family": "skill", "lang": "en"}
                )
                assert html.status_code == 200
                assert "Processing usage limit reached" in html.text
                assert "Failure stage" in html.text and "Generation</p>" in html.text
                assert "Artifact conflict" in html.text and "Access was revoked" in html.text
                zh = await client.get(
                    "/dashboard/artifact-processing-status", params={"scope": scope, "family": "skill", "lang": "zh"}
                )
                zh.raise_for_status()
                assert "产物发生冲突" in zh.text and "访问权限已撤销" in zh.text
                assert "private-error-text" not in response.text + html.text + zh.text

    asyncio.run(scenario())


def test_generator_form_does_not_load_collection_status(dashboard):
    scope = create_scope(dashboard, "Generation scope")["scope_id"]
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "generate": "1", "kind": "skill"})
    assert response.status_code == 200
    assert "data-artifact-processing" not in response.text


def test_profile_status_is_only_shown_once_for_the_current_view(dashboard):
    scope = create_scope(dashboard, "Profile processing")["scope_id"]
    _profile(dashboard, scope, "A saved profile.")
    current = dashboard.get("/dashboard/profile", params={"scope": scope})
    assert current.status_code == 200
    assert current.text.count("data-artifact-processing") == 1
    for params in [{"view": "history"}, {"revision": "1"}]:
        historical = dashboard.get("/dashboard/profile", params={"scope": scope, **params})
        assert historical.status_code == 200
        assert "data-artifact-processing" not in historical.text


def test_http_evidence_is_passive_bounded_and_keeps_independent_paths(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            scope, other = scopes
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="one", content="private input", metadata={})
            )
            async with runtime.primary_database.transaction() as connection:
                await _topic_budget(connection, scope)
                # Tags target the Topic family; a queued run must not hide its independent Source block.
                await _run_record(connection, scope, 0, "revise_tags", "queued", tag_family="topic-memory")
                await _run_record(connection, scope, 1, status="failed")
                for index in range(2, 14):
                    await _run_record(connection, scope, index)
                await _run_record(connection, other, 99)
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            statements = []

            def capture(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            engine = runtime.primary_database.engine.sync_engine
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                event.listen(engine, "before_cursor_execute", capture)
                try:
                    topic = await client.get(f"/v1/scopes/{scope}/processing-status", params={"family": "topic-memory"})
                    html = await client.get(
                        "/dashboard/artifact-processing-status",
                        params={"scope": scope, "family": "topic-memory", "lang": "en"},
                    )
                    skill = await client.get(f"/v1/scopes/{scope}/processing-status", params={"family": "skill"})
                finally:
                    event.remove(engine, "before_cursor_execute", capture)
                assert topic.status_code == 200, topic.text
                result = ArtifactProcessingStatusResponse.model_validate(topic.json())
                assert result.state == "pending" and result.block is not None
                assert not result.block.persisted and result.block.block_id is None
                assert result.dream_pending is True
                assert result.progress is not None
                assert result.progress.source_cursor == 0 and result.progress.source_head == 1
                assert result.executor is None and result.availability["executor"] == "unavailable"
                assert html.status_code == 200, html.text
                assert "The Source path is blocked" in html.text and "queued or running operations" in html.text
                assert "Block projected from existing budget records" in html.text
                assert skill.status_code == 200, skill.text
                value = skill.json()
                assert len(value["dream_operations"]) == 10
                assert value["dream_operations"][0]["run_id"] == "dr_13"
                assert value["dream_operations"][0]["outcome"] == "no_change"
                assert value["block"] is None and value["availability"]["progress"] == "not_applicable"
                assert "private input" not in topic.text + html.text + skill.text
                assert "private-error-text" not in skill.text
                assert "dr_99" not in skill.text
                assert all(
                    not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                    and "FOR UPDATE" not in sql.upper()
                    for sql in statements
                )
                unsupported = await client.get(f"/v1/scopes/{scope}/processing-status", params={"family": "custom"})
                assert (
                    unsupported.status_code == 200
                    and unsupported.json()["unavailable_reason"] == "family_not_supported"
                )
                sdk = PowerContextClient("https://test", http_client=client)
                reading = await sdk.get_artifact_processing_status(
                    scope, GetArtifactProcessingStatusRequest(family="memory")
                )
                assert reading.family == "memory" and reading.scope_id == scope
                path = f"/v1/scopes/{scope}/processing-status"
                responses = app.openapi()["paths"]["/v1/scopes/{scope_id}/processing-status"]["get"]["responses"]
                for params, expected in [
                    ({}, 400),
                    ({"family": ""}, 400),
                    ({"family": "x" * 129}, 400),
                    ((("family", "skill"), ("family", "skill")), 422),
                    ({"family": "skill", "extra": "x"}, 422),
                ]:
                    invalid = await client.get(path, params=params)
                    assert invalid.status_code == expected and str(expected) in responses
                    assert invalid.json()["error"]["code"] == "invalid_request"
                    assert invalid.headers.get("cache-control") == "no-store"
                missing = await client.get("/v1/scopes/missing/processing-status", params={"family": "skill"})
                assert missing.status_code == 404 and missing.headers.get("cache-control") == "no-store"
                invalid_html = await client.get("/dashboard/artifact-processing-status", params={"scope": scope})
                assert invalid_html.status_code == 422 and invalid_html.headers.get("cache-control") == "no-store"
                app.state.application = SimpleNamespace()
                unavailable = await client.get(f"/v1/scopes/{scope}/processing-status", params={"family": "skill"})
                assert (
                    unavailable.status_code == 503
                    and unavailable.json()["error"]["code"] == "processing_status_unavailable"
                )

    asyncio.run(scenario())


def test_periodic_binding_with_an_active_scope_leaves_an_empty_scope_idle(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, (active, empty)):
            db = runtime.primary_database
            binding = FAMILY_BINDINGS["memory"]
            await _request(db, binding, active)
            launcher = _Launcher(db, gates={active: asyncio.Event()})
            async with ArtifactProcessingSupervisor(
                database=db,
                bindings=(
                    ArtifactProcessingBinding(
                        binding, "memory", launcher, automatic_processing_interval=timedelta(hours=1)
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                runtime.artifact_processing_supervisor = ArtifactProcessingSupervisors((supervisor,))
                await _wait(lambda: (binding, active) in launcher.active)
                app = create_app(application=runtime)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    params = {"family": "atomic-memory"}
                    running = await client.get(f"/v1/scopes/{active}/processing-status", params=params)
                    running.raise_for_status()
                    assert running.json()["executor"]["state"] == "running"
                    idle = await client.get(f"/v1/scopes/{empty}/processing-status", params=params)
                    idle.raise_for_status()
                    status = idle.json()
                    assert status["state"] == "idle" and status["invocation"] is None
                    executor = status["executor"]
                    assert executor["state"] == "idle" and executor["reason"] == "idle"
                    assert executor["next_check_seconds"] is None

    asyncio.run(scenario())


def test_last_failed_attempt_is_scoped_and_clears_after_recovery(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, (active, empty)):
            db = runtime.primary_database
            binding = FAMILY_BINDINGS["memory"]
            await _request(db, binding, active)
            recovering = asyncio.Event()
            release = asyncio.Event()

            async def work(_assignment):
                if len(launcher.assignments) == 1:
                    raise InferenceTimeoutError("private-operation", 1)
                recovering.set()
                await release.wait()

            launcher = _Launcher(db, on_work=work)
            async with ArtifactProcessingSupervisor(
                database=db,
                bindings=(ArtifactProcessingBinding(binding, "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=0.05,
                retry_jitter=lambda: 1,
            ) as supervisor:
                runtime.artifact_processing_supervisor = ArtifactProcessingSupervisors((supervisor,))
                app = create_app(application=runtime)
                app.include_router(router, prefix="/dashboard")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:

                    async def executor(scope):
                        response = await client.get(
                            f"/v1/scopes/{scope}/processing-status", params={"family": "memory"}
                        )
                        response.raise_for_status()
                        return response.json()["executor"]

                    await asyncio.wait_for(recovering.wait(), 5)
                    last = await executor(active)
                    assert last["state"] == "running" and last["last_error_code"] == "model_timeout"
                    assert last["last_error_stage"] == "inference"
                    assert datetime.fromisoformat(last["last_error_at"]) <= datetime.fromisoformat(last["observed_at"])
                    assert (await executor(empty))["last_error_code"] is None
                    for lang, clue in [("en", "The last model request timed out"), ("zh", "模型调用超时")]:
                        html = await client.get(
                            "/dashboard/artifact-processing-status",
                            params={"scope": active, "family": "memory", "lang": lang},
                        )
                        html.raise_for_status()
                        assert clue in html.text and "private-operation" not in html.text
                    release.set()
                    await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                    recovered = await executor(active)
                    assert all(
                        recovered[field] is None for field in ("last_error_code", "last_error_stage", "last_error_at")
                    )

    asyncio.run(scenario())


@pytest.mark.parametrize("age", [0, 60])
def test_local_execution_observations_are_scoped_expiring_and_redacted(tmp_path, monkeypatch, age):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            intents = ArtifactProcessingIntentRepository()
            binding = FAMILY_BINDINGS["memory"]
            async with runtime.primary_database.transaction() as connection:
                await intents.mark_dirty_many(connection, scopes[0], (binding,), increment=7)
                await intents.request(connection, scopes[0], binding)

            def observations(self, scope):
                if scope != scopes[0]:
                    return ()
                return (
                    ProcessingObservation(
                        binding=FAMILY_BINDINGS["memory"],
                        family="memory",
                        state="running",
                        reason="executing",
                        scheduler_observed=True,
                        observed_at=datetime.now(UTC) - timedelta(seconds=age),
                        worker_id="private-worker",
                        last_error_code="private-error" if age == 0 else "model_timeout",
                        last_error_at=datetime.now(UTC) - timedelta(seconds=age + 1),
                        last_error_stage="private-stage",
                        last_error_trace_id="private-trace",
                    ),
                )

            monkeypatch.setattr(ArtifactProcessingSupervisors, "observe_scope", observations)
            runtime.artifact_processing_supervisor = ArtifactProcessingSupervisors(())
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(
                    f"/v1/scopes/{scopes[0]}/processing-status", params={"family": "atomic-memory"}
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert (
                    result["invocation"]["requested_generation"] == 1 and result["invocation"]["dirty_generation"] == 7
                )
                assert result["executor"]["state"] == "running"
                assert {
                    "requested_generation",
                    "handled_generation",
                    "dirty_generation",
                    "clean_generation",
                }.isdisjoint(result["executor"])
                assert result["executor"]["last_error_stage"] is None
                assert result["executor"]["last_error_code"] == (None if age == 0 else "model_timeout")
                sample = result["evidence"]["executor"]
                assert datetime.fromisoformat(sample["observed_at"]) <= datetime.fromisoformat(sample["read_at"])
                assert datetime.fromisoformat(sample["read_at"]) <= datetime.fromisoformat(result["queried_at"])
                assert result["evidence"]["invocation"]["read_at"] == result["observed_at"]
                assert result["evidence"]["executor"]["scope_id"] == scopes[0]
                assert result["evidence"]["executor"]["durability"] == "process_local"
                assert result["evidence"]["executor"]["freshness"] == ("fresh" if age == 0 else "stale")
                assert all(
                    secret not in response.text
                    for secret in ["private-worker", "private-error", "private-stage", "private-trace"]
                )
                html = await client.get(
                    "/dashboard/artifact-processing-status",
                    params={"scope": scopes[0], "family": "atomic-memory", "lang": "en"},
                )
                assert html.status_code == 200, html.text
                assert ("was running when observed" if age == 0 else "Local observation is stale") in html.text
                other = await client.get(
                    f"/v1/scopes/{scopes[1]}/processing-status", params={"family": "atomic-memory"}
                )
                assert other.json()["executor"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_http_and_dashboard_inspection_require_scope_read(tmp_path, backend, monkeypatch):
    async def scenario():
        async with _server(tmp_path, backend) as (app, client, _access):
            app.include_router(router, prefix="/dashboard")
            monkeypatch.setattr(app.state.application.scopes, "_id_factory", lambda: "team/proj")
            scope = await _scope(client)
            await _grant(client, scope, "reader", "scope.viewer")
            saved = await client.post(
                "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Shared memory."}
            )
            assert saved.status_code == 200, saved.text
            await _grant(
                client,
                scope,
                "artifact-reader",
                "artifact.viewer",
                _resource(scope, "atomic-memory", saved.json()["records"][0]["artifact"]["artifact_id"]),
            )
            monkeypatch.setattr(app.state.application, "atomic_memory", None)
            for principal, expected in [("reader", 200), ("stranger", 403), ("artifact-reader", 403)]:
                headers = {"Authorization": f"Bearer {principal}"}
                for path, params in [
                    ("/v1/scopes/team%2Fproj/processing-status", {"family": "atomic-memory"}),
                    ("/dashboard/artifact-processing-status", {"scope": scope, "family": "atomic-memory"}),
                ]:
                    response = await client.get(path, params=params, headers=headers)
                    assert response.status_code == expected, response.text
                    assert response.headers.get("cache-control") == "no-store"
                    if expected == 403:
                        assert "source_cursor" not in response.text and "data-processing-snapshot" not in response.text

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
@pytest.mark.parametrize("surface", ["http", "dashboard"])
def test_inspection_rechecks_revoked_access_in_the_read_snapshot(tmp_path, monkeypatch, backend, surface):
    async def scenario():
        async with _server(tmp_path, backend) as (app, client, _access):
            app.include_router(router, prefix="/dashboard")
            scope = await _scope(client)
            grant = await _grant(client, scope, "reader", "scope.viewer")
            original = ScopedProcessingStatusApplication.status

            async def revoke_before_read(self, family, **kwargs):
                # Pause after HTTP admission but before the evidence transaction opens.
                revoked = await client.post(
                    "/v1/access/bindings/revoke",
                    json={
                        "binding_id": grant["binding_id"],
                        "expected_version": grant["version"],
                        "idempotency_key": "revoke-processing-reader",
                    },
                    headers={"Authorization": "Bearer admin"},
                )
                assert revoked.status_code == 200, revoked.text
                captured = await client.post(
                    f"/v1/scopes/{scope}/sources",
                    json={"content": "Input accepted after access was revoked."},
                    headers={"Authorization": "Bearer admin"},
                )
                assert captured.status_code == 201, captured.text
                return await original(self, family, **kwargs)

            monkeypatch.setattr(ScopedProcessingStatusApplication, "status", revoke_before_read)
            path = (
                f"/v1/scopes/{scope}/processing-status"
                if surface == "http"
                else "/dashboard/artifact-processing-status"
            )
            response = await client.get(
                path,
                params={"family": "atomic-memory", **({"scope": scope} if surface == "dashboard" else {})},
                headers={"Authorization": "Bearer reader"},
            )
            assert response.status_code == 403, response.text
            assert "source_head" not in response.text and "data-processing-snapshot" not in response.text

    asyncio.run(scenario())


@pytest.mark.parametrize("surface", ["http", "dashboard"])
def test_inspection_returns_no_evidence_when_snapshot_audit_fails(tmp_path, monkeypatch, surface):
    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            app.include_router(router, prefix="/dashboard")
            scope = await _scope(client)
            await _grant(client, scope, "reader", "scope.viewer")
            original = ScopedProcessingStatusApplication.status

            async def unavailable(events):
                raise RuntimeError

            async def fail_audit_after_admission(self, family, **kwargs):
                monkeypatch.setattr(access.audit, "append_audits", unavailable)
                return await original(self, family, **kwargs)

            monkeypatch.setattr(ScopedProcessingStatusApplication, "status", fail_audit_after_admission)
            path = (
                f"/v1/scopes/{scope}/processing-status"
                if surface == "http"
                else "/dashboard/artifact-processing-status"
            )
            response = await client.get(
                path,
                params={"family": "atomic-memory", **({"scope": scope} if surface == "dashboard" else {})},
                headers={"Authorization": "Bearer reader"},
            )
            assert response.status_code == 503, response.text
            assert "source_head" not in response.text and "data-processing-snapshot" not in response.text

    asyncio.run(scenario())


def test_mounted_status_cache_policy_covers_authentication_and_unhandled_errors(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError

    async def scenario():
        async with _runtime(tmp_path) as (runtime, _):
            monkeypatch.setattr(runtime.scopes, "_id_factory", lambda: "team/proj")
            await runtime.scopes.create(ScopeDraft(title="Team", summary="Project", idempotency_key="team"))
            monkeypatch.setattr(runtime, "atomic_memory", None)
            credential = "local-fixture"
            api = create_app(application=runtime, middleware=[Middleware(StaticBearerMiddleware, token=credential)])
            host = FastAPI()
            host.mount("/base", api)
            transport = httpx.ASGITransport(app=host, raise_app_exceptions=False)
            path = "/base/v1/scopes/team%2Fproj/processing-status"
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                denied = await client.get(path, params={"family": "handoff"})
                assert denied.status_code == 401 and denied.headers.get("cache-control") == "no-store"
                accepted = await client.get(
                    path, params={"family": "handoff"}, headers={"Authorization": f"Bearer {credential}"}
                )
                assert accepted.status_code == 200 and accepted.json()["scope_id"] == "team/proj"
                assert accepted.headers.get("cache-control") == "no-store"
                monkeypatch.setattr(ScopedProcessingStatusApplication, "status", fail)
                failed = await client.get(
                    path, params={"family": "handoff"}, headers={"Authorization": f"Bearer {credential}"}
                )
                assert failed.status_code == 500 and failed.headers.get("cache-control") == "no-store"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unavailable", "timeout"])
def test_status_failure_is_isolated_from_saved_content(dashboard, monkeypatch, failure):
    scope = create_scope(dashboard, "Independent status")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Saved content remains readable."}
    )
    assert saved.status_code == 200
    read = DashboardAPI.read

    async def unavailable(self, path, payload=None):
        if "/processing-status?" in path:
            if failure == "timeout":
                await asyncio.sleep(30)
            raise ReadError(503, "service_unavailable")
        return await read(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", unavailable)
    page = dashboard.get("/dashboard/notes", params={"scope": scope})
    assert page.status_code == 200 and "Saved content remains readable." in page.text
    response = dashboard.get(
        "/dashboard/artifact-processing-status", params={"scope": scope, "family": "atomic-memory"}
    )
    assert response.status_code == 503 and response.text == ""
    assert response.headers["cache-control"] == "no-store"
    assert "x-dashboard-html" not in response.headers
