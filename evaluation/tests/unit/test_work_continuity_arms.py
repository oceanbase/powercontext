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

"""The continuation-method registry and the comparability gate.

The gate is the only thing standing between a run and a comparison that mixes a
changed byte ceiling or a changed task set into a difference attributed to a
method, so most of these tests exercise refusals rather than successes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from powercontext_eval.benchmarks.work_continuity.arms import (
    BASELINE_ARM_IDS,
    CONTINUATION_ARMS,
    FULL_TRANSCRIPT,
    ROLLOVER_HANDOFF,
    TREATMENT_ARM_ID,
    ContinuationArmError,
    arm_manifest_record,
    ensure_comparable_work_continuity_runs,
    get_continuation_arm,
    resolve_continuation_arms,
    supported_continuation_arm_ids,
)

DIGEST = "a" * 64


def manifest(
    arm_id: str,
    *,
    max_bytes: int = 16_000,
    task_lock_digest: str = DIGEST,
    task_ids: tuple[str, ...] = ("t-coding", "t-doc"),
    powercontext: str = "rev-powercontext",
    integration: str = "rev-integration",
    run_id: str = "run-1",
    host: str = "host-a",
    host_revision: str = "runtime-1",
    model: str = "model-1",
    attempt_count: int = 8,
    tasks: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Build the manifest subset the comparability gate reads.

    The host revision defaults to a value that does not embed the host name, so a
    test that changes only the host name is about the host name. The attempt count
    and the per-task breakdown are part of the block because the gate compares
    which configuration recorded which task, and how many times, not only which
    configurations appear.
    """

    per_task = dict(tasks) if tasks is not None else {task_ids[0]: attempt_count}
    return {
        "run_id": run_id,
        "host": host,
        "task_ids": list(task_ids),
        "inputs": {"task_lock": {"content_sha256": task_lock_digest}},
        "assembly": {"max_bytes": max_bytes},
        "experiment_arm": arm_manifest_record(get_continuation_arm(arm_id), max_bytes=max_bytes),
        "revisions": {"powercontext": powercontext, "integration": integration},
        "execution_configuration": [
            {
                "host": host,
                "host_revision": host_revision,
                "model": model,
                "attempt_count": sum(per_task.values()),
                "tasks": per_task,
            },
        ],
    }


def allocation(
    models: list[str],
    *,
    arm_id: str = "full-transcript-v1",
    attempt_count: int = 8,
    tasks: list[dict[str, int]] | None = None,
) -> dict[str, Any]:
    """One manifest whose hosts are split across `models`, one host per entry.

    ``tasks`` optionally gives each host's per-task recording counts, in the same
    order as ``models``; the default puts every attempt on the first declared task.
    """

    built = manifest(arm_id, attempt_count=attempt_count)
    built["hosts"] = [f"host-{index}" for index in range(len(models))]
    per_host = tasks if tasks is not None else [{"t-coding": attempt_count} for _ in models]
    built["execution_configuration"] = [
        {
            "host": f"host-{index}",
            "host_revision": "runtime-1",
            "model": model,
            "attempt_count": sum(per_host[index].values()),
            "tasks": dict(per_host[index]),
        }
        for index, model in enumerate(models)
    ]
    return built


def test_registry_is_the_paired_comparison_of_three_baselines_and_one_treatment() -> None:
    assert supported_continuation_arm_ids() == (
        "full-transcript-v1",
        "compacted-transcript-v1",
        "informal-summary-v1",
        "rollover-handoff-v1",
    )
    assert BASELINE_ARM_IDS == ("full-transcript-v1", "compacted-transcript-v1", "informal-summary-v1")
    assert TREATMENT_ARM_ID == "rollover-handoff-v1"
    assert ROLLOVER_HANDOFF.is_baseline is False
    assert all(arm.is_baseline for arm in CONTINUATION_ARMS if arm is not ROLLOVER_HANDOFF)


