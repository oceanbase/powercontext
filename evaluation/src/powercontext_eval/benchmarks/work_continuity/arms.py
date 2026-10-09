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

"""Continuation-method registry for comparable work-continuity runs.

An arm is a frozen continuation method, not a search mode. Two runs are
comparable when every declared protocol setting matches and only the arm
differs, so each arm records exactly the assembly behaviour it is allowed to
vary. Only methods that are actually implemented may be registered; an unknown
arm id fails loudly instead of falling back to the most complete method, which
would silently make a weak method look strong.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from powercontext_eval.errors import PowerContextEvalError

# The bounded continuation budget is part of the protocol, not of any arm: every
# arm receives the same ceiling so injected bytes measure the method rather than
# the allowance. It is a declared setting so a reader can reproduce a run.
DEFAULT_ASSEMBLY_MAX_BYTES = 16_000


class ContinuationArmError(PowerContextEvalError):
    """A continuation arm lookup or run comparison cannot preserve its contract."""


@dataclass(frozen=True)
class ContinuationArm:
    """One pinned continuation method.

    ``transcript_tail_turns`` is ``None`` when the method delivers the whole
    transcript, and a count otherwise. ``transcript_head_turns`` is always
    explicit, so a method that keeps an opening turn and drops the middle is
    distinguished from one that keeps only the end.
    """

    arm_id: str
    method: str
    is_baseline: bool
    carries_transcript: bool
    transcript_head_turns: int
    transcript_tail_turns: int | None
    carries_state_facts: bool
    carries_evidence_pointers: bool
    carries_next_action: bool
    carries_omissions: bool
    excludes_superseded: bool


FULL_TRANSCRIPT = ContinuationArm(
    arm_id="full-transcript-v1",
    method="full-transcript",
    is_baseline=True,
    carries_transcript=True,
    transcript_head_turns=0,
    transcript_tail_turns=None,
    carries_state_facts=False,
    carries_evidence_pointers=False,
    carries_next_action=False,
    carries_omissions=False,
    excludes_superseded=False,
)

COMPACTED_TRANSCRIPT = ContinuationArm(
    arm_id="compacted-transcript-v1",
    method="compacted-transcript",
    is_baseline=True,
    carries_transcript=True,
    transcript_head_turns=2,
    transcript_tail_turns=4,
    carries_state_facts=False,
    carries_evidence_pointers=False,
    carries_next_action=False,
    carries_omissions=False,
    excludes_superseded=False,
)

INFORMAL_SUMMARY = ContinuationArm(
    arm_id="informal-summary-v1",
    method="informal-summary",
    is_baseline=True,
    carries_transcript=True,
    transcript_head_turns=0,
    transcript_tail_turns=1,
    carries_state_facts=False,
    carries_evidence_pointers=False,
    carries_next_action=False,
    carries_omissions=False,
    excludes_superseded=False,
)

ROLLOVER_HANDOFF = ContinuationArm(
    arm_id="rollover-handoff-v1",
    method="rollover-handoff",
    is_baseline=False,
    carries_transcript=False,
    transcript_head_turns=0,
    transcript_tail_turns=0,
    carries_state_facts=True,
    carries_evidence_pointers=True,
    carries_next_action=True,
    carries_omissions=True,
    excludes_superseded=True,
)

CONTINUATION_ARMS: tuple[ContinuationArm, ...] = (
    FULL_TRANSCRIPT,
    COMPACTED_TRANSCRIPT,
    INFORMAL_SUMMARY,
    ROLLOVER_HANDOFF,
)
BASELINE_ARM_IDS: tuple[str, ...] = tuple(arm.arm_id for arm in CONTINUATION_ARMS if arm.is_baseline)
TREATMENT_ARM_ID = ROLLOVER_HANDOFF.arm_id

_ARMS_BY_ID = {arm.arm_id: arm for arm in CONTINUATION_ARMS}

# Run-manifest fields that must match before two runs may be compared. Run
# identity, timestamps, machine-local paths, the host *name*, and the arm record
# itself may differ: the host is a declared dimension of this benchmark, and the
# arm is exactly the intended difference. What the hosts ran — the execution
# configuration below — is compared separately, because a model or runtime
# difference is not a method difference.
_COMPARED_MANIFEST_PATHS: tuple[tuple[str, ...], ...] = (
    ("inputs", "task_lock", "content_sha256"),
    ("task_ids",),
    ("assembly", "max_bytes"),
    ("revisions", "powercontext"),
    ("revisions", "integration"),
)


def get_continuation_arm(arm_id: str) -> ContinuationArm:
    """Return the registered arm for ``arm_id`` or fail with the supported list."""

    arm = _ARMS_BY_ID.get(arm_id) if isinstance(arm_id, str) else None
    if arm is None:
        supported = ", ".join(arm.arm_id for arm in CONTINUATION_ARMS)
        raise ContinuationArmError(f"unknown continuation arm: {arm_id!r}; supported arms: {supported}")
    return arm


def supported_continuation_arm_ids() -> tuple[str, ...]:
    """Return the stable ids of every registered continuation arm."""

    return tuple(arm.arm_id for arm in CONTINUATION_ARMS)


def resolve_continuation_arms(values: tuple[str, ...] | None) -> tuple[ContinuationArm, ...]:
    """Resolve a selection into arms in registry order.

    ``None`` selects the paired comparison the benchmark is defined around: every
    baseline method plus the Rollover Handoff treatment.
    """

    if values is None:
        return CONTINUATION_ARMS
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ContinuationArmError(f"duplicate continuation arm selection: {value!r}")
        seen.add(value)
    selected = {value: get_continuation_arm(value) for value in values}
    return tuple(arm for arm in CONTINUATION_ARMS if arm.arm_id in selected)


def arm_manifest_record(arm: ContinuationArm, *, max_bytes: int) -> dict[str, object]:
    """Render the arm and its shared budget as the stable manifest block."""

    return {
        "id": arm.arm_id,
        "method": arm.method,
        "is_baseline": arm.is_baseline,
        "carries_transcript": arm.carries_transcript,
        "transcript_head_turns": arm.transcript_head_turns,
        "transcript_tail_turns": arm.transcript_tail_turns,
        "carries_state_facts": arm.carries_state_facts,
        "carries_evidence_pointers": arm.carries_evidence_pointers,
        "carries_next_action": arm.carries_next_action,
        "carries_omissions": arm.carries_omissions,
        "excludes_superseded": arm.excludes_superseded,
        "assembly_max_bytes": max_bytes,
    }


def declared_run_arm_ids(manifest: Mapping[str, object]) -> tuple[str, ...] | None:
    """Return the registered continuation arms a run manifest declares, sorted.

    ``comparable_arms`` is the arm set the run selected, which is what a
    comparison has to read: ``experiment_arm`` is only the first of them. A
    manifest whose arm set is absent, empty, or names an unregistered arm yields
    ``None``, so a caller can refuse the comparison instead of reading a missing
    set as an arm the other run happens to share.
    """

    records = manifest.get("comparable_arms")
    if not isinstance(records, list) or not records:
        return None
    ids: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            return None
        arm_id = record.get("id")
        if not isinstance(arm_id, str) or arm_id not in _ARMS_BY_ID:
            return None
        ids.add(arm_id)
    return tuple(sorted(ids))


def ensure_comparable_work_continuity_runs(
    manifest_a: Mapping[str, object],
    manifest_b: Mapping[str, object],
) -> None:
    """Refuse to compare two runs whose pinned conditions differ beyond the arm.

    Both ``experiment_arm`` records must be registered configurations, and the
    host *name* is deliberately excluded so two host integrations remain
    comparable. The model and host revision those hosts ran under are not
    excluded, and neither is how the recordings were allocated between them: a run
    whose arms ran on different models is not comparable to one whose arms shared
    a model, a run that put most recordings on one model is not comparable to one
    that put most of them on another, and a run that assigned one task to one
    model is not comparable with one that assigned that task to the other. The
    allocation is compared down to the unit this benchmark scores — one task under
    one method — so a run that ran a method on one model is not comparable with
    one that ran that method on the other, because any of those differences would
    be read as a continuation-method difference.

    The two runs must also share at least one arm. The gate exists to hold every
    declared condition fixed and let the arm vary, which only means anything if
    some method was measured on both sides; two runs selecting disjoint methods
    have no method in common for an observed difference to be attributed to.
    """

    differences: list[str] = []
    differences.extend(_unregistered_arm_differences(manifest_a, "first manifest"))
    differences.extend(_unregistered_arm_differences(manifest_b, "second manifest"))
    differences.extend(_shared_arm_differences(manifest_a, manifest_b))
    for path in _COMPARED_MANIFEST_PATHS:
        name = ".".join(path)
        value_a = _manifest_value(manifest_a, path)
        value_b = _manifest_value(manifest_b, path)
        if value_a is None or value_b is None:
            differences.append(f"{name} is missing")
        elif value_a != value_b:
            differences.append(name)
    differences.extend(_configuration_differences(manifest_a, manifest_b))
    if differences:
        raise ContinuationArmError("runs are not comparable: " + "; ".join(differences))


def _shared_arm_differences(manifest_a: Mapping[str, object], manifest_b: Mapping[str, object]) -> list[str]:
    """Refuse a comparison between two runs that share no continuation arm.

    A run that measured only the treatment cannot be compared against one that
    measured only a baseline, and a run pairing two methods cannot be compared
    against one pairing the other two: no method appears on both sides, so nothing
    in the observed difference belongs to a continuation method.
    """

    arms_a = declared_run_arm_ids(manifest_a)
    arms_b = declared_run_arm_ids(manifest_b)
    if arms_a is None:
        return ["first manifest has no usable comparable_arms record"]
    if arms_b is None:
        return ["second manifest has no usable comparable_arms record"]
    if not set(arms_a) & set(arms_b):
        return [f"the runs share no continuation arm ({', '.join(arms_a)} vs {', '.join(arms_b)})"]
    return []


def _configuration_differences(manifest_a: Mapping[str, object], manifest_b: Mapping[str, object]) -> list[str]:
    """Compare how the recordings were allocated across execution configurations.

    Comparing only the *set* of configurations would let the allocation change
    while the set stays the same, and the allocation is what mixes outcomes: a
    run that gave model A one host and model B nine cannot be compared with one
    that gave them nine and one, because that difference moves success counts
    without saying anything about a continuation method. The same holds one level
    down when the totals agree: a run that sent task *X* to model A and task *Y*
    to model B cannot be compared with one that swapped them, because a task is
    scored on its own declared next action. It holds once more at the unit this
    benchmark actually scores — one task under one method: with two arms selected,
    a run whose baseline ran on model A and whose treatment ran on model B cannot
    be compared with one that swapped *those* assignments, because the per-arm
    successes flip with that swap alone while the task totals hide it. Host
    *names* stay out of the comparison, so two host integrations remain
    comparable; what is compared is which configuration recorded which
    ``(task, method)`` unit, and how many times.
    """

    allocation_a = _configuration_allocation(manifest_a)
    allocation_b = _configuration_allocation(manifest_b)
    if allocation_a is None or allocation_b is None:
        return ["execution_configuration is missing or malformed"]
    if allocation_a != allocation_b:
        first = _allocation_text(allocation_a)
        second = _allocation_text(allocation_b)
        return [f"execution_configuration allocation ({first} vs {second})"]
    return []


# One configuration's share of the recordings: the ``(task, method)`` units it
# recorded, each with how many attempts it contributed to that unit.
_ConfigurationUnits = tuple[tuple[tuple[str, str], int], ...]
_ConfigurationAllocation = tuple[tuple[str, str, _ConfigurationUnits], ...]


def _configuration_allocation(
    manifest: Mapping[str, object],
) -> _ConfigurationAllocation | None:
    """Return the recorded attempts per (host revision, model) and (task, method).

    Entries that name the same configuration are merged, so the result is the
    distribution the outcomes were drawn from rather than a per-host listing. The
    distribution is kept *per (task, method)*, because that pair is the unit this
    benchmark scores: a task is scored against one declared next action under one
    continuation method. Two runs can agree on the hosts, the models, the host
    revisions, the tasks and the totals while assigning a method to the other
    model, and with method-specific model behaviour that swap alone moves the
    per-arm success counts without any method difference. A per-task total cannot
    see it, because it merges the attempts of every method recorded on that task.

    A manifest that cannot produce it — a missing block, a missing attempt count,
    a task or method breakdown that does not add up, a non-positive count, a
    per-task entry that is a bare count rather than a per-method one — yields
    ``None`` and the gate refuses the comparison instead of treating the runs as
    comparable by default.
    """

    entries = manifest.get("execution_configuration")
    if not isinstance(entries, list):
        return None
    counts: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            return None
        revision = entry.get("host_revision")
        model = entry.get("model")
        attempt_count = entry.get("attempt_count")
        tasks = entry.get("tasks")
        if not isinstance(revision, str) or not isinstance(model, str):
            return None
        if not isinstance(attempt_count, int) or isinstance(attempt_count, bool) or attempt_count < 1:
            return None
        if not isinstance(tasks, Mapping):
            return None
        units = counts.setdefault((revision, model), {})
        declared = 0
        for task_id, arms in tasks.items():
            if not isinstance(task_id, str) or not isinstance(arms, Mapping):
                return None
            for arm_id, count in arms.items():
                if not isinstance(arm_id, str) or not isinstance(count, int) or isinstance(count, bool) or count < 1:
                    return None
                unit = (task_id, arm_id)
                units[unit] = units.get(unit, 0) + count
                declared += count
        if declared != attempt_count:
            return None
    return tuple(sorted((revision, model, tuple(sorted(units.items()))) for (revision, model), units in counts.items()))


def _allocation_text(allocation: _ConfigurationAllocation) -> str:
    """Render one allocation so a refusal names both distributions it compared."""

    if not allocation:
        return "none"
    return ", ".join(f"{revision}/{model} ({_units_text(units)})" for revision, model, units in allocation)


def _units_text(units: _ConfigurationUnits) -> str:
    return " ".join(f"{task_id}/{arm_id} x{count}" for (task_id, arm_id), count in units)


def _unregistered_arm_differences(manifest: Mapping[str, object], label: str) -> list[str]:
    value = manifest.get("experiment_arm")
    if not isinstance(value, Mapping):
        return [f"{label} has no experiment_arm record"]
    record = {str(key): item for key, item in value.items()}
    for arm in CONTINUATION_ARMS:
        max_bytes = record.get("assembly_max_bytes")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
            continue
        if record == arm_manifest_record(arm, max_bytes=max_bytes):
            return []
    return [f"{label} has an unregistered experiment_arm record"]


def _manifest_value(manifest: Mapping[str, object], path: tuple[str, ...]) -> Any:
    value: object = manifest
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value
