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

"""Service flow reports use real scheduler state and final retrieval results."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from powercontext.builtin.authz.errors import AccessDeniedError
from powercontext.builtin.inference import InferenceTimeoutError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingBlock
from powercontext.builtin.runtime.processing_observation import observe_processing
from powercontext.server.factory import create_server_app
from powercontext.server.logging import JsonFormatter, OperationalContextFilter, _HumanContextFilter
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.server.tracing import ServerTracing
from tests.builtin.runtime.test_processing_scheduler import _Launcher, _profile, _request, _wait


class _PrivateDeniedError(AccessDeniedError):
    stage = "private-stage-credential"
    error_code = "private-code-credential"


def _denied_flow_worker(_assignment):
    raise _PrivateDeniedError()


def _timed_out_flow_worker(_assignment):
    raise TimeoutError("private-timeout-detail")


class _PrivateTimeoutError(TimeoutError):
    pass


def _custom_timeout_flow_worker(_assignment):
    raise _PrivateTimeoutError("private-timeout-detail")


def test_history_events_correlate_a_real_spawned_attempt_and_keep_queue_time_distinct(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="powercontext.builtin.runtime.artifact_processing")

    async def scenario():
        async with _profile(tmp_path, "history") as profile:
            await _request(profile.database, "memory-source-window", "history-scope")
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-source-window", "memory", SpawnArtifactProcessingWorkerLauncher(_denied_flow_worker)
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=60,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1, timeout_seconds=10)

    context_filter = OperationalContextFilter()
    caplog.handler.addFilter(context_filter)
    try:
        asyncio.run(scenario())
    finally:
        caplog.handler.removeFilter(context_filter)
    formatter = JsonFormatter()
    records = [json.loads(formatter.format(record)) for record in caplog.records]
    events = {
        row["event"]: row
        for row in records
        if row.get("event")
        in {"artifact_processing.queued", "artifact_processing.dispatched", "artifact_processing.attempt.completed"}
    }
    assert len(events) == 3
    queued, dispatched, completed = (
        events[key]
        for key in (
            "artifact_processing.queued",
            "artifact_processing.dispatched",
            "artifact_processing.attempt.completed",
        )
    )
    assert all(row["scope"] == "history-scope" and row["family"] == "memory" for row in events.values())
    assert not queued.get("worker_id") and not queued.get("request_id")
    assert dispatched["worker_id"] == completed["worker_id"]
    assert dispatched["queue_wait_ms"] >= 0
    assert completed["queue_wait_ms"] == dispatched["queue_wait_ms"]
    assert completed["duration_ms"] >= 0
    assert completed["request_generation"] == 1
    assert completed["outcome"] == "failure" and completed["error_code"] == "access_denied"
    assert "private-" not in json.dumps(events)


@pytest.mark.parametrize(
    ("worker", "code", "stage"),
    [
        (_denied_flow_worker, "access_denied", "authorization"),
        (_timed_out_flow_worker, "worker_timeout", "worker"),
        (_custom_timeout_flow_worker, "worker_timeout", "worker"),
    ],
)
def test_spawned_failure_reaches_scope_observation_without_private_details(tmp_path, worker, code, stage):
    async def scenario():
        async with _profile(tmp_path, "spawn-flow-failure") as profile:
            await _request(profile.database, "memory-source-window", "denied")
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-source-window", "memory", SpawnArtifactProcessingWorkerLauncher(worker)
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=60,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1, timeout_seconds=10)
                row = (
                    await observe_processing(
                        profile.database, ("denied",), ArtifactProcessingSupervisors((supervisor,))
                    )
                )["denied"][0]
                assert row.state == "retry_wait"
                assert row.last_error_code == code
                assert row.last_error_stage == stage
                assert row.last_error_at is not None and row.last_error_at <= row.observed_at
                assert row.last_error_worker_id is not None
                assert row.last_error_trace_id is None
                assert "private-" not in row.model_dump_json()

    asyncio.run(scenario())


def test_retry_causes_are_scoped_and_cleared_after_acknowledgement(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "scoped-flow-failure") as profile:
            for scope in ("denied", "timeout", "healthy"):
                await _request(profile.database, "memory-source-window", scope)
            failed = set()
            attempts = {}
            release_recovery = asyncio.Event()

            async def work(assignment):
                scope = assignment.scope_id
                attempts[scope] = attempts.get(scope, 0) + 1
                if scope not in failed and scope != "healthy":
                    failed.add(scope)
                    if scope == "denied":
                        raise AccessDeniedError()
                    raise InferenceTimeoutError("private-provider-credential", 1.0)
                if scope == "denied":
                    if attempts[scope] == 2:
                        return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
                    await release_recovery.wait()

            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-source-window", "memory", _Launcher(profile.database, on_work=work), max_workers=3
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=1,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 2)
                group = ArtifactProcessingSupervisors((supervisor,))
                rows = await observe_processing(profile.database, ("denied", "timeout", "healthy"), group)
                assert rows["denied"][0].last_error_code == "access_denied"
                assert rows["timeout"][0].last_error_code == "model_timeout"
                assert rows["timeout"][0].last_error_stage == "inference"
                assert rows["healthy"][0].last_error_code is None
                remote = (await observe_processing(profile.database, ("denied",), None))["denied"][0]
                assert remote.last_error_code is None and remote.last_error_at is None
                await _wait(lambda: attempts.get("denied", 0) >= 3)
                retrying = (await observe_processing(profile.database, ("denied",), group))["denied"][0]
                assert retrying.state == "running" and retrying.last_error_code == "access_denied"
                assert retrying.last_error_at == rows["denied"][0].last_error_at
                assert retrying.last_error_worker_id == rows["denied"][0].last_error_worker_id
                release_recovery.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 3)
                recovered = (await observe_processing(profile.database, ("denied",), group))["denied"][0]
                assert recovered.state == "acknowledged"
                assert recovered.last_error_code is None and recovered.last_error_stage is None
                assert recovered.last_error_at is None
                assert recovered.last_error_worker_id is None and recovered.last_error_trace_id is None

    asyncio.run(scenario())


@pytest.fixture
def failure_block_recovery(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="powercontext.builtin.runtime.artifact_processing")

    async def scenario():
        async with _profile(tmp_path, "failure-block-recovery") as profile:
            await _request(profile.database, "memory-source-window", "blocked")
            policy_blocked = False
            block_checks = 0
            release_recovery = asyncio.Event()

            async def check(_connection, _scope, _binding):
                nonlocal block_checks
                if policy_blocked:
                    block_checks += 1
                    return ArtifactProcessingBlock("frontier", "budget", "window_attempt_limit")
                return None

            async def work(_assignment):
                nonlocal policy_blocked
                if len(launcher.assignments) == 1:
                    policy_blocked = True
                    raise AccessDeniedError()
                await release_recovery.wait()

            launcher = _Launcher(profile.database, on_work=work)
            tracing = ServerTracing.context_only()
            try:
                async with ArtifactProcessingSupervisor(
                    database=profile.database,
                    bindings=(ArtifactProcessingBinding("memory-source-window", "memory", launcher, work_block=check),),
                    lease_mode="single-process",
                    retry_base_seconds=1,
                    retry_jitter=lambda: 1,
                    blocked_check_seconds=0.05,
                    tracing=tracing,
                ) as supervisor:
                    group = ArtifactProcessingSupervisors((supervisor,))

                    async def observation():
                        return (await observe_processing(profile.database, ("blocked",), group))["blocked"][0]

                    await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                    failed = await observation()
                    await _wait(lambda: block_checks >= 2)
                    blocked = await observation()
                    policy_blocked = False
                    await _wait(lambda: len(launcher.assignments) == 2)
                    running = await observation()
                    release_recovery.set()
                    await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                    return failed, blocked, running, await observation()
            finally:
                tracing.provider.shutdown()

    return asyncio.run(scenario()), [
        record for record in caplog.records if getattr(record, "event", None) == "artifact_processing.blocked"
    ]


def test_policy_blocks_retain_failure_evidence_until_acknowledgement(failure_block_recovery):
    (failed, blocked, running, recovered), _records = failure_block_recovery
    assert failed.last_error_code == "access_denied"
    assert failed.last_error_stage == "authorization"
    assert isinstance(failed.last_error_at, datetime)
    assert failed.last_error_worker_id and failed.last_error_trace_id
    assert blocked.state == "blocked" and blocked.block_code == "window_attempt_limit"
    assert running.state == "running" and running.block_code is None
    assert running.worker_id != failed.last_error_worker_id
    assert recovered.state == "acknowledged"
    for field in (
        "last_error_code",
        "last_error_stage",
        "last_error_at",
        "last_error_worker_id",
        "last_error_trace_id",
    ):
        assert getattr(blocked, field) == getattr(failed, field)
        assert getattr(running, field) == getattr(failed, field)
        assert getattr(recovered, field) is None


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_policy_block_logs_render_retained_failure_evidence(failure_block_recovery, log_format):
    (failed, *_observations), records = failure_block_recovery
    assert records
    for source_record in records:
        record = copy.copy(source_record)
        if log_format == "json":
            assert OperationalContextFilter().filter(record)
            rendered = JsonFormatter().format(record)
            payload = json.loads(rendered)
        else:
            assert _HumanContextFilter().filter(record)
            rendered = logging.Formatter("%(message)s%(processing_context)s").format(record)
            payload = {
                field: getattr(record, field)
                for field in (
                    "last_error_code",
                    "last_error_stage",
                    "last_error_at",
                    "last_error_worker_id",
                    "last_error_trace_id",
                )
            }
        assert payload["last_error_at"] == failed.last_error_at.isoformat()
        for field in ("last_error_code", "last_error_stage", "last_error_worker_id", "last_error_trace_id"):
            assert payload[field] == getattr(failed, field)
            assert getattr(failed, field) in rendered
        assert "window_attempt_limit" in rendered


def test_processing_explains_block_and_retry_without_inventing_remote_state(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "flow") as profile:
            await _request(profile.database, "topic-binding", "blocked")
            await _request(profile.database, "topic-binding", "retry")
            await _request(profile.database, "topic-binding", "other-scope")

            async def check(_connection, scope, _binding):
                if scope == "retry":
                    raise TimeoutError
                return ArtifactProcessingBlock("frontier", "budget", "window_attempt_limit")

            launcher = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("topic-binding", "topic-memory", launcher, work_block=check),),
                lease_mode="single-process",
                retry_base_seconds=60,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["topic-memory"]["blocked"] == 2)
                await _wait(lambda: supervisor.family_status["topic-memory"]["retry_wait"] == 3)
                scopes = ("blocked", "retry")
                rows = await observe_processing(profile.database, scopes, ArtifactProcessingSupervisors((supervisor,)))
                assert set(rows) == set(scopes)
                blocked = rows["blocked"][0]
                assert blocked.state == "blocked" and blocked.reason == "policy_block"
                assert blocked.block_code == "window_attempt_limit"
                assert blocked.scheduler_observed and blocked.pending_since is not None
                assert blocked.requested_generation > blocked.handled_generation
                assert blocked.next_check_seconds is not None
                retry = rows["retry"][0]
                assert retry.state == "retry_wait" and retry.reason == "retry_backoff"
                assert retry.retry_failures == 1
                assert retry.last_error_code == "worker_timeout"
                assert retry.last_error_stage == "work_admission"
                assert retry.next_check_seconds is not None and retry.next_check_seconds > 0
                assert not launcher.assignments
                remote = (await observe_processing(profile.database, ("blocked",), None))["blocked"][0]
                assert remote.state == "pending"
                assert not remote.scheduler_observed and remote.reason == "unobserved"
                assert remote.next_check_seconds is None and remote.block_code is None

    asyncio.run(scenario())


def test_public_retrieval_reports_hit_empty_and_lifecycle_effect_without_content(tmp_path, caplog):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'retrieval.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client, caplog.at_level(logging.INFO):
        scope = client.get("/v1/scopes/default").json()["scope_id"]
        record = client.post(
            "/v1/memory/remember",
            json={
                "scope_id": scope,
                "kind": "fact",
                "text": "Private zebrasearch evidence.",
            },
        ).json()["records"][0]
        caplog.clear()
        search = f"/v1/scopes/{scope}/artifacts/atomic-memory/search"
        assert client.post(search, json={"query": "zebrasearch", "mode": "text"}).json()["results"]
        assert client.post(search, json={"query": "nomatchingneedle", "mode": "text"}).json()["results"] == []
        assert (
            client.post(
                "/v1/atomic-memory/lifecycle",
                json={
                    "scope_id": scope,
                    "target": {"artifact": record["artifact"], "state_version": record["state_version"]},
                    "state": "forgotten",
                },
            ).status_code
            == 200
        )
        assert client.post(search, json={"query": "zebrasearch", "mode": "text"}).json()["results"] == []
        # Unsupported family is a dispatched failure, not a successful empty result.
        assert (
            client.post(f"/v1/scopes/{scope}/artifacts/unsupported/search", json={"query": "private-query"}).status_code
            >= 400
        )
        records = [r for r in caplog.records if getattr(r, "event", "") == "retrieval.completed"]
        assert [(r.retrieval_family, r.retrieval_outcome) for r in records] == [
            ("atomic-memory", "nonempty"),
            ("atomic-memory", "empty"),
            ("atomic-memory", "empty"),
            ("other", "failure"),
        ]
        for record in records:
            OperationalContextFilter().filter(record)
            output = JsonFormatter().format(record)
            assert json.loads(output)["scope"] == scope
            assert not any(
                secret in output for secret in ("zebrasearch", "nomatchingneedle", "private-query", "Private")
            )
        metrics = client.get("/metrics").text
        assert (
            'powercontext_server_retrieval_requests_total{family="atomic-memory",operation="search_artifacts",outcome="nonempty"} 1.0'
            in metrics
        )
        assert (
            'powercontext_server_retrieval_requests_total{family="atomic-memory",operation="search_artifacts",outcome="empty"} 2.0'
            in metrics
        )
        assert scope not in metrics and "zebrasearch" not in metrics
        stats = client.post("/v1/stats", json={"selection": {"mode": "exact", "scope_ids": [scope]}})
        assert stats.status_code == 200
        assert "processing" in stats.json()["by_scope"][0]
        assert stats.json()["inventory"]["memory"]["entries"]["inactive"] == 1


def test_mcp_application_observations_are_not_duplicated(tmp_path):
    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    app = create_server_app(
        settings=ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}"))
    )

    def http_client(headers=None, timeout=None, auth=None, **_):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def scenario():
        transport = StreamableHttpTransport("http://testserver/mcp/", httpx_client_factory=http_client)
        async with app.router.lifespan_context(app), http_client() as client, Client(transport) as mcp:
            scope = (await client.get("/v1/scopes/default")).json()["scope_id"]
            await mcp.call_tool("search_atomic_memory", {"scope_id": scope, "query": "no-results", "mode": "text"})
            profile = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={"family": "profile", "content": {"content": "# Private MCP profile"}},
            )
            assert profile.status_code == 201
            await mcp.call_tool("get_artifact", {"scope_id": scope, "family": "profile", "artifact_id": "profile"})
            metrics = (await client.get("/metrics")).text
            assert (
                'powercontext_server_retrieval_requests_total{family="atomic-memory",operation="search_atomic_memory",outcome="empty"} 1.0'
                in metrics
            )
            assert (
                'powercontext_server_artifact_read_duration_seconds_count{family="profile",operation="get_artifact",outcome="success"} 1.0'
                in metrics
            )
            assert scope not in metrics

    asyncio.run(scenario())


def test_profile_head_revision_and_conditional_reads_have_independent_observations(tmp_path, caplog):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile-reads.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client, caplog.at_level(logging.INFO):
        scope = client.get("/v1/scopes/default").json()["scope_id"]
        created = client.post(
            f"/v1/scopes/{scope}/artifacts",
            json={"family": "profile", "content": {"content": "# Private profile evidence"}},
        )
        assert created.status_code == 201
        path = f"/v1/scopes/{scope}/artifacts/profile/profile"
        caplog.clear()
        head = client.get(path)
        assert head.status_code == 200
        assert client.get(path + "/revisions/1").status_code == 200
        unchanged = client.get(path, headers={"If-None-Match": head.headers["etag"]})
        assert unchanged.status_code == 304 and unchanged.content == b""
        assert client.get(f"/v1/scopes/{scope}/artifacts/profile/no-such-head").status_code == 404
        records = [r for r in caplog.records if getattr(r, "event", "") == "artifact.read.completed"]
        assert [(r.operation, r.family, r.read_outcome) for r in records] == [
            ("get_artifact", "profile", "success"),
            ("get_artifact_revision", "profile", "success"),
            ("get_artifact", "profile", "not_modified"),
            ("get_artifact", "profile", "failure"),
        ]
        for record in records:
            OperationalContextFilter().filter(record)
            output = JsonFormatter().format(record)
            value = json.loads(output)
            assert value["scope"] == scope and value["family"] == "profile"
            assert value["read_outcome"] in {"success", "not_modified", "failure"}
            assert not any(secret in output for secret in ("Private profile evidence", "no-such-head", "artifact_id"))
        metrics = client.get("/metrics").text
        observed = {
            (s.labels["operation"], s.labels["family"], s.labels["outcome"]): s.value
            for family in text_string_to_metric_families(metrics)
            for s in family.samples
            if s.name == "powercontext_server_artifact_read_duration_seconds_count"
        }
        assert observed == {
            ("get_artifact", "profile", "success"): 1,
            ("get_artifact_revision", "profile", "success"): 1,
            ("get_artifact", "profile", "not_modified"): 1,
            ("get_artifact", "profile", "failure"): 1,
        }
        assert "powercontext_server_retrieval_requests_total{" not in metrics
        assert scope not in metrics and "Private profile evidence" not in metrics


def test_artifact_read_telemetry_failure_preserves_the_public_result(tmp_path, monkeypatch, caplog):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'read-isolation.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client, caplog.at_level(logging.INFO):
        scope = client.get("/v1/scopes/default").json()["scope_id"]
        created = client.post(
            f"/v1/scopes/{scope}/artifacts",
            json={"family": "profile", "content": {"content": "# Retained profile"}},
        )
        assert created.status_code == 201

        def unavailable(*_args, **_kwargs):
            raise RuntimeError

        monkeypatch.setattr(app.state.metrics, "observe_artifact_read", unavailable)
        caplog.clear()
        response = client.get(f"/v1/scopes/{scope}/artifacts/profile/profile")
        assert response.status_code == 200
        assert response.json()["content"]["content"] == "# Retained profile\n"
        assert any(getattr(r, "event", "") == "artifact.read.completed" for r in caplog.records)


def test_cancelled_artifact_read_still_propagates_cancellation(tmp_path, monkeypatch, caplog):
    import httpx

    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'read-cancel.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client,
        ):
            scope = (await client.get("/v1/scopes/default")).json()["scope_id"]
            started = asyncio.Event()

            async def waiting_read(*_args, **_kwargs):
                started.set()
                await asyncio.Event().wait()

            reader = app.state.application.records.for_scope(scope)
            monkeypatch.setattr(type(reader), "get_artifact", waiting_read)
            request = asyncio.create_task(client.get(f"/v1/scopes/{scope}/artifacts/profile/profile"))
            await asyncio.wait_for(started.wait(), timeout=2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            records = [r for r in caplog.records if getattr(r, "event", "") == "artifact.read.completed"]
            assert len(records) == 1 and records[0].read_outcome == "cancelled"
            metrics = (await client.get("/metrics")).text
            assert (
                'powercontext_server_artifact_read_duration_seconds_count{family="profile",operation="get_artifact",outcome="cancelled"} 1.0'
                in metrics
            )

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())


def test_processing_statistics_keep_scope_authorization(tmp_path):
    from tests.e2e.test_access_control_regressions import _grant, _scope, _server

    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope = await _scope(client)
            payload = {"selection": {"mode": "exact", "scope_ids": [scope]}, "include_processing": True}
            denied = await client.post("/v1/stats", json=payload, headers={"Authorization": "Bearer viewer"})
            assert denied.status_code == 403
            assert "processing" not in denied.json()
            await _grant(client, scope, "viewer", "scope.viewer")
            allowed = await client.post("/v1/stats", json=payload, headers={"Authorization": "Bearer viewer"})
            assert allowed.status_code == 200
            assert allowed.json()["scope_ids"] == [scope]
            assert "processing" in allowed.json()["by_scope"][0]

    asyncio.run(scenario())
