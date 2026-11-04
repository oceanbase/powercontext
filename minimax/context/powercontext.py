"""PowerContext boundary: save task state, restore it after a restart, re-import it.

The competition requires that the task state survive a process restart and be used by
the *next* run.  This module owns three operations:

``save()``     capture everything the run needs (spec state, test results, changed
               files, pending items, last observed output) and store it with a
               revision number, keeping every previous revision.
``load()``     read the latest revision of a task from the database only.
``reimport()`` push a saved state back into the current process, which must refresh
               the specs, drop stale artifacts and show the restored context.

The module never keeps state in files or environment variables: if the database is
empty, the operation fails and says so.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from context.db import connect, dump_json

STATE_KEYS = (
    "task_id",
    "task_title",
    "spec_ids",
    "checks",
    "results",
    "changed_files",
    "completed",
    "pending",
    "last_output",
)


@dataclass
class TaskState:
    task_id: str
    revision: int
    payload: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None

    @property
    def spec_ids(self) -> list[str]:
        return list(self.payload.get("spec_ids", []))

    @property
    def results(self) -> list[dict[str, Any]]:
        return list(self.payload.get("results", []))

    def summary(self) -> str:
        results = self.payload.get("results", [])
        passed = sum(1 for item in results if item.get("status") == "pass")
        return (
            f"{self.task_id}@{self.revision}: {len(self.spec_ids)} specs, "
            f"{passed}/{len(results)} checks passed, "
            f"{len(self.payload.get('pending', []))} pending"
        )


def _now() -> datetime:
    return datetime.now()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save(task_id: str, payload: dict[str, Any], reason: str = "auto") -> TaskState:
    """Store the state as the next revision; previous revisions stay readable."""
    missing = [key for key in STATE_KEYS if key not in payload]
    if missing:
        raise ValueError(f"task state is missing required keys: {missing}")
    serialized = dump_json({key: payload[key] for key in STATE_KEYS})
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO task(task_id, title, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE title = VALUES(title), updated_at = VALUES(updated_at)
                """,
                (task_id, payload.get("task_title", ""), _now(), _now()),
            )
            cur.execute(
                "SELECT revision, payload FROM task_state WHERE task_id = %s FOR UPDATE",
                (task_id,),
            )
            row = cur.fetchone()
            revision = 1
            if row is not None:
                previous = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                if dump_json(previous) == serialized:
                    return TaskState(task_id, int(row[0]), previous)
                revision = int(row[0]) + 1
            cur.execute(
                """
                INSERT INTO task_state(task_id, revision, payload, updated_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE revision = VALUES(revision),
                    payload = VALUES(payload), updated_at = VALUES(updated_at)
                """,
                (task_id, revision, serialized, _now()),
            )
            cur.execute(
                """
                INSERT INTO task_state_history(task_id, revision, payload, reason, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (task_id, revision, serialized, reason, _now()),
            )
    finally:
        conn.close()
    return TaskState(task_id, revision, json.loads(serialized), _now())


def load(task_id: str) -> TaskState:
    """Read the latest revision from the database; never fall back to local files."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT revision, payload, updated_at FROM task_state WHERE task_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise LookupError(f"no saved state for task {task_id!r} in the database")
    payload = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    return TaskState(task_id, int(row[0]), payload, row[2])


def history(task_id: str) -> list[tuple[int, str]]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT revision, reason FROM task_state_history
                WHERE task_id = %s ORDER BY revision
                """,
                (task_id,),
            )
            return [(int(revision), reason) for revision, reason in cur.fetchall()]
    finally:
        conn.close()


def put_artifact(task_id: str, kind: str, content: str) -> str:
    artifact_id = f"{task_id}/{kind}/{_sha(content)[:12]}"
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO artifact(artifact_id, task_id, kind, content, content_sha, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE content = VALUES(content)
                """,
                (artifact_id, task_id, kind, content, _sha(content), _now()),
            )
    finally:
        conn.close()
    return artifact_id


def get_artifact(artifact_id: str) -> str | None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM artifact WHERE artifact_id = %s", (artifact_id,))
            row = cur.fetchone()
            return None if row is None else row[0]
    finally:
        conn.close()


def clear_artifacts(task_id: str, kind: str | None = None) -> int:
    """Delete stored artifacts; used when a proposal must not be reused by accident."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            if kind is None:
                cur.execute("DELETE FROM artifact WHERE task_id = %s", (task_id,))
            else:
                cur.execute("DELETE FROM artifact WHERE task_id = %s AND kind = %s",
                            (task_id, kind))
            return cur.rowcount
    finally:
        conn.close()


def import_state(task_id: str, refresh) -> tuple[TaskState, dict[str, Any]]:
    """Re-import a saved state into the running process.

    Import is a *read* of the database: it loads the newest revision, re-reads every
    spec it references and every check result, and reports which of them changed since
    the snapshot was taken.  Stored artifacts are kept -- they are the evidence (the
    MiniMax proposal and the code diff) that the resumed run continues from.
    """
    state = load(task_id)
    refreshed = refresh(state.spec_ids)
    stored = state.payload.get("spec_snapshot", {})
    drifted = {
        spec_id: (stored.get(spec_id), refreshed["spec_snapshot"].get(spec_id))
        for spec_id in state.spec_ids
        if stored.get(spec_id) != refreshed["spec_snapshot"].get(spec_id)
    }
    strict = state.payload.get("global_spec_snapshot") or {}
    global_drift = sorted(
        spec_id for spec_id, digest in refreshed["spec_snapshot"].items()
        if spec_id in strict and strict[spec_id] != digest
    )
    summary = {
        "task_id": task_id,
        "revision": state.revision,
        "specs_refreshed": sorted(refreshed["spec_snapshot"]),
        "spec_content_changed_since_save": sorted(drifted),
        "other_specs_changed_since_save": global_drift,
        "checks": len(refreshed["checks"]),
        "results": len(refreshed["results"]),
    }
    return state, summary


def log(task_id: str, role: str, action: str, content: str) -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_log(task_id, role, action, content, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (task_id, role, action, content, _now()),
            )
    finally:
        conn.close()


def read_log(task_id: str, limit: int = 200) -> list[dict[str, Any]]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, action, content, created_at FROM session_log
                WHERE task_id = %s ORDER BY log_id LIMIT %s
                """,
                (task_id, limit),
            )
            return [
                {"role": role, "action": action, "content": content, "at": str(at)}
                for role, action, content, at in cur.fetchall()
            ]
    finally:
        conn.close()