def test_every_baseline_carries_a_transcript_and_the_treatment_carries_state_instead() -> None:
    for arm in CONTINUATION_ARMS:
        if arm.is_baseline:
            assert arm.carries_transcript is True
            assert arm.carries_state_facts is False
            assert arm.carries_next_action is False
    assert ROLLOVER_HANDOFF.carries_transcript is False
    assert ROLLOVER_HANDOFF.carries_state_facts is True
    assert ROLLOVER_HANDOFF.carries_evidence_pointers is True
    assert ROLLOVER_HANDOFF.carries_next_action is True
    assert ROLLOVER_HANDOFF.carries_omissions is True
    assert ROLLOVER_HANDOFF.excludes_superseded is True


def test_the_full_transcript_arm_is_the_only_one_that_drops_nothing() -> None:
    assert FULL_TRANSCRIPT.transcript_tail_turns is None
    assert FULL_TRANSCRIPT.transcript_head_turns == 0
    for arm in CONTINUATION_ARMS:
        if arm is FULL_TRANSCRIPT:
            continue
        assert arm.transcript_tail_turns is not None


def test_an_unknown_arm_fails_with_the_supported_list_rather_than_falling_back() -> None:
    with pytest.raises(ContinuationArmError, match="unknown continuation arm: 'nope'") as error:
        get_continuation_arm("nope")

    assert "full-transcript-v1" in str(error.value)
    assert "rollover-handoff-v1" in str(error.value)


@pytest.mark.parametrize("value", [None, 7, object()])
def test_a_non_string_arm_id_is_unknown_rather_than_a_lookup_error(value: object) -> None:
    with pytest.raises(ContinuationArmError, match="unknown continuation arm"):
        get_continuation_arm(value)  # type: ignore[arg-type]


def test_resolving_nothing_selects_the_whole_paired_comparison_in_registry_order() -> None:
    assert tuple(arm.arm_id for arm in resolve_continuation_arms(None)) == supported_continuation_arm_ids()


def test_resolving_returns_registry_order_and_rejects_a_duplicate_selection() -> None:
    selected = resolve_continuation_arms(("rollover-handoff-v1", "full-transcript-v1"))

    assert tuple(arm.arm_id for arm in selected) == ("full-transcript-v1", "rollover-handoff-v1")
    with pytest.raises(ContinuationArmError, match="duplicate continuation arm selection: 'full-transcript-v1'"):
        resolve_continuation_arms(("full-transcript-v1", "full-transcript-v1"))


def test_arm_manifest_record_pins_the_budget_alongside_the_method() -> None:
    compacted = get_continuation_arm("compacted-transcript-v1")

    record = arm_manifest_record(compacted, max_bytes=4_096)

    assert record["id"] == compacted.arm_id
    assert record["method"] == "compacted-transcript"
    assert record["transcript_head_turns"] == 2
    assert record["transcript_tail_turns"] == 4
    assert record["assembly_max_bytes"] == 4_096


def test_manifests_differing_only_by_the_arm_are_comparable() -> None:
    ensure_comparable_work_continuity_runs(manifest("full-transcript-v1"), manifest("rollover-handoff-v1"))


def test_run_identity_and_the_host_are_outside_the_comparison() -> None:
    """Two host integrations must remain comparable, so the host is not a compared field."""

    ensure_comparable_work_continuity_runs(
        manifest("rollover-handoff-v1", run_id="run-a", host="host-a"),
        manifest("rollover-handoff-v1", run_id="run-b", host="host-b"),
    )


def test_a_run_whose_hosts_ran_different_configurations_is_not_comparable() -> None:
    """A model or runtime difference is not a method difference.

    Comparing such runs would attribute the configuration change to the
    continuation method, and the tampered run used to pass this gate.
    """

    with pytest.raises(ContinuationArmError, match="execution_configuration"):
        ensure_comparable_work_continuity_runs(
            manifest("rollover-handoff-v1", host="host-a"),
            manifest("rollover-handoff-v1", host="host-a", model="model-2"),
        )

    with pytest.raises(ContinuationArmError, match="execution_configuration"):
        ensure_comparable_work_continuity_runs(
            manifest("rollover-handoff-v1"),
            manifest("rollover-handoff-v1", host="host-b", host_revision="runtime-2"),
        )


