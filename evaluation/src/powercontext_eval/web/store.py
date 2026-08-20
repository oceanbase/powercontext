"""Durable SQLite-backed FIFO task queue for the evaluation console."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from re import fullmatch
from typing import Any, Literal, TypedDict

from powercontext_eval.codex import DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT
from powercontext_eval.paths import EvaluationPaths
from powercontext_eval.web.batches import (
    BatchControlEvent,
    BatchControlEventType,
    BatchCreate,
    BatchRecord,
    BatchStatus,
)
from powercontext_eval.web.config import (
    DEFAULT_MAX_ATTEMPTS,
    MAX_ATTEMPTS_LIMIT,
    MAX_TASK_PARALLELISM,
    MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS,
)
from powercontext_eval.web.controls import (
    BatchControlIntent,
    BatchControlState,
    BatchPauseReason,
    derive_controlled_batch_status,
)
from powercontext_eval.web.models import (
    FailureCategory,
    FailureCode,
    RetryDisposition,
    SafeFailure,
    TaskAttemptRecord,
    TaskCreate,
    TaskPhase,
    TaskRecord,
    TaskResult,
    TaskStatus,
    TaskSummary,
)
from powercontext_eval.web.usage import UsageSnapshot

_RETRY_BACKOFF_SECONDS = (30, 120, 300, 600)
# Some Gold suites contain wall-clock tests with a deterministic two-hour bad window.
# Keep the bounded final attempt until that window has rolled over instead of burning it immediately.
_GOLD_VALIDATION_RETRY_BACKOFF_SECONDS = (30, 120, 300, 7_200)


class TaskStoreError(RuntimeError):
    """Base class for task-store domain failures."""


class TaskNotFound(TaskStoreError):
    """The requested task does not exist."""


class BatchNotFound(TaskStoreError):
    """The requested batch does not exist."""


class TaskConflict(TaskStoreError):
    """The requested transition conflicts with the task lifecycle."""


class TaskAdmissionRejected(TaskStoreError):
    """A genuinely new task or batch fails the current admission policy."""


class TaskOwnershipError(TaskStoreError):
    """The worker does not own the active task lease."""


class HealthSnapshot(TypedDict):
    worker_lease_active: bool
    active_task_pairs: int
    task_parallelism: int
    queued_tasks: int
    running_tasks: int


class DeploymentSnapshot(TypedDict):
    web_revision: str | None
    worker_revision: str | None
    web_schema_version: int | None
    worker_schema_version: int | None
    deployment_consistent: bool


class FinalizationState(StrEnum):
    """Durable TokensFlow resource-owner lifecycle exposed as sanitized telemetry."""

    PENDING = "pending"
    RUNNING = "running"
    CLEANUP_PENDING = "cleanup_pending"
    PASSED = "passed"
    TIMED_OUT = "timed_out"
    CAPACITY_EVICTED = "capacity_evicted"
    CLEANUP_FAILED = "cleanup_failed"


class AttemptEvidenceState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    EXPORTED = "exported"


class AttemptCleanupState(StrEnum):
    COMPLETE = "complete"
    PENDING = "pending"


_TERMINAL_FINALIZATION_STATES = frozenset(
    {
        FinalizationState.PASSED,
        FinalizationState.TIMED_OUT,
        FinalizationState.CAPACITY_EVICTED,
        FinalizationState.CLEANUP_FAILED,
    }
)


@dataclass(frozen=True)
class TokensFlowFinalizationCreate:
    """Allowlisted, credential-free resource descriptor transferred by one arm."""

    attempt_id: str
    task_id: str
    batch_id: str | None
    arm: str
    run_id: str
    container_name: str
    runtime_path: str
    wrapper_path: str
    egress_network: str
    daemon_pid_file: str
    evidence_sha256: str
    evidence_bytes: int

    def __post_init__(self) -> None:
        safe_id = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}"
        safe_network = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
        if self.arm not in {"off", "on"}:
            raise ValueError("TokensFlow finalization arm is invalid")
        for value in (self.attempt_id, self.task_id, self.run_id, self.container_name):
            if fullmatch(safe_id, value) is None:
                raise ValueError("TokensFlow finalization identifier is unsafe")
        if self.batch_id is not None and fullmatch(safe_id, self.batch_id) is None:
            raise ValueError("TokensFlow finalization batch identifier is unsafe")
        if fullmatch(safe_network, self.egress_network) is None:
            raise ValueError("TokensFlow finalization network is unsafe")
        for value in (self.runtime_path, self.wrapper_path):
            path = Path(value)
            if path.is_absolute() or not path.parts or ".." in path.parts or "\x00" in value:
                raise ValueError("TokensFlow finalization path is unsafe")
        if (
            not self.daemon_pid_file.startswith(("/runtime/", "/root/"))
            or ".." in Path(self.daemon_pid_file).parts
            or "\x00" in self.daemon_pid_file
        ):
            raise ValueError("TokensFlow daemon path is unsafe")
        if fullmatch(r"[0-9a-f]{64}", self.evidence_sha256) is None:
            raise ValueError("TokensFlow evidence hash is invalid")
        if isinstance(self.evidence_bytes, bool) or not isinstance(self.evidence_bytes, int) or self.evidence_bytes < 0:
            raise ValueError("TokensFlow evidence byte count is invalid")


@dataclass(frozen=True)
class AttemptCleanupCandidate:
    """Allowlisted metadata required to export evidence and remove one exact attempt."""

    attempt_id: str
    task_id: str
    batch_id: str | None
    attempt_number: int
    run_id: str
    failure_code: FailureCode
    failure_phase: TaskPhase | None
    failure_summary: str


@dataclass(frozen=True)
class TokensFlowFinalizationRecord(TokensFlowFinalizationCreate):
    job_id: str
    registered_at: datetime
    deadline_at: datetime
    state: FinalizationState
    attempts: int
    queue_passed: bool
    doctor_rc: int | None
    checked_at: datetime | None
    finished_at: datetime | None
    error_category: str | None
    reason: str | None
    lease_owner: str | None
    lease_expires_at: datetime | None


class TaskStore:
    """Persist tasks and coordinate a single global worker through SQLite."""

    def __init__(
        self,
        database: Path,
        *,
        lease_duration: timedelta,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= MAX_ATTEMPTS_LIMIT
        ):
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_LIMIT}")
        self._database = database
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> None:
        """Create the queue schema and indexes if they do not exist."""
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._write() as connection:
            _execute_transactional_script(
                connection,
                """
                CREATE TABLE IF NOT EXISTS batches (
                    batch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    total_tasks INTEGER NOT NULL CHECK (total_tasks > 0),
                    created_at TEXT NOT NULL,
                    resolved_powercontext_sha TEXT,
                    control_intent TEXT NOT NULL DEFAULT 'run',
                    usage_pause_percent INTEGER NOT NULL DEFAULT 80,
                    pause_reason TEXT,
                    control_updated_at TEXT,
                    control_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tasks (
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
                    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
                    failure_category TEXT,
                    failure_phase TEXT,
                    failure_summary TEXT,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS task_attempts (
                    attempt_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    phase TEXT,
                    created_at TEXT NOT NULL,
                    eligible_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
                    failure_category TEXT,
                    failure_code TEXT,
                    retry_disposition TEXT,
                    failure_phase TEXT,
                    failure_summary TEXT,
                    result_json TEXT,
                    evidence_state TEXT NOT NULL DEFAULT 'not_required',
                    cleanup_state TEXT NOT NULL DEFAULT 'complete',
                    cleanup_eligible_at TEXT,
                    cleanup_error_code TEXT,
                    UNIQUE(task_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS worker_leases (
                    attempt_id TEXT PRIMARY KEY REFERENCES task_attempts(attempt_id),
                    worker_id TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_runtime (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    task_parallelism INTEGER NOT NULL CHECK (task_parallelism BETWEEN 1 AND 20),
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_revisions (
                    component TEXT PRIMARY KEY CHECK (component IN ('web', 'worker')),
                    build_revision TEXT NOT NULL,
                    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_snapshots (
                    snapshot_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_control_events (
                    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tokensflow_finalizations (
                    finalization_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL UNIQUE,
                    attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    batch_id TEXT REFERENCES batches(batch_id),
                    arm TEXT NOT NULL CHECK (arm IN ('off', 'on')),
                    run_id TEXT NOT NULL,
                    container_name TEXT NOT NULL,
                    runtime_path TEXT NOT NULL,
                    wrapper_path TEXT NOT NULL,
                    egress_network TEXT NOT NULL,
                    daemon_pid_file TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    evidence_bytes INTEGER NOT NULL CHECK (evidence_bytes >= 0),
                    registered_at TEXT NOT NULL,
                    deadline_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    queue_passed INTEGER NOT NULL DEFAULT 0 CHECK (queue_passed IN (0, 1)),
                    doctor_rc INTEGER,
                    checked_at TEXT,
                    finished_at TEXT,
                    error_category TEXT,
                    reason TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    UNIQUE(attempt_id, arm)
                );
                """,
            )
            task_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
            if "batch_id" not in task_columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN batch_id TEXT REFERENCES batches(batch_id)")
            if "instance_id" not in task_columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN instance_id TEXT")
            if "source_index" not in task_columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN source_index INTEGER")
            batch_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(batches)").fetchall()}
            if "control_intent" not in batch_columns:
                connection.execute("ALTER TABLE batches ADD COLUMN control_intent TEXT NOT NULL DEFAULT 'run'")
            if "usage_pause_percent" not in batch_columns:
                connection.execute("ALTER TABLE batches ADD COLUMN usage_pause_percent INTEGER NOT NULL DEFAULT 80")
            if "pause_reason" not in batch_columns:
                connection.execute("ALTER TABLE batches ADD COLUMN pause_reason TEXT")
            if "control_updated_at" not in batch_columns:
                connection.execute("ALTER TABLE batches ADD COLUMN control_updated_at TEXT")
            if "control_version" not in batch_columns:
                connection.execute("ALTER TABLE batches ADD COLUMN control_version INTEGER NOT NULL DEFAULT 0")
            attempt_columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(task_attempts)").fetchall()
            }
            if "eligible_at" not in attempt_columns:
                connection.execute("ALTER TABLE task_attempts ADD COLUMN eligible_at TEXT")
            if "failure_code" not in attempt_columns:
                connection.execute("ALTER TABLE task_attempts ADD COLUMN failure_code TEXT")
            if "retry_disposition" not in attempt_columns:
                connection.execute("ALTER TABLE task_attempts ADD COLUMN retry_disposition TEXT")
            if "evidence_state" not in attempt_columns:
                connection.execute(
                    "ALTER TABLE task_attempts ADD COLUMN evidence_state TEXT NOT NULL DEFAULT 'not_required'"
                )
            if "cleanup_state" not in attempt_columns:
                connection.execute(
                    "ALTER TABLE task_attempts ADD COLUMN cleanup_state TEXT NOT NULL DEFAULT 'complete'"
                )
            if "cleanup_eligible_at" not in attempt_columns:
                connection.execute("ALTER TABLE task_attempts ADD COLUMN cleanup_eligible_at TEXT")
            if "cleanup_error_code" not in attempt_columns:
                connection.execute("ALTER TABLE task_attempts ADD COLUMN cleanup_error_code TEXT")
            connection.execute("UPDATE task_attempts SET eligible_at = created_at WHERE eligible_at IS NULL")
            connection.execute("UPDATE batches SET control_updated_at = created_at WHERE control_updated_at IS NULL")
            _migrate_worker_runtime_parallelism_constraint(connection)
            _backfill_legacy_batch_requests(connection)
            _backfill_legacy_task_requests(connection)
            connection.execute(
                """
                UPDATE batches
                SET control_intent = ?
                WHERE EXISTS (
                    SELECT 1 FROM tasks WHERE tasks.batch_id = batches.batch_id
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM tasks
                    WHERE tasks.batch_id = batches.batch_id
                    AND tasks.status != ?
                )
                """,
                (BatchControlIntent.CANCEL.value, TaskStatus.CANCELLED.value),
            )
            connection.execute(
                """
                INSERT INTO task_attempts(
                    attempt_id, task_id, attempt_number, idempotency_key, status,
                    phase, created_at, eligible_at, started_at, finished_at, version,
                    failure_category, failure_phase, failure_summary, result_json
                )
                SELECT
                    task_id || '.attempt-0001',
                    task_id,
                    1,
                    task_id || '.attempt-0001',
                    status,
                    phase,
                    created_at,
                    created_at,
                    started_at,
                    finished_at,
                    version,
                    failure_category,
                    failure_phase,
                    failure_summary,
                    result_json
                FROM tasks
                WHERE NOT EXISTS (
                    SELECT 1 FROM task_attempts WHERE task_attempts.task_id = tasks.task_id
                )
                """
            )
            singular_lease = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'worker_lease'"
            ).fetchone()
            if singular_lease is not None:
                lease_columns = {
                    str(row["name"]) for row in connection.execute("PRAGMA table_info(worker_lease)").fetchall()
                }
                if "attempt_id" in lease_columns:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO worker_leases(attempt_id, worker_id, expires_at)
                        SELECT attempt_id, worker_id, expires_at FROM worker_lease
                        """
                    )
                else:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO worker_leases(attempt_id, worker_id, expires_at)
                        SELECT attempts.attempt_id, legacy.worker_id, legacy.expires_at
                        FROM worker_lease AS legacy
                        JOIN task_attempts AS attempts
                          ON attempts.task_id = legacy.task_id
                         AND attempts.attempt_number = (
                             SELECT MAX(newest.attempt_number)
                             FROM task_attempts AS newest
                             WHERE newest.task_id = legacy.task_id
                         )
                        """
                    )
                connection.execute("DROP TABLE worker_lease")
            _execute_transactional_script(
                connection,
                """
                CREATE INDEX IF NOT EXISTS tasks_status_queue
                    ON tasks(status, queue_seq);
                CREATE UNIQUE INDEX IF NOT EXISTS tasks_batch_instance
                    ON tasks(batch_id, instance_id)
                    WHERE batch_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS tasks_batch_source_index
                    ON tasks(batch_id, source_index)
                    WHERE batch_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS usage_snapshots_observed
                    ON usage_snapshots(observed_at, snapshot_seq);
                CREATE INDEX IF NOT EXISTS batch_control_events_batch_sequence
                    ON batch_control_events(batch_id, event_seq);
                CREATE INDEX IF NOT EXISTS task_attempts_task_number
                    ON task_attempts(task_id, attempt_number);
                CREATE INDEX IF NOT EXISTS task_attempts_status_sequence
                    ON task_attempts(status, attempt_seq);
                CREATE INDEX IF NOT EXISTS task_attempts_claim_eligibility
                    ON task_attempts(status, eligible_at, attempt_seq);
                CREATE INDEX IF NOT EXISTS task_attempts_cleanup_eligibility
                    ON task_attempts(cleanup_state, cleanup_eligible_at, attempt_seq);
                CREATE INDEX IF NOT EXISTS worker_leases_expiry
                    ON worker_leases(expires_at);
                CREATE INDEX IF NOT EXISTS tokensflow_finalizations_open_sequence
                    ON tokensflow_finalizations(state, registered_at, finalization_seq);
                CREATE INDEX IF NOT EXISTS tokensflow_finalizations_lease
                    ON tokensflow_finalizations(lease_expires_at);
                CREATE INDEX IF NOT EXISTS tokensflow_finalizations_attempt
                    ON tokensflow_finalizations(attempt_id, arm);
                """,
            )

    def create_batch(
        self,
        request: BatchCreate,
        instance_ids: Sequence[str],
        *,
        now: datetime,
        resolved_powercontext_sha: str | None = None,
        admit_model: Callable[[str], bool] | None = None,
    ) -> tuple[BatchRecord, bool]:
        """Create a durable batch and all of its queued children atomically."""

        ordered_ids = tuple(instance_ids)
        if not ordered_ids:
            raise ValueError("A batch must contain at least one instance")
        if len(set(ordered_ids)) != len(ordered_ids):
            raise ValueError("A batch cannot contain duplicate instance IDs")
        if resolved_powercontext_sha is not None and fullmatch(r"[0-9a-f]{40}", resolved_powercontext_sha) is None:
            raise ValueError("Resolved PowerContext SHA must be 40 lowercase hexadecimal characters")
        created_at = _timestamp(now)
        initial_intent = BatchControlIntent(request.initial_control_intent)
        initial_pause_reason = BatchPauseReason.USER if initial_intent is BatchControlIntent.PAUSE else None
        request_json = request.model_dump_json()
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM batches WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing is not None:
                self._require_idempotent_request(existing["request_json"], request_json)
                return self._batch_record(connection, existing), False
            if admit_model is not None and not admit_model(request.model):
                raise TaskAdmissionRejected("The requested model is not admitted for new work")

            placeholder = f"pending-batch-{uuid.uuid4().hex}"
            cursor = connection.execute(
                """
                INSERT INTO batches(
                    batch_id, idempotency_key, request_json, total_tasks, created_at,
                    resolved_powercontext_sha, control_intent, usage_pause_percent,
                    pause_reason, control_updated_at, control_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    placeholder,
                    request.idempotency_key,
                    request_json,
                    len(ordered_ids),
                    created_at,
                    resolved_powercontext_sha,
                    initial_intent.value,
                    request.usage_pause_percent,
                    initial_pause_reason.value if initial_pause_reason is not None else None,
                    created_at,
                    0,
                ),
            )
            sequence = cursor.lastrowid
            if sequence is None:  # pragma: no cover - SQLite guarantees this for INTEGER PRIMARY KEY
                raise TaskStoreError("SQLite did not assign a batch sequence")
            batch_id = _batch_id(now, sequence)
            connection.execute(
                "UPDATE batches SET batch_id = ? WHERE batch_seq = ?",
                (batch_id, sequence),
            )
            for source_index, instance_id in enumerate(ordered_ids):
                task_id = _batch_task_id(now, sequence, source_index)
                child = TaskCreate(
                    powercontext_ref=request.powercontext_ref,
                    benchmark=request.benchmark,
                    instance_id=instance_id,
                    model=request.model,
                    reasoning_effort=request.reasoning_effort,
                    treatment_mode=request.treatment_mode,
                    idempotency_key=f"{batch_id}.{source_index:04d}",
                    container_env=request.container_env,
                )
                connection.execute(
                    """
                    INSERT INTO tasks(
                        task_id, idempotency_key, request_json, batch_id, instance_id,
                        source_index, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        child.idempotency_key,
                        child.model_dump_json(),
                        batch_id,
                        instance_id,
                        source_index,
                        TaskStatus.QUEUED.value,
                        created_at,
                    ),
                )
                self._insert_initial_attempt(
                    connection,
                    task_id=task_id,
                    idempotency_key=f"{task_id}.attempt-0001",
                    created_at=created_at,
                )
            self._append_control_event(
                connection,
                batch_id,
                BatchControlEventType.BATCH_CREATED,
                "system",
                {
                    "usage_pause_percent": request.usage_pause_percent,
                    "initial_control_intent": initial_intent.value,
                },
                now,
            )
            return self._batch_record(connection, self._select_batch(connection, batch_id)), True

    def find_batch_replay(self, request: BatchCreate) -> BatchRecord | None:
        """Return only an existing exact replay before expensive batch preparation."""

        request_json = request.model_dump_json()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM batches WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing is None:
                return None
            self._require_idempotent_request(existing["request_json"], request_json)
            return self._batch_record(connection, existing)

    def get_batch(self, batch_id: str) -> BatchRecord:
        """Return one batch with lifecycle state derived from its children."""

        with self._connection() as connection:
            return self._batch_record(connection, self._select_batch(connection, batch_id))

    def save_usage_snapshot(self, snapshot: UsageSnapshot) -> UsageSnapshot:
        """Append one normalized account-wide usage observation."""

        observed_at = _timestamp(snapshot.observed_at)
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO usage_snapshots(snapshot_json, observed_at)
                VALUES (?, ?)
                """,
                (snapshot.model_dump_json(), observed_at),
            )
        return snapshot

    def latest_usage_snapshot(self) -> UsageSnapshot | None:
        """Return the newest immutable usage observation, if one exists."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json
                FROM usage_snapshots
                ORDER BY snapshot_seq DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return UsageSnapshot.model_validate_json(row["snapshot_json"], strict=True)

    def list_control_events(self, batch_id: str) -> tuple[BatchControlEvent, ...]:
        """Return the sanitized control audit trail in insertion order."""

        with self._connection() as connection:
            self._select_batch(connection, batch_id)
            rows = connection.execute(
                """
                SELECT *
                FROM batch_control_events
                WHERE batch_id = ?
                ORDER BY event_seq ASC
                """,
                (batch_id,),
            ).fetchall()
        return tuple(self._control_event(row) for row in rows)

    def request_pause(
        self,
        batch_id: str,
        *,
        reason: BatchPauseReason,
        now: datetime,
    ) -> BatchRecord:
        """Persist a pause intent and stop only at a benchmark-task boundary."""

        if reason is not BatchPauseReason.USER:
            raise ValueError("Only an operator may persist a pause intent")
        now_text = _timestamp(now)
        with self._write() as connection:
            row = self._select_batch(connection, batch_id)
            intent = BatchControlIntent(row["control_intent"])
            if intent is BatchControlIntent.PAUSE:
                return self._batch_record(connection, row)
            if intent is BatchControlIntent.CANCEL:
                raise TaskConflict("A cancelling batch cannot be paused")
            if self._all_batch_tasks_terminal(connection, batch_id):
                raise TaskConflict("A completed batch cannot be paused")

            connection.execute(
                """
                UPDATE batches
                SET control_intent = ?, pause_reason = ?, control_updated_at = ?,
                    control_version = control_version + 1
                WHERE batch_id = ?
                """,
                (BatchControlIntent.PAUSE.value, reason.value, now_text, batch_id),
            )
            self._append_control_event(connection, batch_id, BatchControlEventType.PAUSE_REQUESTED, "user", {}, now)
            self._finalize_batch_intent(connection, batch_id, now=now)
            return self._batch_record(connection, self._select_batch(connection, batch_id))

    def request_resume(
        self,
        batch_id: str,
        *,
        now: datetime,
    ) -> BatchRecord:
        """Persist the user's request to run; admission gates remain non-persistent."""

        _timestamp(now)
        with self._write() as connection:
            row = self._select_batch(connection, batch_id)
            intent = BatchControlIntent(row["control_intent"])
            if intent is BatchControlIntent.RUN:
                return self._batch_record(connection, row)
            if intent is BatchControlIntent.CANCEL:
                raise TaskConflict("A cancelling batch cannot be resumed")
            if self._all_batch_tasks_terminal(connection, batch_id):
                raise TaskConflict("A completed batch cannot be resumed")
            self._append_control_event(
                connection,
                batch_id,
                BatchControlEventType.RESUME_REQUESTED,
                "user",
                {},
                now,
            )
            connection.execute(
                """
                UPDATE batches
                SET control_intent = ?, pause_reason = NULL, control_updated_at = ?,
                    control_version = control_version + 1
                WHERE batch_id = ?
                """,
                (BatchControlIntent.RUN.value, _timestamp(now), batch_id),
            )
            self._append_control_event(
                connection,
                batch_id,
                BatchControlEventType.RESUMED,
                "system",
                {},
                now,
            )
            return self._batch_record(connection, self._select_batch(connection, batch_id))

    def request_cancel(self, batch_id: str, *, now: datetime) -> BatchRecord:
        """Persist cancellation and cancel queued work once no child is running."""

        now_text = _timestamp(now)
        with self._write() as connection:
            row = self._select_batch(connection, batch_id)
            intent = BatchControlIntent(row["control_intent"])
            if intent is BatchControlIntent.CANCEL:
                self._finalize_batch_intent(connection, batch_id, now=now)
                return self._batch_record(connection, self._select_batch(connection, batch_id))
            if self._all_batch_tasks_terminal(connection, batch_id):
                raise TaskConflict("A completed batch cannot be cancelled")

            connection.execute(
                """
                UPDATE batches
                SET control_intent = ?, pause_reason = NULL, control_updated_at = ?,
                    control_version = control_version + 1
                WHERE batch_id = ?
                """,
                (BatchControlIntent.CANCEL.value, now_text, batch_id),
            )
            self._append_control_event(
                connection,
                batch_id,
                BatchControlEventType.CANCEL_REQUESTED,
                "user",
                {},
                now,
            )
            self._finalize_batch_intent(connection, batch_id, now=now)
            return self._batch_record(connection, self._select_batch(connection, batch_id))

    def update_usage_threshold(
        self,
        batch_id: str,
        *,
        percent: int,
        expected_version: int,
        now: datetime,
    ) -> BatchRecord:
        """Update a threshold with optimistic concurrency and no implicit resume."""

        _validate_percentage(percent)
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be a non-negative integer")
        now_text = _timestamp(now)
        with self._write() as connection:
            row = self._select_batch(connection, batch_id)
            version = _stored_int(row["control_version"], name="control version")
            if version != expected_version:
                raise TaskConflict("Batch control version does not match")
            previous = _stored_int(row["usage_pause_percent"], name="usage threshold")
            if previous == percent:
                return self._batch_record(connection, row)

            connection.execute(
                """
                UPDATE batches
                SET usage_pause_percent = ?, control_updated_at = ?,
                    control_version = control_version + 1
                WHERE batch_id = ?
                """,
                (percent, now_text, batch_id),
            )
            self._append_control_event(
                connection,
                batch_id,
                BatchControlEventType.THRESHOLD_CHANGED,
                "user",
                {"from_percent": previous, "to_percent": percent},
                now,
            )
            return self._batch_record(connection, self._select_batch(connection, batch_id))

    def finalize_batch_intent_after_attempt(self, batch_id: str, *, now: datetime) -> BatchRecord:
        """Apply a pending pause or cancel after the active benchmark task ends."""

        _timestamp(now)
        with self._write() as connection:
            self._select_batch(connection, batch_id)
            self._finalize_batch_intent(connection, batch_id, now=now)
            return self._batch_record(connection, self._select_batch(connection, batch_id))

    def pin_batch_revision(self, batch_id: str, sha: str) -> BatchRecord:
        """Persist the one immutable PowerContext revision shared by all children."""

        if fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise ValueError("Pinned PowerContext SHA must be 40 lowercase hexadecimal characters")
        with self._write() as connection:
            row = self._select_batch(connection, batch_id)
            existing = row["resolved_powercontext_sha"]
            if existing is not None and existing != sha:
                raise TaskConflict("Batch is already pinned to a different PowerContext revision")
            if existing is None:
                connection.execute(
                    "UPDATE batches SET resolved_powercontext_sha = ? WHERE batch_id = ?",
                    (sha, batch_id),
                )
            return self._batch_record(connection, self._select_batch(connection, batch_id))

    def list_batches(self) -> list[BatchRecord]:
        """List batches in stable creation order."""

        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM batches ORDER BY batch_seq ASC").fetchall()
            return [self._batch_record(connection, row) for row in rows]

    def list_batch_tasks(self, batch_id: str) -> list[TaskRecord]:
        """List every child task in immutable dataset source order."""

        with self._connection() as connection:
            self._select_batch(connection, batch_id)
            rows = connection.execute(
                "SELECT * FROM tasks WHERE batch_id = ? ORDER BY source_index ASC",
                (batch_id,),
            ).fetchall()
            return [self._record(connection, row) for row in rows]

    def get_batch_task(self, batch_id: str, task_id: str) -> TaskRecord:
        """Return one task only when it belongs to the requested batch."""

        with self._connection() as connection:
            self._select_batch(connection, batch_id)
            row = connection.execute(
                "SELECT * FROM tasks WHERE batch_id = ? AND task_id = ?",
                (batch_id, task_id),
            ).fetchone()
            if row is None:
                raise TaskNotFound(f"Task not found in batch: {task_id}")
            return self._record(connection, row)

    def list_task_attempts(self, batch_id: str, task_id: str) -> tuple[TaskAttemptRecord, ...]:
        """List every immutable execution attempt for one logical batch task."""

        with self._connection() as connection:
            self._select_batch(connection, batch_id)
            task = connection.execute(
                "SELECT 1 FROM tasks WHERE batch_id = ? AND task_id = ?",
                (batch_id, task_id),
            ).fetchone()
            if task is None:
                raise TaskNotFound(f"Task not found in batch: {task_id}")
            rows = connection.execute(
                """
                SELECT *
                FROM task_attempts
                WHERE task_id = ?
                ORDER BY attempt_number ASC
                """,
                (task_id,),
            ).fetchall()
        return tuple(self._attempt_record(row) for row in rows)

    def retry_failed_task(
        self,
        batch_id: str,
        task_id: str,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> tuple[TaskAttemptRecord, bool]:
        """Create one new queued attempt without modifying retained failures."""

        if not isinstance(idempotency_key, str) or fullmatch(r"[A-Za-z0-9._-]{8,128}", idempotency_key) is None:
            raise ValueError("Retry idempotency key is invalid")
        with self._write() as connection:
            self._select_batch(connection, batch_id)
            task = connection.execute(
                "SELECT * FROM tasks WHERE batch_id = ? AND task_id = ?",
                (batch_id, task_id),
            ).fetchone()
            if task is None:
                raise TaskNotFound(f"Task not found in batch: {task_id}")
            existing = connection.execute(
                "SELECT * FROM task_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["task_id"] != task_id:
                    raise TaskConflict("Retry idempotency key belongs to another task")
                return self._attempt_record(existing), False

            latest = self._select_latest_attempt(connection, task_id)
            latest_record = self._attempt_record(latest)
            if latest_record.status not in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
                raise TaskConflict("The current task outcome is not retryable")
            if latest_record.attempt_number >= self._max_attempts:
                raise TaskConflict("The current task outcome is not retryable")
            if latest["cleanup_state"] != AttemptCleanupState.COMPLETE.value:
                raise TaskConflict("The current task cleanup is not complete")
            self._enqueue_next_attempt(
                connection,
                task_id=task_id,
                batch_id=batch_id,
                attempt_number=latest_record.attempt_number + 1,
                idempotency_key=idempotency_key,
                actor="user",
                details={},
                now=now,
                eligible_at=now,
            )
            return self._attempt_record(self._select_latest_attempt(connection, task_id)), True

    def _accepts_another_attempt(self, connection: sqlite3.Connection, task_row: sqlite3.Row) -> bool:
        """Report whether a batch child may still be retried rather than left failed."""

        batch_id = task_row["batch_id"]
        if batch_id is None:
            return False
        attempt = self._select_latest_attempt(connection, str(task_row["task_id"]))
        return (
            int(attempt["attempt_number"]) < self._max_attempts
            and BatchControlIntent(self._select_batch(connection, str(batch_id))["control_intent"])
            is not BatchControlIntent.CANCEL
        )

    def _enqueue_next_attempt(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        batch_id: str | None,
        attempt_number: int,
        idempotency_key: str,
        actor: Literal["user", "system"],
        details: Mapping[str, int | str | None],
        now: datetime,
        eligible_at: datetime,
    ) -> None:
        """Queue one further attempt for a task whose latest attempt is retryable."""

        connection.execute(
            """
            INSERT INTO task_attempts(
                attempt_id, task_id, attempt_number, idempotency_key,
                status, created_at, eligible_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{task_id}.attempt-{attempt_number:04d}",
                task_id,
                attempt_number,
                idempotency_key,
                TaskStatus.QUEUED.value,
                _timestamp(now),
                _timestamp(eligible_at),
            ),
        )
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, phase = NULL, started_at = NULL, finished_at = NULL,
                version = 0, failure_category = NULL, failure_phase = NULL,
                failure_summary = NULL, result_json = NULL
            WHERE task_id = ?
            """,
            (TaskStatus.QUEUED.value, task_id),
        )
        if batch_id is not None:
            self._append_control_event(
                connection,
                batch_id,
                BatchControlEventType.TASK_RETRY_REQUESTED,
                actor,
                {"task_id": task_id, "attempt_number": attempt_number, **details},
                now,
            )

    def cancel_batch_queued(self, batch_id: str, *, now: datetime) -> BatchRecord:
        """Compatibility alias for the durable boundary-based cancellation action."""

        return self.request_cancel(batch_id, now=now)

    def create(
        self,
        request: TaskCreate,
        *,
        now: datetime,
        admit_model: Callable[[str], bool] | None = None,
    ) -> tuple[TaskRecord, bool]:
        """Create a queued task, or replay the task for an idempotency key."""
        created_at = _timestamp(now)
        request_json = request.model_dump_json()
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM tasks WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing is not None:
                self._require_idempotent_request(existing["request_json"], request_json)
                return self._record(connection, existing), False
            if admit_model is not None and not admit_model(request.model):
                raise TaskAdmissionRejected("The requested model is not admitted for new work")

            placeholder = f"pending-{uuid.uuid4().hex}"
            cursor = connection.execute(
                """
                INSERT INTO tasks (
                    task_id, idempotency_key, request_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (placeholder, request.idempotency_key, request_json, TaskStatus.QUEUED.value, created_at),
            )
            sequence = cursor.lastrowid
            if sequence is None:  # pragma: no cover - sqlite guarantees this for an INTEGER PRIMARY KEY
                raise TaskStoreError("SQLite did not assign a task sequence")
            task_id = _task_id(now, sequence)
            connection.execute(
                "UPDATE tasks SET task_id = ? WHERE queue_seq = ?",
                (task_id, sequence),
            )
            self._insert_initial_attempt(
                connection,
                task_id=task_id,
                idempotency_key=f"{task_id}.attempt-0001",
                created_at=created_at,
            )
            row = self._select_task(connection, task_id)
            return self._record(connection, row), True

    def find_task_replay(self, request: TaskCreate) -> TaskRecord | None:
        """Return only an existing exact replay before creating a task."""

        request_json = request.model_dump_json()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM tasks WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing is None:
                return None
            self._require_idempotent_request(existing["request_json"], request_json)
            return self._record(connection, existing)

    @staticmethod
    def _require_idempotent_request(stored_json: object, request_json: str) -> None:
        if not isinstance(stored_json, str) or stored_json != request_json:
            raise TaskConflict("The idempotency key belongs to a different request")

    def get(self, task_id: str) -> TaskRecord:
        """Return one task or raise :class:`TaskNotFound`."""
        with self._connection() as connection:
            return self._record(connection, self._select_task(connection, task_id))

    def list_tasks(
        self,
        *,
        status: TaskStatus | None,
        order: Literal["oldest", "newest"] = "oldest",
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskSummary]:
        """List tasks in a stable requested creation order."""
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must not be negative")
        if order not in ("oldest", "newest"):
            raise ValueError("order must be oldest or newest")
        sql = "SELECT * FROM tasks"
        parameters: list[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            parameters.append(status.value)
        sql += " ORDER BY queue_seq ASC" if order == "oldest" else " ORDER BY queue_seq DESC"
        sql += " LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._connection() as connection:
            return [
                self._summary(self._record(connection, row)) for row in connection.execute(sql, parameters).fetchall()
            ]

    def list_succeeded_tasks_for_workspace_reclaim(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskRecord]:
        """List successful current attempts whose deferred cleanup is safely terminal."""

        if limit < 1:
            raise ValueError("Workspace reclaim limit must be positive")
        if offset < 0:
            raise ValueError("Workspace reclaim offset must not be negative")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT tasks.*
                FROM tasks
                JOIN task_attempts
                  ON task_attempts.task_id = tasks.task_id
                 AND task_attempts.attempt_number = (
                     SELECT MAX(current_attempt.attempt_number)
                     FROM task_attempts AS current_attempt
                     WHERE current_attempt.task_id = tasks.task_id
                 )
                WHERE tasks.status = ?
                  AND task_attempts.status = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM tokensflow_finalizations
                      WHERE tokensflow_finalizations.attempt_id = task_attempts.attempt_id
                        AND tokensflow_finalizations.state NOT IN (?, ?, ?)
                  )
                ORDER BY tasks.queue_seq ASC
                LIMIT ? OFFSET ?
                """,
                (
                    TaskStatus.SUCCEEDED.value,
                    TaskStatus.SUCCEEDED.value,
                    FinalizationState.PASSED.value,
                    FinalizationState.TIMED_OUT.value,
                    FinalizationState.CAPACITY_EVICTED.value,
                    limit,
                    offset,
                ),
            ).fetchall()
            return [self._record(connection, row) for row in rows]

    def queue_position(self, task_id: str) -> int | None:
        """Return the one-based position among currently queued tasks."""
        with self._connection() as connection:
            row = self._select_task(connection, task_id)
            if TaskStatus(row["status"]) is not TaskStatus.QUEUED:
                return None
            position = connection.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE status = ? AND queue_seq <= ?
                """,
                (TaskStatus.QUEUED.value, row["queue_seq"]),
            ).fetchone()[0]
            if not isinstance(position, int):
                raise TypeError("SQLite queue count is not an integer")
            return position

    def health_snapshot(self, *, now: datetime) -> HealthSnapshot:
        """Return queue counts and observable lease state without mutating tasks."""
        now_text = _timestamp(now)
        with self._connection() as connection:
            counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
            }
            active_task_pairs = connection.execute(
                "SELECT COUNT(*) FROM worker_leases WHERE expires_at > ?",
                (now_text,),
            ).fetchone()[0]
            runtime = connection.execute(
                "SELECT task_parallelism FROM worker_runtime WHERE singleton = ?",
                (1,),
            ).fetchone()
        if not isinstance(active_task_pairs, int):
            raise TypeError("SQLite active lease count is not an integer")
        task_parallelism = 1 if runtime is None else _stored_int(runtime["task_parallelism"], name="task parallelism")
        return {
            "worker_lease_active": active_task_pairs > 0,
            "active_task_pairs": active_task_pairs,
            "task_parallelism": task_parallelism,
            "queued_tasks": counts.get(TaskStatus.QUEUED.value, 0),
            "running_tasks": counts.get(TaskStatus.RUNNING.value, 0),
        }

    def record_worker_capacity(self, task_parallelism: int, *, now: datetime) -> None:
        """Publish the capacity owned by the active Worker supervisor."""

        _validate_task_parallelism(task_parallelism)
        observed_at = _timestamp(now)
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO worker_runtime(singleton, task_parallelism, observed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    task_parallelism = excluded.task_parallelism,
                    observed_at = excluded.observed_at
                """,
                (1, task_parallelism, observed_at),
            )

    def record_runtime_revision(
        self,
        component: Literal["web", "worker"],
        *,
        build_revision: str,
        schema_version: int,
        now: datetime,
    ) -> None:
        """Publish one process revision without exposing deployment internals."""

        if component not in {"web", "worker"}:
            raise ValueError("Runtime component is invalid")
        if fullmatch(r"[0-9a-f]{40}|unknown", build_revision) is None:
            raise ValueError("Build revision is invalid")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version <= 0:
            raise ValueError("Schema version must be positive")
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO runtime_revisions(component, build_revision, schema_version, observed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    build_revision = excluded.build_revision,
                    schema_version = excluded.schema_version,
                    observed_at = excluded.observed_at
                """,
                (component, build_revision, schema_version, _timestamp(now)),
            )

    def deployment_snapshot(self) -> DeploymentSnapshot:
        """Return the sanitized Web/Worker compatibility boundary."""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT component, build_revision, schema_version FROM runtime_revisions ORDER BY component"
            ).fetchall()
        values = {str(row["component"]): row for row in rows}
        web = values.get("web")
        worker = values.get("worker")
        web_revision = None if web is None else str(web["build_revision"])
        worker_revision = None if worker is None else str(worker["build_revision"])
        web_schema = None if web is None else _stored_int(web["schema_version"], name="Web schema version")
        worker_schema = None if worker is None else _stored_int(worker["schema_version"], name="Worker schema version")
        both_published = web is not None and worker is not None
        return {
            "web_revision": web_revision,
            "worker_revision": worker_revision,
            "web_schema_version": web_schema,
            "worker_schema_version": worker_schema,
            "deployment_consistent": bool(
                both_published
                and web_revision != "unknown"
                and web_revision == worker_revision
                and web_schema == worker_schema
            ),
        }

    def deployment_admission_open(self) -> bool:
        """Require published process markers to match before claiming new work.

        Both processes must publish an exact non-placeholder match. Direct queue
        consumers that intentionally do not use this gate remain unaffected.
        """

        snapshot = self.deployment_snapshot()
        return snapshot["deployment_consistent"]

    def register_tokensflow_finalization(
        self,
        create: TokensFlowFinalizationCreate,
        *,
        now: datetime,
        timeout_seconds: int,
    ) -> tuple[TokensFlowFinalizationRecord, bool]:
        """Durably transfer one arm's credential-free resource descriptor."""

        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("TokensFlow finalization timeout must be a positive integer")
        if timeout_seconds > MAX_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS:
            raise ValueError("TokensFlow finalization timeout must not exceed 600 seconds")
        registered_at = _timestamp(now)
        deadline_at = _timestamp(now + timedelta(seconds=timeout_seconds))
        with self._write() as connection:
            attempt = connection.execute(
                "SELECT task_id FROM task_attempts WHERE attempt_id = ?",
                (create.attempt_id,),
            ).fetchone()
            if attempt is None or attempt["task_id"] != create.task_id:
                raise TaskConflict("TokensFlow finalization attempt does not match its task")
            task = self._select_task(connection, create.task_id)
            if task["batch_id"] != create.batch_id:
                raise TaskConflict("TokensFlow finalization batch does not match its task")
            existing = connection.execute(
                "SELECT * FROM tokensflow_finalizations WHERE attempt_id = ? AND arm = ?",
                (create.attempt_id, create.arm),
            ).fetchone()
            if existing is not None:
                stored = self._finalization_record(existing)
                if any(
                    getattr(stored, field) != getattr(create, field)
                    for field in TokensFlowFinalizationCreate.__dataclass_fields__
                ):
                    raise TaskConflict("TokensFlow finalization registration conflicts with its durable descriptor")
                return stored, False
            job_id = f"tokensflow-finalization-{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO tokensflow_finalizations(
                    job_id, attempt_id, task_id, batch_id, arm, run_id, container_name,
                    runtime_path, wrapper_path, egress_network, daemon_pid_file,
                    evidence_sha256, evidence_bytes, registered_at, deadline_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    create.attempt_id,
                    create.task_id,
                    create.batch_id,
                    create.arm,
                    create.run_id,
                    create.container_name,
                    create.runtime_path,
                    create.wrapper_path,
                    create.egress_network,
                    create.daemon_pid_file,
                    create.evidence_sha256,
                    create.evidence_bytes,
                    registered_at,
                    deadline_at,
                    FinalizationState.PENDING.value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM tokensflow_finalizations WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            assert row is not None
            return self._finalization_record(row), True

    def list_open_tokensflow_finalizations(self) -> list[TokensFlowFinalizationRecord]:
        """Return resumable jobs oldest first, independent from task leases."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tokensflow_finalizations
                WHERE state IN (?, ?, ?)
                ORDER BY registered_at ASC, finalization_seq ASC
                """,
                (
                    FinalizationState.PENDING.value,
                    FinalizationState.RUNNING.value,
                    FinalizationState.CLEANUP_PENDING.value,
                ),
            ).fetchall()
            return [self._finalization_record(row) for row in rows]

    def tokensflow_finalizations_for_attempt(self, attempt_id: str) -> list[TokensFlowFinalizationRecord]:
        """Return OFF/ON finalization telemetry for one exact attempt."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tokensflow_finalizations
                WHERE attempt_id = ?
                ORDER BY CASE arm WHEN 'off' THEN 0 ELSE 1 END
                """,
                (attempt_id,),
            ).fetchall()
            return [self._finalization_record(row) for row in rows]

    def claim_tokensflow_finalization(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_seconds: int,
        process_lock_recovery_at: datetime | None = None,
        job_id: str | None = None,
    ) -> TokensFlowFinalizationRecord | None:
        """Claim a resumable job; threshold recovery requires the caller to hold the unique Worker process lock."""

        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", worker_id) is None:
            raise ValueError("TokensFlow finalizer worker ID is unsafe")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValueError("TokensFlow finalizer lease must be a positive integer")
        if process_lock_recovery_at is not None and job_id is None:
            raise ValueError("TokensFlow process-lock recovery requires an exact job ID")
        now_text = _timestamp(now)
        recovery_at_text = _timestamp(process_lock_recovery_at) if process_lock_recovery_at is not None else None
        expires_at = _timestamp(now + timedelta(seconds=lease_seconds))
        with self._write() as connection:
            job_filter = "" if job_id is None else "AND job_id = ?"
            recovery_filter = "" if recovery_at_text is None else "OR (state = ? AND ? <= ?)"
            parameters: tuple[object, ...] = (
                FinalizationState.PENDING.value,
                FinalizationState.RUNNING.value,
                FinalizationState.CLEANUP_PENDING.value,
                now_text,
                *(() if recovery_at_text is None else (FinalizationState.RUNNING.value, recovery_at_text, now_text)),
                *((job_id,) if job_id is not None else ()),
            )
            row = connection.execute(
                f"""
                SELECT * FROM tokensflow_finalizations
                WHERE (state = ? OR (state IN (?, ?) AND lease_expires_at <= ?) {recovery_filter})
                {job_filter}
                ORDER BY registered_at ASC, finalization_seq ASC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE tokensflow_finalizations
                SET state = CASE WHEN state = ? THEN ? ELSE ? END,
                    attempts = attempts + 1, lease_owner = ?, lease_expires_at = ?
                WHERE job_id = ?
                """,
                (
                    FinalizationState.CLEANUP_PENDING.value,
                    FinalizationState.CLEANUP_PENDING.value,
                    FinalizationState.RUNNING.value,
                    worker_id,
                    expires_at,
                    row["job_id"],
                ),
            )
            claimed = connection.execute(
                "SELECT * FROM tokensflow_finalizations WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
            assert claimed is not None
            return self._finalization_record(claimed)

    def release_tokensflow_finalization(
        self,
        job_id: str,
        worker_id: str,
        *,
        now: datetime,
        error_category: str | None = None,
    ) -> TokensFlowFinalizationRecord:
        """Release a retryable job promptly without waiting for its lease to expire."""

        with self._write() as connection:
            self._require_finalization_owner(connection, job_id, worker_id, now=now)
            connection.execute(
                """
                UPDATE tokensflow_finalizations
                SET state = ?, error_category = ?, lease_owner = NULL, lease_expires_at = NULL
                WHERE job_id = ?
                """,
                (FinalizationState.PENDING.value, error_category, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM tokensflow_finalizations WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            assert updated is not None
            return self._finalization_record(updated)

    def defer_tokensflow_finalization_cleanup(
        self,
        job_id: str,
        worker_id: str,
        *,
        now: datetime,
        retry_seconds: float,
        reason: str,
    ) -> TokensFlowFinalizationRecord:
        """Keep failed resource removal durable and retryable without owning a task slot."""

        if isinstance(retry_seconds, bool) or not isinstance(retry_seconds, (int, float)) or retry_seconds <= 0:
            raise ValueError("TokensFlow cleanup retry interval must be positive")
        retry_at = _timestamp(now + timedelta(seconds=retry_seconds))
        with self._write() as connection:
            self._require_finalization_owner(connection, job_id, worker_id, now=now)
            connection.execute(
                """
                UPDATE tokensflow_finalizations
                SET state = ?, finished_at = NULL, reason = ?, error_category = ?,
                    lease_owner = NULL, lease_expires_at = ?
                WHERE job_id = ?
                """,
                (
                    FinalizationState.CLEANUP_PENDING.value,
                    reason,
                    "resource_removal_failed",
                    retry_at,
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM tokensflow_finalizations WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            assert updated is not None
            return self._finalization_record(updated)

    def record_tokensflow_finalization_check(
        self,
        job_id: str,
        worker_id: str,
        *,
        queue_passed: bool,
        doctor_rc: int | None,
        now: datetime,
        error_category: str | None = None,
    ) -> TokensFlowFinalizationRecord:
        """Persist allowlisted poll evidence while retaining finalizer ownership."""

        if type(queue_passed) is not bool:
            raise TypeError("queue_passed must be an exact bool")
        if doctor_rc is not None and (isinstance(doctor_rc, bool) or not isinstance(doctor_rc, int)):
            raise TypeError("doctor_rc must be an integer or None")
        checked_at = _timestamp(now)
        with self._write() as connection:
            row = self._require_finalization_owner(connection, job_id, worker_id, now=now)
            persisted_queue_passed = bool(row["queue_passed"]) or queue_passed
            connection.execute(
                """
                UPDATE tokensflow_finalizations
                SET queue_passed = ?, doctor_rc = ?, checked_at = ?, error_category = ?
                WHERE job_id = ?
                """,
                (int(persisted_queue_passed), doctor_rc, checked_at, error_category, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM tokensflow_finalizations WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            assert updated is not None
            return self._finalization_record(updated)

    def finish_tokensflow_finalization(
        self,
        job_id: str,
        worker_id: str,
        *,
        state: FinalizationState,
        now: datetime,
        reason: str | None = None,
        error_category: str | None = None,
    ) -> TokensFlowFinalizationRecord:
        """Finish cleanup without mutating any evaluation task or batch row."""

        if state not in _TERMINAL_FINALIZATION_STATES:
            raise ValueError("TokensFlow finalization terminal state is invalid")
        finished_at = _timestamp(now)
        with self._write() as connection:
            self._require_finalization_owner(connection, job_id, worker_id, now=now)
            connection.execute(
                """
                UPDATE tokensflow_finalizations
                SET state = ?, finished_at = ?, reason = ?, error_category = ?,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE job_id = ?
                """,
                (state.value, finished_at, reason, error_category, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM tokensflow_finalizations WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            assert updated is not None
            return self._finalization_record(updated)

    @staticmethod
    def _require_finalization_owner(
        connection: sqlite3.Connection,
        job_id: str,
        worker_id: str,
        *,
        now: datetime,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM tokensflow_finalizations WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"TokensFlow finalization not found: {job_id}")
        if (
            row["state"] not in {FinalizationState.RUNNING.value, FinalizationState.CLEANUP_PENDING.value}
            or row["lease_owner"] != worker_id
            or row["lease_expires_at"] is None
            or _parse_timestamp(row["lease_expires_at"]) < now
        ):
            raise TaskOwnershipError("TokensFlow finalization lease is not owned")
        return row

    def cancel_queued(self, task_id: str, *, now: datetime) -> TaskRecord:
        """Cancel a queued task."""
        finished_at = _timestamp(now)
        with self._write() as connection:
            row = self._select_task(connection, task_id)
            if TaskStatus(row["status"]) is not TaskStatus.QUEUED:
                raise TaskConflict("Only queued tasks can be cancelled")
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, finished_at = ?, version = version + 1
                WHERE task_id = ?
                """,
                (TaskStatus.CANCELLED.value, finished_at, task_id),
            )
            attempt = self._select_latest_attempt(connection, task_id)
            connection.execute(
                """
                UPDATE task_attempts
                SET status = ?, finished_at = ?, version = version + 1
                WHERE attempt_id = ? AND status = ?
                """,
                (
                    TaskStatus.CANCELLED.value,
                    finished_at,
                    attempt["attempt_id"],
                    TaskStatus.QUEUED.value,
                ),
            )
            return self._record(connection, self._select_task(connection, task_id))

    def claim_next(
        self,
        worker_id: str,
        *,
        now: datetime,
        max_concurrency: int = 1,
    ) -> TaskRecord | None:
        """Atomically claim the oldest task when capacity is available."""
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        _validate_task_parallelism(max_concurrency)
        _timestamp(now)
        with self._write() as connection:
            return self._claim_next(
                connection,
                worker_id,
                now=now,
                allow_standalone=True,
                max_concurrency=max_concurrency,
            )

    def claim_next_with_usage(
        self,
        worker_id: str,
        *,
        snapshot: UsageSnapshot,
        default_threshold: int,
        max_concurrency: int = 1,
        now: datetime,
    ) -> TaskRecord | None:
        """Persist one snapshot and claim atomically when transient admission is open."""

        if not worker_id:
            raise ValueError("worker_id must not be empty")
        _validate_percentage(default_threshold)
        _validate_task_parallelism(max_concurrency)
        _timestamp(now)
        with self._write() as connection:
            self._save_usage_snapshot(connection, snapshot)
            allow_standalone = snapshot.rate_limit_reached_type is None and snapshot.used_percent < default_threshold
            return self._claim_next(
                connection,
                worker_id,
                now=now,
                allow_standalone=allow_standalone,
                max_concurrency=max_concurrency,
                snapshot=snapshot,
            )

    def apply_usage_snapshot(self, snapshot: UsageSnapshot, *, now: datetime) -> None:
        """Persist an observation without changing user-owned batch control intent."""

        _timestamp(now)
        with self._write() as connection:
            self._save_usage_snapshot(connection, snapshot)

    def _claim_next(
        self,
        connection: sqlite3.Connection,
        worker_id: str,
        *,
        now: datetime,
        allow_standalone: bool,
        max_concurrency: int,
        snapshot: UsageSnapshot | None = None,
    ) -> TaskRecord | None:
        now_text = _timestamp(now)
        owned = connection.execute(
            "SELECT 1 FROM worker_leases WHERE worker_id = ? AND expires_at > ?",
            (worker_id, now_text),
        ).fetchone()
        if owned is not None:
            return None
        active = connection.execute(
            "SELECT COUNT(*) FROM worker_leases WHERE expires_at > ?",
            (now_text,),
        ).fetchone()[0]
        if not isinstance(active, int):
            raise TypeError("SQLite active lease count is not an integer")
        if active >= max_concurrency:
            return None

        row = connection.execute(
            """
            SELECT tasks.*
            FROM tasks
            LEFT JOIN batches ON batches.batch_id = tasks.batch_id
            JOIN task_attempts AS latest_attempt
              ON latest_attempt.task_id = tasks.task_id
             AND latest_attempt.attempt_number = (
                 SELECT MAX(candidate.attempt_number)
                 FROM task_attempts AS candidate
                 WHERE candidate.task_id = tasks.task_id
             )
            WHERE tasks.status = ?
              AND latest_attempt.status = ?
              AND latest_attempt.eligible_at <= ?
              AND (
                  (tasks.batch_id IS NULL AND ?)
                  OR (
                      tasks.batch_id IS NOT NULL
                      AND batches.control_intent = ?
                      AND ?
                      AND ? < batches.usage_pause_percent
                  )
              )
            ORDER BY latest_attempt.attempt_seq ASC
            LIMIT 1
            """,
            (
                TaskStatus.QUEUED.value,
                TaskStatus.QUEUED.value,
                now_text,
                int(allow_standalone),
                BatchControlIntent.RUN.value,
                int(snapshot is None or snapshot.rate_limit_reached_type is None),
                snapshot.used_percent if snapshot is not None else 0,
            ),
        ).fetchone()
        if row is None:
            return None

        task_id = str(row["task_id"])
        attempt = self._select_latest_attempt(connection, task_id)
        effective_claim_time = max(now, _parse_timestamp(attempt["created_at"]))
        claim_time_text = _timestamp(effective_claim_time)
        expires_at = _timestamp(effective_claim_time + self._lease_duration)
        connection.execute(
            """
            INSERT INTO worker_leases(attempt_id, worker_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (attempt["attempt_id"], worker_id, expires_at),
        )
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, started_at = ?, version = version + 1
            WHERE task_id = ? AND status = ?
            """,
            (TaskStatus.RUNNING.value, claim_time_text, task_id, TaskStatus.QUEUED.value),
        )
        connection.execute(
            """
            UPDATE task_attempts
            SET status = ?, started_at = ?, version = version + 1
            WHERE attempt_id = ? AND status = ?
            """,
            (
                TaskStatus.RUNNING.value,
                claim_time_text,
                attempt["attempt_id"],
                TaskStatus.QUEUED.value,
            ),
        )
        return self._record(connection, self._select_task(connection, task_id))

    @staticmethod
    def _save_usage_snapshot(connection: sqlite3.Connection, snapshot: UsageSnapshot) -> None:
        connection.execute(
            """
            INSERT INTO usage_snapshots(snapshot_json, observed_at)
            VALUES (?, ?)
            """,
            (snapshot.model_dump_json(), _timestamp(snapshot.observed_at)),
        )

    def heartbeat(self, task_id: str, worker_id: str, *, now: datetime) -> TaskRecord:
        """Renew the active lease owned by a worker."""
        with self._write() as connection:
            self._require_running_owner(connection, task_id, worker_id, now=now)
            attempt = self._select_latest_attempt(connection, task_id)
            started_at = _parse_optional_timestamp(attempt["started_at"])
            if started_at is None:
                raise TaskConflict("Running task has no start time")
            lease = connection.execute(
                "SELECT expires_at FROM worker_leases WHERE attempt_id = ? AND worker_id = ?",
                (attempt["attempt_id"], worker_id),
            ).fetchone()
            if lease is None:
                raise TaskOwnershipError("Worker lease is not active")
            expires_at = _timestamp(
                max(
                    _parse_timestamp(lease["expires_at"]),
                    max(now, started_at) + self._lease_duration,
                )
            )
            connection.execute(
                "UPDATE worker_leases SET expires_at = ? WHERE attempt_id = ? AND worker_id = ?",
                (expires_at, attempt["attempt_id"], worker_id),
            )
            connection.execute(
                "UPDATE tasks SET version = version + 1 WHERE task_id = ?",
                (task_id,),
            )
            connection.execute(
                "UPDATE task_attempts SET version = version + 1 WHERE attempt_id = ?",
                (attempt["attempt_id"],),
            )
            return self._record(connection, self._select_task(connection, task_id))

    def set_phase(
        self,
        task_id: str,
        worker_id: str,
        phase: TaskPhase,
        *,
        now: datetime,
    ) -> TaskRecord:
        """Set the current phase of a running task."""
        with self._write() as connection:
            self._require_running_owner(connection, task_id, worker_id, now=now)
            attempt = self._select_latest_attempt(connection, task_id)
            connection.execute(
                "UPDATE tasks SET phase = ?, version = version + 1 WHERE task_id = ?",
                (phase.value, task_id),
            )
            connection.execute(
                "UPDATE task_attempts SET phase = ?, version = version + 1 WHERE attempt_id = ?",
                (phase.value, attempt["attempt_id"]),
            )
            return self._record(connection, self._select_task(connection, task_id))

    def succeed(
        self,
        task_id: str,
        worker_id: str,
        result: TaskResult,
        *,
        now: datetime,
    ) -> TaskRecord:
        """Complete an owned running task successfully."""
        finished_at = _timestamp(now)
        with self._write() as connection:
            self._require_running_owner(connection, task_id, worker_id, now=now)
            attempt = self._select_latest_attempt(connection, task_id)
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, finished_at = ?, result_json = ?, version = version + 1
                WHERE task_id = ?
                """,
                (TaskStatus.SUCCEEDED.value, finished_at, result.model_dump_json(), task_id),
            )
            connection.execute(
                """
                UPDATE task_attempts
                SET status = ?, finished_at = ?, result_json = ?, version = version + 1
                WHERE attempt_id = ?
                """,
                (
                    TaskStatus.SUCCEEDED.value,
                    finished_at,
                    result.model_dump_json(),
                    attempt["attempt_id"],
                ),
            )
            connection.execute(
                "DELETE FROM worker_leases WHERE attempt_id = ? AND worker_id = ?",
                (attempt["attempt_id"], worker_id),
            )
            return self._record(connection, self._select_task(connection, task_id))

    def fail(
        self,
        task_id: str,
        worker_id: str,
        failure: SafeFailure,
        *,
        now: datetime,
    ) -> TaskRecord:
        """Complete an owned running task with a safe failure."""
        finished_at = _timestamp(now)
        phase = failure.phase.value if failure.phase is not None else None
        with self._write() as connection:
            self._require_running_owner(connection, task_id, worker_id, now=now)
            attempt = self._select_latest_attempt(connection, task_id)
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, phase = ?, finished_at = ?, failure_category = ?,
                    failure_phase = ?, failure_summary = ?, version = version + 1
                WHERE task_id = ?
                """,
                (
                    TaskStatus.FAILED.value,
                    phase,
                    finished_at,
                    failure.category.value,
                    phase,
                    failure.summary,
                    task_id,
                ),
            )
            connection.execute(
                """
                UPDATE task_attempts
                SET status = ?, phase = ?, finished_at = ?, failure_category = ?,
                    failure_code = ?, retry_disposition = ?, failure_phase = ?,
                    failure_summary = ?, evidence_state = ?, cleanup_state = ?,
                    cleanup_eligible_at = ?, cleanup_error_code = NULL,
                    version = version + 1
                WHERE attempt_id = ?
                """,
                (
                    TaskStatus.FAILED.value,
                    phase,
                    finished_at,
                    failure.category.value,
                    failure.failure_code.value,
                    failure.retry_disposition.value,
                    phase,
                    failure.summary,
                    AttemptEvidenceState.PENDING.value,
                    AttemptCleanupState.PENDING.value,
                    finished_at,
                    attempt["attempt_id"],
                ),
            )
            connection.execute(
                "DELETE FROM worker_leases WHERE attempt_id = ? AND worker_id = ?",
                (attempt["attempt_id"], worker_id),
            )
            return self._record(connection, self._select_task(connection, task_id))

    def recover_expired(self, *, now: datetime) -> list[str]:
        """Interrupt running tasks whose independent leases have expired."""
        _timestamp(now)
        with self._write() as connection:
            return self._recover_expired_leases(connection, now=now)

    def begin_startup_recovery(self, *, now: datetime) -> list[str]:
        """Fence every predecessor attempt after the caller acquires the process lock."""

        _timestamp(now)
        with self._write() as connection:
            rows = connection.execute(
                """
                SELECT attempts.*
                FROM task_attempts AS attempts
                JOIN tasks ON tasks.task_id = attempts.task_id
                WHERE attempts.status = ? AND tasks.status = ?
                ORDER BY attempts.attempt_seq ASC
                """,
                (TaskStatus.RUNNING.value, TaskStatus.RUNNING.value),
            ).fetchall()
            recovered = [self._interrupt_attempt(connection, attempt, now=now) for attempt in rows]
            connection.execute(
                "DELETE FROM worker_leases WHERE attempt_id IN (SELECT attempt_id FROM task_attempts WHERE status != ?)",
                (TaskStatus.RUNNING.value,),
            )
            return recovered

    def list_attempt_cleanup_candidates(self, *, limit: int, now: datetime) -> list[AttemptCleanupCandidate]:
        """Return a bounded FIFO snapshot whose TokensFlow owners are already terminal."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("cleanup candidate limit must be positive")
        now_text = _timestamp(now)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT attempts.*, tasks.batch_id
                FROM task_attempts AS attempts
                JOIN tasks ON tasks.task_id = attempts.task_id
                WHERE attempts.cleanup_state = ?
                  AND attempts.cleanup_eligible_at <= ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM tokensflow_finalizations AS finalizations
                      WHERE finalizations.attempt_id = attempts.attempt_id
                        AND finalizations.state NOT IN (?, ?, ?, ?)
                  )
                ORDER BY attempts.attempt_seq ASC
                LIMIT ?
                """,
                (
                    AttemptCleanupState.PENDING.value,
                    now_text,
                    FinalizationState.PASSED.value,
                    FinalizationState.TIMED_OUT.value,
                    FinalizationState.CAPACITY_EVICTED.value,
                    FinalizationState.CLEANUP_FAILED.value,
                    limit,
                ),
            ).fetchall()
        return [self._cleanup_candidate(row) for row in rows]

    def mark_attempt_evidence_exported(self, attempt_id: str) -> None:
        """Record successful creation of the bounded public incident manifest."""

        with self._write() as connection:
            updated = connection.execute(
                """
                UPDATE task_attempts
                SET evidence_state = ?, version = version + 1
                WHERE attempt_id = ? AND evidence_state = ? AND cleanup_state = ?
                """,
                (
                    AttemptEvidenceState.EXPORTED.value,
                    attempt_id,
                    AttemptEvidenceState.PENDING.value,
                    AttemptCleanupState.PENDING.value,
                ),
            ).rowcount
            if updated == 0:
                row = connection.execute("SELECT * FROM task_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
                if row is None:
                    raise TaskNotFound(f"Attempt not found: {attempt_id}")
                if row["evidence_state"] != AttemptEvidenceState.EXPORTED.value:
                    raise TaskConflict("Attempt evidence is not pending")

    def defer_attempt_cleanup(
        self,
        attempt_id: str,
        *,
        error_code: str,
        retry_seconds: int,
        now: datetime,
    ) -> None:
        """Persist only a fixed cleanup error code and bounded retry time."""

        if fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is None:
            raise ValueError("cleanup error code is invalid")
        if isinstance(retry_seconds, bool) or not isinstance(retry_seconds, int) or retry_seconds < 1:
            raise ValueError("cleanup retry delay must be positive")
        with self._write() as connection:
            updated = connection.execute(
                """
                UPDATE task_attempts
                SET cleanup_eligible_at = ?, cleanup_error_code = ?, version = version + 1
                WHERE attempt_id = ? AND cleanup_state = ?
                """,
                (
                    _timestamp(now + timedelta(seconds=retry_seconds)),
                    error_code,
                    attempt_id,
                    AttemptCleanupState.PENDING.value,
                ),
            ).rowcount
            if updated == 0:
                raise TaskConflict("Attempt cleanup is not pending")

    def complete_attempt_cleanup_and_schedule_retry(self, attempt_id: str, *, now: datetime) -> bool:
        """Mark cleanup complete and atomically create at most one bounded retry."""

        _timestamp(now)
        with self._write() as connection:
            attempt = connection.execute("SELECT * FROM task_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if attempt is None:
                raise TaskNotFound(f"Attempt not found: {attempt_id}")
            if attempt["cleanup_state"] == AttemptCleanupState.COMPLETE.value:
                return False
            if attempt["evidence_state"] != AttemptEvidenceState.EXPORTED.value:
                raise TaskConflict("Attempt evidence must be exported before cleanup completion")
            connection.execute(
                """
                UPDATE task_attempts
                SET cleanup_state = ?, cleanup_eligible_at = NULL, cleanup_error_code = NULL,
                    version = version + 1
                WHERE attempt_id = ? AND cleanup_state = ?
                """,
                (AttemptCleanupState.COMPLETE.value, attempt_id, AttemptCleanupState.PENDING.value),
            )
            task_row = self._select_task(connection, str(attempt["task_id"]))
            if attempt["retry_disposition"] != RetryDisposition.RETRY.value or not self._accepts_another_attempt(
                connection, task_row
            ):
                return False
            batch_id = task_row["batch_id"]
            if batch_id is None:
                return False
            attempt_number = int(attempt["attempt_number"]) + 1
            self._enqueue_next_attempt(
                connection,
                task_id=str(attempt["task_id"]),
                batch_id=str(batch_id),
                attempt_number=attempt_number,
                idempotency_key=f"{attempt['task_id']}.attempt-{attempt_number:04d}",
                actor="system",
                details={"reason": str(attempt["failure_code"] or FailureCode.INTERNAL.value)},
                now=now,
                eligible_at=now
                + _retry_backoff(
                    int(attempt["attempt_number"]),
                    failure_code=str(attempt["failure_code"]) if attempt["failure_code"] is not None else None,
                ),
            )
            return True

    def _recover_expired_leases(self, connection: sqlite3.Connection, *, now: datetime) -> list[str]:
        now_text = _timestamp(now)
        leases = connection.execute(
            "SELECT * FROM worker_leases WHERE expires_at <= ? ORDER BY expires_at, attempt_id",
            (now_text,),
        ).fetchall()
        recovered: list[str] = []
        for lease in leases:
            attempt = connection.execute(
                "SELECT * FROM task_attempts WHERE attempt_id = ?",
                (lease["attempt_id"],),
            ).fetchone()
            if attempt is None:
                raise TaskStoreError("Worker lease references a missing attempt")
            task_id = str(attempt["task_id"])
            row = self._select_task(connection, task_id)
            if TaskStatus(row["status"]) is TaskStatus.RUNNING:
                recovered.append(self._interrupt_attempt(connection, attempt, now=now))
            connection.execute("DELETE FROM worker_leases WHERE attempt_id = ?", (lease["attempt_id"],))
        return recovered

    def _interrupt_attempt(self, connection: sqlite3.Connection, attempt: sqlite3.Row, *, now: datetime) -> str:
        task_id = str(attempt["task_id"])
        now_text = _timestamp(now)
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, finished_at = ?, failure_category = ?,
                failure_phase = phase, failure_summary = ?, version = version + 1
            WHERE task_id = ? AND status = ?
            """,
            (
                TaskStatus.INTERRUPTED.value,
                now_text,
                FailureCategory.WORKER_INTERRUPTION.value,
                "Evaluation worker lease expired",
                task_id,
                TaskStatus.RUNNING.value,
            ),
        )
        connection.execute(
            """
            UPDATE task_attempts
            SET status = ?, finished_at = ?, failure_category = ?, failure_code = ?,
                retry_disposition = ?, failure_phase = phase, failure_summary = ?,
                evidence_state = ?, cleanup_state = ?, cleanup_eligible_at = ?,
                cleanup_error_code = NULL, version = version + 1
            WHERE attempt_id = ? AND status = ?
            """,
            (
                TaskStatus.INTERRUPTED.value,
                now_text,
                FailureCategory.WORKER_INTERRUPTION.value,
                FailureCode.WORKER_INTERRUPTION.value,
                RetryDisposition.RETRY.value,
                "Evaluation worker lease expired",
                AttemptEvidenceState.PENDING.value,
                AttemptCleanupState.PENDING.value,
                now_text,
                attempt["attempt_id"],
                TaskStatus.RUNNING.value,
            ),
        )
        connection.execute("DELETE FROM worker_leases WHERE attempt_id = ?", (attempt["attempt_id"],))
        return task_id

    def _finalize_batch_intent(
        self,
        connection: sqlite3.Connection,
        batch_id: str,
        *,
        now: datetime,
    ) -> None:
        running = connection.execute(
            "SELECT 1 FROM tasks WHERE batch_id = ? AND status = ? LIMIT 1",
            (batch_id, TaskStatus.RUNNING.value),
        ).fetchone()
        if running is not None:
            return

        row = self._select_batch(connection, batch_id)
        intent = BatchControlIntent(row["control_intent"])
        if intent is BatchControlIntent.CANCEL:
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, finished_at = ?, version = version + 1
                WHERE batch_id = ? AND status = ?
                """,
                (
                    TaskStatus.CANCELLED.value,
                    _timestamp(now),
                    batch_id,
                    TaskStatus.QUEUED.value,
                ),
            )
            connection.execute(
                """
                UPDATE task_attempts
                SET status = ?, finished_at = ?, version = version + 1
                WHERE status = ?
                  AND task_id IN (
                      SELECT task_id FROM tasks WHERE batch_id = ?
                  )
                  AND attempt_number = (
                      SELECT MAX(newest.attempt_number)
                      FROM task_attempts AS newest
                      WHERE newest.task_id = task_attempts.task_id
                  )
                """,
                (
                    TaskStatus.CANCELLED.value,
                    _timestamp(now),
                    TaskStatus.QUEUED.value,
                    batch_id,
                ),
            )
            self._append_control_event_once(
                connection,
                batch_id,
                BatchControlEventType.CANCELLED,
                "system",
                {},
                now,
            )
        elif intent is BatchControlIntent.PAUSE:
            self._append_control_event_once(
                connection,
                batch_id,
                BatchControlEventType.PAUSED,
                "system",
                {},
                now,
            )
        elif self._all_batch_tasks_terminal(connection, batch_id):
            self._append_control_event_once(
                connection,
                batch_id,
                BatchControlEventType.BATCH_COMPLETED,
                "system",
                {},
                now,
            )

    def _all_batch_tasks_terminal(self, connection: sqlite3.Connection, batch_id: str) -> bool:
        nonterminal = connection.execute(
            """
            SELECT 1
            FROM tasks
            JOIN task_attempts AS attempts
              ON attempts.task_id = tasks.task_id
             AND attempts.attempt_number = (
                 SELECT MAX(newest.attempt_number)
                 FROM task_attempts AS newest
                 WHERE newest.task_id = tasks.task_id
             )
            WHERE tasks.batch_id = ?
              AND (
                  tasks.status NOT IN (?, ?, ?, ?)
                  OR (
                      attempts.retry_disposition = ?
                      AND attempts.cleanup_state = ?
                      AND attempts.attempt_number < ?
                  )
              )
            LIMIT 1
            """,
            (
                batch_id,
                TaskStatus.SUCCEEDED.value,
                TaskStatus.FAILED.value,
                TaskStatus.INTERRUPTED.value,
                TaskStatus.CANCELLED.value,
                RetryDisposition.RETRY.value,
                AttemptCleanupState.PENDING.value,
                self._max_attempts,
            ),
        ).fetchone()
        return nonterminal is None

    @staticmethod
    def _append_control_event(
        connection: sqlite3.Connection,
        batch_id: str,
        event_type: BatchControlEventType,
        actor: Literal["user", "system"],
        details: dict[str, int | str | None],
        now: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO batch_control_events(
                batch_id, event_type, actor, details_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                event_type.value,
                actor,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
                _timestamp(now),
            ),
        )

    @classmethod
    def _append_control_event_once(
        cls,
        connection: sqlite3.Connection,
        batch_id: str,
        event_type: BatchControlEventType,
        actor: Literal["user", "system"],
        details: dict[str, int | str | None],
        now: datetime,
    ) -> None:
        existing = connection.execute(
            """
            SELECT event_type
            FROM batch_control_events
            WHERE batch_id = ?
            ORDER BY event_seq DESC
            LIMIT 1
            """,
            (batch_id,),
        ).fetchone()
        if existing is None or existing["event_type"] != event_type.value:
            cls._append_control_event(connection, batch_id, event_type, actor, details, now)

    def _require_running_owner(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        worker_id: str,
        *,
        now: datetime,
    ) -> None:
        attempt = self._select_latest_attempt(connection, task_id)
        if TaskStatus(attempt["status"]) is not TaskStatus.RUNNING:
            raise TaskConflict("Task is not running")
        lease = connection.execute(
            "SELECT * FROM worker_leases WHERE attempt_id = ?",
            (attempt["attempt_id"],),
        ).fetchone()
        if lease is None or lease["worker_id"] != worker_id or lease["expires_at"] <= _timestamp(now):
            raise TaskOwnershipError("Worker does not own an active lease for this task")

    @staticmethod
    def _select_task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound(f"Task not found: {task_id}")
        return row

    @staticmethod
    def _select_latest_attempt(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM task_attempts
            WHERE task_id = ?
            ORDER BY attempt_number DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(f"Task has no execution attempt: {task_id}")
        return row

    @staticmethod
    def _insert_initial_attempt(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        idempotency_key: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO task_attempts(
                attempt_id, task_id, attempt_number, idempotency_key,
                status, created_at, eligible_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{task_id}.attempt-0001",
                task_id,
                1,
                idempotency_key,
                TaskStatus.QUEUED.value,
                created_at,
                created_at,
            ),
        )

    @staticmethod
    def _select_batch(connection: sqlite3.Connection, batch_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            raise BatchNotFound(f"Batch not found: {batch_id}")
        return row

    @staticmethod
    def _control_event(row: sqlite3.Row) -> BatchControlEvent:
        details = json.loads(row["details_json"])
        if not isinstance(details, dict):
            raise TypeError("Stored control event details are not an object")
        return BatchControlEvent.model_validate(
            {
                "sequence": row["event_seq"],
                "batch_id": row["batch_id"],
                "event_type": BatchControlEventType(row["event_type"]),
                "actor": row["actor"],
                "details": details,
                "occurred_at": _parse_timestamp(row["occurred_at"]),
            },
            strict=True,
        )

    def _batch_record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> BatchRecord:
        batch_id = row["batch_id"]
        if not isinstance(batch_id, str):
            raise TypeError("Stored batch ID is not text")
        child_rows = connection.execute(
            """
            SELECT attempts.status, attempts.started_at, attempts.finished_at,
                   attempts.retry_disposition, attempts.cleanup_state, attempts.attempt_number
            FROM tasks
            JOIN task_attempts AS attempts
              ON attempts.task_id = tasks.task_id
             AND attempts.attempt_number = (
                 SELECT MAX(newest.attempt_number)
                 FROM task_attempts AS newest
                 WHERE newest.task_id = tasks.task_id
             )
            WHERE tasks.batch_id = ?
            ORDER BY tasks.source_index ASC
            """,
            (batch_id,),
        ).fetchall()
        intent = BatchControlIntent(row["control_intent"])
        statuses = tuple(
            TaskStatus.QUEUED
            if (
                intent is not BatchControlIntent.CANCEL
                and child["retry_disposition"] == RetryDisposition.RETRY.value
                and child["cleanup_state"] == AttemptCleanupState.PENDING.value
                and int(child["attempt_number"]) < self._max_attempts
            )
            else TaskStatus(child["status"])
            for child in child_rows
        )
        status = derive_controlled_batch_status(intent=intent, task_statuses=statuses)
        starts = [_parse_timestamp(child["started_at"]) for child in child_rows if child["started_at"] is not None]
        finishes = [_parse_timestamp(child["finished_at"]) for child in child_rows if child["finished_at"] is not None]
        terminal = status in {BatchStatus.COMPLETED, BatchStatus.CANCELLED}
        pause_reason = row["pause_reason"]
        control = BatchControlState(
            intent=intent,
            usage_pause_percent=_stored_int(row["usage_pause_percent"], name="usage threshold"),
            pause_reason=BatchPauseReason(pause_reason) if pause_reason is not None else None,
            updated_at=_parse_timestamp(row["control_updated_at"]),
            version=_stored_int(row["control_version"], name="control version"),
        )
        return BatchRecord.model_validate(
            {
                "batch_id": batch_id,
                "request": BatchCreate.model_validate_json(row["request_json"], strict=True),
                "total_tasks": row["total_tasks"],
                "status": status,
                "control": control,
                "created_at": _parse_timestamp(row["created_at"]),
                "started_at": min(starts) if starts else None,
                "finished_at": max(finishes) if terminal and finishes else None,
                "resolved_powercontext_sha": row["resolved_powercontext_sha"],
            },
            strict=True,
        )

    def _record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> TaskRecord:
        task_id = row["task_id"]
        if not isinstance(task_id, str):
            raise TypeError("Stored task ID is not text")
        EvaluationPaths(Path("."), task_id)
        request = TaskCreate.model_validate_json(row["request_json"], strict=True)
        attempt = self._select_latest_attempt(connection, task_id)
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM task_attempts WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]
        if not isinstance(attempt_count, int) or attempt_count < 1:
            raise TypeError("Stored attempt count is invalid")
        result = None
        if attempt["result_json"] is not None:
            result = TaskResult.model_validate_json(attempt["result_json"], strict=True)
        failure = None
        retry_disposition = None
        if (
            attempt["failure_category"] is not None
            or attempt["failure_phase"] is not None
            or attempt["failure_summary"] is not None
        ):
            category = FailureCategory(attempt["failure_category"]) if attempt["failure_category"] is not None else None
            retry_disposition = (
                RetryDisposition(attempt["retry_disposition"])
                if attempt["retry_disposition"] is not None
                else RetryDisposition.RETRY
            )
            failure = SafeFailure.model_validate(
                {
                    "category": category,
                    "failure_code": (
                        FailureCode(attempt["failure_code"])
                        if attempt["failure_code"] is not None
                        else _legacy_failure_code(category)
                    ),
                    "phase": (TaskPhase(attempt["failure_phase"]) if attempt["failure_phase"] is not None else None),
                    "summary": attempt["failure_summary"],
                    "retry_disposition": retry_disposition,
                },
                strict=True,
            )
        status = TaskStatus(attempt["status"])
        failure_category = failure.category if failure is not None else None
        return TaskRecord.model_validate(
            {
                "task_id": task_id,
                "attempt_id": attempt["attempt_id"],
                "attempt_number": attempt["attempt_number"],
                "attempt_count": attempt_count,
                "retryable": (
                    status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}
                    and retry_disposition is RetryDisposition.RETRY
                    and int(attempt["attempt_number"]) < self._max_attempts
                ),
                "request": request,
                "status": status,
                "batch_id": row["batch_id"],
                "instance_id": row["instance_id"] or request.instance_id,
                "source_index": row["source_index"],
                "phase": TaskPhase(attempt["phase"]) if attempt["phase"] is not None else None,
                "created_at": _parse_timestamp(attempt["created_at"]),
                "eligible_at": _parse_timestamp(attempt["eligible_at"]),
                "started_at": _parse_optional_timestamp(attempt["started_at"]),
                "finished_at": _parse_optional_timestamp(attempt["finished_at"]),
                "version": attempt["version"],
                "failure_category": failure_category,
                "failure_code": failure.failure_code if failure is not None else None,
                "retry_disposition": failure.retry_disposition if failure is not None else None,
                "failure_phase": failure.phase if failure is not None else None,
                "failure_summary": failure.summary if failure is not None else None,
                "result": result,
            },
            strict=True,
        )

    def _attempt_record(self, row: sqlite3.Row) -> TaskAttemptRecord:
        status = TaskStatus(row["status"])
        category = FailureCategory(row["failure_category"]) if row["failure_category"] is not None else None
        result = (
            TaskResult.model_validate_json(row["result_json"], strict=True) if row["result_json"] is not None else None
        )
        retry_disposition = (
            RetryDisposition(row["retry_disposition"])
            if row["retry_disposition"] is not None
            else RetryDisposition.RETRY
        )
        return TaskAttemptRecord.model_validate(
            {
                "attempt_id": row["attempt_id"],
                "task_id": row["task_id"],
                "attempt_number": row["attempt_number"],
                "status": status,
                "phase": TaskPhase(row["phase"]) if row["phase"] is not None else None,
                "created_at": _parse_timestamp(row["created_at"]),
                "eligible_at": _parse_timestamp(row["eligible_at"]),
                "started_at": _parse_optional_timestamp(row["started_at"]),
                "finished_at": _parse_optional_timestamp(row["finished_at"]),
                "version": row["version"],
                "failure_category": category,
                "failure_code": (
                    FailureCode(row["failure_code"])
                    if row["failure_code"] is not None
                    else (_legacy_failure_code(category) if category is not None else None)
                ),
                "retry_disposition": retry_disposition if category is not None else None,
                "failure_phase": (TaskPhase(row["failure_phase"]) if row["failure_phase"] is not None else None),
                "failure_summary": row["failure_summary"],
                "result": result,
                "retryable": (
                    status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}
                    and retry_disposition is RetryDisposition.RETRY
                    and int(row["attempt_number"]) < self._max_attempts
                ),
            },
            strict=True,
        )

    @staticmethod
    def _cleanup_candidate(row: sqlite3.Row) -> AttemptCleanupCandidate:
        attempt_number = _stored_int(row["attempt_number"], name="attempt number")
        task_id = str(row["task_id"])
        run_id = task_id if attempt_number == 1 else f"{task_id}-attempt-{attempt_number:04d}"
        EvaluationPaths(Path("."), run_id)
        summary = row["failure_summary"]
        if not isinstance(summary, str):
            raise TypeError("Cleanup candidate has no safe failure summary")
        return AttemptCleanupCandidate(
            attempt_id=str(row["attempt_id"]),
            task_id=task_id,
            batch_id=str(row["batch_id"]) if row["batch_id"] is not None else None,
            attempt_number=attempt_number,
            run_id=run_id,
            failure_code=(
                FailureCode(row["failure_code"])
                if row["failure_code"] is not None
                else _legacy_failure_code(
                    FailureCategory(row["failure_category"]) if row["failure_category"] is not None else None
                )
            ),
            failure_phase=TaskPhase(row["failure_phase"]) if row["failure_phase"] is not None else None,
            failure_summary=summary,
        )

    @staticmethod
    def _finalization_record(row: sqlite3.Row) -> TokensFlowFinalizationRecord:
        return TokensFlowFinalizationRecord(
            job_id=str(row["job_id"]),
            attempt_id=str(row["attempt_id"]),
            task_id=str(row["task_id"]),
            batch_id=str(row["batch_id"]) if row["batch_id"] is not None else None,
            arm=str(row["arm"]),
            run_id=str(row["run_id"]),
            container_name=str(row["container_name"]),
            runtime_path=str(row["runtime_path"]),
            wrapper_path=str(row["wrapper_path"]),
            egress_network=str(row["egress_network"]),
            daemon_pid_file=str(row["daemon_pid_file"]),
            evidence_sha256=str(row["evidence_sha256"]),
            evidence_bytes=_stored_int(row["evidence_bytes"], name="TokensFlow evidence byte count"),
            registered_at=_parse_timestamp(row["registered_at"]),
            deadline_at=_parse_timestamp(row["deadline_at"]),
            state=FinalizationState(row["state"]),
            attempts=_stored_int(row["attempts"], name="TokensFlow finalization attempt count"),
            queue_passed=bool(_stored_int(row["queue_passed"], name="TokensFlow queue evidence")),
            doctor_rc=(
                _stored_int(row["doctor_rc"], name="TokensFlow doctor return code")
                if row["doctor_rc"] is not None
                else None
            ),
            checked_at=_parse_optional_timestamp(row["checked_at"]),
            finished_at=_parse_optional_timestamp(row["finished_at"]),
            error_category=str(row["error_category"]) if row["error_category"] is not None else None,
            reason=str(row["reason"]) if row["reason"] is not None else None,
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
            lease_expires_at=_parse_optional_timestamp(row["lease_expires_at"]),
        )

    @staticmethod
    def _summary(record: TaskRecord) -> TaskSummary:
        result = record.result
        return TaskSummary(
            task_id=record.task_id,
            attempt_id=record.attempt_id,
            attempt_number=record.attempt_number,
            attempt_count=record.attempt_count,
            retryable=record.retryable,
            powercontext_ref=record.request.powercontext_ref,
            instance_id=record.request.instance_id,
            model=record.request.model,
            status=record.status,
            phase=record.phase,
            created_at=record.created_at,
            eligible_at=record.eligible_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            version=record.version,
            off_resolved=result.off_resolved if result is not None else None,
            on_resolved=result.on_resolved if result is not None else None,
        )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Timestamps must use UTC")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _retry_backoff(failed_attempt_number: int, *, failure_code: str | None) -> timedelta:
    """Return the bounded persisted delay before the next automatic attempt."""

    if failed_attempt_number < 1:
        raise ValueError("failed attempt number must be positive")
    schedule = (
        _GOLD_VALIDATION_RETRY_BACKOFF_SECONDS
        if failure_code == FailureCode.GOLD_VALIDATION.value
        else _RETRY_BACKOFF_SECONDS
    )
    index = min(failed_attempt_number - 1, len(schedule) - 1)
    return timedelta(seconds=schedule[index])


def _legacy_failure_code(category: FailureCategory | None) -> FailureCode:
    """Map pre-migration failure rows to a conservative stable cause."""

    return {
        FailureCategory.SOURCE_RESOLUTION: FailureCode.SOURCE_RESOLUTION,
        FailureCategory.ENVIRONMENT_PREPARATION: FailureCode.INTERNAL,
        FailureCategory.GOLD_VALIDATION: FailureCode.GOLD_VALIDATION,
        FailureCategory.CODEX_EXECUTION: FailureCode.CODEX_EXECUTION,
        FailureCategory.CODEX_CAPACITY: FailureCode.CODEX_CAPACITY,
        FailureCategory.TREATMENT_VALIDATION: FailureCode.INVALID_TREATMENT_CONTRACT,
        FailureCategory.OFFICIAL_EVALUATOR: FailureCode.OFFICIAL_EVALUATOR,
        FailureCategory.REPORT_GENERATION: FailureCode.REPORT_GENERATION,
        FailureCategory.WORKER_INTERRUPTION: FailureCode.WORKER_INTERRUPTION,
        FailureCategory.INTERNAL: FailureCode.INTERNAL,
        FailureCategory.INVALID_REQUEST: FailureCode.INTERNAL,
        FailureCategory.QUEUE_UNAVAILABLE: FailureCode.INTERNAL,
        None: FailureCode.INTERNAL,
    }[category]


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Stored timestamp is not text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Stored timestamp is not UTC")
    return parsed


def _parse_optional_timestamp(value: Any) -> datetime | None:
    return None if value is None else _parse_timestamp(value)


def _stored_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Stored {name} is not an integer")
    return value


def _validate_percentage(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("percent must be an integer between 1 and 100")


def _validate_task_parallelism(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TASK_PARALLELISM:
        raise ValueError(f"task parallelism must be an integer between 1 and {MAX_TASK_PARALLELISM}")


def _migrate_worker_runtime_parallelism_constraint(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'worker_runtime'"
    ).fetchone()
    if row is None:
        return
    table_sql = str(row["sql"]).upper()
    if "BETWEEN 1 AND 20" in table_sql:
        return
    if not any(constraint in table_sql for constraint in ("BETWEEN 1 AND 4", "BETWEEN 1 AND 10")):
        return
    connection.execute("ALTER TABLE worker_runtime RENAME TO worker_runtime_v1")
    connection.execute(
        """
        CREATE TABLE worker_runtime (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            task_parallelism INTEGER NOT NULL CHECK (task_parallelism BETWEEN 1 AND 20),
            observed_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO worker_runtime(singleton, task_parallelism, observed_at)
        SELECT singleton, task_parallelism, observed_at FROM worker_runtime_v1
        """
    )
    connection.execute("DROP TABLE worker_runtime_v1")


def _backfill_legacy_batch_requests(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT batch_seq, request_json FROM batches").fetchall():
        value = json.loads(row["request_json"])
        if not isinstance(value, dict) or (
            "model" in value and "reasoning_effort" in value and "initial_control_intent" in value
        ):
            continue
        value.setdefault("model", DEFAULT_CODEX_MODEL)
        value.setdefault("reasoning_effort", DEFAULT_REASONING_EFFORT)
        value.setdefault("initial_control_intent", BatchControlIntent.RUN.value)
        canonical = BatchCreate.model_validate(value).model_dump_json()
        connection.execute(
            "UPDATE batches SET request_json = ? WHERE batch_seq = ?",
            (canonical, row["batch_seq"]),
        )


def _backfill_legacy_task_requests(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT queue_seq, request_json FROM tasks").fetchall():
        value = json.loads(row["request_json"])
        if not isinstance(value, dict) or ("model" in value and "reasoning_effort" in value):
            continue
        value.setdefault("model", DEFAULT_CODEX_MODEL)
        value.setdefault("reasoning_effort", DEFAULT_REASONING_EFFORT)
        canonical = TaskCreate.model_validate(value).model_dump_json()
        connection.execute(
            "UPDATE tasks SET request_json = ? WHERE queue_seq = ?",
            (canonical, row["queue_seq"]),
        )


def _execute_transactional_script(connection: sqlite3.Connection, script: str) -> None:
    """Execute complete SQL statements without SQLite's implicit script commit."""

    pending = ""
    for line in script.splitlines():
        pending += f"{line}\n"
        if sqlite3.complete_statement(pending):
            connection.execute(pending)
            pending = ""
    if pending.strip():
        raise TaskStoreError("Schema script ended with an incomplete SQL statement")


def _task_id(now: datetime, sequence: int) -> str:
    _timestamp(now)
    task_id = f"run-{now.astimezone(UTC):%Y%m%d-%H%M%S-%f}-{sequence:010d}-{uuid.uuid4().hex[:8]}"
    EvaluationPaths(Path("."), task_id)
    return task_id


def _batch_id(now: datetime, sequence: int) -> str:
    _timestamp(now)
    batch_id = f"batch-{now.astimezone(UTC):%Y%m%d-%H%M%S-%f}-{sequence:010d}-{uuid.uuid4().hex[:8]}"
    EvaluationPaths(Path("."), batch_id)
    return batch_id


def _batch_task_id(now: datetime, batch_sequence: int, source_index: int) -> str:
    _timestamp(now)
    task_id = f"run-{now.astimezone(UTC):%Y%m%d-%H%M%S-%f}-b{batch_sequence:010d}-t{source_index:04d}"
    EvaluationPaths(Path("."), task_id)
    return task_id
