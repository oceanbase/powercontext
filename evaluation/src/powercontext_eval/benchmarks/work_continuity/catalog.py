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

"""Fail-closed catalog for the pinned work-continuity task set.

A continuation task is only usable as a measurement if its ground truth is
unambiguous. Every task therefore declares, ahead of any run:

- the long session it continues, as numbered turns that every evidence pointer
  resolves against;
- the state facts a fresh session must hold to continue safely;
- the next action, plus the facts that action depends on;
- the facts that were true earlier and were later superseded, so a method that
  carries stale material can be told apart from one that does not;
- the omissions, and the facts whose evidence cannot be produced here.

The loader rejects a task that cannot be scored rather than letting a partially
specified task produce a number.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeAlias, cast

from powercontext_eval.errors import PowerContextEvalError

TASK_LOCK_SCHEMA = "powercontext.work-continuity-task-lock.v1"

TaskKind: TypeAlias = Literal["coding", "documentation"]
TurnRole: TypeAlias = Literal["user", "assistant", "tool", "human"]

_KINDS: frozenset[str] = frozenset({"coding", "documentation"})
_ROLES: frozenset[str] = frozenset({"user", "assistant", "tool", "human"})

# A transcript shorter than this cannot represent the multi-turn, multi-read
# session that a continuation method is supposed to recover state from.
MIN_TRANSCRIPT_TURNS = 4


class WorkContinuityCatalogError(PowerContextEvalError):
    """The pinned work-continuity inputs cannot be trusted."""


class WorkContinuityInputError(WorkContinuityCatalogError):
    """The requested work-continuity configuration is invalid."""


class WorkContinuityEnvironmentError(WorkContinuityCatalogError):
    """The pinned task lock or output environment is not ready."""


@dataclass(frozen=True)
class Turn:
    """One numbered turn of the long session a fresh session continues."""

    turn: int
    role: TurnRole
    text: str

    @property
    def pointer(self) -> str:
        """Return the canonical evidence pointer for this turn."""

        return f"turn:{self.turn}"


@dataclass(frozen=True)
class StateFact:
    """One fact a fresh session must hold to continue the task safely."""

    fact_id: str
    text: str
    evidence: str | None


@dataclass(frozen=True)
class NextAction:
    """The first action a correctly recovered fresh session should take."""

    action_id: str
    text: str
    required_fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class ObsoleteFact:
    """A fact that was true earlier in the session and was later superseded."""

    fact_id: str
    text: str
    evidence: str
    superseded_by: str


@dataclass(frozen=True)
class UnavailableEvidence:
    """A state fact whose evidence cannot be produced in this environment."""

    fact_id: str
    pointer: str
    reason: str


@dataclass(frozen=True)
class ContinuationTask:
    """One pinned continuation task with complete scoring ground truth."""

    task_id: str
    kind: TaskKind
    objective: str
    transcript: tuple[Turn, ...]
    required_state_facts: tuple[StateFact, ...]
    expected_next_action: NextAction
    obsolete_facts: tuple[ObsoleteFact, ...]
    known_omissions: tuple[str, ...]
    unavailable_evidence: tuple[UnavailableEvidence, ...]

    @property
    def turn_numbers(self) -> tuple[int, ...]:
        return tuple(turn.turn for turn in self.transcript)

    @property
    def required_fact_ids(self) -> tuple[str, ...]:
        return tuple(fact.fact_id for fact in self.required_state_facts)

    @property
    def obsolete_fact_ids(self) -> tuple[str, ...]:
        return tuple(fact.fact_id for fact in self.obsolete_facts)

    @property
    def unavailable_fact_ids(self) -> tuple[str, ...]:
        return tuple(entry.fact_id for entry in self.unavailable_evidence)

    @property
    def obsolete_source_turns(self) -> tuple[int, ...]:
        """Return the turns that carry superseded material, in transcript order."""

        numbers = {int(fact.evidence.split(":", 1)[1]) for fact in self.obsolete_facts}
        return tuple(number for number in self.turn_numbers if number in numbers)

    def fact(self, fact_id: str) -> StateFact | None:
        return next((fact for fact in self.required_state_facts if fact.fact_id == fact_id), None)

    def turn(self, number: int) -> Turn | None:
        return next((turn for turn in self.transcript if turn.turn == number), None)


@dataclass(frozen=True)
class TaskCatalog:
    """Validated work-continuity task identities for one checked-in lock file."""

    path: Path
    task_set_id: str
    content_sha256: str
    tasks: Mapping[str, ContinuationTask]

    @classmethod
    def load(cls, path: Path) -> TaskCatalog:
        """Read and fully validate one task lock before any task is used."""

        resolved = path.resolve()
        try:
            raw = resolved.read_bytes()
        except OSError as error:
            raise WorkContinuityEnvironmentError(f"Cannot read work-continuity task lock: {resolved}") from error
        if not raw.strip():
            raise WorkContinuityEnvironmentError(f"Work-continuity task lock is blank: {resolved}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkContinuityInputError("Work-continuity task lock is not valid UTF-8 JSON") from error
        if not isinstance(value, dict) or set(value) != {"schema", "task_set_id", "tasks"}:
            raise WorkContinuityInputError("Work-continuity task lock must contain only schema, task_set_id, and tasks")
        if value["schema"] != TASK_LOCK_SCHEMA:
            raise WorkContinuityInputError("Work-continuity task lock schema is unsupported")
        task_set_id = _nonblank(value["task_set_id"], "task_set_id")
        raw_tasks = value["tasks"]
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise WorkContinuityInputError("Work-continuity task lock must declare at least one task")
        tasks: dict[str, ContinuationTask] = {}
        for index, raw_task in enumerate(raw_tasks):
            task = _task(raw_task, index)
            if task.task_id in tasks:
                raise WorkContinuityInputError(f"Duplicate work-continuity task id: {task.task_id}")
            tasks[task.task_id] = task
        _validate_task_set(tasks.values())
        return cls(
            path=resolved,
            task_set_id=task_set_id,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            tasks=MappingProxyType(tasks),
        )

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(self.tasks)

    def require(self, task_id: str) -> ContinuationTask:
        """Return one task or fail with the declared ids."""

        task = self.tasks.get(task_id)
        if task is None:
            known = ", ".join(self.task_ids)
            raise WorkContinuityInputError(f"unknown work-continuity task: {task_id!r}; declared tasks: {known}")
        return task

    def select(self, task_ids: Sequence[str] | None) -> tuple[ContinuationTask, ...]:
        """Return the selected tasks in lock order, or every task when unspecified."""

        if task_ids is None:
            return tuple(self.tasks.values())
        seen: set[str] = set()
        for task_id in task_ids:
            if task_id in seen:
                raise WorkContinuityInputError(f"Duplicate work-continuity task selection: {task_id}")
            seen.add(task_id)
        selected = {task_id: self.require(task_id) for task_id in task_ids}
        return tuple(task for task_id, task in self.tasks.items() if task_id in selected)


def _mapping(raw: object, error: str) -> dict[str, object]:
    """Return ``raw`` as a JSON object mapping, or reject it with ``error``."""

    if not isinstance(raw, dict):
        raise WorkContinuityInputError(error)
    return cast("dict[str, object]", raw)


def _task(raw: object, index: int) -> ContinuationTask:
    label = f"task {index}"
    task = _mapping(raw, f"Work-continuity {label} must be an object")
    expected = {
        "task_id",
        "kind",
        "objective",
        "transcript",
        "required_state_facts",
        "expected_next_action",
        "obsolete_facts",
        "known_omissions",
        "unavailable_evidence",
    }
    if set(task) != expected:
        raise WorkContinuityInputError(f"Work-continuity {label} must declare exactly {sorted(expected)}")
    task_id = _nonblank(task["task_id"], f"{label} task_id")
    kind = task["kind"]
    if kind not in _KINDS:
        raise WorkContinuityInputError(f"Work-continuity task {task_id} kind must be coding or documentation")
    objective = _nonblank(task["objective"], f"{task_id} objective")
    transcript = _transcript(task["transcript"], task_id)
    turn_numbers = {turn.turn for turn in transcript}
    facts = _state_facts(task["required_state_facts"], task_id, turn_numbers)
    action = _next_action(task["expected_next_action"], task_id, facts)
    obsolete = _obsolete_facts(task["obsolete_facts"], task_id, turn_numbers, facts)
    omissions = _strings(task["known_omissions"], f"{task_id} known_omissions")
    unavailable = _unavailable_evidence(task["unavailable_evidence"], task_id, facts)
    return ContinuationTask(
        task_id=task_id,
        kind=cast(TaskKind, kind),
        objective=objective,
        transcript=transcript,
        required_state_facts=facts,
        expected_next_action=action,
        obsolete_facts=obsolete,
        known_omissions=omissions,
        unavailable_evidence=unavailable,
    )


def _transcript(raw: object, task_id: str) -> tuple[Turn, ...]:
    if not isinstance(raw, list) or len(raw) < MIN_TRANSCRIPT_TURNS:
        raise WorkContinuityInputError(
            f"Work-continuity task {task_id} transcript must hold at least {MIN_TRANSCRIPT_TURNS} turns"
        )
    turns: list[Turn] = []
    for index, raw_turn in enumerate(raw):
        turn = _mapping(raw_turn, f"Work-continuity task {task_id} transcript turn {index} is malformed")
        if set(turn) != {"turn", "role", "text"}:
            raise WorkContinuityInputError(f"Work-continuity task {task_id} transcript turn {index} is malformed")
        number = turn["turn"]
        if not isinstance(number, int) or isinstance(number, bool) or number != index + 1:
            raise WorkContinuityInputError(
                f"Work-continuity task {task_id} transcript turns must be numbered 1..n in order"
            )
        role = turn["role"]
        if role not in _ROLES:
            raise WorkContinuityInputError(
                f"Work-continuity task {task_id} transcript turn {number} has an invalid role"
            )
        turns.append(
            Turn(turn=number, role=cast(TurnRole, role), text=_nonblank(turn["text"], f"{task_id} turn {number}"))
        )
    return tuple(turns)


def _state_facts(raw: object, task_id: str, turn_numbers: set[int]) -> tuple[StateFact, ...]:
    if not isinstance(raw, list) or not raw:
        raise WorkContinuityInputError(f"Work-continuity task {task_id} must declare at least one required state fact")
    facts: list[StateFact] = []
    for index, raw_fact in enumerate(raw):
        fact = _mapping(raw_fact, f"Work-continuity task {task_id} state fact {index} is malformed")
        if set(fact) != {"fact_id", "text", "evidence"}:
            raise WorkContinuityInputError(f"Work-continuity task {task_id} state fact {index} is malformed")
        fact_id = _nonblank(fact["fact_id"], f"{task_id} state fact {index} fact_id")
        if any(entry.fact_id == fact_id for entry in facts):
            raise WorkContinuityInputError(f"Work-continuity task {task_id} repeats state fact id {fact_id}")
        evidence = fact["evidence"]
        if evidence is not None:
            evidence = _turn_pointer(evidence, task_id, turn_numbers, f"state fact {fact_id}")
        facts.append(
            StateFact(
                fact_id=fact_id, text=_nonblank(fact["text"], f"{task_id} state fact {fact_id}"), evidence=evidence
            )
        )
    return tuple(facts)


def _next_action(raw: object, task_id: str, facts: Sequence[StateFact]) -> NextAction:
    action = _mapping(raw, f"Work-continuity task {task_id} expected_next_action is malformed")
    if set(action) != {"action_id", "text", "required_fact_ids"}:
        raise WorkContinuityInputError(f"Work-continuity task {task_id} expected_next_action is malformed")
    action_id = _nonblank(action["action_id"], f"{task_id} expected_next_action action_id")
    raw_ids = action["required_fact_ids"]
    if not isinstance(raw_ids, list) or not raw_ids:
        raise WorkContinuityInputError(
            f"Work-continuity task {task_id} expected action must depend on at least one state fact"
        )
    known = {fact.fact_id for fact in facts}
    required: list[str] = []
    for entry in raw_ids:
        fact_id = _nonblank(entry, f"{task_id} expected action required_fact_ids")
        if fact_id not in known:
            raise WorkContinuityInputError(
                f"Work-continuity task {task_id} expected action references unknown state fact {fact_id}"
            )
        if fact_id in required:
            raise WorkContinuityInputError(f"Work-continuity task {task_id} expected action repeats fact {fact_id}")
        required.append(fact_id)
    return NextAction(
        action_id=action_id,
        text=_nonblank(action["text"], f"{task_id} expected_next_action text"),
        required_fact_ids=tuple(required),
    )


def _obsolete_facts(
    raw: object,
    task_id: str,
    turn_numbers: set[int],
    facts: Sequence[StateFact],
) -> tuple[ObsoleteFact, ...]:
    if not isinstance(raw, list):
        raise WorkContinuityInputError(f"Work-continuity task {task_id} obsolete_facts must be an array")
    known = {fact.fact_id for fact in facts}
    obsolete: list[ObsoleteFact] = []
    for index, raw_fact in enumerate(raw):
        fact = _mapping(raw_fact, f"Work-continuity task {task_id} obsolete fact {index} is malformed")
        if set(fact) != {"fact_id", "text", "evidence", "superseded_by"}:
            raise WorkContinuityInputError(f"Work-continuity task {task_id} obsolete fact {index} is malformed")
        fact_id = _nonblank(fact["fact_id"], f"{task_id} obsolete fact {index} fact_id")
        if fact_id in known:
            raise WorkContinuityInputError(
                f"Work-continuity task {task_id} obsolete fact {fact_id} reuses a required state fact id"
            )
        if any(entry.fact_id == fact_id for entry in obsolete):
            raise WorkContinuityInputError(f"Work-continuity task {task_id} repeats obsolete fact id {fact_id}")
        superseded_by = _nonblank(fact["superseded_by"], f"{task_id} obsolete fact {fact_id} superseded_by")
        if superseded_by not in known:
            raise WorkContinuityInputError(
                f"Work-continuity task {task_id} obsolete fact {fact_id} is superseded by unknown fact {superseded_by}"
            )
        obsolete.append(
            ObsoleteFact(
                fact_id=fact_id,
                text=_nonblank(fact["text"], f"{task_id} obsolete fact {fact_id}"),
                evidence=_turn_pointer(fact["evidence"], task_id, turn_numbers, f"obsolete fact {fact_id}"),
                superseded_by=superseded_by,
            )
        )
    return tuple(obsolete)


def _unavailable_evidence(raw: object, task_id: str, facts: Sequence[StateFact]) -> tuple[UnavailableEvidence, ...]:
    if not isinstance(raw, list):
        raise WorkContinuityInputError(f"Work-continuity task {task_id} unavailable_evidence must be an array")
    by_id = {fact.fact_id: fact for fact in facts}
    declared: set[str] = set()
    entries: list[UnavailableEvidence] = []
    for index, raw_entry in enumerate(raw):
        entry = _mapping(raw_entry, f"Work-continuity task {task_id} unavailable evidence {index} is malformed")
        if set(entry) != {"fact_id", "pointer", "reason"}:
            raise WorkContinuityInputError(f"Work-continuity task {task_id} unavailable evidence {index} is malformed")
        fact_id = _nonblank(entry["fact_id"], f"{task_id} unavailable evidence {index} fact_id")
        if fact_id not in by_id:
            raise WorkContinuityInputError(
                f"Work-continuity task {task_id} unavailable evidence references unknown state fact {fact_id}"
            )
        if fact_id in declared:
            raise WorkContinuityInputError(f"Work-continuity task {task_id} repeats unavailable evidence for {fact_id}")
        declared.add(fact_id)
        entries.append(
            UnavailableEvidence(
                fact_id=fact_id,
                pointer=_nonblank(entry["pointer"], f"{task_id} unavailable evidence {fact_id} pointer"),
                reason=_nonblank(entry["reason"], f"{task_id} unavailable evidence {fact_id} reason"),
            )
        )
    # A fact is either backed by a turn, or it is listed here. A null-evidence
    # fact without an entry would silently disable every evidence check, and an
    # entry for a turn-backed fact would claim an omission that does not exist.
    null_evidence = {fact.fact_id for fact in facts if fact.evidence is None}
    if null_evidence != declared:
        missing = ", ".join(sorted(null_evidence - declared)) or "none"
        surplus = ", ".join(sorted(declared - null_evidence)) or "none"
        raise WorkContinuityInputError(
            f"Work-continuity task {task_id} unavailable evidence must match its evidence-free facts exactly: "
            f"undeclared={missing}; declared_but_turn_backed={surplus}"
        )
    return tuple(entries)


def _validate_task_set(tasks: Iterable[ContinuationTask]) -> None:
    """Reject a fixture set that cannot exercise the comparison it claims to."""

    kinds = {task.kind for task in tasks}
    if kinds != _KINDS:
        raise WorkContinuityInputError(
            "Work-continuity task set must cover both coding and documentation tasks; "
            f"declared kinds: {', '.join(sorted(kinds))}"
        )
    if not any(task.obsolete_facts for task in tasks):
        raise WorkContinuityInputError("Work-continuity task set must declare at least one superseded state fact")
    if not any(task.unavailable_evidence for task in tasks):
        raise WorkContinuityInputError("Work-continuity task set must declare at least one unavailable-evidence case")
    if not any(task.known_omissions for task in tasks):
        raise WorkContinuityInputError("Work-continuity task set must declare at least one known omission")


def _turn_pointer(value: object, task_id: str, turn_numbers: set[int], label: str) -> str:
    pointer = _nonblank(value, f"{task_id} {label} evidence")
    prefix, separator, suffix = pointer.partition(":")
    if prefix != "turn" or not separator or not suffix.isdigit() or int(suffix) not in turn_numbers:
        raise WorkContinuityInputError(
            f"Work-continuity task {task_id} {label} evidence must be a turn:N pointer to an existing turn"
        )
    return pointer


def _strings(raw: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise WorkContinuityInputError(f"Work-continuity {label} must be an array")
    return tuple(_nonblank(entry, label) for entry in raw)


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkContinuityInputError(f"Work-continuity {label} must be a non-empty string")
    return value