def test_a_manifest_without_an_execution_configuration_is_not_comparable() -> None:
    forged = manifest("rollover-handoff-v1")
    del forged["execution_configuration"]

    with pytest.raises(ContinuationArmError, match="execution_configuration is missing"):
        ensure_comparable_work_continuity_runs(manifest("full-transcript-v1"), forged)


def test_a_changed_configuration_allocation_is_not_comparable() -> None:
    """The same two models, allocated differently, is not a comparison.

    One run gave model A a single host and model B nine; the other reversed that.
    Both list the same set of configurations, so a set comparison accepts them,
    while the mix alone moves the success counts by a wide margin.
    """

    one_of_ten = allocation(["model-a"] + ["model-b"] * 9)
    nine_of_ten = allocation(["model-a"] * 9 + ["model-b"])

    with pytest.raises(ContinuationArmError, match="execution_configuration allocation") as error:
        ensure_comparable_work_continuity_runs(one_of_ten, nine_of_ten)

    message = str(error.value)
    assert "runtime-1/model-a (t-coding x8)" in message
    assert "runtime-1/model-a (t-coding x72)" in message


def test_the_same_configuration_allocation_is_comparable_under_renamed_hosts() -> None:
    """Host names stay outside the comparison, so only the allocation has to match."""

    first = allocation(["model-a", "model-b"])
    second = allocation(["model-a", "model-b"])
    second["hosts"] = ["integration-one", "integration-two"]
    for index, entry in enumerate(second["execution_configuration"]):  # type: ignore[union-attr]
        entry["host"] = ["integration-one", "integration-two"][index]

    ensure_comparable_work_continuity_runs(first, second)


def test_a_manifest_whose_entries_lost_their_attempt_counts_is_not_comparable() -> None:
    """A block that cannot show the allocation is refused, not assumed equal."""

    forged = allocation(["model-a"])
    del forged["execution_configuration"][0]["attempt_count"]  # type: ignore[index]

    with pytest.raises(ContinuationArmError, match="execution_configuration is missing or malformed"):
        ensure_comparable_work_continuity_runs(allocation(["model-a"]), forged)


def test_a_manifest_whose_task_breakdown_does_not_add_up_is_not_comparable() -> None:
    """A block whose parts disagree with its total cannot state the allocation."""

    forged = allocation(["model-a"])
    forged["execution_configuration"][0]["tasks"] = {"t-coding": 3}  # type: ignore[index]

    with pytest.raises(ContinuationArmError, match="execution_configuration is missing or malformed"):
        ensure_comparable_work_continuity_runs(allocation(["model-a"]), forged)


def test_weighting_the_attempts_differently_across_the_same_hosts_is_not_comparable() -> None:
    """Two hosts on two models, but the recordings sit mostly on the other one."""

    first = allocation(["model-a", "model-b"], attempt_count=4)
    second = allocation(["model-a", "model-b"], attempt_count=4)
    second["execution_configuration"][0]["attempt_count"] = 20  # type: ignore[index]
    second["execution_configuration"][0]["tasks"] = {"t-coding": 20}  # type: ignore[index]
    second["execution_configuration"][1]["attempt_count"] = 2  # type: ignore[index]
    second["execution_configuration"][1]["tasks"] = {"t-coding": 2}  # type: ignore[index]

    with pytest.raises(ContinuationArmError, match="execution_configuration allocation"):
        ensure_comparable_work_continuity_runs(first, second)


