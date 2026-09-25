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

"""Experiment arm registry for comparable LongMemEval-V2 retrieval runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from powercontext_eval.errors import PowerContextEvalError


class ExperimentArmError(PowerContextEvalError):
    """An experiment arm lookup or run comparison cannot preserve its contract."""


@dataclass(frozen=True)
class ExperimentArm:
    """One pinned experiment configuration identity for the smoke workload.

    Two runs are comparable when every field outside this record matches, so an arm
    must declare every behavioural knob it is allowed to vary. Only arms whose
    configuration is actually implemented may be registered; unimplemented arms must
    fail loudly instead of silently executing the default behaviour.
    """

    arm_id: str
    retrieval_strategy: str
    search_mode: str | None
    memory_projection: str
    query_projection: str
    prepared_context_max_bytes: int | None
    temporal_filter: str | None
    task_lens: str | None


CURRENT_MEMORY_FTS = ExperimentArm(
    arm_id="current-memory-fts-v1",
    retrieval_strategy="memory-search",
    search_mode="fts",
    memory_projection="deterministic-compact-v1",
    query_projection="question-text-v1",
    prepared_context_max_bytes=None,
    temporal_filter=None,
    task_lens=None,
)

CURRENT_MEMORY_HYBRID = ExperimentArm(
    arm_id="current-memory-hybrid-v1",
    retrieval_strategy="memory-search",
    search_mode="hybrid",
    memory_projection="deterministic-compact-v1",
    query_projection="question-text-v1",
    prepared_context_max_bytes=None,
    temporal_filter=None,
    task_lens=None,
)

QUERY_TIME_COMPACT = ExperimentArm(
    arm_id="query-time-compact-v1",
    retrieval_strategy="prepared-context",
    search_mode=None,
    memory_projection="deterministic-compact-v1",
    query_projection="powercontext-prepared-context-v1",
    prepared_context_max_bytes=8_000,
    temporal_filter=None,
    task_lens=None,
)

WRITE_TIME_L0_L1 = ExperimentArm(
    arm_id="write-time-l0-l1-v1",
    retrieval_strategy="memory-search",
    search_mode="fts",
    memory_projection="deterministic-l0-l1-v1",
    query_projection="question-text-v1",
    prepared_context_max_bytes=None,
    temporal_filter=None,
    task_lens=None,
)

TASK_LENSED_SELECTION = ExperimentArm(
    arm_id="task-lensed-selection-v1",
    retrieval_strategy="memory-search",
    search_mode="fts",
    memory_projection="deterministic-compact-v1",
    query_projection="deterministic-keyword-lens-v1",
    prepared_context_max_bytes=None,
    temporal_filter=None,
    task_lens="question-keywords-v1",
)

EXPERIMENT_ARMS: tuple[ExperimentArm, ...] = (
    CURRENT_MEMORY_FTS,
    CURRENT_MEMORY_HYBRID,
    QUERY_TIME_COMPACT,
    WRITE_TIME_L0_L1,
    TASK_LENSED_SELECTION,
)
DEFAULT_EXPERIMENT_ARM_ID = CURRENT_MEMORY_FTS.arm_id

_ARMS_BY_ID = {arm.arm_id: arm for arm in EXPERIMENT_ARMS}

# Run-manifest fields that must match before two runs may be compared. Everything
# else — run identity, timestamps, machine-local paths, and the arm record itself —
# may differ. ``powercontext.search_mode`` is excluded because it is derived from
# the arm and is exactly the declared difference between two arms.
_COMPARED_MANIFEST_PATHS: tuple[tuple[str, ...], ...] = (
    ("inputs", "dataset_lock", "content_sha256"),
    ("inputs", "smoke_manifest", "content_sha256"),
    ("inputs", "harness", "commit"),
    ("processor", "model"),
    ("processor", "revision"),
    ("memory_context_max_tokens",),
    ("powercontext", "search_limit"),
    ("reader",),
    ("judge",),
    ("revisions", "powercontext"),
    ("revisions", "integration"),
)

# ``reader`` and ``judge`` are legitimately null for model-free runs, so a null value
# there is a comparable configuration rather than a missing field.
_NULLABLE_COMPARED_PATHS = frozenset({("reader",), ("judge",)})


def get_experiment_arm(arm_id: str) -> ExperimentArm:
    """Return the registered arm for ``arm_id`` or fail with the supported list."""

    arm = _ARMS_BY_ID.get(arm_id) if isinstance(arm_id, str) else None
    if arm is None:
        supported = ", ".join(registered.arm_id for registered in EXPERIMENT_ARMS)
        raise ExperimentArmError(f"unknown experiment arm: {arm_id!r}; supported arms: {supported}")
    return arm


def supported_experiment_arm_ids() -> tuple[str, ...]:
    """Return the stable IDs of every registered experiment arm."""

    return tuple(arm.arm_id for arm in EXPERIMENT_ARMS)


def resolve_experiment_arm(value: str | ExperimentArm | None) -> ExperimentArm:
    """Resolve a CLI argument or direct value into one experiment arm.

    ``None`` keeps the default FTS arm so existing callers keep their behaviour.
    """

    if isinstance(value, ExperimentArm):
        return value
    if value is None:
        return CURRENT_MEMORY_FTS
    return get_experiment_arm(value)


def arm_manifest_record(arm: ExperimentArm) -> dict[str, object]:
    """Render the arm as the stable block recorded in every run manifest and report."""

    return {
        "id": arm.arm_id,
        "retrieval_strategy": arm.retrieval_strategy,
        "search_mode": arm.search_mode,
        "memory_projection": arm.memory_projection,
        "query_projection": arm.query_projection,
        "prepared_context_max_bytes": arm.prepared_context_max_bytes,
        "temporal_filter": arm.temporal_filter,
        "task_lens": arm.task_lens,
    }


def ensure_comparable_experiment_runs(manifest_a: Mapping[str, object], manifest_b: Mapping[str, object]) -> None:
    """Refuse to compare two runs whose pinned conditions differ beyond the arm.

    Every fair-comparison field must be present in both manifests, both
    ``experiment_arm`` records must be registered arm configurations, and only the arm
    record and run-identity fields may differ.
    """

    differences: list[str] = []
    differences.extend(_unregistered_arm_differences(manifest_a, "first manifest"))
    differences.extend(_unregistered_arm_differences(manifest_b, "second manifest"))
    for path in _COMPARED_MANIFEST_PATHS:
        name = ".".join(path)
        value_a = _manifest_value(manifest_a, path)
        value_b = _manifest_value(manifest_b, path)
        if path not in _NULLABLE_COMPARED_PATHS and (value_a is None or value_b is None):
            differences.append(f"{name} is missing")
        elif value_a != value_b:
            differences.append(name)
    if differences:
        raise ExperimentArmError("runs are not comparable: " + "; ".join(differences))


def _unregistered_arm_differences(manifest: Mapping[str, object], label: str) -> list[str]:
    value = manifest.get("experiment_arm")
    if not isinstance(value, Mapping):
        return [f"{label} has no experiment_arm record"]
    record = {str(key): item for key, item in value.items()}
    if any(record == arm_manifest_record(arm) for arm in EXPERIMENT_ARMS):
        return []
    return [f"{label} has an unregistered experiment_arm record"]


def _manifest_value(manifest: Mapping[str, object], path: tuple[str, ...]) -> Any:
    value: object = manifest
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value
