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

"""Recorded continuation attempts as a scored input artifact.

The harness never runs a model. A host integration records what a fresh session
did with the continuation context it received, and this module validates that
recording before it is scored. A recorder maps each step onto the task's declared
fact ids, so scoring stays exact instead of depending on text matching against
free-form model output.

A recording is evidence about one exact context delivered under one exact
protocol, so the artifact declares both. The protocol names the task lock and the
byte ceiling that produced the contexts, and every attempt carries the digest of
the context it actually received. Without that binding a recording made under one
protocol could be scored against a context assembled under another, and the
resulting numbers would describe neither.

Validation is fail-closed: an attempt that names an undeclared fact id or action
id, an unknown task or arm, an inconsistent host configuration, or a malformed
step is rejected rather than scored, because a silently dropped reference would
understate every conflict metric.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from powercontext_eval_work_continuity.arms import ContinuationArmError, get_continuation_arm
from powercontext_eval_work_continuity.catalog import TaskCatalog, WorkContinuityCatalogError
from powercontext_eval_work_continuity.errors import PowerContextEvalError

ATTEMPTS_SCHEMA = "powercontext.work-continuity-attempts.v1"


class AttemptInputError(PowerContextEvalError):
    """A recorded continuation attempt cannot be trusted."""


class AttemptEnvironmentError(AttemptInputError):
    """The recorded attempt artifact or its environment is not ready."""


@dataclass(frozen=True)
class AttemptProtocol:
    """The protocol one recording was made under.

    The task lock and the byte ceiling together determine every assembled
    context, so pinning them pins what the recording is evidence about.
    """

    task_set_id: str
    task_lock_sha256: str
    assembly_max_bytes: int


@dataclass(frozen=True)
class RecordedStep:
    """One action a fresh session took, with the declared facts it relied on.

    ``performed_action_id`` is set only on a step that claims to have carried out
    the task's declared next action. Reading the facts the action depends on is
    not the same as performing it, so the two are recorded separately and only the
    latter can be a recovery.
    """

    step: int
    action_text: str
    relied_on: tuple[str, ...]
    performed_action_id: str | None = None
    correction: bool = False


@dataclass(frozen=True)
class RecordedAttempt:
    """One recorded continuation attempt for one task under one method.

    ``host_revision`` and ``model`` are the execution configuration the attempt
    ran under. They are required because a comparison that ignores them would
    attribute a configuration difference to the continuation method.
    """

    task_id: str
    arm_id: str
    host: str
    host_revision: str
    model: str
    context_sha256: str
    steps: tuple[RecordedStep, ...]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.task_id, self.arm_id, self.host)

    @property
    def configuration(self) -> tuple[str, str]:
        return (self.host_revision, self.model)


@dataclass(frozen=True)
class AttemptSet:
    """Validated recorded attempts for one task catalog and protocol."""

    path: Path
    content_sha256: str
    protocol: AttemptProtocol
    attempts: tuple[RecordedAttempt, ...]

    def for_task_and_arm(self, task_id: str, arm_id: str) -> tuple[RecordedAttempt, ...]:
        return tuple(attempt for attempt in self.attempts if attempt.task_id == task_id and attempt.arm_id == arm_id)

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(attempt.host for attempt in self.attempts))

    @property
    def keys(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(attempt.key for attempt in self.attempts)

    @property
    def configurations(self) -> tuple[tuple[str, str], ...]:
        """Return the distinct execution configurations, without host names.

        Two runs may be compared when they ran the same configurations; the host
        name itself is a declared dimension of this benchmark, but the model and
        host revision are not.
        """

        return tuple(sorted({attempt.configuration for attempt in self.attempts}))


def load_attempts(path: Path, *, catalog: TaskCatalog) -> AttemptSet:
    """Read and validate a recorded-attempt artifact against one task catalog."""

    resolved = path.resolve()
    try:
        raw = resolved.read_bytes()
    except OSError as error:
        raise AttemptEnvironmentError(f"Cannot read work-continuity attempts: {resolved}") from error
    if not raw.strip():
        raise AttemptEnvironmentError(f"Work-continuity attempts are blank: {resolved}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AttemptInputError("Work-continuity attempts are not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != {"schema", "protocol", "attempts"}:
        raise AttemptInputError("Work-continuity attempts must contain only schema, protocol, and attempts")
    if value["schema"] != ATTEMPTS_SCHEMA:
        raise AttemptInputError("Work-continuity attempts schema is unsupported")
    protocol = _protocol(value["protocol"])
    raw_attempts = value["attempts"]
    if not isinstance(raw_attempts, list) or not raw_attempts:
        raise AttemptInputError("Work-continuity attempts must contain at least one attempt")
    attempts: list[RecordedAttempt] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw_attempt in enumerate(raw_attempts):
        attempt = _attempt(raw_attempt, index, catalog)
        if attempt.key in seen:
            task_id, arm_id, host = attempt.key
            raise AttemptInputError(f"Work-continuity attempts repeat task {task_id} arm {arm_id} host {host!r}")
        seen.add(attempt.key)
        attempts.append(attempt)
    _require_one_configuration_per_host(attempts)
    return AttemptSet(
        path=resolved,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        protocol=protocol,
        attempts=tuple(attempts),
    )


def _protocol(raw: object) -> AttemptProtocol:
    protocol = _mapping(raw, "Work-continuity attempts protocol must be an object")
    required = {"task_set_id", "task_lock_sha256", "assembly_max_bytes"}
    if set(protocol) != required:
        raise AttemptInputError(f"Work-continuity attempts protocol must declare exactly {sorted(required)}")
    max_bytes = protocol["assembly_max_bytes"]
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise AttemptInputError("Work-continuity attempts protocol assembly_max_bytes must be a positive integer")
    return AttemptProtocol(
        task_set_id=_text(protocol["task_set_id"], "protocol task_set_id"),
        task_lock_sha256=_sha256(protocol["task_lock_sha256"], "protocol task_lock_sha256"),
        assembly_max_bytes=max_bytes,
    )


def _require_one_configuration_per_host(attempts: list[RecordedAttempt]) -> None:
    """Reject a host that reports more than one execution configuration.

    Attempts are compared within a host, so a host whose arms ran under different
    models or host revisions cannot support a comparison: the difference would be
    attributed to the continuation method.
    """

    by_host: dict[str, tuple[str, str]] = {}
    for attempt in attempts:
        existing = by_host.setdefault(attempt.host, attempt.configuration)
        if existing != attempt.configuration:
            raise AttemptInputError(
                f"Work-continuity host {attempt.host!r} reports more than one execution configuration: "
                f"{existing[0]!r}/{existing[1]!r} and {attempt.host_revision!r}/{attempt.model!r}"
            )


def _mapping(raw: object, error: str) -> dict[str, object]:
    """Return ``raw`` as a JSON object mapping, or reject it with ``error``."""

    if not isinstance(raw, dict):
        raise AttemptInputError(error)
    return cast("dict[str, object]", raw)


def _attempt(raw: object, index: int, catalog: TaskCatalog) -> RecordedAttempt:
    label = f"attempt {index}"
    raw_attempt = _mapping(raw, f"Work-continuity {label} must be an object")
    required = {"task_id", "arm_id", "host", "host_revision", "model", "context_sha256", "steps"}
    if set(raw_attempt) != required:
        raise AttemptInputError(f"Work-continuity {label} must declare exactly {sorted(required)}")
    try:
        task = catalog.require(_text(raw_attempt["task_id"], f"{label} task_id"))
    except WorkContinuityCatalogError as error:
        raise AttemptInputError(str(error)) from None
    arm_id = _text(raw_attempt["arm_id"], f"{label} arm_id")
    try:
        get_continuation_arm(arm_id)
    except ContinuationArmError as error:
        raise AttemptInputError(str(error)) from None
    declared = set(task.required_fact_ids) | set(task.obsolete_fact_ids)
    steps = _steps(
        raw_attempt["steps"],
        label,
        task.task_id,
        declared,
        task.expected_next_action.action_id,
    )
    return RecordedAttempt(
        task_id=task.task_id,
        arm_id=arm_id,
        host=_text(raw_attempt["host"], f"{label} host"),
        host_revision=_text(raw_attempt["host_revision"], f"{label} host_revision"),
        model=_text(raw_attempt["model"], f"{label} model"),
        context_sha256=_sha256(raw_attempt["context_sha256"], f"{label} context_sha256"),
        steps=steps,
    )


def _steps(
    raw: object,
    label: str,
    task_id: str,
    declared: set[str],
    expected_action_id: str,
) -> tuple[RecordedStep, ...]:
    if not isinstance(raw, list) or not raw:
        raise AttemptInputError(f"Work-continuity {label} must record at least one step")
    steps: list[RecordedStep] = []
    for index, raw_step in enumerate(raw):
        step = _mapping(raw_step, f"Work-continuity {label} step {index} must be an object")
        keys = set(step)
        if not {"step", "action_text", "relied_on"} <= keys or keys - {
            "step",
            "action_text",
            "relied_on",
            "performed_action_id",
            "correction",
        }:
            raise AttemptInputError(
                f"Work-continuity {label} step {index} must declare step, action_text, and relied_on, "
                "and may add performed_action_id and correction"
            )
        number = step["step"]
        if not isinstance(number, int) or isinstance(number, bool) or number != index + 1:
            raise AttemptInputError(f"Work-continuity {label} steps must be numbered 1..n in order")
        relied_on = step["relied_on"]
        if not isinstance(relied_on, list):
            raise AttemptInputError(f"Work-continuity {label} step {number} relied_on must be an array")
        references: list[str] = []
        for entry in relied_on:
            fact_id = _text(entry, f"{label} step {number} relied_on")
            if fact_id not in declared:
                raise AttemptInputError(
                    f"Work-continuity {label} step {number} relies on undeclared fact {fact_id!r} for task {task_id}"
                )
            if fact_id in references:
                raise AttemptInputError(f"Work-continuity {label} step {number} repeats fact {fact_id}")
            references.append(fact_id)
        performed_action_id = _performed_action_id(step, label, number, expected_action_id)
        correction = step.get("correction", False)
        if not isinstance(correction, bool):
            raise AttemptInputError(f"Work-continuity {label} step {number} correction must be a boolean")
        steps.append(
            RecordedStep(
                step=number,
                action_text=_text(step["action_text"], f"{label} step {number} action_text"),
                relied_on=tuple(references),
                performed_action_id=performed_action_id,
                correction=correction,
            )
        )
    return tuple(steps)


def _performed_action_id(step: dict[str, object], label: str, number: int, expected_action_id: str) -> str | None:
    """Return the declared performed action, rejecting an undeclared one."""

    value = step.get("performed_action_id")
    if value is None:
        return None
    action_id = _text(value, f"{label} step {number} performed_action_id")
    if action_id != expected_action_id:
        raise AttemptInputError(
            f"Work-continuity {label} step {number} declares performed_action_id {action_id!r}, "
            f"but the only action this task declares is {expected_action_id!r}"
        )
    return action_id


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttemptInputError(f"Work-continuity {label} must be a non-empty string")
    return value


def _sha256(value: object, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise AttemptInputError(f"Work-continuity {label} must be a lowercase hex sha256 digest")
    return digest
