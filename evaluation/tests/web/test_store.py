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

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from powercontext_eval.paths import EvaluationPaths
from powercontext_eval.web.batches import BatchControlEventType, BatchCreate, BatchStatus
from powercontext_eval.web.controls import BatchControlIntent, BatchPauseReason
from powercontext_eval.web.models import (
    FailureCategory,
    FailureCode,
    RetryDisposition,
    SafeFailure,
    TaskCreate,
    TaskPhase,
    TaskResult,
    TaskStatus,
)
from powercontext_eval.web.store import (
    FinalizationState,
    TaskAdmissionRejected,
    TaskConflict,
    TaskNotFound,
    TaskOwnershipError,
    TaskStore,
    TokensFlowFinalizationCreate,
)
from powercontext_eval.web.usage import UsageSnapshot

NOW = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)


def request(key: str) -> TaskCreate:
    return TaskCreate(
        powercontext_ref="commit:" + "a" * 40,
        benchmark="swebench-pro",
        instance_id="instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        treatment_mode="off_on",
        idempotency_key=key,
    )


def batch_request(key: str, *, model: str = "gpt-5.6-sol") -> BatchCreate:
    return BatchCreate(
        powercontext_ref="latest",
        benchmark="swebench-pro",
        task_set="swebench-pro-public-v2",
        model=model,
        reasoning_effort="medium",
        treatment_mode="off_on",
        idempotency_key=key,
    )


def usage_snapshot(*, used_percent: int, observed_at: datetime = NOW) -> UsageSnapshot:
    return UsageSnapshot(
        limit_id="codex",
        used_percent=used_percent,
        remaining_percent=100 - used_percent,
        window_duration_minutes=10_080,
        resets_at=NOW + timedelta(days=7),
        observed_at=observed_at,
        plan_type="pro",
        account_tokens=1_234,
    )


def complete_attempt_cleanup(store: TaskStore, task_id: str, *, now: datetime) -> bool:
    candidates = [
        candidate
        for candidate in store.list_attempt_cleanup_candidates(limit=32, now=now)
        if candidate.task_id == task_id
    ]
    assert len(candidates) == 1
    store.mark_attempt_evidence_exported(candidates[0].attempt_id)
    return store.complete_attempt_cleanup_and_schedule_retry(candidates[0].attempt_id, now=now)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "tasks.sqlite3"


@pytest.fixture
def store(database: Path) -> TaskStore:
    task_store = TaskStore(database, lease_duration=timedelta(seconds=60))
    task_store.initialize()
    return task_store


def finalization_create(
    task_id: str, attempt_id: str, *, arm: str = "off", run_id: str | None = None
) -> TokensFlowFinalizationCreate:
    selected_run_id = run_id or task_id
    return TokensFlowFinalizationCreate(
        attempt_id=attempt_id,
        task_id=task_id,
        batch_id=None,
        arm=arm,
        run_id=selected_run_id,
        container_name=f"powercontext-eval-{selected_run_id}-{arm}",
        runtime_path=f"work/{selected_run_id}/{arm}/runtime",
        wrapper_path=f"work/{selected_run_id}/{arm}/evaluation-control/tokensflow-wrapper",
        egress_network="tokensflow-egress",
        daemon_pid_file="/runtime/tokensflow-home/.local/share/tokensflow/evaluation-daemon.pid",
        evidence_sha256="a" * 64,
        evidence_bytes=123,
    )


def _queued_task_with_attempt(store: TaskStore, key: str = "finalization") -> tuple[str, str]:
    task, _ = store.create(request(key), now=NOW)
    assert task.attempt_id is not None
    return task.task_id, task.attempt_id


