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

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from starlette.requests import Request

from powercontext_eval.artifacts import ArmState
from powercontext_eval.benchmarks.swebench_pro.adapter import SweBenchProInstance
from powercontext_eval.benchmarks.swebench_pro.catalog import STABILITY_V1_CASES, STABILITY_V1_TASK_SET
from powercontext_eval.benchmarks.swebench_pro.gold_overrides import (
    SOURCE559_DATASET_PATCH_SHA256,
    SOURCE559_INSTANCE_ID,
    SOURCE559_REFERENCE_DATASET,
    SOURCE559_REFERENCE_FILE_OID,
    SOURCE559_REFERENCE_PATCH_SHA256,
    SOURCE559_REFERENCE_REVISION,
)
from powercontext_eval.git_source import GitSource
from powercontext_eval.report import ArmReport, GoldValidationAudit, MetricSet, ReportBundle, TestGroupReport
from powercontext_eval.web.api import TaskEventStream, create_app
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.models import (
    FailureCategory,
    FailureCode,
    RetryDisposition,
    SafeFailure,
    TaskCreate,
    TaskPhase,
    TaskRecord,
    TaskResult,
)
from powercontext_eval.web.resources import FilesystemCapacity, ResourceUnavailable
from powercontext_eval.web.store import FinalizationState, TaskStore, TokensFlowFinalizationCreate
from powercontext_eval.web.usage import UsageSnapshot

NOW = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)
INSTANCE = "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"
SECRET = "secret-proxy-token"


def _usage(used_percent: int, *, observed_at: datetime | None = None) -> UsageSnapshot:
    observed = datetime.now(UTC) if observed_at is None else observed_at
    return UsageSnapshot(
        limit_id="codex",
        used_percent=used_percent,
        remaining_percent=100 - used_percent,
        window_duration_minutes=10_080,
        resets_at=observed + timedelta(days=7),
        observed_at=observed,
        plan_type="pro",
        account_tokens=1_234,
    )


def payload(key: str = "api-task-key") -> dict[str, str]:
    return {
        "powercontext_ref": "commit:" + "a" * 40,
        "benchmark": "swebench-pro",
        "instance_id": INSTANCE,
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "treatment_mode": "off_on",
        "idempotency_key": key,
    }


@pytest.fixture
def config(tmp_path: Path) -> WebConfig:
    return WebConfig.for_root(
        tmp_path,
        tokensflow_egress_network="bridge",
        database_path=tmp_path / "tasks.sqlite3",
        run_root=tmp_path / "runs",
        frontend_dist=tmp_path / "deploy" / "frontend",
        proxy_url="http://127.0.0.1:8081",
    )


@pytest.fixture
def store(config: WebConfig) -> TaskStore:
    task_store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    task_store.initialize()
    task_store.save_usage_snapshot(_usage(9))
    return task_store


