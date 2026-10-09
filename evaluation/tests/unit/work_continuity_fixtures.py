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

"""Shared builders for work-continuity tests.

These are local two-task locks, not the shipped fixture: they are small enough to
reason about by hand, so a test that changes one field reads as a change to that
one field rather than to a large checked-in dataset.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from powercontext_eval.benchmarks.work_continuity.arms import (
    ContinuationArm,
    ContinuationArmError,
    get_continuation_arm,
)
from powercontext_eval.benchmarks.work_continuity.assembly import assemble_context, build_items
from powercontext_eval.benchmarks.work_continuity.catalog import (
    TASK_LOCK_SCHEMA,
    ContinuationTask,
    TaskCatalog,
    WorkContinuityCatalogError,
)

ATTEMPTS_SCHEMA = "powercontext.work-continuity-attempts.v1"
DEFAULT_MAX_BYTES = 16_000

# Scoring is a pure function of a recording and the context it is scored against.
# The binding between the two is enforced when a run binds recordings to the
# contexts it assembled, so a placeholder digest here keeps these tests about the
# rubric instead of about the run-level gate.
UNBOUND_CONTEXT_SHA256 = "0" * 64


def attempts_protocol(lock: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, object]:
    """Return the protocol block a recording made from ``lock`` must declare."""

    resolved = lock.resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return {
        "task_set_id": payload["task_set_id"],
        "task_lock_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "assembly_max_bytes": max_bytes,
    }


def context_digest(
    lock: Path,
    task_id: str,
    arm_id: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    """Return the digest of the context a recording for this task and arm received."""

    catalog = TaskCatalog.load(lock)
    return assemble_context(catalog.require(task_id), get_continuation_arm(arm_id), max_bytes=max_bytes).content_sha256


def budget_through(task: ContinuationTask, arm: ContinuationArm, item_id: str) -> int:
    """Return the exact byte ceiling that keeps items up to and including ``item_id``.

    Computed from the items rather than hard-coded, so a change to a label or a
    fact's wording does not silently turn a truncation test into a no-op.
    """

    items = build_items(task, arm)
    index = next(position for position, item in enumerate(items) if item.item_id == item_id)
    return len("\n\n".join(item.text for item in items[: index + 1]).encode("utf-8"))


def coding_task() -> dict[str, object]:
    """A coding task whose second turn was superseded by its fourth."""

    return {
        "task_id": "t-coding",
        "kind": "coding",
        "objective": "Fix the retry base.",
        "transcript": [
            {"turn": 1, "role": "user", "text": "Fix the retry base."},
            {"turn": 2, "role": "assistant", "text": "First pass used a 30 second base."},
            {"turn": 3, "role": "user", "text": "Too slow for a 5 second drain."},
            {"turn": 4, "role": "assistant", "text": "The retry base is 1 second with full jitter."},
            {"turn": 5, "role": "tool", "text": "read helper.py -> run_with_backoff"},
        ],
        "required_state_facts": [
            {"fact_id": "f1", "text": "the retry base is 1 second", "evidence": "turn:4"},
            {"fact_id": "f2", "text": "the helper is run_with_backoff", "evidence": "turn:5"},
        ],
        "expected_next_action": {
            "action_id": "a1",
            "text": "call run_with_backoff with a 1 second base",
            "required_fact_ids": ["f1", "f2"],
        },
        "obsolete_facts": [
            {"fact_id": "o1", "text": "use a 30 second base", "evidence": "turn:2", "superseded_by": "f1"}
        ],
        "known_omissions": ["The maximum attempt count was never agreed."],
        "unavailable_evidence": [],
    }


def documentation_task(*, evidence: object = None, pointer: str = "turn:9") -> dict[str, object]:
    """A documentation task carrying one fact whose evidence cannot be produced."""

    return {
        "task_id": "t-doc",
        "kind": "documentation",
        "objective": "Document the conflict behaviour.",
        "transcript": [
            {"turn": 1, "role": "user", "text": "Document the conflict behaviour."},
            {"turn": 2, "role": "assistant", "text": "Commit compares the expected head with the stored head."},
            {"turn": 3, "role": "tool", "text": "read docs/guide.md -> 3 sections"},
            {"turn": 4, "role": "assistant", "text": "Add a section to docs/guide.md."},
        ],
        "required_state_facts": [
            {"fact_id": "g1", "text": "commit compares the expected head", "evidence": "turn:2"},
            {"fact_id": "g2", "text": "the change belongs in docs/guide.md", "evidence": "turn:4"},
            {"fact_id": "g3", "text": "the docs build passes", "evidence": evidence},
        ],
        "expected_next_action": {
            "action_id": "b1",
            "text": "add a conflict section to docs/guide.md",
            "required_fact_ids": ["g1", "g2"],
        },
        "obsolete_facts": [],
        "known_omissions": ["The docs build was never run."],
        "unavailable_evidence": [{"fact_id": "g3", "pointer": pointer, "reason": "the build needs a mirror"}],
    }


def audit_task() -> dict[str, object]:
    """A coding task carrying a third fact the expected action does not depend on.

    The extra fact is what lets a test separate "the context never delivered what
    the action needs" from "the session relied on something the context dropped
    even though the action's own facts arrived". Its evidence turn sits outside
    the window a compacted transcript retains.
    """

    return {
        "task_id": "t-audit",
        "kind": "coding",
        "objective": "Audit the retry helper.",
        "transcript": [
            {"turn": 1, "role": "user", "text": "Audit the retry helper."},
            {"turn": 2, "role": "assistant", "text": "Start from the recorded budget."},
            {"turn": 3, "role": "assistant", "text": "The audit notes live in docs/retry-audit.md."},
            {"turn": 4, "role": "tool", "text": "read retry.py -> run_with_backoff"},
            {"turn": 5, "role": "user", "text": "Keep the public signature."},
            {"turn": 6, "role": "assistant", "text": "Only the helper body changes."},
            {"turn": 7, "role": "assistant", "text": "The retry base is 1 second with full jitter."},
            {"turn": 8, "role": "assistant", "text": "The helper is run_with_backoff."},
        ],
        "required_state_facts": [
            {"fact_id": "h1", "text": "the retry base is 1 second", "evidence": "turn:7"},
            {"fact_id": "h2", "text": "the helper is run_with_backoff", "evidence": "turn:8"},
            {"fact_id": "h3", "text": "the audit notes live in docs/retry-audit.md", "evidence": "turn:3"},
        ],
        "expected_next_action": {
            "action_id": "c1",
            "text": "call run_with_backoff with a 1 second base",
            "required_fact_ids": ["h1", "h2"],
        },
        "obsolete_facts": [
            {"fact_id": "o1", "text": "use a 30 second base", "evidence": "turn:1", "superseded_by": "h1"}
        ],
        "known_omissions": ["The maximum attempt count was never agreed."],
        "unavailable_evidence": [],
    }


def write_lock(tmp_path: Path, tasks: list[dict[str, object]], **overrides: object) -> Path:
    payload: dict[str, object] = {"schema": TASK_LOCK_SCHEMA, "task_set_id": "test-set", "tasks": tasks}
    payload.update(overrides)
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def standard_lock(tmp_path: Path) -> Path:
    return write_lock(tmp_path, [coding_task(), documentation_task()])


def analysis_lock(tmp_path: Path) -> Path:
    """A lock whose coding task exposes a fact the next action does not depend on."""

    return write_lock(tmp_path, [audit_task(), documentation_task()])


def valid_attempt(lock: Path, **overrides: object) -> dict[str, object]:
    """A well-formed recording against the standard lock's coding task.

    The context digest is derived from the entry's own task and arm, so a test that
    swaps either one still produces a recording bound to the context it names.
    """

    entry: dict[str, object] = {
        "task_id": "t-coding",
        "arm_id": "rollover-handoff-v1",
        "host": "fixture-host",
        "host_revision": "fixture-host@1",
        "model": "fixture-model",
        "context_sha256": UNBOUND_CONTEXT_SHA256,
        "steps": [
            {"step": 1, "action_text": "Compared the checkpoint with the module.", "relied_on": ["f1"]},
            {
                "step": 2,
                "action_text": "Applied the retry base.",
                "relied_on": ["f1", "f2"],
                "performed_action_id": "a1",
            },
        ],
    }
    entry.update(overrides)
    if "context_sha256" not in overrides:
        try:
            entry["context_sha256"] = context_digest(lock, str(entry["task_id"]), str(entry["arm_id"]))
        except (WorkContinuityCatalogError, ContinuationArmError):
            # These overrides deliberately name an unknown task or arm, which the
            # validator under test rejects before the digest could matter.
            pass
    return entry


def write_attempts(
    tmp_path: Path,
    entries: list[dict[str, object]],
    *,
    lock: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    payload = {
        "schema": ATTEMPTS_SCHEMA,
        "protocol": attempts_protocol(lock, max_bytes=max_bytes),
        "attempts": entries,
    }
    path = tmp_path / "attempts.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def shipped_locks_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "locks"