def test_tokensflow_finalization_registration_is_unique_and_deadline_is_immutable(store: TaskStore) -> None:
    task_id, attempt_id = _queued_task_with_attempt(store)
    create = finalization_create(task_id, attempt_id)

    first, created = store.register_tokensflow_finalization(create, now=NOW, timeout_seconds=600)
    replay, replay_created = store.register_tokensflow_finalization(
        create,
        now=NOW + timedelta(minutes=5),
        timeout_seconds=1,
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    assert first.state is FinalizationState.PENDING
    assert first.registered_at == NOW
    assert first.deadline_at == NOW + timedelta(minutes=10)
    assert first.attempts == 0
    assert first.queue_passed is False
    assert first.finished_at is None
    assert first.evidence_sha256 == "a" * 64
    assert first.evidence_bytes == 123


def test_tokensflow_finalization_accepts_native_root_daemon_pid_file(store: TaskStore) -> None:
    task_id, attempt_id = _queued_task_with_attempt(store, "finalization-root-home")
    create = finalization_create(task_id, attempt_id)
    root_create = TokensFlowFinalizationCreate(
        **{
            **create.__dict__,
            "daemon_pid_file": "/root/.local/share/tokensflow/evaluation-daemon.pid",
        }
    )

    registered, created = store.register_tokensflow_finalization(root_create, now=NOW, timeout_seconds=600)

    assert created is True
    assert registered.daemon_pid_file == "/root/.local/share/tokensflow/evaluation-daemon.pid"


@pytest.mark.parametrize("timeout_seconds", [601, 3600])
def test_tokensflow_finalization_registration_rejects_deadline_beyond_hard_limit(
    store: TaskStore,
    timeout_seconds: int,
) -> None:
    task_id, attempt_id = _queued_task_with_attempt(store, f"finalization-timeout-{timeout_seconds}")

    with pytest.raises(ValueError, match="must not exceed 600 seconds"):
        store.register_tokensflow_finalization(
            finalization_create(task_id, attempt_id),
            now=NOW,
            timeout_seconds=timeout_seconds,
        )

    assert store.tokensflow_finalizations_for_attempt(attempt_id) == []


def test_tokensflow_finalization_claim_is_oldest_first_and_recovers_expired_running_job(store: TaskStore) -> None:
    first_task, first_attempt = _queued_task_with_attempt(store, "finalization-first")
    second_task, second_attempt = _queued_task_with_attempt(store, "finalization-second")
    first = store.register_tokensflow_finalization(
        finalization_create(first_task, first_attempt), now=NOW, timeout_seconds=600
    )[0]
    second = store.register_tokensflow_finalization(
        finalization_create(second_task, second_attempt, run_id=second_task),
        now=NOW + timedelta(seconds=1),
        timeout_seconds=600,
    )[0]

    claimed = store.claim_tokensflow_finalization("finalizer-a", now=NOW + timedelta(seconds=2), lease_seconds=30)
    assert claimed is not None
    assert claimed.job_id == first.job_id
    assert claimed.state is FinalizationState.RUNNING
    assert claimed.attempts == 1
    second_claimed = store.claim_tokensflow_finalization(
        "finalizer-b", now=NOW + timedelta(seconds=3), lease_seconds=30
    )
    assert second_claimed is not None and second_claimed.job_id == second.job_id

    recovered = store.claim_tokensflow_finalization("finalizer-c", now=NOW + timedelta(seconds=33), lease_seconds=30)
    assert recovered is not None
    assert recovered.job_id == first.job_id
    assert recovered.attempts == 2


def test_tokensflow_cleanup_retry_survives_reopen_and_honors_retry_time(tmp_path: Path) -> None:
    database = tmp_path / "tasks.sqlite3"
    store = TaskStore(database, lease_duration=timedelta(seconds=60))
    store.initialize()
    task_id, attempt_id = _queued_task_with_attempt(store, "cleanup-retry-reopen")
    job = store.register_tokensflow_finalization(
        finalization_create(task_id, attempt_id),
        now=NOW,
        timeout_seconds=600,
    )[0]
    claimed = store.claim_tokensflow_finalization("finalizer-a", now=NOW, lease_seconds=300)
    assert claimed is not None
    deferred = store.defer_tokensflow_finalization_cleanup(
        job.job_id,
        "finalizer-a",
        now=NOW,
        retry_seconds=5,
        reason="deadline",
    )
    assert deferred.state is FinalizationState.CLEANUP_PENDING

    reopened = TaskStore(database, lease_duration=timedelta(seconds=60))
    reopened.initialize()
    assert reopened.list_open_tokensflow_finalizations()[0].state is FinalizationState.CLEANUP_PENDING
    assert (
        reopened.claim_tokensflow_finalization(
            "finalizer-b",
            now=NOW + timedelta(seconds=4),
            lease_seconds=300,
        )
        is None
    )
    retried = reopened.claim_tokensflow_finalization(
        "finalizer-b",
        now=NOW + timedelta(seconds=5),
        lease_seconds=300,
    )
    assert retried is not None
    assert retried.state is FinalizationState.CLEANUP_PENDING
    assert retried.attempts == 2


def test_tokensflow_finalization_progress_and_terminal_states_survive_store_restart(
    database: Path,
) -> None:
    store = TaskStore(database, lease_duration=timedelta(seconds=60))
    store.initialize()
    task_id, attempt_id = _queued_task_with_attempt(store, "finalization-restart")
    registered = store.register_tokensflow_finalization(
        finalization_create(task_id, attempt_id), now=NOW, timeout_seconds=600
    )[0]
    claimed = store.claim_tokensflow_finalization("finalizer-a", now=NOW, lease_seconds=30)
    assert claimed is not None
    checked = store.record_tokensflow_finalization_check(
        registered.job_id,
        "finalizer-a",
        queue_passed=True,
        doctor_rc=0,
        now=NOW + timedelta(seconds=1),
    )

    restarted = TaskStore(database, lease_duration=timedelta(seconds=60))
    restarted.initialize()
    restored = restarted.tokensflow_finalizations_for_attempt(attempt_id)
    assert len(restored) == 1
    assert restored[0].state is FinalizationState.RUNNING
    assert restored[0].queue_passed is True
    assert restored[0].doctor_rc == 0
    assert checked == restored[0]

    finished = restarted.finish_tokensflow_finalization(
        registered.job_id,
        "finalizer-a",
        state=FinalizationState.PASSED,
        now=NOW + timedelta(seconds=2),
    )
    assert finished.state is FinalizationState.PASSED
    assert finished.finished_at == NOW + timedelta(seconds=2)
    assert restarted.list_open_tokensflow_finalizations() == []


def test_tokensflow_finalization_rejects_secret_or_unsafe_persisted_values(store: TaskStore) -> None:
    task_id, attempt_id = _queued_task_with_attempt(store, "finalization-secret")
    create = finalization_create(task_id, attempt_id)

    for changed in (
        {"runtime_path": "/tmp/private"},
        {"wrapper_path": "../private"},
        {"egress_network": "network;rm"},
        {"evidence_sha256": "not-a-hash"},
    ):
        with pytest.raises(ValueError):
            store.register_tokensflow_finalization(
                TokensFlowFinalizationCreate(**cast(Any, {**create.__dict__, **changed})),
                now=NOW,
                timeout_seconds=600,
            )


def test_initialize_is_idempotent_and_creates_expected_schema(database: Path) -> None:
    store = TaskStore(database, lease_duration=timedelta(seconds=60))

    store.initialize()
    store.initialize()

    assert store.list_tasks(status=None, limit=10, offset=0) == []


def test_initialize_rolls_back_entire_lease_migration_when_drop_fails(
    database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                queue_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                failure_category TEXT,
                failure_phase TEXT,
                failure_summary TEXT,
                result_json TEXT
            );
            CREATE TABLE worker_lease (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                task_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO tasks(task_id, idempotency_key, request_json, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy-run", "legacy-key", request("legacy-key").model_dump_json(), "running", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO worker_lease(singleton, task_id, worker_id, expires_at) VALUES (1, ?, ?, ?)",
            ("legacy-run", "legacy-worker", (NOW + timedelta(minutes=1)).isoformat()),
        )

    original_connect = sqlite3.connect

    class FailingDropConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.strip() == "DROP TABLE worker_lease":
                raise RuntimeError("injected migration failure")
            return super().execute(sql, parameters)

    def connect_with_failing_drop(
        database: Path,
        *,
        timeout: float = 5.0,
        isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None = "DEFERRED",
    ) -> sqlite3.Connection:
        return original_connect(
            database,
            timeout=timeout,
            isolation_level=isolation_level,
            factory=FailingDropConnection,
        )

    monkeypatch.setattr("powercontext_eval.web.store.sqlite3.connect", connect_with_failing_drop)
    store = TaskStore(database, lease_duration=timedelta(seconds=60))

    with pytest.raises(RuntimeError, match="injected migration failure"):
        store.initialize()

    with original_connect(database) as connection:
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    assert "worker_lease" in tables
    assert "worker_leases" not in tables
    assert "task_attempts" not in tables


def test_initialize_migrates_legacy_task_without_deleting_it(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                queue_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                failure_category TEXT,
                failure_phase TEXT,
                failure_summary TEXT,
                result_json TEXT
            );
            CREATE TABLE worker_lease (
                singleton INTEGER PRIMARY KEY,
                worker_id TEXT NOT NULL,
                task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
                expires_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO tasks(
                task_id, idempotency_key, request_json, status, created_at, started_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "run-legacy",
                "legacy-key",
                request("legacy-key").model_dump_json(),
                "running",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO worker_lease(singleton, worker_id, task_id, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (1, "legacy-worker", "run-legacy", (NOW + timedelta(seconds=60)).isoformat()),
        )
    store = TaskStore(database, lease_duration=timedelta(seconds=60))

    store.initialize()

    legacy = store.get("run-legacy")
    assert legacy.batch_id is None
    assert legacy.source_index is None
    assert legacy.instance_id == request("legacy-key").instance_id
    assert legacy.attempt_id == "run-legacy.attempt-0001"
    renewed = store.heartbeat("run-legacy", "legacy-worker", now=NOW + timedelta(seconds=30))
    assert renewed.version == legacy.version + 1
    with sqlite3.connect(database) as connection:
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        lease_columns = [row[1] for row in connection.execute("PRAGMA table_info(worker_leases)").fetchall()]
    assert "worker_lease" not in tables
    assert lease_columns == ["attempt_id", "worker_id", "expires_at"]
    assert store.list_batches() == []


def test_initialize_migrates_current_attempt_lease_without_losing_owner(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                queue_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL,
                batch_id TEXT,
                instance_id TEXT,
                source_index INTEGER,
                status TEXT NOT NULL,
                phase TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                failure_category TEXT,
                failure_phase TEXT,
                failure_summary TEXT,
                result_json TEXT
            );
            CREATE TABLE task_attempts (
                attempt_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                attempt_number INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                phase TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                failure_category TEXT,
                failure_phase TEXT,
                failure_summary TEXT,
                result_json TEXT,
                UNIQUE(task_id, attempt_number)
            );
            CREATE TABLE worker_lease (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                worker_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL UNIQUE REFERENCES task_attempts(attempt_id),
                expires_at TEXT NOT NULL
            );
            """
        )
        task = request("current-lease-key")
        connection.execute(
            """
            INSERT INTO tasks(
                task_id, idempotency_key, request_json, instance_id, status, created_at, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-current",
                task.idempotency_key,
                task.model_dump_json(),
                task.instance_id,
                TaskStatus.RUNNING.value,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO task_attempts(
                attempt_id, task_id, attempt_number, idempotency_key, status, created_at, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-current.attempt-0001",
                "run-current",
                1,
                "run-current.attempt-0001",
                TaskStatus.RUNNING.value,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO worker_lease(singleton, worker_id, attempt_id, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (1, "current-worker", "run-current.attempt-0001", (NOW + timedelta(seconds=60)).isoformat()),
        )

    store = TaskStore(database, lease_duration=timedelta(seconds=60))
    store.initialize()
    store.initialize()

    renewed = store.heartbeat("run-current", "current-worker", now=NOW + timedelta(seconds=30))
    assert renewed.status is TaskStatus.RUNNING
    with sqlite3.connect(database) as connection:
        lease = connection.execute("SELECT * FROM worker_leases").fetchone()
        singular = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'worker_lease'"
        ).fetchone()
    assert lease[:2] == ("run-current.attempt-0001", "current-worker")
    assert datetime.fromisoformat(lease[2]) == NOW + timedelta(seconds=90)
    assert singular is None


def test_initialize_migrates_current_cancelled_batch_control_without_rewriting_children(database: Path) -> None:
    batch = batch_request("legacy-batch")
    legacy_batch = batch.model_dump(mode="json")
    legacy_batch.pop("model")
    legacy_batch.pop("reasoning_effort")
    legacy_batch.pop("initial_control_intent")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE batches (
                batch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL,
                total_tasks INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                resolved_powercontext_sha TEXT
            );
            CREATE TABLE tasks (
                queue_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_json TEXT NOT NULL,
                batch_id TEXT REFERENCES batches(batch_id),
                instance_id TEXT,
                source_index INTEGER,
                status TEXT NOT NULL,
                phase TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                failure_category TEXT,
                failure_phase TEXT,
                failure_summary TEXT,
                result_json TEXT
            );
            CREATE TABLE worker_lease (
                singleton INTEGER PRIMARY KEY,
                worker_id TEXT NOT NULL,
                task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
                expires_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO batches(
                batch_id, idempotency_key, request_json, total_tasks, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("batch-legacy", batch.idempotency_key, json.dumps(legacy_batch), 2, NOW.isoformat()),
        )
        for index in range(2):
            child = request(f"legacy-child-{index}").model_copy(update={"instance_id": f"instance_owner__repo-{index}"})
            legacy_child = child.model_dump(mode="json")
            legacy_child.pop("model")
            legacy_child.pop("reasoning_effort")
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, idempotency_key, request_json, batch_id, instance_id,
                    source_index, status, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"run-legacy-{index}",
                    child.idempotency_key,
                    json.dumps(legacy_child),
                    "batch-legacy",
                    child.instance_id,
                    index,
                    TaskStatus.CANCELLED.value,
                    NOW.isoformat(),
                    (NOW + timedelta(seconds=1)).isoformat(),
                ),
            )
    store = TaskStore(database, lease_duration=timedelta(seconds=60))

    store.initialize()
    store.initialize()

    migrated = store.get_batch("batch-legacy")
    assert migrated.status is BatchStatus.CANCELLED
    assert migrated.control.intent is BatchControlIntent.CANCEL
    assert migrated.control.usage_pause_percent == 80
    assert migrated.control.version == 0
    assert (migrated.request.model, migrated.request.reasoning_effort) == ("gpt-5.6-sol", "medium")
    assert {(task.request.model, task.request.reasoning_effort) for task in store.list_batch_tasks("batch-legacy")} == {
        ("gpt-5.6-sol", "medium")
    }
    assert [task.status for task in store.list_batch_tasks("batch-legacy")] == [
        TaskStatus.CANCELLED,
        TaskStatus.CANCELLED,
    ]
    assert store.latest_usage_snapshot() is None
    assert store.list_control_events("batch-legacy") == ()


def test_usage_snapshots_are_append_only_and_survive_restart(database: Path) -> None:
    store = TaskStore(database, lease_duration=timedelta(seconds=60))
    store.initialize()
    first = usage_snapshot(used_percent=9)
    second = usage_snapshot(used_percent=12, observed_at=NOW + timedelta(minutes=1))

    assert store.save_usage_snapshot(first) == first
    assert store.save_usage_snapshot(second) == second
    assert store.latest_usage_snapshot() == second

    restarted = TaskStore(database, lease_duration=timedelta(seconds=60))
    restarted.initialize()
    assert restarted.latest_usage_snapshot() == second
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT snapshot_json FROM usage_snapshots ORDER BY snapshot_seq ASC").fetchall()
    assert [UsageSnapshot.model_validate_json(row[0], strict=True) for row in rows] == [first, second]


def test_create_batch_expands_every_instance_atomically_in_source_order(store: TaskStore, tmp_path: Path) -> None:
    instance_ids = (
        "instance_owner__repo-a",
        "instance_owner__repo-b",
        "instance_owner__repo-c",
    )

    batch, created = store.create_batch(batch_request("batch-key"), instance_ids, now=NOW)

    assert created is True
    assert batch.total_tasks == 3
    assert batch.status is BatchStatus.QUEUED
    children = store.list_batch_tasks(batch.batch_id)
    assert [task.instance_id for task in children] == list(instance_ids)
    assert [task.source_index for task in children] == [0, 1, 2]
    assert all(task.batch_id == batch.batch_id for task in children)
    assert all(EvaluationPaths(tmp_path, task.task_id) for task in children)
    assert store.health_snapshot(now=NOW)["queued_tasks"] == 3


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6-luna"])
def test_create_paused_batch_has_no_claim_window_under_concurrent_workers(
    database: Path,
    model: str,
) -> None:
    creator = TaskStore(database, lease_duration=timedelta(seconds=60))
    creator.initialize()
    workers = [TaskStore(database, lease_duration=timedelta(seconds=60)) for _ in range(4)]
    start = threading.Barrier(len(workers) + 1)
    creation_finished = threading.Event()
    claims: list[object] = []
    claims_lock = threading.Lock()

    def claim(index: int) -> None:
        start.wait()
        while not creation_finished.is_set():
            claimed = workers[index].claim_next(f"worker-{index}", now=NOW, max_concurrency=10)
            if claimed is not None:
                with claims_lock:
                    claims.append(claimed)

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(len(workers))]
    for thread in threads:
        thread.start()
    start.wait()
    request = batch_request(f"paused-{model}", model=model).model_copy(update={"initial_control_intent": "pause"})
    batch = creator.create_batch(request, ("instance_owner__repo-paused",), now=NOW)[0]
    creation_finished.set()
    for thread in threads:
        thread.join(timeout=2)
    claims.extend(
        worker.claim_next(f"worker-final-{index}", now=NOW, max_concurrency=10) for index, worker in enumerate(workers)
    )

    assert all(not thread.is_alive() for thread in threads)
    assert claims == [None, None, None, None]
    assert batch.status is BatchStatus.PAUSED
    assert batch.control.intent is BatchControlIntent.PAUSE
    assert batch.control.pause_reason is BatchPauseReason.USER
    assert creator.list_batch_tasks(batch.batch_id)[0].status is TaskStatus.QUEUED
    assert creator.list_control_events(batch.batch_id)[0].details["initial_control_intent"] == "pause"


def test_sol_and_luna_batches_keep_children_and_retries_model_isolated(store: TaskStore) -> None:
    luna = store.create_batch(
        batch_request("luna-batch", model="gpt-5.6-luna"),
        ("instance_owner__repo-luna",),
        now=NOW,
    )[0]
    sol = store.create_batch(batch_request("sol-batch"), ("instance_owner__repo-sol",), now=NOW)[0]
    sol_task = store.list_batch_tasks(sol.batch_id)[0]
    luna_task = store.list_batch_tasks(luna.batch_id)[0]
    assert sol_task.request.model == "gpt-5.6-sol"
    assert luna_task.request.model == "gpt-5.6-luna"

    claimed = store.claim_next("luna-worker", now=NOW + timedelta(seconds=1))
    assert claimed is not None and claimed.task_id == luna_task.task_id
    store.fail(
        luna_task.task_id,
        "luna-worker",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="retry isolation",
        ),
        now=NOW + timedelta(seconds=2),
    )
    assert complete_attempt_cleanup(store, luna_task.task_id, now=NOW + timedelta(seconds=3)) is True
    retried = store.get_batch_task(luna.batch_id, luna_task.task_id)

    assert store.get_batch_task(sol.batch_id, sol_task.task_id).request.model == "gpt-5.6-sol"
    assert retried.attempt_number == 2
    assert store.get_batch_task(luna.batch_id, luna_task.task_id).request.model == "gpt-5.6-luna"


@pytest.mark.parametrize("existing_max", [4, 10])
def test_store_migrates_worker_capacity_and_enforces_twenty_pair_limit(database: Path, existing_max: int) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"""
            CREATE TABLE worker_runtime (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                task_parallelism INTEGER NOT NULL CHECK (task_parallelism BETWEEN 1 AND {existing_max}),
                observed_at TEXT NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO worker_runtime VALUES (1, 4, ?)", (NOW.isoformat(),))
    store = TaskStore(database, lease_duration=timedelta(seconds=60))

    store.initialize()
    store.record_worker_capacity(20, now=NOW)

    assert store.health_snapshot(now=NOW)["task_parallelism"] == 20
    with pytest.raises(ValueError, match="between 1 and 20"):
        store.record_worker_capacity(21, now=NOW)


def test_pause_without_a_running_task_is_immediate_idempotent_and_restart_safe(database: Path) -> None:
    store = TaskStore(database, lease_duration=timedelta(seconds=60))
    store.initialize()
    batch = store.create_batch(
        batch_request("pause-immediate"),
        ("instance_owner__repo-a", "instance_owner__repo-b"),
        now=NOW,
    )[0]

    paused = store.request_pause(batch.batch_id, reason=BatchPauseReason.USER, now=NOW + timedelta(seconds=1))
    replay = store.request_pause(batch.batch_id, reason=BatchPauseReason.USER, now=NOW + timedelta(seconds=2))

    assert paused.status is BatchStatus.PAUSED
    assert paused.control.intent is BatchControlIntent.PAUSE
    assert paused.control.pause_reason is BatchPauseReason.USER
    assert paused.control.version == 1
    assert replay == paused
    assert [task.status for task in store.list_batch_tasks(batch.batch_id)] == [
        TaskStatus.QUEUED,
        TaskStatus.QUEUED,
    ]
    assert [event.event_type for event in store.list_control_events(batch.batch_id)] == [
        BatchControlEventType.BATCH_CREATED,
        BatchControlEventType.PAUSE_REQUESTED,
        BatchControlEventType.PAUSED,
    ]

    restarted = TaskStore(database, lease_duration=timedelta(seconds=60))
    restarted.initialize()
    assert restarted.get_batch(batch.batch_id) == paused


def test_pause_and_cancel_wait_for_the_running_benchmark_task_boundary(store: TaskStore) -> None:
    paused_batch = store.create_batch(
        batch_request("pause-boundary"),
        ("instance_owner__repo-a", "instance_owner__repo-b"),
        now=NOW,
    )[0]
    running = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert running is not None

    pausing = store.request_pause(
        paused_batch.batch_id,
        reason=BatchPauseReason.USER,
        now=NOW + timedelta(seconds=2),
    )

    assert pausing.status is BatchStatus.PAUSING
    assert [event.event_type for event in store.list_control_events(paused_batch.batch_id)][-1] is (
        BatchControlEventType.PAUSE_REQUESTED
    )
    store.succeed(
        running.task_id,
        "worker-a",
        TaskResult(
            artifact_dir="/safe/artifacts",
            report_path="/safe/artifacts/report.md",
            off_resolved=False,
            on_resolved=True,
        ),
        now=NOW + timedelta(seconds=3),
    )
    paused = store.finalize_batch_intent_after_attempt(paused_batch.batch_id, now=NOW + timedelta(seconds=3))

    assert paused.status is BatchStatus.PAUSED
    assert [task.status for task in store.list_batch_tasks(paused_batch.batch_id)] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.QUEUED,
    ]
    assert [event.event_type for event in store.list_control_events(paused_batch.batch_id)][-1] is (
        BatchControlEventType.PAUSED
    )
    store.request_cancel(paused_batch.batch_id, now=NOW + timedelta(seconds=4))

    cancelled_batch = store.create_batch(
        batch_request("cancel-boundary"),
        ("instance_owner__repo-c", "instance_owner__repo-d"),
        now=NOW + timedelta(seconds=5),
    )[0]
    cancelled_running = store.claim_next("worker-a", now=NOW + timedelta(seconds=6))
    assert cancelled_running is not None
    assert cancelled_running.batch_id == cancelled_batch.batch_id

    cancelling = store.request_cancel(cancelled_batch.batch_id, now=NOW + timedelta(seconds=7))

    assert cancelling.status is BatchStatus.CANCELLING
    store.fail(
        cancelled_running.task_id,
        "worker-a",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Safe failure",
        ),
        now=NOW + timedelta(seconds=8),
    )
    cancelled = store.finalize_batch_intent_after_attempt(
        cancelled_batch.batch_id,
        now=NOW + timedelta(seconds=8),
    )

    assert cancelled.status is BatchStatus.CANCELLED
    assert [task.status for task in store.list_batch_tasks(cancelled_batch.batch_id)] == [
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ]
    assert [event.event_type for event in store.list_control_events(cancelled_batch.batch_id)][-3:] == [
        BatchControlEventType.BATCH_CREATED,
        BatchControlEventType.CANCEL_REQUESTED,
        BatchControlEventType.CANCELLED,
    ]


def test_infrastructure_failure_keeps_batch_running_and_does_not_stop_other_pair(
    store: TaskStore,
) -> None:
    batch = store.create_batch(
        batch_request("infrastructure-failure-pause"),
        (
            "instance_owner__repo-a",
            "instance_owner__repo-b",
            "instance_owner__repo-c",
        ),
        now=NOW,
    )[0]
    first = store.claim_next("worker-a", max_concurrency=2, now=NOW + timedelta(seconds=1))
    second = store.claim_next("worker-b", max_concurrency=2, now=NOW + timedelta(seconds=1))
    assert first is not None
    assert second is not None

    store.fail(
        first.task_id,
        "worker-a",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Codex process failed",
        ),
        now=NOW + timedelta(seconds=2),
    )

    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.RUNNING
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None
    assert [task.status for task in store.list_batch_tasks(batch.batch_id)] == [
        TaskStatus.FAILED,
        TaskStatus.RUNNING,
        TaskStatus.QUEUED,
    ]
    third = store.claim_next("worker-c", max_concurrency=2, now=NOW + timedelta(seconds=3))
    assert third is not None
    assert third.task_id not in {first.task_id, second.task_id}
    assert all(
        event.event_type is not BatchControlEventType.INFRASTRUCTURE_FAILURE
        for event in store.list_control_events(batch.batch_id)
    )

    store.succeed(
        second.task_id,
        "worker-b",
        TaskResult(
            artifact_dir="/safe/artifacts",
            report_path="/safe/artifacts/report.md",
            off_resolved=False,
            on_resolved=True,
        ),
        now=NOW + timedelta(seconds=4),
    )
    assert store.finalize_batch_intent_after_attempt(batch.batch_id, now=NOW + timedelta(seconds=4)).status is (
        BatchStatus.RUNNING
    )


def test_expired_batch_lease_is_fenced_without_pausing_other_running_pair(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("expired-infrastructure-failure-pause"),
        (
            "instance_owner__repo-a",
            "instance_owner__repo-b",
            "instance_owner__repo-c",
        ),
        now=NOW,
    )[0]
    first = store.claim_next("worker-a", max_concurrency=2, now=NOW)
    second = store.claim_next("worker-b", max_concurrency=2, now=NOW)
    assert first is not None
    assert second is not None
    store.heartbeat(second.task_id, "worker-b", now=NOW + timedelta(seconds=30))

    recovered = store.recover_expired(now=NOW + timedelta(seconds=61))

    assert recovered == [first.task_id]
    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.RUNNING
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None
    assert [task.status for task in store.list_batch_tasks(batch.batch_id)] == [
        TaskStatus.INTERRUPTED,
        TaskStatus.RUNNING,
        TaskStatus.QUEUED,
    ]
    third = store.claim_next("worker-c", max_concurrency=2, now=NOW + timedelta(seconds=61))
    assert third is not None and third.task_id not in {first.task_id, second.task_id}


def test_standalone_infrastructure_failure_does_not_pause_unrelated_batch(store: TaskStore) -> None:
    standalone, _ = store.create(request("standalone-infrastructure-failure"), now=NOW)
    batch = store.create_batch(
        batch_request("standalone-failure-batch"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    claimed = store.claim_next("worker-a", max_concurrency=2, now=NOW + timedelta(seconds=1))
    assert claimed is not None and claimed.task_id == standalone.task_id

    store.fail(
        standalone.task_id,
        "worker-a",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Codex process failed",
        ),
        now=NOW + timedelta(seconds=2),
    )

    current = store.get_batch(batch.batch_id)
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None


def test_parallel_failures_preserve_user_pause_without_system_pause_events(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("parallel-failures-under-user-pause"),
        ("instance_owner__repo-a", "instance_owner__repo-b"),
        now=NOW,
    )[0]
    first = store.claim_next("worker-a", max_concurrency=2, now=NOW + timedelta(seconds=1))
    second = store.claim_next("worker-b", max_concurrency=2, now=NOW + timedelta(seconds=1))
    assert first is not None
    assert second is not None
    store.request_pause(batch.batch_id, reason=BatchPauseReason.USER, now=NOW + timedelta(seconds=2))

    for offset, (task, worker) in enumerate(((first, "worker-a"), (second, "worker-b")), start=3):
        store.fail(
            task.task_id,
            worker,
            SafeFailure(
                category=FailureCategory.CODEX_EXECUTION,
                phase=TaskPhase.RUNNING_OFF,
                summary="Codex process failed",
            ),
            now=NOW + timedelta(seconds=offset),
        )

    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.PAUSED
    assert current.control.intent is BatchControlIntent.PAUSE
    assert current.control.pause_reason is BatchPauseReason.USER
    failure_events = [
        event
        for event in store.list_control_events(batch.batch_id)
        if event.event_type is BatchControlEventType.INFRASTRUCTURE_FAILURE
    ]
    assert failure_events == []


def test_each_simultaneously_expired_batch_lease_becomes_cleanup_pending_without_pause(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("parallel-expired-events"),
        ("instance_owner__repo-a", "instance_owner__repo-b", "instance_owner__repo-c"),
        now=NOW,
    )[0]
    first = store.claim_next("worker-a", max_concurrency=2, now=NOW)
    second = store.claim_next("worker-b", max_concurrency=2, now=NOW)
    assert first is not None
    assert second is not None

    assert store.recover_expired(now=NOW + timedelta(seconds=61)) == [first.task_id, second.task_id]

    candidates = store.list_attempt_cleanup_candidates(limit=10, now=NOW + timedelta(seconds=61))
    assert [candidate.attempt_id for candidate in candidates] == [first.attempt_id, second.attempt_id]
    assert store.get_batch(batch.batch_id).control.intent is BatchControlIntent.RUN
    assert store.get_batch(batch.batch_id).control.pause_reason is None


def test_cancel_without_a_running_task_marks_queued_tasks_once(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("cancel-immediate"),
        ("instance_owner__repo-a", "instance_owner__repo-b"),
        now=NOW,
    )[0]

    cancelled = store.request_cancel(batch.batch_id, now=NOW + timedelta(seconds=1))
    replay = store.request_cancel(batch.batch_id, now=NOW + timedelta(seconds=2))

    assert cancelled.status is BatchStatus.CANCELLED
    assert replay == cancelled
    assert [task.status for task in store.list_batch_tasks(batch.batch_id)] == [
        TaskStatus.CANCELLED,
        TaskStatus.CANCELLED,
    ]
    assert [event.event_type for event in store.list_control_events(batch.batch_id)] == [
        BatchControlEventType.BATCH_CREATED,
        BatchControlEventType.CANCEL_REQUESTED,
        BatchControlEventType.CANCELLED,
    ]


def test_resume_is_pure_user_intent_even_when_latest_usage_is_at_threshold(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("resume-control"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    paused = store.request_pause(batch.batch_id, reason=BatchPauseReason.USER, now=NOW + timedelta(seconds=1))
    at_threshold = usage_snapshot(used_percent=80, observed_at=NOW + timedelta(seconds=2))
    store.save_usage_snapshot(at_threshold)

    resumed = store.request_resume(batch.batch_id, now=NOW + timedelta(seconds=2))

    assert resumed.status is BatchStatus.QUEUED
    assert resumed.control.intent is BatchControlIntent.RUN
    assert resumed.control.pause_reason is None
    assert resumed.control.version == paused.control.version + 1
    assert [event.event_type for event in store.list_control_events(batch.batch_id)][-2:] == [
        BatchControlEventType.RESUME_REQUESTED,
        BatchControlEventType.RESUMED,
    ]


def test_threshold_updates_use_optimistic_concurrency_and_do_not_auto_resume(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("threshold-control"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    paused = store.request_pause(
        batch.batch_id,
        reason=BatchPauseReason.USER,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(TaskConflict, match="version"):
        store.update_usage_threshold(
            batch.batch_id,
            percent=90,
            expected_version=0,
            now=NOW + timedelta(seconds=2),
        )

    updated = store.update_usage_threshold(
        batch.batch_id,
        percent=90,
        expected_version=paused.control.version,
        now=NOW + timedelta(seconds=2),
    )
    replay = store.update_usage_threshold(
        batch.batch_id,
        percent=90,
        expected_version=updated.control.version,
        now=NOW + timedelta(seconds=3),
    )

    assert updated.status is BatchStatus.PAUSED
    assert updated.control.intent is BatchControlIntent.PAUSE
    assert updated.control.usage_pause_percent == 90
    assert updated.control.version == paused.control.version + 1
    assert replay == updated
    threshold_events = [
        event
        for event in store.list_control_events(batch.batch_id)
        if event.event_type is BatchControlEventType.THRESHOLD_CHANGED
    ]
    assert len(threshold_events) == 1
    assert threshold_events[0].details == {"from_percent": 80, "to_percent": 90}


def test_existing_tasks_are_backfilled_as_attempt_one(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("attempt-backfill"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    task = store.list_batch_tasks(batch.batch_id)[0]

    attempts = store.list_task_attempts(batch.batch_id, task.task_id)

    assert [attempt.attempt_number for attempt in attempts] == [1]
    assert attempts[0].attempt_id == f"{task.task_id}.attempt-0001"
    assert attempts[0].task_id == task.task_id
    assert task.attempt_id == attempts[0].attempt_id
    assert task.attempt_number == 1
    assert task.attempt_count == 1
    assert task.retryable is False


def test_cleanup_preserves_failed_attempt_and_idempotently_creates_attempt_two(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("attempt-retry"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    task = store.list_batch_tasks(batch.batch_id)[0]
    claimed = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert claimed is not None
    failed = store.fail(
        task.task_id,
        "worker-a",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Codex process failed",
        ),
        now=NOW + timedelta(seconds=2),
    )
    store.finalize_batch_intent_after_attempt(batch.batch_id, now=NOW + timedelta(seconds=2))

    assert complete_attempt_cleanup(store, task.task_id, now=NOW + timedelta(seconds=3)) is True
    assert failed.attempt_id is not None
    assert (
        store.complete_attempt_cleanup_and_schedule_retry(
            failed.attempt_id,
            now=NOW + timedelta(seconds=4),
        )
        is False
    )
    retry = store.get_batch_task(batch.batch_id, task.task_id)

    assert failed.retryable is True
    assert retry.attempt_number == 2
    assert retry.status is TaskStatus.QUEUED
    assert retry.eligible_at == NOW + timedelta(seconds=33)
    attempts = store.list_task_attempts(batch.batch_id, task.task_id)
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert attempts[0].status is TaskStatus.FAILED
    assert attempts[0].failure_summary == "Codex process failed"
    assert attempts[1].attempt_id == retry.attempt_id
    assert attempts[1].eligible_at == retry.eligible_at
    current = store.get_batch_task(batch.batch_id, task.task_id)
    assert current.status is TaskStatus.QUEUED
    assert current.attempt_id == retry.attempt_id
    assert current.attempt_count == 2
    current_batch = store.get_batch(batch.batch_id)
    assert current_batch.status is BatchStatus.QUEUED
    assert current_batch.control.intent is BatchControlIntent.RUN
    assert current_batch.control.pause_reason is None
    assert [event.event_type for event in store.list_control_events(batch.batch_id)][-1] is (
        BatchControlEventType.TASK_RETRY_REQUESTED
    )


def test_gold_validation_retries_span_long_time_dependent_failure_windows(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("gold-retry-window"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    task = store.list_batch_tasks(batch.batch_id)[0]
    eligible_at = NOW

    for attempt_number, backoff_seconds in enumerate((30, 120, 300, 7_200), start=1):
        worker_id = f"gold-worker-{attempt_number}"
        claimed = store.claim_next(worker_id, now=eligible_at)
        assert claimed is not None
        assert claimed.task_id == task.task_id
        assert claimed.attempt_number == attempt_number
        failed_at = eligible_at + timedelta(seconds=1)
        store.fail(
            task.task_id,
            worker_id,
            SafeFailure(
                category=FailureCategory.GOLD_VALIDATION,
                failure_code=FailureCode.GOLD_VALIDATION,
                phase=TaskPhase.VALIDATING_GOLD,
                summary="Gold patch validation failed",
            ),
            now=failed_at,
        )
        cleanup_at = failed_at + timedelta(seconds=1)

        assert complete_attempt_cleanup(store, task.task_id, now=cleanup_at) is True
        retry = store.get_batch_task(batch.batch_id, task.task_id)
        assert retry.attempt_number == attempt_number + 1
        assert retry.eligible_at == cleanup_at + timedelta(seconds=backoff_seconds)
        eligible_at = retry.eligible_at


def test_retry_in_a_paused_batch_preserves_pause_control(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("attempt-retry-paused"),
        ("instance_owner__repo-a", "instance_owner__repo-b"),
        now=NOW,
    )[0]
    task = store.list_batch_tasks(batch.batch_id)[0]
    claimed = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert claimed is not None
    paused = store.request_pause(
        batch.batch_id,
        reason=BatchPauseReason.USER,
        now=NOW + timedelta(seconds=2),
    )
    store.fail(
        task.task_id,
        "worker-a",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Codex process failed",
        ),
        now=NOW + timedelta(seconds=3),
    )
    store.finalize_batch_intent_after_attempt(batch.batch_id, now=NOW + timedelta(seconds=3))

    assert complete_attempt_cleanup(store, task.task_id, now=NOW + timedelta(seconds=4)) is True
    retry = store.get_batch_task(batch.batch_id, task.task_id)

    assert retry.status is TaskStatus.QUEUED
    current_batch = store.get_batch(batch.batch_id)
    assert current_batch.status is BatchStatus.PAUSED
    assert current_batch.control.intent is BatchControlIntent.PAUSE
    assert current_batch.control.pause_reason is BatchPauseReason.USER
    assert current_batch.control.version == paused.control.version


@pytest.mark.parametrize(
    ("result", "label"),
    [
        (
            TaskResult(
                artifact_dir="/safe/resolved",
                report_path="/safe/resolved/report.md",
                off_resolved=True,
                on_resolved=True,
            ),
            "RESOLVED",
        ),
        (
            TaskResult(
                artifact_dir="/safe/unresolved",
                report_path="/safe/unresolved/report.md",
                off_resolved=False,
                on_resolved=False,
            ),
            "UNRESOLVED",
        ),
    ],
)
def test_valid_official_outcomes_are_never_retryable(
    store: TaskStore,
    result: TaskResult,
    label: str,
) -> None:
    batch = store.create_batch(
        batch_request(f"valid-outcome-{label.lower()}"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    task = store.list_batch_tasks(batch.batch_id)[0]
    claimed = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert claimed is not None
    completed = store.succeed(task.task_id, "worker-a", result, now=NOW + timedelta(seconds=2))

    assert completed.retryable is False
    with pytest.raises(TaskConflict, match="not retryable"):
        store.retry_failed_task(
            batch.batch_id,
            task.task_id,
            idempotency_key=f"retry-valid-{label.lower()}",
            now=NOW + timedelta(seconds=3),
        )
    assert len(store.list_task_attempts(batch.batch_id, task.task_id)) == 1


def test_operator_retry_of_terminal_failure_waits_for_cleanup(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("nonretryable-failure"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]
    task = store.list_batch_tasks(batch.batch_id)[0]
    claimed = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert claimed is not None
    failed = store.fail(
        task.task_id,
        "worker-a",
        SafeFailure(
            category=FailureCategory.INVALID_REQUEST,
            failure_code=FailureCode.UNSAFE_SUT_CONFIGURATION,
            phase=TaskPhase.PREPARING,
            summary="Invalid request",
            retry_disposition=RetryDisposition.TERMINAL,
        ),
        now=NOW + timedelta(seconds=2),
    )

    assert failed.retryable is False
    with pytest.raises(TaskConflict, match="cleanup is not complete"):
        store.retry_failed_task(
            batch.batch_id,
            task.task_id,
            idempotency_key="retry-invalid-request",
            now=NOW + timedelta(seconds=3),
        )
    candidate = store.list_attempt_cleanup_candidates(limit=1, now=NOW + timedelta(seconds=3))[0]
    store.mark_attempt_evidence_exported(candidate.attempt_id)
    assert (
        store.complete_attempt_cleanup_and_schedule_retry(candidate.attempt_id, now=NOW + timedelta(seconds=3)) is False
    )
    retry, created = store.retry_failed_task(
        batch.batch_id,
        task.task_id,
        idempotency_key="retry-invalid-request",
        now=NOW + timedelta(seconds=4),
    )
    assert created is True
    assert retry.attempt_number == 2


def test_create_batch_replays_idempotency_key_without_duplicate_children(store: TaskStore) -> None:
    instance_ids = ("instance_owner__repo-a", "instance_owner__repo-b")
    original, original_created = store.create_batch(batch_request("batch-replay"), instance_ids, now=NOW)

    replay, replay_created = store.create_batch(
        batch_request("batch-replay"),
        ("instance_other__repo-x",),
        now=NOW + timedelta(seconds=1),
    )

    assert original_created is True
    assert replay_created is False
    assert replay == original
    assert [task.instance_id for task in store.list_batch_tasks(replay.batch_id)] == list(instance_ids)


def test_create_batch_admits_only_new_work_and_rejects_conflicting_reuse(store: TaskStore) -> None:
    original = batch_request("batch-admission", model="gpt-5.6-luna")
    created, created_new = store.create_batch(
        original,
        ("instance_owner__repo-a",),
        now=NOW,
        admit_model=lambda model: model == "gpt-5.6-luna",
    )

    replay, replay_new = store.create_batch(
        original,
        ("instance_other__repo-b",),
        now=NOW + timedelta(seconds=1),
        admit_model=lambda _model: False,
    )

    assert created_new is True
    assert replay_new is False
    assert replay == created
    with pytest.raises(TaskConflict, match="idempotency"):
        store.create_batch(
            original.model_copy(update={"usage_pause_percent": 79}),
            ("instance_owner__repo-a",),
            now=NOW + timedelta(seconds=2),
            admit_model=lambda _model: False,
        )
    with pytest.raises(TaskAdmissionRejected):
        store.create_batch(
            batch_request("new-disabled-batch", model="gpt-5.6-luna"),
            ("instance_owner__repo-a",),
            now=NOW + timedelta(seconds=3),
            admit_model=lambda _model: False,
        )


def test_create_batch_serializes_concurrent_idempotent_admission(store: TaskStore) -> None:
    batch = batch_request("concurrent-batch-admission", model="gpt-5.6-luna")
    barrier = threading.Barrier(3)
    lock = threading.Lock()
    admission_calls = 0
    outcomes: list[tuple[str, bool]] = []

    def admit(_model: str) -> bool:
        nonlocal admission_calls
        with lock:
            admission_calls += 1
        return True

    def create_batch() -> None:
        barrier.wait()
        record, created = store.create_batch(
            batch,
            ("instance_owner__repo-a",),
            now=NOW,
            admit_model=admit,
        )
        with lock:
            outcomes.append((record.batch_id, created))

    workers = [threading.Thread(target=create_batch) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=5)

    assert len(outcomes) == 2
    assert len({batch_id for batch_id, _created in outcomes}) == 1
    assert sorted(created for _batch_id, created in outcomes) == [False, True]
    assert admission_calls == 1


def test_create_batch_failure_leaves_neither_batch_nor_children(store: TaskStore) -> None:
    with pytest.raises(ValueError):
        store.create_batch(
            batch_request("batch-rollback"),
            ("instance_owner__repo-a", "unsafe/instance"),
            now=NOW,
        )

    assert store.list_batches() == []
    assert store.list_tasks(status=None, limit=10, offset=0) == []


def test_create_batch_rejects_duplicate_instance_ids(store: TaskStore) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        store.create_batch(
            batch_request("batch-duplicates"),
            ("instance_owner__repo-a", "instance_owner__repo-a"),
            now=NOW,
        )


def test_batch_revision_pin_is_idempotent_and_rejects_conflict(store: TaskStore) -> None:
    batch = store.create_batch(
        batch_request("batch-pin"),
        ("instance_owner__repo-a",),
        now=NOW,
    )[0]

    pinned = store.pin_batch_revision(batch.batch_id, "a" * 40)
    replay = store.pin_batch_revision(batch.batch_id, "a" * 40)

    assert pinned.resolved_powercontext_sha == "a" * 40
    assert replay == pinned
    with pytest.raises(TaskConflict, match="different"):
        store.pin_batch_revision(batch.batch_id, "b" * 40)


def test_create_replays_idempotency_key_without_reordering(store: TaskStore) -> None:
    original, original_created = store.create(request("same-key"), now=NOW)
    later, _ = store.create(request("later-key"), now=NOW + timedelta(seconds=1))

    replay, replay_created = store.create(request("same-key"), now=NOW + timedelta(seconds=2))

    assert original_created is True
    assert replay_created is False
    assert replay == original
    assert [item.task_id for item in store.list_tasks(status=None, limit=10, offset=0)] == [
        original.task_id,
        later.task_id,
    ]


def test_create_task_admits_only_new_work_and_serializes_concurrent_replays(store: TaskStore) -> None:
    task = request("concurrent-admission").model_copy(update={"model": "gpt-5.6-luna"})
    barrier = threading.Barrier(3)
    lock = threading.Lock()
    admission_calls = 0
    outcomes: list[tuple[str, bool]] = []

    def admit(_model: str) -> bool:
        nonlocal admission_calls
        with lock:
            admission_calls += 1
        return True

    def create_task() -> None:
        barrier.wait()
        record, created = store.create(task, now=NOW, admit_model=admit)
        with lock:
            outcomes.append((record.task_id, created))

    workers = [threading.Thread(target=create_task) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=5)

    assert len(outcomes) == 2
    assert len({task_id for task_id, _created in outcomes}) == 1
    assert sorted(created for _task_id, created in outcomes) == [False, True]
    assert admission_calls == 1
    replay, replay_created = store.create(task, now=NOW, admit_model=lambda _model: False)
    assert replay.task_id == outcomes[0][0]
    assert replay_created is False
    with pytest.raises(TaskConflict, match="idempotency"):
        store.create(task.model_copy(update={"model": "gpt-5.6-sol"}), now=NOW, admit_model=lambda _model: True)
    with pytest.raises(TaskAdmissionRejected):
        store.create(
            task.model_copy(update={"idempotency_key": "new-disabled-task"}),
            now=NOW,
            admit_model=lambda _model: False,
        )


def test_distinct_idempotency_keys_create_distinct_safe_sortable_ids(store: TaskStore, tmp_path: Path) -> None:
    first, _ = store.create(request("distinct-1"), now=NOW)
    second, _ = store.create(request("distinct-2"), now=NOW)

    EvaluationPaths(tmp_path, first.task_id)
    EvaluationPaths(tmp_path, second.task_id)
    assert first.task_id != second.task_id
    assert first.task_id < second.task_id


def test_fifo_order_stable_pagination_and_status_filtering(store: TaskStore) -> None:
    first, _ = store.create(request("fifo-key-1"), now=NOW + timedelta(seconds=2))
    second, _ = store.create(request("fifo-key-2"), now=NOW)
    third, _ = store.create(request("fifo-key-3"), now=NOW)
    store.cancel_queued(second.task_id, now=NOW + timedelta(seconds=3))

    page = store.list_tasks(status=None, limit=2, offset=1)

    assert [item.task_id for item in page] == [second.task_id, third.task_id]
    assert [item.task_id for item in store.list_tasks(status=TaskStatus.QUEUED, limit=10, offset=0)] == [
        first.task_id,
        third.task_id,
    ]
    assert store.list_tasks(status=TaskStatus.CANCELLED, limit=10, offset=0)[0].task_id == second.task_id


def test_newest_order_is_applied_before_stable_pagination(store: TaskStore) -> None:
    created = [store.create(request(f"newest-key-{index:02d}"), now=NOW)[0] for index in range(55)]

    newest_page = store.list_tasks(status=None, order="newest", limit=50, offset=0)
    oldest_page = store.list_tasks(status=None, limit=50, offset=0)

    assert [item.task_id for item in newest_page] == [item.task_id for item in reversed(created[-50:])]
    assert [item.task_id for item in oldest_page] == [item.task_id for item in created[:50]]


def test_get_returns_record_and_unknown_task_raises(store: TaskStore) -> None:
    created, _ = store.create(request("lookup-key"), now=NOW)

    assert store.get(created.task_id) == created
    with pytest.raises(TaskNotFound):
        store.get("missing-task")


def test_cancel_only_queued_task_and_increments_version(store: TaskStore) -> None:
    queued, _ = store.create(request("cancel-key"), now=NOW)

    cancelled = store.cancel_queued(queued.task_id, now=NOW + timedelta(seconds=1))

    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.finished_at == NOW + timedelta(seconds=1)
    assert cancelled.version == queued.version + 1
    with pytest.raises(TaskConflict):
        store.cancel_queued(cancelled.task_id, now=NOW + timedelta(seconds=2))


def test_queue_position_and_read_only_health_snapshot(store: TaskStore) -> None:
    first, _ = store.create(request("position-key-1"), now=NOW)
    second, _ = store.create(request("position-key-2"), now=NOW)
    store.cancel_queued(first.task_id, now=NOW + timedelta(seconds=1))

    assert store.queue_position(first.task_id) is None
    assert store.queue_position(second.task_id) == 1
    assert store.health_snapshot(now=NOW + timedelta(seconds=1)) == {
        "worker_lease_active": False,
        "active_task_pairs": 0,
        "task_parallelism": 1,
        "queued_tasks": 1,
        "running_tasks": 0,
    }


def test_health_observes_only_nonexpired_worker_lease(store: TaskStore) -> None:
    task, _ = store.create(request("lease-health-key"), now=NOW)
    store.claim_next("worker", now=NOW)

    assert store.health_snapshot(now=NOW + timedelta(seconds=30))["worker_lease_active"] is True
    assert store.health_snapshot(now=NOW + timedelta(seconds=61))["worker_lease_active"] is False
    assert store.get(task.task_id).status is TaskStatus.RUNNING


def test_worker_capacity_is_published_without_web_configuration_reload(store: TaskStore) -> None:
    assert store.health_snapshot(now=NOW)["task_parallelism"] == 1

    store.record_worker_capacity(4, now=NOW + timedelta(seconds=1))

    assert store.health_snapshot(now=NOW + timedelta(seconds=2))["task_parallelism"] == 4


def test_runtime_revision_gate_reopens_only_for_matching_web_and_worker(store: TaskStore) -> None:
    assert store.deployment_admission_open() is False
    assert store.deployment_snapshot()["deployment_consistent"] is False

    store.record_runtime_revision("web", build_revision="a" * 40, schema_version=2, now=NOW)
    assert store.deployment_admission_open() is False

    store.record_runtime_revision("worker", build_revision="b" * 40, schema_version=2, now=NOW)
    assert store.deployment_admission_open() is False

    store.record_runtime_revision("worker", build_revision="a" * 40, schema_version=2, now=NOW)
    snapshot = store.deployment_snapshot()
    assert snapshot == {
        "web_revision": "a" * 40,
        "worker_revision": "a" * 40,
        "web_schema_version": 2,
        "worker_schema_version": 2,
        "deployment_consistent": True,
    }
    assert store.deployment_admission_open() is True


def test_initialize_preserves_all_legacy_pause_intents(database: Path) -> None:
    store = TaskStore(database, lease_duration=timedelta(seconds=60))
    store.initialize()
    system_batch = store.create_batch(
        batch_request("legacy-system-pause"),
        ("instance_owner__repo-system",),
        now=NOW,
    )[0]
    user_batch = store.create_batch(
        batch_request("legacy-user-pause"),
        ("instance_owner__repo-user",),
        now=NOW,
    )[0]
    store.request_pause(user_batch.batch_id, reason=BatchPauseReason.USER, now=NOW + timedelta(seconds=1))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE batches SET control_intent = ?, pause_reason = ? WHERE batch_id = ?",
            (BatchControlIntent.PAUSE.value, BatchPauseReason.INFRASTRUCTURE_FAILURE.value, system_batch.batch_id),
        )
    system_events = store.list_control_events(system_batch.batch_id)
    user_events = store.list_control_events(user_batch.batch_id)

    store.initialize()

    preserved_system = store.get_batch(system_batch.batch_id)
    preserved_user = store.get_batch(user_batch.batch_id)
    assert preserved_system.control.intent is BatchControlIntent.PAUSE
    assert preserved_system.control.pause_reason is BatchPauseReason.INFRASTRUCTURE_FAILURE
    assert preserved_user.control.intent is BatchControlIntent.PAUSE
    assert preserved_user.control.pause_reason is BatchPauseReason.USER
    assert store.list_control_events(system_batch.batch_id) == system_events
    assert store.list_control_events(user_batch.batch_id) == user_events


@pytest.mark.parametrize("value", [0, 21, True])
def test_worker_capacity_rejects_out_of_range_values(store: TaskStore, value: object) -> None:
    with pytest.raises(ValueError):
        store.record_worker_capacity(value, now=NOW)  # ty: ignore[invalid-argument-type]


def test_claim_is_fifo_atomic_and_globally_excludes_other_connection(database: Path) -> None:
    first_store = TaskStore(database, lease_duration=timedelta(seconds=60))
    second_store = TaskStore(database, lease_duration=timedelta(seconds=60))
    first_store.initialize()
    first, _ = first_store.create(request("claim-key-1"), now=NOW)
    second, _ = first_store.create(request("claim-key-2"), now=NOW)

    claimed = first_store.claim_next("worker-a", now=NOW + timedelta(seconds=1))

    assert claimed is not None
    assert claimed.task_id == first.task_id
    assert claimed.status is TaskStatus.RUNNING
    assert claimed.started_at == NOW + timedelta(seconds=1)
    assert claimed.version == first.version + 1
    assert second_store.claim_next("worker-b", now=NOW + timedelta(seconds=2)) is None
    assert second_store.list_tasks(status=TaskStatus.QUEUED, limit=10, offset=0)[0].task_id == second.task_id


def test_claim_allows_exactly_four_concurrent_task_pairs(store: TaskStore) -> None:
    created = [store.create(request(f"parallel-claim-{index}"), now=NOW)[0] for index in range(5)]

    claimed = [
        store.claim_next(f"worker-{index}", now=NOW + timedelta(seconds=1), max_concurrency=4) for index in range(4)
    ]

    assert [task.task_id for task in claimed if task is not None] == [task.task_id for task in created[:4]]
    assert store.claim_next("worker-5", now=NOW + timedelta(seconds=1), max_concurrency=4) is None
    health = store.health_snapshot(now=NOW + timedelta(seconds=1))
    assert health["active_task_pairs"] == 4
    assert health["running_tasks"] == 4
    assert [task.task_id for task in store.list_tasks(status=TaskStatus.QUEUED, limit=10, offset=0)] == [
        created[4].task_id
    ]


def test_finishing_one_of_four_leases_releases_only_its_capacity(store: TaskStore) -> None:
    tasks = [store.create(request(f"release-one-{index}"), now=NOW)[0] for index in range(4)]
    claimed = [store.claim_next(f"worker-{index}", max_concurrency=4, now=NOW) for index in range(4)]
    assert all(task is not None for task in claimed)

    store.succeed(
        tasks[0].task_id,
        "worker-0",
        TaskResult(
            artifact_dir="/safe/artifacts",
            report_path="/safe/artifacts/report.md",
            off_resolved=False,
            on_resolved=True,
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert store.health_snapshot(now=NOW + timedelta(seconds=1))["active_task_pairs"] == 3
    assert [store.get(task.task_id).status for task in tasks] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.RUNNING,
        TaskStatus.RUNNING,
        TaskStatus.RUNNING,
    ]


def test_one_worker_cannot_hold_two_active_leases(store: TaskStore) -> None:
    first, _ = store.create(request("one-worker-first"), now=NOW)
    second, _ = store.create(request("one-worker-second"), now=NOW)

    claimed = store.claim_next("same-worker", max_concurrency=4, now=NOW)

    assert claimed is not None and claimed.task_id == first.task_id
    assert store.claim_next("same-worker", max_concurrency=4, now=NOW) is None
    assert store.get(second.task_id).status is TaskStatus.QUEUED
    assert store.health_snapshot(now=NOW)["active_task_pairs"] == 1


def test_concurrent_claim_race_never_exceeds_capacity_or_duplicates_task(database: Path) -> None:
    creator = TaskStore(database, lease_duration=timedelta(seconds=60))
    creator.initialize()
    for index in range(5):
        creator.create(request(f"parallel-race-{index}"), now=NOW)
    barrier = threading.Barrier(5)
    claimed: list[str] = []
    claimed_lock = threading.Lock()

    def claim(index: int) -> None:
        store = TaskStore(database, lease_duration=timedelta(seconds=60))
        barrier.wait()
        task = store.claim_next(f"race-worker-{index}", now=NOW + timedelta(seconds=1), max_concurrency=4)
        if task is not None:
            with claimed_lock:
                claimed.append(task.task_id)

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(claimed) == 4
    assert len(set(claimed)) == 4
    assert creator.health_snapshot(now=NOW + timedelta(seconds=1))["active_task_pairs"] == 4


def test_claim_waits_until_persisted_task_eligibility(store: TaskStore) -> None:
    created_at = NOW + timedelta(seconds=10)
    stale_worker_now = NOW
    queued, _ = store.create(request("stale-claim-clock"), now=created_at)

    assert store.claim_next("worker-a", now=stale_worker_now) is None
    claimed = store.claim_next("worker-a", now=created_at)
    assert claimed is not None
    assert claimed.task_id == queued.task_id
    assert claimed.started_at == created_at
    phased = store.set_phase(queued.task_id, "worker-a", TaskPhase.PREPARING, now=created_at)
    heartbeat = store.heartbeat(queued.task_id, "worker-a", now=created_at)
    assert phased.started_at == heartbeat.started_at == created_at
    assert store.health_snapshot(now=created_at + timedelta(seconds=59))["worker_lease_active"] is True
    assert store.health_snapshot(now=created_at + timedelta(seconds=61))["worker_lease_active"] is False


def test_heartbeat_requires_owner_and_increments_version(store: TaskStore) -> None:
    queued, _ = store.create(request("heartbeat-key"), now=NOW)
    running = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert running is not None

    heartbeat = store.heartbeat(queued.task_id, "worker-a", now=NOW + timedelta(seconds=2))

    assert heartbeat.version == running.version + 1
    with pytest.raises(TaskOwnershipError):
        store.heartbeat(queued.task_id, "worker-b", now=NOW + timedelta(seconds=3))


def test_stale_heartbeat_cannot_shorten_an_existing_lease(store: TaskStore) -> None:
    queued, _ = store.create(request("monotonic-heartbeat"), now=NOW)
    running = store.claim_next("worker-a", now=NOW)
    assert running is not None

    renewed = store.heartbeat(queued.task_id, "worker-a", now=NOW + timedelta(seconds=30))
    stale = store.heartbeat(queued.task_id, "worker-a", now=NOW + timedelta(seconds=10))

    assert stale.version == renewed.version + 1
    assert store.health_snapshot(now=NOW + timedelta(seconds=75))["worker_lease_active"] is True
    assert store.health_snapshot(now=NOW + timedelta(seconds=91))["worker_lease_active"] is False


def test_phase_success_and_lease_release(store: TaskStore) -> None:
    queued, _ = store.create(request("success-key"), now=NOW)
    next_queued, _ = store.create(request("success-key-2"), now=NOW)
    running = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert running is not None

    phased = store.set_phase(queued.task_id, "worker-a", TaskPhase.RUNNING_OFF, now=NOW + timedelta(seconds=2))
    result = TaskResult(
        artifact_dir="/safe/artifacts",
        report_path="/safe/artifacts/report.md",
        off_resolved=False,
        on_resolved=True,
    )
    succeeded = store.succeed(queued.task_id, "worker-a", result, now=NOW + timedelta(seconds=3))

    assert phased.phase is TaskPhase.RUNNING_OFF
    assert phased.version == running.version + 1
    assert succeeded.status is TaskStatus.SUCCEEDED
    assert succeeded.result == result
    assert succeeded.finished_at == NOW + timedelta(seconds=3)
    assert succeeded.version == phased.version + 1
    next_claimed = store.claim_next("worker-b", now=NOW + timedelta(seconds=4))
    assert next_claimed is not None and next_claimed.task_id == next_queued.task_id


def test_failure_records_safe_failure(store: TaskStore) -> None:
    queued, _ = store.create(request("failure-key"), now=NOW)
    running = store.claim_next("worker-a", now=NOW + timedelta(seconds=1))
    assert running is not None
    failure = SafeFailure(
        category=FailureCategory.CODEX_EXECUTION,
        phase=TaskPhase.RUNNING_ON,
        summary="Codex execution did not complete",
    )

    failed = store.fail(queued.task_id, "worker-a", failure, now=NOW + timedelta(seconds=2))

    assert failed.status is TaskStatus.FAILED
    assert failed.failure_category is FailureCategory.CODEX_EXECUTION
    assert failed.failure_phase is TaskPhase.RUNNING_ON
    assert failed.failure_summary == failure.summary
    assert failed.version == running.version + 1


def test_terminal_records_are_immutable(store: TaskStore) -> None:
    queued, _ = store.create(request("terminal-key"), now=NOW)
    store.cancel_queued(queued.task_id, now=NOW + timedelta(seconds=1))

    with pytest.raises(TaskConflict):
        store.set_phase(queued.task_id, "worker-a", TaskPhase.PREPARING, now=NOW + timedelta(seconds=2))


def test_expired_lease_recovery_interrupts_running_only(store: TaskStore) -> None:
    first, _ = store.create(request("recover-key-1"), now=NOW)
    second, _ = store.create(request("recover-key-2"), now=NOW)
    store.claim_next("worker-a", now=NOW + timedelta(seconds=1))

    recovered = store.recover_expired(now=NOW + timedelta(seconds=62))

    interrupted = store.get(first.task_id)
    assert recovered == [first.task_id]
    assert interrupted.status is TaskStatus.INTERRUPTED
    assert interrupted.failure_category is FailureCategory.WORKER_INTERRUPTION
    assert interrupted.failure_summary == "Evaluation worker lease expired"
    assert interrupted.version == 2
    assert store.get(second.task_id).status is TaskStatus.QUEUED
    claimed = store.claim_next("worker-b", now=NOW + timedelta(seconds=63))
    assert claimed is not None and claimed.task_id == second.task_id


def test_recover_unexpired_lease_is_noop(store: TaskStore) -> None:
    queued, _ = store.create(request("unexpired-key"), now=NOW)
    store.claim_next("worker-a", now=NOW + timedelta(seconds=1))

    assert store.recover_expired(now=NOW + timedelta(seconds=60)) == []
    assert store.get(queued.task_id).status is TaskStatus.RUNNING


def test_create_batch_propagates_container_env_to_child_tasks(
    store: TaskStore,
) -> None:
    request = batch_request("env-batch")
    request = request.model_copy(
        update={"container_env": {"POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL": "openrouter:test"}},
    )
    batch, _created = store.create_batch(request, ("instance_a",), now=NOW)
    children = store.list_batch_tasks(batch.batch_id)

    child = children[0]
    assert child.request.container_env == {
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL": "openrouter:test",
    }


def test_batch_record_round_trips_container_env(store: TaskStore) -> None:
    request = batch_request("env-roundtrip")
    request = request.model_copy(
        update={"container_env": {"OPENROUTER_API_KEY": "sk-test", "POWERCONTEXT_SERVER_HTTP_PORT": "8000"}},
    )
    batch, _ = store.create_batch(request, ("instance_a",), now=NOW)
    reloaded = store.get_batch(batch.batch_id)
    assert reloaded.request.container_env == {
        "OPENROUTER_API_KEY": "sk-test",
        "POWERCONTEXT_SERVER_HTTP_PORT": "8000",
    }