@pytest.fixture(autouse=True)
def resolve_existing_api_test_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unrelated API tests focused while exact source validation runs at its real boundary."""

    original = GitSource.resolve

    def resolve(source: GitSource, repository: str | Path, requested: Any) -> Any:
        if requested.kind == "commit" and requested.value == "a" * 40:
            return SimpleNamespace(sha="a" * 40)
        return original(source, repository, requested)

    monkeypatch.setattr(GitSource, "resolve", resolve)


@pytest.fixture
def client(config: WebConfig, store: TaskStore) -> TestClient:
    return TestClient(create_app(config, store))


def _complete_failed_attempt_cleanup(store: TaskStore, task_id: str, *, now: datetime) -> bool:
    candidate = next(
        candidate
        for candidate in store.list_attempt_cleanup_candidates(limit=100, now=now)
        if candidate.task_id == task_id
    )
    store.mark_attempt_evidence_exported(candidate.attempt_id)
    return store.complete_attempt_cleanup_and_schedule_retry(candidate.attempt_id, now=now)


def assert_safe(response: Response) -> None:
    assert SECRET not in response.text


def test_health_and_capabilities_are_server_owned_and_secret_free(client: TestClient) -> None:
    health = client.get("/api/health")
    capabilities = client.get("/api/capabilities")

    health_payload = health.json()
    assert health_payload.pop("resource_admission_open") is True
    assert health_payload.pop("filesystem_free_bytes") > 0
    assert health_payload.pop("filesystem_total_bytes") > 0
    assert health_payload.pop("filesystem_min_free_bytes") == 10 * 1024**3
    assert health_payload.pop("filesystem_free_inodes") > 0
    assert health_payload.pop("filesystem_total_inodes") > 0
    assert health_payload.pop("filesystem_min_free_inodes") == 1_000_000
    assert health_payload.pop("web_revision") != "unknown"
    assert health_payload.pop("worker_revision") is None
    assert health_payload.pop("web_schema_version") == 3
    assert health_payload.pop("worker_schema_version") is None
    assert health_payload.pop("deployment_consistent") is False
    assert health_payload == {
        "service": "ok",
        "worker_lease_active": False,
        "active_task_pairs": 0,
        "task_parallelism": 1,
        "queued_tasks": 0,
        "running_tasks": 0,
    }
    assert capabilities.json() == {
        "benchmarks": ["swebench-pro"],
        "instances": [INSTANCE],
        "models": ["gpt-5.6-sol"],
        "reasoning_efforts": ["medium"],
        "treatment_modes": ["off_on", "on_only", "off_only"],
    }
    assert_safe(health)
    assert_safe(capabilities)


def test_baseline_selection_accepts_a_json_array_before_batch_lookup(client: TestClient) -> None:
    response = client.put("/api/batches/missing/baseline-selections", json={"selections": []})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "batch_not_found"


def test_health_fails_resource_admission_closed_when_capacity_is_unavailable(
    config: WebConfig,
    store: TaskStore,
) -> None:
    class UnavailableResourceProbe:
        def read(self) -> FilesystemCapacity:
            raise ResourceUnavailable("do not expose filesystem details")

    health = TestClient(create_app(config, store, resource_probe=UnavailableResourceProbe())).get("/api/health")

    assert health.status_code == 200
    payload = health.json()
    assert payload["service"] == "ok"
    assert payload["resource_admission_open"] is False
    assert payload["filesystem_free_bytes"] is None
    assert payload["filesystem_total_bytes"] is None
    assert payload["filesystem_free_inodes"] is None
    assert payload["filesystem_total_inodes"] is None
    assert "do not expose" not in health.text


def test_capabilities_and_new_task_inputs_share_the_configured_model_allowlist(
    tmp_path: Path,
) -> None:
    configured = WebConfig.for_root(
        tmp_path,
        tokensflow_egress_network="bridge",
        proxy_url="http://127.0.0.1:8081",
        codex_models=("gpt-5.6-sol", "gpt-5.6-luna"),
    )
    task_store = TaskStore(configured.database_path, lease_duration=timedelta(seconds=configured.lease_seconds))
    task_store.initialize()
    task_store.save_usage_snapshot(_usage(9))
    app = TestClient(create_app(configured, task_store, catalog=_BatchCatalog()))

    capabilities = app.get("/api/capabilities")
    preview = app.post(
        "/api/batches/preview",
        json={"powercontext_ref": "latest", "model": "gpt-5.6-luna", "usage_pause_percent": 80},
    )
    batch = app.post("/api/batches", json=_batch_payload("configured-luna", model="gpt-5.6-luna"))
    task = app.post("/api/tasks", json=payload("configured-luna-task") | {"model": "gpt-5.6-luna"})

    assert capabilities.json()["models"] == ["gpt-5.6-sol", "gpt-5.6-luna"]
    assert preview.status_code == 200
    assert batch.status_code == 201
    assert task.status_code == 201


@pytest.mark.parametrize("endpoint", ["/api/batches", "/api/tasks"])
def test_new_api_submission_runs_model_admission_exactly_once(
    endpoint: str,
    config: WebConfig,
    store: TaskStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission_calls = 0

    def accept(_config: WebConfig, model: str) -> bool:
        nonlocal admission_calls
        admission_calls += 1
        return model == "gpt-5.6-sol"

    monkeypatch.setattr(WebConfig, "accepts_codex_model", accept)
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    request = (
        _batch_payload("single-admission-batch") if endpoint.endswith("batches") else payload("single-admission-task")
    )

    response = client.post(endpoint, json=request)

    assert response.status_code == 201
    assert admission_calls == 1


def test_unconfigured_and_malicious_models_are_rejected_for_all_new_api_inputs(
    config: WebConfig,
    store: TaskStore,
) -> None:
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    for model in ("gpt-5.6-luna", "unsafe model"):
        assert (
            client.post(
                "/api/batches/preview",
                json={"powercontext_ref": "latest", "model": model, "usage_pause_percent": 80},
            ).status_code
            == 422
        )
        assert client.post("/api/batches", json=_batch_payload(f"rejected-{model}", model=model)).status_code == 422
        assert client.post("/api/tasks", json=payload(f"rejected-{model}") | {"model": model}).status_code == 422


def test_removed_model_remains_readable_runnable_and_retryable_for_existing_batch(
    config: WebConfig,
    store: TaskStore,
) -> None:
    configured = config.model_copy(update={"codex_models": ("gpt-5.6-sol", "gpt-5.6-luna")})
    enabled_client = TestClient(create_app(configured, store, catalog=_BatchCatalog()))
    created = enabled_client.post(
        "/api/batches",
        json=_batch_payload("legacy-luna-batch", model="gpt-5.6-luna"),
    ).json()
    task = store.list_batch_tasks(created["batch_id"])[0]
    started = datetime.now(UTC) + timedelta(seconds=1)
    claimed = store.claim_next("legacy-model-worker", now=started)
    assert claimed is not None and claimed.request.model == "gpt-5.6-luna"
    store.fail(
        task.task_id,
        "legacy-model-worker",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            failure_code=FailureCode.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Codex execution did not complete",
            retry_disposition=RetryDisposition.TERMINAL,
        ),
        now=started + timedelta(seconds=1),
    )
    assert (
        _complete_failed_attempt_cleanup(
            store,
            task.task_id,
            now=started + timedelta(seconds=2),
        )
        is False
    )

    current_client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    replayed = current_client.post(
        "/api/batches",
        json=_batch_payload("legacy-luna-batch", model="gpt-5.6-luna"),
    )
    conflicted = current_client.post(
        "/api/batches",
        json=_batch_payload("legacy-luna-batch", model="gpt-5.6-luna") | {"usage_pause_percent": 79},
    )
    listed = current_client.get(f"/api/batches/{created['batch_id']}")
    retried = current_client.post(
        f"/api/batches/{created['batch_id']}/tasks/{task.task_id}/retry",
        json={"idempotency_key": "legacy-luna-retry"},
    )
    rejected_new = current_client.post(
        "/api/batches",
        json=_batch_payload("new-luna-rejected", model="gpt-5.6-luna"),
    )

    assert listed.status_code == 200
    assert replayed.status_code == 200
    assert replayed.json()["batch_id"] == created["batch_id"]
    assert conflicted.status_code == 409
    assert listed.json()["request"]["model"] == "gpt-5.6-luna"
    assert retried.status_code == 201
    assert store.get(task.task_id).request.model == "gpt-5.6-luna"
    assert rejected_new.status_code == 422


def test_removed_model_task_replay_precedes_current_allowlist_but_conflicts_do_not(
    config: WebConfig,
    store: TaskStore,
) -> None:
    configured = config.model_copy(update={"codex_models": ("gpt-5.6-sol", "gpt-5.6-luna")})
    enabled_client = TestClient(create_app(configured, store))
    luna = payload("legacy-luna-task") | {"model": "gpt-5.6-luna"}
    created = enabled_client.post("/api/tasks", json=luna)

    current_client = TestClient(create_app(config, store))
    replayed = current_client.post("/api/tasks", json=luna)
    conflicted = current_client.post("/api/tasks", json=luna | {"model": "gpt-5.6-sol"})
    rejected_new = current_client.post(
        "/api/tasks",
        json=payload("new-disabled-luna-task") | {"model": "gpt-5.6-luna"},
    )

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["task_id"] == created.json()["task_id"]
    assert conflicted.status_code == 409
    assert rejected_new.status_code == 422


def test_health_reads_four_active_pairs_and_published_capacity_from_store(
    config: WebConfig,
    store: TaskStore,
) -> None:
    observed = datetime.now(UTC)
    store.record_worker_capacity(4, now=observed)
    for index in range(4):
        store.create(TaskCreate.model_validate(payload(f"health-pair-{index}")), now=observed)
        assert store.claim_next(f"health-worker-{index}", max_concurrency=4, now=observed) is not None

    health = TestClient(create_app(config, store)).get("/api/health")

    health_payload = health.json()
    assert health_payload.pop("resource_admission_open") is True
    assert health_payload.pop("filesystem_free_bytes") > 0
    assert health_payload.pop("filesystem_total_bytes") > 0
    assert health_payload.pop("filesystem_min_free_bytes") == 16 * 1024**3
    assert health_payload.pop("filesystem_free_inodes") > 0
    assert health_payload.pop("filesystem_total_inodes") > 0
    assert health_payload.pop("filesystem_min_free_inodes") == 1_000_000
    assert health_payload.pop("web_revision") != "unknown"
    assert health_payload.pop("worker_revision") is None
    assert health_payload.pop("web_schema_version") == 3
    assert health_payload.pop("worker_schema_version") is None
    assert health_payload.pop("deployment_consistent") is False
    assert health_payload == {
        "service": "ok",
        "worker_lease_active": True,
        "active_task_pairs": 4,
        "task_parallelism": 4,
        "queued_tasks": 0,
        "running_tasks": 4,
    }
    assert_safe(health)


def test_newest_succeeded_query_orders_before_limit_and_default_remains_fifo(
    client: TestClient, store: TaskStore
) -> None:
    result = TaskResult(
        artifact_dir="/safe/artifacts",
        report_path="/safe/report.md",
        off_resolved=False,
        on_resolved=True,
    )
    succeeded_ids: list[str] = []
    for index in range(55):
        created, _ = store.create(TaskCreate.model_validate(payload(f"succeeded-{index:02d}")), now=NOW)
        claimed = store.claim_next("worker", now=NOW + timedelta(seconds=index + 1))
        assert claimed is not None and claimed.task_id == created.task_id
        store.succeed(created.task_id, "worker", result, now=NOW + timedelta(seconds=index + 2))
        succeeded_ids.append(created.task_id)

    newest = client.get("/api/tasks?status=succeeded&order=newest&limit=50&offset=0")
    oldest = client.get("/api/tasks?status=succeeded&limit=50&offset=0")
    invalid = client.get("/api/tasks?order=sideways")

    assert [item["task_id"] for item in newest.json()] == list(reversed(succeeded_ids[-50:]))
    assert [item["task_id"] for item in oldest.json()] == succeeded_ids[:50]
    assert invalid.status_code == 422


def test_create_replay_and_task_detail_have_truthful_queue_positions(client: TestClient) -> None:
    first = client.post("/api/tasks", json=payload("create-key-1"))
    second = client.post("/api/tasks", json=payload("create-key-2"))
    replay = client.post("/api/tasks", json=payload("create-key-1"))
    detail = client.get(f"/api/tasks/{first.json()['task_id']}")

    assert first.status_code == 201
    assert first.json()["status"] == "queued"
    assert first.json()["queue_position"] == 1
    assert second.json()["queue_position"] == 2
    assert replay.status_code == 200
    assert replay.json()["task_id"] == first.json()["task_id"]
    assert detail.json()["queue_position"] == 1
    assert detail.json()["phase"] is None
    assert detail.json()["version"] == 0
    assert all(response.headers["cache-control"] == "no-store" for response in (first, second, replay, detail))


def test_create_validation_uses_fixed_error_envelope_and_forbids_extra_fields(client: TestClient) -> None:
    invalid = payload()
    invalid["model"] = "other"
    invalid["unexpected"] = SECRET

    response = client.post("/api/tasks", json=invalid)

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The evaluation request is invalid.",
        }
    }
    assert_safe(response)


def test_list_filters_paginates_in_stable_order_and_recomputes_queue_positions(client: TestClient) -> None:
    first = client.post("/api/tasks", json=payload("list-key-1")).json()
    second = client.post("/api/tasks", json=payload("list-key-2")).json()
    third = client.post("/api/tasks", json=payload("list-key-3")).json()
    client.post(f"/api/tasks/{second['task_id']}/cancel")

    response = client.get("/api/tasks", params={"status": "queued", "limit": 1, "offset": 1})

    assert response.status_code == 200
    assert [item["task_id"] for item in response.json()] == [third["task_id"]]
    assert response.json()[0]["queue_position"] == 2
    assert client.get("/api/tasks", params={"status": "cancelled"}).json()[0]["task_id"] == second["task_id"]
    assert client.get(f"/api/tasks/{first['task_id']}").json()["queue_position"] == 1
    assert response.headers["cache-control"] == "no-store"


def test_cancel_queued_and_reject_running_or_terminal(client: TestClient, store: TaskStore) -> None:
    queued = client.post("/api/tasks", json=payload("cancel-key-1")).json()
    cancelled = client.post(f"/api/tasks/{queued['task_id']}/cancel")
    running = client.post("/api/tasks", json=payload("cancel-key-2")).json()
    store.claim_next("worker", now=datetime.now(UTC) + timedelta(seconds=1))

    terminal_conflict = client.post(f"/api/tasks/{queued['task_id']}/cancel")
    running_conflict = client.post(f"/api/tasks/{running['task_id']}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["queue_position"] is None
    for response in (terminal_conflict, running_conflict):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "task_conflict"
        assert_safe(response)


def test_missing_task_and_unknown_api_route_use_json_errors(client: TestClient) -> None:
    missing = client.get("/api/tasks/missing")
    unknown = client.get("/api/not-a-route")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "task_not_found"
    assert unknown.status_code == 404
    assert unknown.json() == {"error": {"code": "not_found", "message": "The requested API route does not exist."}}


def test_method_and_internal_errors_use_fixed_secret_free_envelopes(
    config: WebConfig, store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(create_app(config, store), raise_server_exceptions=False)
    method = client.put("/api/health")
    monkeypatch.setattr(store, "get", lambda _task_id: (_ for _ in ()).throw(RuntimeError(SECRET)))
    internal = client.get("/api/tasks/anything")

    assert method.status_code == 404
    assert method.json() == {"error": {"code": "not_found", "message": "The requested API route does not exist."}}
    assert internal.status_code == 500
    assert internal.json() == {
        "error": {"code": "internal_error", "message": "The evaluation service could not complete the request."}
    }
    assert_safe(internal)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("HEAD", "/api/unknown"),
        ("TRACE", "/api/unknown"),
        ("CONNECT", "/api/unknown"),
        ("HEAD", "/api/tasks"),
        ("TRACE", "/api/health"),
    ],
)
def test_every_api_method_uses_fixed_no_store_error_envelope(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    if method != "HEAD":
        assert response.json() == {"error": {"code": "not_found", "message": "The requested API route does not exist."}}


def _write_treatment_evidence(run_root: Path, task_id: str) -> None:
    run_dir = run_root / task_id
    for arm in ("off", "on"):
        target = run_dir / "arms" / arm / "powercontext"
        target.mkdir(parents=True)
        (target / "treatment.json").write_text(
            json.dumps(
                {
                    "mcp_requests": 0 if arm == "off" else 2,
                    "prompt_sources": 0 if arm == "off" else 1,
                    "plugin_checkout_sha": "a" * 40,
                    "plugin_id": "powercontext@powercontext",
                    "plugin_installed": True,
                    "plugin_version": "0.1.0",
                    "scope_id": f"eval:{task_id}:{arm}",
                    "server_ready": True,
                }
            )
        )


def _write_report(run_root: Path, task_id: str) -> None:
    run_dir = run_root / task_id
    _write_treatment_evidence(run_root, task_id)

    def arm(name: Literal["off", "on"]) -> ArmReport:
        return ArmReport(
            arm=name,
            state=ArmState.TREATMENT_VALIDATED,
            resolved=True,
            passed=True,
            treatment_valid=True,
            metrics=MetricSet(input_tokens=10, output_tokens=5, elapsed_seconds=1.5, patch_bytes=20),
        )

    bundle = ReportBundle(
        title="Evaluation",
        revisions={"powercontext": "a" * 40},
        configuration={"model": "gpt-5.6-sol"},
        off=arm("off"),
        on=arm("on"),
    )
    (run_dir / "report.json").write_text(bundle.model_dump_json())
    (run_dir / "report.md").write_text("# Résumé\n")


def test_structured_and_raw_reports_use_validated_artifacts(
    client: TestClient, config: WebConfig, store: TaskStore
) -> None:
    task = client.post("/api/tasks", json=payload("report-key")).json()
    created_at = datetime.fromisoformat(task["created_at"])
    claimed = store.claim_next("worker", now=created_at)
    assert claimed is not None
    worker_run_dir = config.run_root / "runs" / task["task_id"]
    _write_report(config.run_root / "runs", task["task_id"])
    store.succeed(
        task["task_id"],
        "worker",
        TaskResult(
            artifact_dir=str(worker_run_dir.relative_to(config.run_root)),
            report_path=str((worker_run_dir / "report.md").relative_to(config.run_root)),
            off_resolved=True,
            on_resolved=True,
        ),
        now=created_at + timedelta(seconds=1),
    )

    structured = client.get(f"/api/tasks/{task['task_id']}/report")
    raw = client.get(f"/api/tasks/{task['task_id']}/report.md")

    assert structured.status_code == 200
    assert structured.json()["task_id"] == task["task_id"]
    assert structured.json()["acceptance_valid"] is True
    assert structured.json()["off"] == {
        "arm": "off",
        "state": "treatment_validated",
        "resolution": "resolved",
        "passed": True,
        "treatment_valid": True,
        "input_tokens": 10,
        "output_tokens": 5,
        "elapsed_seconds": 1.5,
        "patch_bytes": 20,
    }
    assert raw.status_code == 200
    assert raw.text == "# Résumé\n"
    assert raw.headers["content-type"].startswith("text/plain")
    assert structured.headers["cache-control"] == raw.headers["cache-control"] == "no-store"


def test_report_api_reads_the_canonical_worker_artifact_directory(
    client: TestClient, config: WebConfig, store: TaskStore
) -> None:
    task = client.post("/api/tasks", json=payload("worker-report-key")).json()
    created_at = datetime.fromisoformat(task["created_at"])
    claimed = store.claim_next("worker", now=created_at)
    assert claimed is not None
    worker_run_dir = config.run_root / "runs" / task["task_id"]
    _write_report(config.run_root / "runs", task["task_id"])
    store.succeed(
        task["task_id"],
        "worker",
        TaskResult(
            artifact_dir=str(worker_run_dir.relative_to(config.run_root)),
            report_path=str((worker_run_dir / "report.md").relative_to(config.run_root)),
            off_resolved=True,
            on_resolved=True,
        ),
        now=created_at + timedelta(seconds=1),
    )

    assert client.get(f"/api/tasks/{task['task_id']}/report").status_code == 200
    assert client.get(f"/api/tasks/{task['task_id']}/report.md").status_code == 200


def test_report_unavailable_is_safe_for_queued_and_missing_tasks(client: TestClient) -> None:
    task = client.post("/api/tasks", json=payload("missing-report")).json()

    unavailable = client.get(f"/api/tasks/{task['task_id']}/report")
    missing = client.get("/api/tasks/missing/report.md")

    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "report_unavailable"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "task_not_found"
    assert_safe(unavailable)


def test_terminal_event_stream_emits_compact_task_event_and_security_headers(client: TestClient) -> None:
    task = client.post("/api/tasks", json=payload("event-key")).json()
    client.post(f"/api/tasks/{task['task_id']}/cancel")

    response = client.get(f"/api/tasks/{task['task_id']}/events")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text.startswith('event: task\ndata: {"task_id":')
    assert '"status":"cancelled"' in response.text
    assert SECRET not in response.text


class _Request:
    def __init__(self) -> None:
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


def _as_request(request: _Request | None = None) -> Request:
    return cast(Request, request or _Request())


async def _next(stream: Any) -> str:
    return await anext(stream)


async def _load_task(store: TaskStore, task_id: str) -> Any:
    return store.get(task_id)


def test_event_stream_suppresses_unchanged_versions_emits_change_and_final_then_exits(store: TaskStore) -> None:
    record, _ = store.create(
        TaskCreate.model_validate(payload("sse-task-key")),
        now=NOW,
    )
    request = _Request()
    now = 0.0
    sleeps = 0

    async def sleep(seconds: float) -> None:
        nonlocal now, sleeps
        await asyncio.sleep(0)
        now += seconds
        sleeps += 1
        if sleeps == 2:
            store.cancel_queued(record.task_id, now=NOW + timedelta(seconds=1))

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(request),
            store,
            record.task_id,
            poll_seconds=0.1,
            heartbeat_seconds=15,
            sleep=sleep,
            monotonic=lambda: now,
            wall_clock=lambda: NOW,
            load=lambda task_id: _load_task(store, task_id),
        ).__aiter__()
        initial = await _next(stream)
        final = await _next(stream)
        assert '"status":"queued"' in initial
        assert '"status":"cancelled"' in final
        with pytest.raises(StopAsyncIteration):
            await _next(stream)

    asyncio.run(scenario())


def test_event_stream_heartbeat_at_fifteen_seconds_and_disconnect_exit(store: TaskStore) -> None:
    record, _ = store.create(
        TaskCreate.model_validate(payload("heartbeat-key")),
        now=NOW,
    )
    request = _Request()
    now = 0.0

    async def sleep(seconds: float) -> None:
        nonlocal now
        await asyncio.sleep(0)
        now += seconds

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(request),
            store,
            record.task_id,
            poll_seconds=30,
            heartbeat_seconds=15,
            sleep=sleep,
            monotonic=lambda: now,
            wall_clock=lambda: NOW,
            load=lambda task_id: _load_task(store, task_id),
        ).__aiter__()
        assert '"status":"queued"' in await _next(stream)
        assert await _next(stream) == ": heartbeat\n\n"
        request.disconnected = True
        with pytest.raises(StopAsyncIteration):
            await _next(stream)

    asyncio.run(scenario())


def test_event_stream_does_not_hold_sqlite_transaction_during_wait(config: WebConfig, store: TaskStore) -> None:
    record, _ = store.create(
        TaskCreate.model_validate(payload("db-wait-key")),
        now=NOW,
    )
    second = TaskStore(config.database_path, lease_duration=timedelta(seconds=60))
    request = _Request()
    now = 0.0
    mutated = False

    async def sleep(seconds: float) -> None:
        nonlocal mutated, now
        await asyncio.sleep(0)
        now += seconds
        if not mutated:
            second.cancel_queued(record.task_id, now=NOW + timedelta(seconds=1))
            mutated = True

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(request),
            store,
            record.task_id,
            poll_seconds=0.1,
            heartbeat_seconds=15,
            sleep=sleep,
            monotonic=lambda: now,
            wall_clock=lambda: NOW,
            load=lambda task_id: _load_task(store, task_id),
        ).__aiter__()
        await _next(stream)
        assert '"status":"cancelled"' in await _next(stream)

    asyncio.run(scenario())


def test_event_stream_loads_sqlite_without_blocking_event_loop(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, _ = store.create(TaskCreate.model_validate(payload("async-load-key")), now=NOW)
    original_get = store.get
    order: list[str] = []

    def slow_get(task_id: str) -> Any:
        time.sleep(0.05)
        return original_get(task_id)

    monkeypatch.setattr(store, "get", slow_get)

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(),
            store,
            record.task_id,
            poll_seconds=1,
            sleep=asyncio.sleep,
            monotonic=time.monotonic,
            wall_clock=lambda: NOW,
        ).__aiter__()

        async def consume() -> None:
            await _next(stream)
            order.append("event")

        async def timer() -> None:
            await asyncio.sleep(0.005)
            order.append("timer")

        await asyncio.gather(consume(), timer())

    asyncio.run(scenario())
    assert order == ["timer", "event"]


def test_event_stream_heartbeat_is_not_delayed_by_thirty_second_poll(store: TaskStore) -> None:
    record, _ = store.create(TaskCreate.model_validate(payload("long-poll-key")), now=NOW)
    request = _Request()
    now = 0.0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        nonlocal now
        await asyncio.sleep(0)
        sleeps.append(seconds)
        now += seconds

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(request),
            store,
            record.task_id,
            poll_seconds=30,
            heartbeat_seconds=15,
            sleep=sleep,
            monotonic=lambda: now,
            wall_clock=lambda: NOW,
            load=lambda task_id: _load_task(store, task_id),
        ).__aiter__()
        assert '"status":"queued"' in await _next(stream)
        assert await _next(stream) == ": heartbeat\n\n"
        request.disconnected = True

    asyncio.run(scenario())
    assert sleeps == [15]


def test_event_stream_clamps_heartbeat_to_fifteen_seconds(store: TaskStore) -> None:
    record, _ = store.create(TaskCreate.model_validate(payload("clamped-heartbeat-key")), now=NOW)
    now = 0.0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        nonlocal now
        await asyncio.sleep(0)
        sleeps.append(seconds)
        now += seconds

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(),
            store,
            record.task_id,
            poll_seconds=30,
            heartbeat_seconds=60,
            sleep=sleep,
            monotonic=lambda: now,
            wall_clock=lambda: NOW,
            load=lambda task_id: _load_task(store, task_id),
        ).__aiter__()
        await _next(stream)
        assert await _next(stream) == ": heartbeat\n\n"

    asyncio.run(scenario())
    assert sleeps == [15]


def test_event_stream_prioritizes_due_heartbeat_over_due_database_poll(store: TaskStore) -> None:
    record, _ = store.create(TaskCreate.model_validate(payload("heartbeat-priority-key")), now=NOW)
    now = 0.0
    load_times: list[float] = []

    async def load(task_id: str) -> Any:
        nonlocal now
        load_times.append(now)
        if len(load_times) > 1:
            now += 5
        return store.get(task_id)

    async def sleep(_seconds: float) -> None:
        nonlocal now
        await asyncio.sleep(0)
        now = 15

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(),
            store,
            record.task_id,
            poll_seconds=10,
            heartbeat_seconds=15,
            sleep=sleep,
            monotonic=lambda: now,
            wall_clock=lambda: NOW,
            load=load,
        ).__aiter__()
        await _next(stream)
        assert await _next(stream) == ": heartbeat\n\n"
        assert now == 15

    asyncio.run(scenario())
    assert load_times == [0]


def test_event_stream_races_pending_poll_with_heartbeat_and_reuses_result(store: TaskStore) -> None:
    record, _ = store.create(TaskCreate.model_validate(payload("pending-poll-key")), now=NOW)
    now = 0.0
    load_calls = 0
    release = asyncio.Event()

    async def load(task_id: str) -> Any:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 2:
            await release.wait()
        return store.get(task_id)

    async def sleep(seconds: float) -> None:
        nonlocal now
        await asyncio.sleep(0)
        now += seconds

    async def scenario() -> None:
        stream = TaskEventStream(
            _as_request(),
            store,
            record.task_id,
            poll_seconds=10,
            heartbeat_seconds=15,
            sleep=sleep,
            monotonic=lambda: now,
            wall_clock=lambda: NOW,
            load=load,
        ).__aiter__()
        assert '"status":"queued"' in await _next(stream)
        assert await asyncio.wait_for(_next(stream), timeout=0.1) == ": heartbeat\n\n"
        assert now == 15
        assert load_calls == 2

        store.cancel_queued(record.task_id, now=NOW + timedelta(seconds=1))
        release.set()
        final = await _next(stream)
        assert '"status":"cancelled"' in final
        assert load_calls == 2
        with pytest.raises(StopAsyncIteration):
            await _next(stream)

    asyncio.run(scenario())


def test_frontend_fallback_is_confined_and_does_not_capture_api(config: WebConfig, store: TaskStore) -> None:
    assets = config.frontend_dist / "assets"
    assets.mkdir(parents=True)
    (config.frontend_dist / "index.html").write_text("<main>console</main>")
    (assets / "app.js").write_text("ok")
    client = TestClient(create_app(config, store))

    assert client.get("/").text == "<main>console</main>"
    assert client.get("/tasks/example").text == "<main>console</main>"
    assert client.get("/assets/app.js").text == "ok"
    assert client.get("/assets/%2e%2e/index.html").status_code == 404
    assert client.get("/api/unknown").headers["content-type"].startswith("application/json")
    assert client.get("/assets/").status_code == 404


def test_frontend_is_an_immutable_snapshot_with_cache_policy(
    tmp_path: Path, config: WebConfig, store: TaskStore
) -> None:
    assets = config.frontend_dist / "assets"
    assets.mkdir(parents=True)
    index = config.frontend_dist / "index.html"
    hashed = assets / "app-a1b2c3d4.js"
    index.write_text("<main>safe</main>")
    hashed.write_text("safe-code")
    client = TestClient(create_app(config, store))

    outside_index = tmp_path / "secret-index"
    outside_asset = tmp_path / "secret-asset"
    outside_index.write_text(SECRET)
    outside_asset.write_text(SECRET)
    index.unlink()
    index.symlink_to(outside_index)
    hashed.unlink()
    hashed.symlink_to(outside_asset)

    root = client.get("/")
    asset = client.get("/assets/app-a1b2c3d4.js")
    assert root.text == "<main>safe</main>"
    assert root.headers["cache-control"] == "no-store"
    assert asset.text == "safe-code"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert SECRET not in root.text + asset.text


def test_frontend_snapshot_rejects_oversized_regular_file(config: WebConfig, store: TaskStore) -> None:
    assets = config.frontend_dist / "assets"
    assets.mkdir(parents=True)
    (config.frontend_dist / "index.html").write_text("index")
    (assets / "large.js").write_bytes(b"x" * (8 * 1024 * 1024 + 1))

    client = TestClient(create_app(config, store))

    assert client.get("/").status_code == 503


@pytest.mark.parametrize("link", ["parent", "dist", "index", "assets", "descendant"])
def test_frontend_rejects_symlinked_tree(tmp_path: Path, config: WebConfig, store: TaskStore, link: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("outside")
    (outside / "app.js").write_text("outside")
    dist = config.frontend_dist
    if link == "parent":
        linked_dist = outside / "frontend"
        (linked_dist / "assets").mkdir(parents=True)
        (linked_dist / "index.html").write_text("outside")
        (linked_dist / "assets" / "app.js").write_text("outside")
        config.frontend_dist.parent.symlink_to(outside, target_is_directory=True)
    elif link == "dist":
        dist.parent.mkdir(parents=True)
        dist.symlink_to(outside, target_is_directory=True)
    else:
        assets = dist / "assets"
        assets.mkdir(parents=True)
        (dist / "index.html").write_text("index")
        (assets / "app.js").write_text("ok")
        if link == "index":
            (dist / "index.html").unlink()
            (dist / "index.html").symlink_to(outside / "index.html")
        elif link == "assets":
            for child in assets.iterdir():
                child.unlink()
            assets.rmdir()
            assets.symlink_to(outside, target_is_directory=True)
        else:
            (assets / "linked.js").symlink_to(outside / "app.js")

    client = TestClient(create_app(config, store))

    assert client.get("/").status_code == 503
    assert client.get("/assets/app.js").status_code == 503


def test_frontend_rejects_dist_outside_root_deploy(tmp_path: Path, config: WebConfig, store: TaskStore) -> None:
    outside = tmp_path / "outside-dist"
    (outside / "assets").mkdir(parents=True)
    (outside / "index.html").write_text("outside")
    unsafe = config.model_copy(update={"frontend_dist": outside})

    client = TestClient(create_app(unsafe, store))

    assert client.get("/").status_code == 503

    (config.root / "deploy").mkdir()
    lexical_escape = config.model_copy(update={"frontend_dist": config.root / "deploy" / ".." / "outside-dist"})
    escaped_client = TestClient(create_app(lexical_escape, store))
    assert escaped_client.get("/").status_code == 503


class _BatchCatalog:
    instance_ids = tuple(f"instance_org__repo-{letter}" for letter in "abcde")

    def __init__(self, instance_ids: tuple[str, ...] | None = None) -> None:
        if instance_ids is not None:
            self.instance_ids = instance_ids
        labels = tuple("abcde"[index] if index < 5 else str(index) for index in range(len(self.instance_ids)))
        self.instances = {
            instance_id: SimpleNamespace(
                instance_id=instance_id,
                repo=f"org/repo-{label}",
                problem_statement=f"Fix the complete problem for repository {label}.",
                fail_to_pass=(f"test_fix_{label}",),
                pass_to_pass=(f"test_regression_{label}",),
                test_patch=f"diff --git a/test_{label}.py b/test_{label}.py\n",
                selected_test_files_to_run=json.dumps([f"test_{label}.py"]),
            )
            for label, instance_id in zip(labels, self.instance_ids, strict=True)
        }

    def require(self, instance_id: str) -> SweBenchProInstance:
        return cast(SweBenchProInstance, self.instances[instance_id])


def _batch_payload(
    key: str = "batch-api-key",
    *,
    model: str = "gpt-5.6-sol",
    initial_control_intent: str = "run",
) -> dict[str, object]:
    return {
        "powercontext_ref": "commit:" + "a" * 40,
        "benchmark": "swebench-pro",
        "task_set": "swebench-pro-public-v2",
        "model": model,
        "reasoning_effort": "medium",
        "treatment_mode": "off_on",
        "idempotency_key": key,
        "initial_control_intent": initial_control_intent,
    }


def _write_batch_artifacts(
    config: WebConfig,
    task_id: str,
    instance_id: str,
    *,
    off_resolved: bool,
    on_resolved: bool,
    off_tokens: tuple[int, int],
    on_tokens: tuple[int, int],
    gold_validation: GoldValidationAudit | None = None,
) -> None:
    run_dir = config.run_root / "runs" / task_id
    run_dir.mkdir(parents=True)

    def arm(name: Literal["off", "on"], resolved: bool, tokens: tuple[int, int]) -> ArmReport:
        failed = () if resolved else (f"test_fix_{instance_id[-1]}",)
        return ArmReport(
            arm=name,
            state=ArmState.TREATMENT_VALIDATED,
            resolved=resolved,
            passed=resolved,
            treatment_valid=True,
            patch_applied=True,
            fail_to_pass=TestGroupReport(
                passed=1 if resolved else 0,
                total=1,
                failed=failed,
            ),
            pass_to_pass=TestGroupReport(passed=1, total=1),
            log_excerpt=None if resolved else "required test failed",
            metrics=MetricSet(input_tokens=tokens[0], output_tokens=tokens[1]),
        )

    bundle = ReportBundle(
        title="SWE-bench Pro evaluation",
        revisions={
            "dataset": "7ab5114912baf22bb098818e604c02fe7ad2c11f",
            "harness": "ca10a60a5fcae51e6948ffe1485d4153d421e6c5",
            "powercontext": "a" * 40,
        },
        configuration={
            "codex": "0.145.0",
            "instance": instance_id,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
        },
        off=arm("off", off_resolved, off_tokens),
        on=arm("on", on_resolved, on_tokens),
        gold_validation=gold_validation,
    )
    (run_dir / "report.json").write_text(bundle.model_dump_json())
    for arm_name in ("off", "on"):
        context = run_dir / "arms" / arm_name / "context"
        context.mkdir(parents=True)
        events = [
            {
                "sequence": 1,
                "observed_at": "2026-07-29T08:10:11.100000Z",
                "elapsed_ms": 0,
                "arm": arm_name,
                "actor": "benchmark",
                "event_type": "benchmark_prompt",
                "input": {"prompt": f"full prompt for {instance_id}"},
                "output": None,
                "source_artifact": "instance.jsonl",
                "source_sequence": 0,
            },
            {
                "sequence": 2,
                "observed_at": "2026-07-29T08:10:11.200000Z",
                "elapsed_ms": 100,
                "arm": arm_name,
                "actor": "powercontext" if arm_name == "on" else "codex",
                "event_type": "powercontext_injection" if arm_name == "on" else "agent_message",
                "input": {"query": "fix context"} if arm_name == "on" else None,
                "output": (
                    {"injected_text": "PowerContext recalled exact context.", "hits": [{"text": "exact context"}]}
                    if arm_name == "on"
                    else {"event": {"type": "agent_message", "message": "done"}}
                ),
                "source_artifact": (
                    "context/powercontext-injections.jsonl" if arm_name == "on" else "context/codex-observed.jsonl"
                ),
                "source_sequence": 1,
            },
        ]
        (context / "timeline.jsonl").write_text(
            "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)
        )


def _finish_batch(
    config: WebConfig,
    store: TaskStore,
    batch_id: str,
) -> list[TaskRecord]:
    outcomes = [
        (False, True, (100, 10), (80, 8)),
        (True, False, (120, 12), (160, 16)),
        (True, True, (90, 9), (85, 8)),
        (False, False, (130, 13), (110, 11)),
    ]
    children = store.list_batch_tasks(batch_id)
    started = datetime.now(UTC) + timedelta(seconds=1)
    for index, (off_resolved, on_resolved, off_tokens, on_tokens) in enumerate(outcomes):
        claimed = store.claim_next("batch-worker", now=started + timedelta(seconds=index * 2))
        assert claimed is not None and claimed.task_id == children[index].task_id
        _write_batch_artifacts(
            config,
            claimed.task_id,
            claimed.request.instance_id,
            off_resolved=off_resolved,
            on_resolved=on_resolved,
            off_tokens=off_tokens,
            on_tokens=on_tokens,
        )
        run_dir = config.run_root / "runs" / claimed.task_id
        store.succeed(
            claimed.task_id,
            "batch-worker",
            TaskResult(
                artifact_dir=str(run_dir.relative_to(config.run_root)),
                report_path=str((run_dir / "report.json").relative_to(config.run_root)),
                off_resolved=off_resolved,
                on_resolved=on_resolved,
            ),
            now=started + timedelta(seconds=index * 2 + 1),
        )
    failed = store.claim_next("batch-worker", now=started + timedelta(seconds=10))
    assert failed is not None and failed.task_id == children[4].task_id
    store.fail(
        failed.task_id,
        "batch-worker",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            failure_code=FailureCode.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_ON,
            summary="Codex execution did not complete",
            retry_disposition=RetryDisposition.TERMINAL,
        ),
        now=started + timedelta(seconds=11),
    )
    store.pin_batch_revision(batch_id, "a" * 40)
    return children


def test_batch_preview_is_read_only_and_exposes_fixed_facts_usage_and_estimate(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))

    response = client.post(
        "/api/batches/preview",
        json={"powercontext_ref": "latest", "usage_pause_percent": 75},
    )

    assert response.status_code == 200
    assert store.list_batches() == []
    assert response.json() == {
        "powercontext_ref": "latest",
        "benchmark": "swebench-pro",
        "task_set": "swebench-pro-public-v2",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "treatment_mode": "off_on",
        "total_tasks": 5,
        "usage_pause_percent": 75,
        "usage": response.json()["usage"],
        "estimate": {
            "quality": "unavailable",
            "basis": "none",
            "sample_size": 0,
            "remaining_tasks": 5,
            "remaining_tokens": None,
            "remaining_duration_seconds": None,
            "low_tokens": None,
            "high_tokens": None,
            "low_duration_seconds": None,
            "high_duration_seconds": None,
        },
        "can_start": True,
        "block_reason": None,
    }
    assert response.json()["usage"]["used_percent"] == 9


def test_stability_batch_preview_and_create_use_the_exact_pinned_subset(
    config: WebConfig,
    store: TaskStore,
) -> None:
    public_ids = [f"unselected-{index}" for index in range(731)]
    for source_index, instance_id in STABILITY_V1_CASES:
        public_ids[source_index] = instance_id
    client = TestClient(create_app(config, store, catalog=_BatchCatalog(tuple(public_ids))))

    preview = client.post(
        "/api/batches/preview",
        json={"powercontext_ref": "latest", "task_set": STABILITY_V1_TASK_SET, "usage_pause_percent": 75},
    )
    request = _batch_payload("stability-v1-batch")
    request["task_set"] = STABILITY_V1_TASK_SET
    created = client.post("/api/batches", json=request)

    assert preview.status_code == 200
    assert preview.json()["task_set"] == STABILITY_V1_TASK_SET
    assert preview.json()["total_tasks"] == 24
    assert created.status_code == 201
    assert created.json()["request"]["task_set"] == STABILITY_V1_TASK_SET
    assert created.json()["total_tasks"] == 24
    assert [task.instance_id for task in store.list_batch_tasks(created.json()["batch_id"])] == [
        instance_id for _, instance_id in STABILITY_V1_CASES
    ]


def test_luna_preview_batch_tasks_detail_and_report_keep_the_requested_model(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog()
    configured = config.model_copy(update={"codex_models": ("gpt-5.6-sol", "gpt-5.6-luna")})
    client = TestClient(create_app(configured, store, catalog=catalog))

    preview = client.post(
        "/api/batches/preview",
        json={"powercontext_ref": "latest", "model": "gpt-5.6-luna", "usage_pause_percent": 75},
    )
    created = client.post(
        "/api/batches",
        json=_batch_payload("luna-api-batch", model="gpt-5.6-luna"),
    )
    batch_id = created.json()["batch_id"]
    task = store.list_batch_tasks(batch_id)[0]
    tasks = client.get(f"/api/batches/{batch_id}/tasks")
    detail = client.get(f"/api/batches/{batch_id}/tasks/{task.task_id}")
    report = client.get(f"/api/batches/{batch_id}/report")

    assert preview.status_code == 200
    assert preview.json()["model"] == "gpt-5.6-luna"
    assert created.status_code == 201
    assert created.json()["request"]["model"] == "gpt-5.6-luna"
    assert tasks.json()["items"][0]["model"] == "gpt-5.6-luna"
    assert tasks.json()["items"][0]["reasoning_effort"] == "medium"
    assert detail.json()["task"]["model"] == "gpt-5.6-luna"
    assert report.json()["configuration"]["model"] == "gpt-5.6-luna"


def test_batch_task_detail_exposes_only_selected_attempt_finalization_summary(
    config: WebConfig,
    store: TaskStore,
) -> None:
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    created = client.post("/api/batches", json=_batch_payload("finalization-detail"))
    batch_id = created.json()["batch_id"]
    task = store.list_batch_tasks(batch_id)[0]
    assert task.attempt_id is not None

    jobs = {}
    for arm in ("off", "on"):
        jobs[arm] = store.register_tokensflow_finalization(
            TokensFlowFinalizationCreate(
                attempt_id=task.attempt_id,
                task_id=task.task_id,
                batch_id=batch_id,
                arm=arm,
                run_id=task.task_id,
                container_name=f"powercontext-eval-{task.task_id}-{arm}",
                runtime_path=f"work/{task.task_id}/{arm}/runtime",
                wrapper_path=f"work/{task.task_id}/{arm}/evaluation-control/tokensflow-wrapper",
                egress_network="tokensflow-egress",
                daemon_pid_file="/runtime/tokensflow-home/.local/share/tokensflow/evaluation-daemon.pid",
                evidence_sha256=("b" if arm == "off" else "c") * 64,
                evidence_bytes=456,
            ),
            now=NOW,
            timeout_seconds=600,
        )[0]
    claimed = store.claim_tokensflow_finalization(
        "api-test-finalizer",
        now=NOW + timedelta(seconds=1),
        lease_seconds=300,
        job_id=jobs["off"].job_id,
    )
    assert claimed is not None
    store.finish_tokensflow_finalization(
        claimed.job_id,
        "api-test-finalizer",
        state=FinalizationState.PASSED,
        now=NOW + timedelta(seconds=2),
    )

    response = client.get(f"/api/batches/{batch_id}/tasks/{task.task_id}")

    assert response.status_code == 200
    finalization = response.json()["tokensflow_finalization"]
    assert finalization["off"]["state"] == "passed"
    assert finalization["on"]["state"] == "pending"
    expected_keys = {
        "state",
        "registered_at",
        "deadline_at",
        "finished_at",
        "attempts",
        "queue_passed",
        "doctor_rc",
        "error_category",
        "reason",
    }
    assert set(finalization["off"]) == set(finalization["on"]) == expected_keys
    serialized = json.dumps(finalization).casefold()
    for forbidden in ("container", "runtime", "wrapper", "home", "credential", "token", "doctor_output"):
        assert forbidden not in serialized


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6-luna"])
def test_api_can_create_batch_atomically_paused_without_a_claimable_window(
    config: WebConfig,
    store: TaskStore,
    model: str,
) -> None:
    configured = config.model_copy(update={"codex_models": ("gpt-5.6-sol", model)} if model != "gpt-5.6-sol" else {})
    client = TestClient(create_app(configured, store, catalog=_BatchCatalog()))

    response = client.post(
        "/api/batches",
        json=_batch_payload(f"paused-api-{model}", model=model, initial_control_intent="pause"),
    )

    assert response.status_code == 201
    assert response.json()["request"]["initial_control_intent"] == "pause"
    assert response.json()["status"] == "paused"
    assert response.json()["control"]["intent"] == "pause"
    assert response.json()["control"]["pause_reason"] == "user"
    assert store.claim_next("api-racing-worker", now=NOW, max_concurrency=10) is None


def test_batch_preview_and_confirmation_fail_closed_without_fresh_usage(tmp_path: Path) -> None:
    root = tmp_path / "no-current-usage"
    config = WebConfig.for_root(root, tokensflow_egress_network="bridge", proxy_url="http://127.0.0.1:8081")
    store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    store.initialize()
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))

    preview = client.post(
        "/api/batches/preview",
        json={"powercontext_ref": "latest", "usage_pause_percent": 80},
    )
    created = client.post("/api/batches", json=_batch_payload("batch-no-usage-key"))

    assert preview.status_code == created.status_code == 503
    assert preview.json()["error"]["code"] == created.json()["error"]["code"] == "usage_unavailable"
    assert store.list_batches() == []


def test_api_key_mode_does_not_require_subscription_usage_for_preview_or_creation(tmp_path: Path) -> None:
    root = tmp_path / "api-key-mode"
    config = WebConfig.for_root(
        root, tokensflow_egress_network="bridge", proxy_url="http://127.0.0.1:8081", usage_mode="api_key"
    )
    store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    store.initialize()
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))

    preview = client.post(
        "/api/batches/preview",
        json={"powercontext_ref": "latest", "usage_pause_percent": 80},
    )
    created = client.post("/api/batches", json=_batch_payload("batch-api-key-mode"))
    usage = client.get("/api/account-usage")

    assert preview.status_code == 200
    assert preview.json()["usage"] is None
    assert preview.json()["can_start"] is True
    assert created.status_code == 201
    assert usage.status_code == 200
    assert usage.json() == {"mode": "api_key", "sufficient": True, "usage": None}


def test_batch_confirmation_rejects_usage_at_the_selected_threshold(
    config: WebConfig,
    store: TaskStore,
) -> None:
    store.save_usage_snapshot(_usage(80))
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    request = _batch_payload("batch-threshold-key")
    request["usage_pause_percent"] = 80

    response = client.post("/api/batches", json=request)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "usage_threshold_reached"
    assert store.list_batches() == []


def test_batch_confirmation_pins_an_exact_commit_before_queuing_children(
    config: WebConfig,
    store: TaskStore,
) -> None:
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))

    response = client.post("/api/batches", json=_batch_payload("batch-pinned-commit-key"))

    assert response.status_code == 201
    assert response.json()["resolved_powercontext_sha"] == "a" * 40
    batch = store.get_batch(response.json()["batch_id"])
    assert batch.resolved_powercontext_sha == "a" * 40
    assert len(store.list_batch_tasks(batch.batch_id)) == len(_BatchCatalog.instance_ids)


def test_batch_confirmation_rejects_unresolvable_latest_before_creating_children(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing-source"
    config = WebConfig.for_root(
        root,
        powercontext_source=root / "source" / "powercontext.git",
        tokensflow_egress_network="bridge",
        proxy_url="http://127.0.0.1:8081",
    )
    store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    store.initialize()
    store.save_usage_snapshot(_usage(9))
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    request = _batch_payload("batch-unresolvable-source-key")
    request["powercontext_ref"] = "latest"

    response = client.post("/api/batches", json=request)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "source_unavailable",
            "message": "The selected PowerContext source could not be resolved.",
        }
    }
    assert store.list_batches() == []


def test_batch_confirmation_rejects_unresolvable_exact_commit_before_creating_children(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing-exact-commit"
    config = WebConfig.for_root(
        root,
        powercontext_source=root / "source" / "powercontext.git",
        tokensflow_egress_network="bridge",
        proxy_url="http://127.0.0.1:8081",
    )
    store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    store.initialize()
    store.save_usage_snapshot(_usage(9))
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    request = _batch_payload("batch-unresolvable-commit-key")
    request["powercontext_ref"] = "commit:" + "b" * 40

    response = client.post("/api/batches", json=request)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "source_unavailable",
            "message": "The selected PowerContext source could not be resolved.",
        }
    }
    assert store.list_batches() == []


def test_batch_control_usage_attempt_and_retry_routes_are_durable(
    config: WebConfig,
    store: TaskStore,
) -> None:
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    batch = client.post("/api/batches", json=_batch_payload("batch-controls-key")).json()

    paused = client.post(f"/api/batches/{batch['batch_id']}/pause")
    resumed = client.post(f"/api/batches/{batch['batch_id']}/resume")
    patched = client.patch(
        f"/api/batches/{batch['batch_id']}/controls",
        json={"usage_pause_percent": 75, "expected_version": resumed.json()["control"]["version"]},
    )
    stale_patch = client.patch(
        f"/api/batches/{batch['batch_id']}/controls",
        json={"usage_pause_percent": 70, "expected_version": 0},
    )
    usage = client.get("/api/account-usage")
    events = client.get(f"/api/batches/{batch['batch_id']}/control-events")

    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "queued"
    assert patched.json()["control"]["usage_pause_percent"] == 75
    assert stale_patch.status_code == 409
    assert stale_patch.json()["error"]["code"] == "batch_control_version_conflict"
    assert usage.json()["mode"] == "subscription"
    assert usage.json()["sufficient"] is True
    assert usage.json()["usage"]["used_percent"] == 9
    assert [event["event_type"] for event in events.json()] == [
        "batch_created",
        "pause_requested",
        "paused",
        "resume_requested",
        "resumed",
        "threshold_changed",
    ]

    task = store.list_batch_tasks(batch["batch_id"])[0]
    started = datetime.now(UTC) + timedelta(seconds=1)
    claimed = store.claim_next("batch-worker", now=started)
    assert claimed is not None
    store.fail(
        task.task_id,
        "batch-worker",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            failure_code=FailureCode.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Codex execution did not complete",
            retry_disposition=RetryDisposition.TERMINAL,
        ),
        now=started + timedelta(seconds=1),
    )
    assert (
        _complete_failed_attempt_cleanup(
            store,
            task.task_id,
            now=started + timedelta(seconds=2),
        )
        is False
    )
    retry_request = {"idempotency_key": "api-retry-0001"}
    retried = client.post(
        f"/api/batches/{batch['batch_id']}/tasks/{task.task_id}/retry",
        json=retry_request,
    )
    replayed = client.post(
        f"/api/batches/{batch['batch_id']}/tasks/{task.task_id}/retry",
        json=retry_request,
    )
    attempts = client.get(f"/api/batches/{batch['batch_id']}/tasks/{task.task_id}/attempts")

    assert retried.status_code == 201
    assert replayed.status_code == 200
    assert retried.json() == replayed.json()
    assert [attempt["attempt_number"] for attempt in attempts.json()] == [1, 2]
    assert attempts.json()[0]["failure_summary"] == "Codex execution did not complete"


def test_batch_runtime_lists_running_and_retry_waiting_tasks_without_secrets(
    config: WebConfig,
    store: TaskStore,
) -> None:
    client = TestClient(create_app(config, store, catalog=_BatchCatalog()))
    batch = client.post("/api/batches", json=_batch_payload("batch-runtime-key")).json()
    started = datetime.now(UTC) + timedelta(seconds=1)

    running = store.claim_next("runtime-worker-1", now=started, max_concurrency=2)
    assert running is not None
    store.set_phase(running.task_id, "runtime-worker-1", TaskPhase.RUNNING_ON, now=started)
    failed = store.claim_next("runtime-worker-2", now=started, max_concurrency=2)
    assert failed is not None
    store.fail(
        failed.task_id,
        "runtime-worker-2",
        SafeFailure(
            category=FailureCategory.REPORT_GENERATION,
            failure_code=FailureCode.REPORT_GENERATION,
            phase=TaskPhase.GENERATING_REPORT,
            summary="Report assembly failed safely",
            retry_disposition=RetryDisposition.RETRY,
        ),
        now=started + timedelta(seconds=1),
    )
    assert _complete_failed_attempt_cleanup(store, failed.task_id, now=started + timedelta(seconds=2)) is True

    response = client.get(f"/api/batches/{batch['batch_id']}/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["batch_id"] == batch["batch_id"]
    assert payload["status_counts"] == {
        "queued": 4,
        "running": 1,
        "succeeded": 0,
        "failed": 0,
        "interrupted": 0,
        "cancelled": 0,
    }
    assert len(payload["tasks"]) == 2
    running_payload = next(task for task in payload["tasks"] if task["status"] == "running")
    retry_payload = next(task for task in payload["tasks"] if task["status"] == "queued")
    assert running_payload["task_id"] == running.task_id
    assert running_payload["phase"] == "running_on"
    assert retry_payload["task_id"] == failed.task_id
    assert retry_payload["attempt_number"] == 2
    assert retry_payload["last_failure"] == {
        "category": "report_generation_failure",
        "code": "report_generation",
        "phase": "generating_report",
        "summary": "Report assembly failed safely",
        "finished_at": (started + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
    }
    assert_safe(response)


def test_batch_runtime_rejects_an_unknown_batch(config: WebConfig, store: TaskStore) -> None:
    response = TestClient(create_app(config, store, catalog=_BatchCatalog())).get("/api/batches/missing/runtime")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "batch_not_found"


def test_batch_api_creates_replays_lists_gets_and_cancels_the_complete_catalog(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))
    store.create(TaskCreate.model_validate(payload("legacy-before-batch")), now=NOW)

    created = client.post("/api/batches", json=_batch_payload("batch-create-key"))
    replay = client.post("/api/batches", json=_batch_payload("batch-create-key"))
    listed = client.get("/api/batches")
    detail = client.get(f"/api/batches/{created.json()['batch_id']}")

    assert created.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["batch_id"] == created.json()["batch_id"]
    assert created.json()["total_tasks"] == 5
    assert [batch["batch_id"] for batch in listed.json()] == [created.json()["batch_id"]]
    assert detail.json()["status"] == "queued"
    assert [task.instance_id for task in store.list_batch_tasks(created.json()["batch_id"])] == list(
        catalog.instance_ids
    )

    cancelled = client.post(f"/api/batches/{created.json()['batch_id']}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert all(task.status.value == "cancelled" for task in store.list_batch_tasks(created.json()["batch_id"]))
    events = client.get(f"/api/batches/{created.json()['batch_id']}/events")
    assert events.status_code == 200
    assert events.text.startswith("event: batch\ndata: ")
    assert '"status":"cancelled"' in events.text
    assert client.get("/api/batches/missing").status_code == 404


def test_batch_report_reconciles_resolution_pairs_failures_and_total_tokens(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload("batch-report-key")).json()
    _finish_batch(config, store, batch["batch_id"])

    response = client.get(f"/api/batches/{batch['batch_id']}/report")

    assert response.status_code == 200
    report = response.json()
    assert report["total_tasks"] == report["terminal_tasks"] == 5
    assert report["comparable_pairs"] == 4
    assert report["execution_failures"] == 1
    assert report["off"] == {"resolved": 2, "total": 5, "rate_percent": 40.0}
    assert report["on"] == {"resolved": 2, "total": 5, "rate_percent": 40.0}
    assert report["resolution_rate_delta_points"] == 0.0
    assert report["pair_categories"] == {
        "off_fail_on_pass": 1,
        "off_pass_on_fail": 1,
        "both_pass": 1,
        "both_fail": 1,
        "execution_failure": 1,
    }
    assert report["tokens"]["input"] == {
        "off": 440,
        "on": 435,
        "delta": -5,
        "off_measured_tasks": 4,
        "on_measured_tasks": 4,
    }
    assert report["tokens"]["output"]["off"] == 44
    assert report["tokens"]["total"]["on"] == 478
    assert report["control"]["intent"] == "run"
    assert report["control"]["pause_reason"] is None
    assert report["latest_usage"]["used_percent"] == 9
    assert report["estimate"] == {
        "quality": "preliminary",
        "basis": "current_batch",
        "sample_size": 4,
        "remaining_tasks": 0,
        "remaining_tokens": 0,
        "remaining_duration_seconds": 0,
        "low_tokens": 0,
        "high_tokens": 0,
        "low_duration_seconds": 0,
        "high_duration_seconds": 0,
    }
    assert report["report_revision"] > 0
    assert "acceptance_valid" not in report
    assert "conclusion" not in report
    assert "patch_bytes" not in response.text
    assert "treatment_valid" not in response.text
    assert "elapsed" not in response.text


def test_batch_report_mixes_legacy_and_source559_audited_reports_without_changing_aggregates(
    config: WebConfig,
    store: TaskStore,
) -> None:
    source559_audit = GoldValidationAudit(
        instance_id=SOURCE559_INSTANCE_ID,
        mode="verified_override",
        dataset_patch_sha256=SOURCE559_DATASET_PATCH_SHA256,
        validation_patch_sha256=SOURCE559_REFERENCE_PATCH_SHA256,
        dataset_patch_status="known_failed",
        reference_validation_status="passed",
        attempt_gold_validation_status="passed",
        official_evaluation_transport="proxy_bypassed_for_test_isolation",
        source_dataset=SOURCE559_REFERENCE_DATASET,
        source_revision=SOURCE559_REFERENCE_REVISION,
        source_file_oid=SOURCE559_REFERENCE_FILE_OID,
        source_kind="verified_reference_submission",
    )
    catalog = _BatchCatalog(("instance_org__repo-a", SOURCE559_INSTANCE_ID))
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload("mixed-audit-report")).json()
    children = store.list_batch_tasks(batch["batch_id"])
    started = datetime.now(UTC) + timedelta(seconds=1)
    outcomes = ((True, False, None), (False, True, source559_audit))
    for index, (off_resolved, on_resolved, audit) in enumerate(outcomes):
        claimed = store.claim_next("batch-worker", now=started + timedelta(seconds=index * 2))
        assert claimed is not None and claimed.task_id == children[index].task_id
        _write_batch_artifacts(
            config,
            claimed.task_id,
            claimed.request.instance_id,
            off_resolved=off_resolved,
            on_resolved=on_resolved,
            off_tokens=(100 + index, 10 + index),
            on_tokens=(80 + index, 8 + index),
            gold_validation=audit,
        )
        run_dir = config.run_root / "runs" / claimed.task_id
        store.succeed(
            claimed.task_id,
            "batch-worker",
            TaskResult(
                artifact_dir=str(run_dir.relative_to(config.run_root)),
                report_path=str((run_dir / "report.json").relative_to(config.run_root)),
                off_resolved=off_resolved,
                on_resolved=on_resolved,
            ),
            now=started + timedelta(seconds=index * 2 + 1),
        )

    report = client.get(f"/api/batches/{batch['batch_id']}/report")

    assert report.status_code == 200
    payload = report.json()
    assert payload["total_tasks"] == payload["terminal_tasks"] == payload["comparable_pairs"] == 2
    assert payload["off"] == {"resolved": 1, "total": 2, "rate_percent": 50.0}
    assert payload["on"] == {"resolved": 1, "total": 2, "rate_percent": 50.0}
    assert payload["resolution_rate_delta_points"] == 0.0
    assert payload["pair_categories"] == {
        "off_fail_on_pass": 1,
        "off_pass_on_fail": 1,
        "both_pass": 0,
        "both_fail": 0,
        "execution_failure": 0,
    }
    assert payload["tokens"]["input"] == {
        "off": 201,
        "on": 161,
        "delta": -40,
        "off_measured_tasks": 2,
        "on_measured_tasks": 2,
    }
    assert payload["tokens"]["output"] == {
        "off": 21,
        "on": 17,
        "delta": -4,
        "off_measured_tasks": 2,
        "on_measured_tasks": 2,
    }


def test_batch_preview_uses_only_complete_historical_pair_measurements(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload("batch-estimate-history-key")).json()
    _finish_batch(config, store, batch["batch_id"])

    response = client.post(
        "/api/batches/preview",
        json={"powercontext_ref": "latest", "usage_pause_percent": 80},
    )

    assert response.status_code == 200
    estimate = response.json()["estimate"]
    assert estimate["quality"] == "preliminary"
    assert estimate["basis"] == "historical_compatible"
    assert estimate["sample_size"] == 4
    assert estimate["remaining_tasks"] == 5
    assert estimate["remaining_tokens"] == 1_203
    assert estimate["remaining_duration_seconds"] == 5


def test_batch_token_totals_use_only_pairs_with_both_arm_measurements(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload("batch-paired-token-key")).json()
    children = _finish_batch(config, store, batch["batch_id"])
    report_path = config.run_root / "runs" / children[0].task_id / "report.json"
    bundle = json.loads(report_path.read_text())
    bundle["on"]["metrics"]["output_tokens"] = None
    report_path.write_text(json.dumps(bundle))

    response = client.get(f"/api/batches/{batch['batch_id']}/report")

    assert response.status_code == 200
    tokens = response.json()["tokens"]
    assert tokens["input"]["off_measured_tasks"] == tokens["input"]["on_measured_tasks"] == 4
    assert tokens["output"] == {
        "off": 34,
        "on": 35,
        "delta": 1,
        "off_measured_tasks": 3,
        "on_measured_tasks": 3,
    }
    assert tokens["total"] == {
        "off": 374,
        "on": 390,
        "delta": 16,
        "off_measured_tasks": 3,
        "on_measured_tasks": 3,
    }


def test_batch_report_uses_the_successful_retry_once_and_reads_its_attempt_artifacts(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog(("instance_org__repo-a",))
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload("batch-retry-report-key")).json()
    task = store.list_batch_tasks(batch["batch_id"])[0]
    started = datetime.now(UTC) + timedelta(seconds=1)
    claimed = store.claim_next("batch-worker", now=started)
    assert claimed is not None and claimed.task_id == task.task_id
    store.fail(
        task.task_id,
        "batch-worker",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            failure_code=FailureCode.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="First attempt failed",
            retry_disposition=RetryDisposition.TERMINAL,
        ),
        now=started + timedelta(seconds=1),
    )
    assert (
        _complete_failed_attempt_cleanup(
            store,
            task.task_id,
            now=started + timedelta(seconds=2),
        )
        is False
    )
    retry, created = store.retry_failed_task(
        batch["batch_id"],
        task.task_id,
        idempotency_key="retry-report-0001",
        now=started + timedelta(seconds=2),
    )
    assert created is True
    store.request_resume(
        batch["batch_id"],
        now=started + timedelta(seconds=3),
    )
    claimed_retry = store.claim_next("batch-worker", now=started + timedelta(seconds=3))
    assert claimed_retry is not None and claimed_retry.attempt_id == retry.attempt_id
    retry_run_id = f"{task.task_id}-attempt-{retry.attempt_number:04d}"
    _write_batch_artifacts(
        config,
        retry_run_id,
        task.request.instance_id,
        off_resolved=False,
        on_resolved=True,
        off_tokens=(100, 10),
        on_tokens=(80, 8),
    )
    _write_treatment_evidence(config.run_root / "runs", retry_run_id)
    run_dir = config.run_root / "runs" / retry_run_id
    (run_dir / "report.md").write_text("# Successful retry\n")
    store.succeed(
        task.task_id,
        "batch-worker",
        TaskResult(
            artifact_dir=str(run_dir.relative_to(config.run_root)),
            report_path=str((run_dir / "report.json").relative_to(config.run_root)),
            off_resolved=False,
            on_resolved=True,
        ),
        now=started + timedelta(seconds=4),
    )

    report = client.get(f"/api/batches/{batch['batch_id']}/report")
    task_report = client.get(f"/api/tasks/{task.task_id}/report")
    raw_task_report = client.get(f"/api/tasks/{task.task_id}/report.md")
    task_page = client.get(f"/api/batches/{batch['batch_id']}/tasks")
    latest_detail = client.get(f"/api/batches/{batch['batch_id']}/tasks/{task.task_id}")
    first_detail = client.get(
        f"/api/batches/{batch['batch_id']}/tasks/{task.task_id}",
        params={"attempt_id": f"{task.task_id}.attempt-0001"},
    )
    missing_attempt = client.get(
        f"/api/batches/{batch['batch_id']}/tasks/{task.task_id}",
        params={"attempt_id": f"{task.task_id}.attempt-9999"},
    )

    assert report.status_code == 200
    assert report.json()["comparable_pairs"] == 1
    assert report.json()["execution_failures"] == 0
    assert report.json()["on"]["resolved"] == 1
    assert task_report.status_code == 200
    assert task_report.json()["task_id"] == task.task_id
    assert raw_task_report.status_code == 200
    assert raw_task_report.text == "# Successful retry\n"
    item = task_page.json()["items"][0]
    assert item["attempt_number"] == item["attempt_count"] == 2
    assert item["pair_category"] == "off_fail_on_pass"
    assert latest_detail.json()["task"]["attempt_number"] == 2
    assert first_detail.status_code == 200
    assert first_detail.json()["task"]["attempt_number"] == 1
    assert first_detail.json()["task"]["failure_summary"] == "First attempt failed"
    assert missing_attempt.status_code == 404
    assert missing_attempt.json()["error"]["code"] == "attempt_not_found"
    assert [attempt.failure_summary for attempt in store.list_task_attempts(batch["batch_id"], task.task_id)] == [
        "First attempt failed",
        None,
    ]


def test_batch_task_report_filters_searches_sorts_and_drills_into_full_context(
    config: WebConfig,
    store: TaskStore,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload("batch-detail-key")).json()
    children = _finish_batch(config, store, batch["batch_id"])

    negative = client.get(
        f"/api/batches/{batch['batch_id']}/tasks",
        params={"category": "off_pass_on_fail"},
    )
    searched = client.get(
        f"/api/batches/{batch['batch_id']}/tasks",
        params={"q": "repo-c"},
    )
    sorted_page = client.get(
        f"/api/batches/{batch['batch_id']}/tasks",
        params={"sort": "token_delta_desc", "limit": 2},
    )

    assert negative.status_code == 200
    assert negative.json()["total"] == 1
    assert negative.json()["items"][0]["instance_id"] == catalog.instance_ids[1]
    assert negative.json()["items"][0]["off"]["resolved"] is True
    assert negative.json()["items"][0]["on"]["resolved"] is False
    assert negative.json()["items"][0]["tokens"]["delta"] == 44
    assert searched.json()["items"][0]["repository"] == "org/repo-c"
    assert [item["instance_id"] for item in sorted_page.json()["items"]] == [
        catalog.instance_ids[1],
        catalog.instance_ids[2],
    ]

    task_id = children[1].task_id
    detail = client.get(f"/api/batches/{batch['batch_id']}/tasks/{task_id}")
    timeline = client.get(
        f"/api/batches/{batch['batch_id']}/tasks/{task_id}/context/on",
        params={"limit": 1, "offset": 1},
    )
    event = client.get(f"/api/batches/{batch['batch_id']}/tasks/{task_id}/context/on/2")

    assert detail.status_code == 200
    assert detail.json()["problem_statement"] == "Fix the complete problem for repository b."
    assert detail.json()["required_tests"] == {
        "fail_to_pass": ["test_fix_b"],
        "pass_to_pass": ["test_regression_b"],
        "selected_test_files_to_run": '["test_b.py"]',
        "test_patch": "diff --git a/test_b.py b/test_b.py\n",
    }
    assert detail.json()["off"]["resolved"] is True
    assert detail.json()["on"]["resolved"] is False
    assert detail.json()["on"]["fail_to_pass"]["failed"] == ["test_fix_b"]
    assert timeline.json()["total"] == 2
    assert timeline.json()["items"][0]["sequence"] == 2
    assert timeline.json()["items"][0]["output"]["injected_text"] == "PowerContext recalled exact context."
    assert event.json() == timeline.json()["items"][0]
    assert client.get(f"/api/batches/{batch['batch_id']}/tasks/missing").status_code == 404


@pytest.mark.parametrize("corruption", ["malformed", "secret-shaped", "symlink", "oversized"])
def test_batch_context_api_rejects_unsafe_or_unbounded_timeline_artifacts(
    config: WebConfig,
    store: TaskStore,
    tmp_path: Path,
    corruption: str,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload(f"batch-context-{corruption}")).json()
    children = _finish_batch(config, store, batch["batch_id"])
    task = children[0]
    timeline = config.run_root / "runs" / task.task_id / "arms" / "on" / "context" / "timeline.jsonl"
    if corruption == "malformed":
        timeline.write_text("{\n")
    elif corruption == "secret-shaped":
        event = json.loads(timeline.read_text().splitlines()[0])
        event["output"] = {"authorization": SECRET}
        timeline.write_text(json.dumps(event) + "\n")
    elif corruption == "symlink":
        outside = tmp_path / "outside-timeline.jsonl"
        outside.write_text(timeline.read_text())
        timeline.unlink()
        timeline.symlink_to(outside)
    else:
        with timeline.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)

    response = client.get(f"/api/batches/{batch['batch_id']}/tasks/{task.task_id}/context/on")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "context_unavailable"
    assert SECRET not in response.text


@pytest.mark.parametrize("corruption", ["symlink", "oversized"])
def test_batch_report_rejects_unsafe_report_artifacts(
    config: WebConfig,
    store: TaskStore,
    tmp_path: Path,
    corruption: str,
) -> None:
    catalog = _BatchCatalog()
    client = TestClient(create_app(config, store, catalog=catalog))
    batch = client.post("/api/batches", json=_batch_payload(f"batch-report-{corruption}")).json()
    children = _finish_batch(config, store, batch["batch_id"])
    report = config.run_root / "runs" / children[0].task_id / "report.json"
    if corruption == "symlink":
        outside = tmp_path / "outside-report.json"
        outside.write_text(report.read_text())
        report.unlink()
        report.symlink_to(outside)
    else:
        with report.open("wb") as stream:
            stream.truncate(1024 * 1024 + 1)

    response = client.get(f"/api/batches/{batch['batch_id']}/report")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_unavailable"


def test_put_auth_replaces_the_credential_file(config: WebConfig, client: TestClient) -> None:
    auth_path = config.auth_json
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    original = {"auth_mode": "chatgpt", "tokens": {"access_token": "old"}}
    auth_path.write_text(json.dumps(original))
    new_auth = {"auth_mode": "chatgpt", "tokens": {"access_token": "new", "refresh_token": "rt-new"}}

    response = client.put("/api/auth", json={"auth_json": json.dumps(new_auth)})

    assert response.status_code == 200
    assert "updated_at" in response.json()
    loaded = json.loads(auth_path.read_text())
    assert loaded == new_auth
    assert auth_path.with_suffix(".json.backup").exists() or any(
        p.name.startswith("auth.json.backup") for p in auth_path.parent.iterdir()
    )


def test_put_auth_rejects_invalid_json(config: WebConfig, client: TestClient) -> None:
    response = client.put("/api/auth", json={"auth_json": "not-json"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_auth_json"


def test_put_auth_rejects_missing_tokens(config: WebConfig, client: TestClient) -> None:
    response = client.put("/api/auth", json={"auth_json": json.dumps({"auth_mode": "chatgpt"})})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_auth_json"