def test_sending_a_task_to_another_model_is_not_comparable() -> None:
    """Equal totals still hide which task each configuration recorded.

    A task is scored on its own declared next action, so two runs that agree on
    every host, model, revision and total can still move success counts purely by
    handing the same task to the other model.
    """

    first = allocation(["model-a", "model-b"], attempt_count=1, tasks=[{"t-coding": 1}, {"t-doc": 1}])
    second = allocation(["model-a", "model-b"], attempt_count=1, tasks=[{"t-doc": 1}, {"t-coding": 1}])

    with pytest.raises(ContinuationArmError, match="execution_configuration allocation") as error:
        ensure_comparable_work_continuity_runs(first, second)

    message = str(error.value)
    assert "runtime-1/model-a (t-coding x1)" in message
    assert "runtime-1/model-a (t-doc x1)" in message


def test_the_same_per_task_allocation_is_comparable_under_renamed_hosts() -> None:
    """Host names stay outside the comparison even when tasks are split across hosts."""

    first = allocation(["model-a", "model-b"], attempt_count=1, tasks=[{"t-coding": 1}, {"t-doc": 1}])
    second = allocation(["model-a", "model-b"], attempt_count=1, tasks=[{"t-coding": 1}, {"t-doc": 1}])
    second["hosts"] = ["integration-one", "integration-two"]
    for index, entry in enumerate(second["execution_configuration"]):  # type: ignore[union-attr]
        entry["host"] = ["integration-one", "integration-two"][index]

    ensure_comparable_work_continuity_runs(first, second)


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        (manifest("rollover-handoff-v1", max_bytes=99_999), "assembly.max_bytes"),
        (manifest("rollover-handoff-v1", task_lock_digest="b" * 64), "inputs.task_lock.content_sha256"),
        (manifest("rollover-handoff-v1", task_ids=("t-doc",)), "task_ids"),
        (manifest("rollover-handoff-v1", powercontext="rev-other"), "revisions.powercontext"),
        (manifest("rollover-handoff-v1", integration="rev-other"), "revisions.integration"),
    ],
)
def test_a_difference_outside_the_arm_refuses_the_comparison(second: dict[str, Any], expected: str) -> None:
    with pytest.raises(ContinuationArmError, match="runs are not comparable") as error:
        ensure_comparable_work_continuity_runs(manifest("full-transcript-v1"), second)

    assert expected in str(error.value)


def test_a_difference_in_the_byte_ceiling_alone_is_still_refused() -> None:
    """The budget is protocol, so an arm that injected under a smaller ceiling is not comparable."""

    first = manifest("full-transcript-v1", max_bytes=16_000)
    second = manifest("full-transcript-v1", max_bytes=16_000)
    second["assembly"] = {"max_bytes": 8_000}

    with pytest.raises(ContinuationArmError, match="assembly.max_bytes"):
        ensure_comparable_work_continuity_runs(first, second)


def test_a_missing_compared_field_is_reported_as_missing() -> None:
    second = manifest("rollover-handoff-v1")
    del second["task_ids"]

    with pytest.raises(ContinuationArmError, match="task_ids is missing"):
        ensure_comparable_work_continuity_runs(manifest("full-transcript-v1"), second)


def test_an_unregistered_arm_record_is_refused_on_either_side() -> None:
    forged = manifest("rollover-handoff-v1")
    record = dict(forged["experiment_arm"])  # type: ignore[arg-type]
    record["carries_omissions"] = False
    forged["experiment_arm"] = record

    with pytest.raises(ContinuationArmError, match="second manifest has an unregistered experiment_arm record"):
        ensure_comparable_work_continuity_runs(manifest("full-transcript-v1"), forged)

    with pytest.raises(ContinuationArmError, match="first manifest has an unregistered experiment_arm record"):
        ensure_comparable_work_continuity_runs(forged, manifest("full-transcript-v1"))


def test_a_manifest_without_an_arm_record_is_refused() -> None:
    forged = manifest("rollover-handoff-v1")
    del forged["experiment_arm"]

    with pytest.raises(ContinuationArmError, match="has no experiment_arm record"):
        ensure_comparable_work_continuity_runs(forged, manifest("full-transcript-v1"))
