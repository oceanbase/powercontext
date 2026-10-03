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

"""Orchestration for one work-continuity run.

The run has two halves that must stay separable: assembly always works and needs
no host, while scoring needs recorded attempts. These tests pin that split, the
manifest a reader reproduces a run from, and the refusals that stop a run from
looking complete when it is not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from work_continuity_fixtures import (
    analysis_lock,
    audit_task,
    context_digest,
    documentation_task,
    write_attempts,
    write_lock,
)

from powercontext_eval.benchmarks.work_continuity.analysis import FAILURE_CLASSES, NO_RECORDING
from powercontext_eval.benchmarks.work_continuity.arms import (
    COMPACTED_TRANSCRIPT,
    FULL_TRANSCRIPT,
    INFORMAL_SUMMARY,
    ROLLOVER_HANDOFF,
    supported_continuation_arm_ids,
)
from powercontext_eval.benchmarks.work_continuity.catalog import TaskCatalog
from powercontext_eval.benchmarks.work_continuity.runner import (
    ASSEMBLY_ONLY_CLASSIFICATION,
    RECORDED_CLASSIFICATION,
    RUN_MANIFEST_SCHEMA,
    WorkContinuityRun,
    WorkContinuityRunError,
    run_summary,
    run_work_continuity,
    write_run_artifacts,
)

HOST = "fixture-host"
ARMS = (FULL_TRANSCRIPT, COMPACTED_TRANSCRIPT, INFORMAL_SUMMARY, ROLLOVER_HANDOFF)


def step(number: int, relied_on: list[str], *, performed: str | None = None) -> dict[str, Any]:
    return {
        "step": number,
        "action_text": f"action {number}",
        "relied_on": relied_on,
        "performed_action_id": performed,
    }


def attempt(lock: Path, task_id: str, arm_id: str, *steps: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "task_id": task_id,
        "arm_id": arm_id,
        "host": HOST,
        "host_revision": f"{HOST}@1",
        "model": "declared-model",
        "context_sha256": context_digest(lock, task_id, arm_id),
        "steps": list(steps),
    }
    entry.update(overrides)
    return entry


def attempts_entries(lock: Path) -> list[dict[str, Any]]:
    """Cover every arm on both tasks, with one deliberate treatment failure."""

    return [
        attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1")),
        attempt(lock, "t-audit", COMPACTED_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1")),
        attempt(lock, "t-audit", INFORMAL_SUMMARY.arm_id, step(1, ["h2"])),
        attempt(lock, "t-audit", ROLLOVER_HANDOFF.arm_id, step(1, ["h1"])),
        attempt(lock, "t-doc", FULL_TRANSCRIPT.arm_id, step(1, ["g1", "g2"], performed="b1")),
        attempt(lock, "t-doc", COMPACTED_TRANSCRIPT.arm_id, step(1, ["g1", "g2"], performed="b1")),
        attempt(lock, "t-doc", INFORMAL_SUMMARY.arm_id, step(1, ["g2"])),
        attempt(lock, "t-doc", ROLLOVER_HANDOFF.arm_id, step(1, ["g1", "g2"], performed="b1")),
    ]


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return analysis_lock(tmp_path)


@pytest.fixture
def attempts(tmp_path: Path, lock: Path) -> Path:
    return write_attempts(tmp_path, attempts_entries(lock), lock=lock)


def recorded_run(lock: Path, attempts: Path, **overrides: Any) -> WorkContinuityRun:
    return run_work_continuity(task_lock=lock, run_id="run-1", attempts_path=attempts, **overrides)


def summary_arms(run: WorkContinuityRun) -> dict[str, dict[str, Any]]:
    """Return the per-arm summary blocks keyed by arm id."""

    arms = run_summary(run)["arms"]
    assert isinstance(arms, list)
    entries: dict[str, dict[str, Any]] = {}
    for entry in arms:
        assert isinstance(entry, dict)
        arm_id = entry["arm_id"]
        assert isinstance(arm_id, str)
        entries[arm_id] = entry
    return entries


def nested(block: dict[str, Any], key: str) -> dict[str, Any]:
    value = block[key]
    assert isinstance(value, dict)
    return value


def write_isolated_attempts(tmp_path: Path, lock: Path, name: str, entries: list[dict[str, Any]]) -> Path:
    """Write an attempts artifact into its own directory so fixtures never collide."""

    directory = tmp_path / name
    directory.mkdir()
    return write_attempts(directory, entries, lock=lock)


def test_an_assembly_only_run_needs_no_attempts(lock: Path) -> None:
    run = run_work_continuity(task_lock=lock, run_id="assembly-only")

    assert run.classification == ASSEMBLY_ONLY_CLASSIFICATION
    assert run.analysis is None
    assert run.scores == ()
    assert run.hosts == ()
    assert len(run.contexts) == len(run.tasks) * len(supported_continuation_arm_ids())


def test_a_recorded_run_classifies_itself_as_incomplete_on_purpose(lock: Path, attempts: Path) -> None:
    run = recorded_run(lock, attempts)

    assert run.classification == RECORDED_CLASSIFICATION
    assert run.classification != "complete-benchmark-result"
    assert run.analysis is not None
    assert run.hosts == (HOST,)
    assert len(run.scores) == len(attempts_entries(lock))


def test_the_manifest_pins_every_input_a_reader_needs_to_reproduce_the_run(lock: Path, attempts: Path) -> None:
    manifest = recorded_run(lock, attempts).manifest
    catalog = TaskCatalog.load(lock)

    assert manifest["schema"] == RUN_MANIFEST_SCHEMA
    assert manifest["workload"] == "work-continuity"
    assert manifest["run_id"] == "run-1"
    assert manifest["task_set_id"] == catalog.task_set_id
    assert manifest["task_ids"] == ["t-audit", "t-doc"]
    assert manifest["assembly"] == {"max_bytes": 16_000}
    assert manifest["hosts"] == [HOST]
    assert len(str(manifest["task_evidence_digest"])) == 64

    inputs = nested(manifest, "inputs")
    assert nested(inputs, "task_lock")["content_sha256"] == catalog.content_sha256
    recorded_input = nested(inputs, "attempts")
    assert recorded_input["path"] == attempts.name
    assert recorded_input["protocol"] == {
        "task_set_id": catalog.task_set_id,
        "task_lock_sha256": catalog.content_sha256,
        "assembly_max_bytes": 16_000,
    }

    comparable = manifest["comparable_arms"]
    assert isinstance(comparable, list)
    assert [record["id"] for record in comparable] == [arm.arm_id for arm in ARMS]


def test_the_run_retains_each_recorded_hosts_execution_configuration(lock: Path, attempts: Path) -> None:
    """The configuration, its share, and its per-task split are kept, not only validated."""

    expected = [
        {
            "host": HOST,
            "host_revision": f"{HOST}@1",
            "model": "declared-model",
            "attempt_count": 8,
            "tasks": {"t-audit": 4, "t-doc": 4},
        },
    ]

    assert recorded_run(lock, attempts).manifest["execution_configuration"] == expected
    assert run_summary(recorded_run(lock, attempts))["execution_configuration"] == expected


def test_the_summary_states_whether_a_comparison_was_actually_held(lock: Path, attempts: Path) -> None:
    """Coverage and comparison are different questions, and the summary answers both."""

    comparison = run_summary(recorded_run(lock, attempts))["comparison"]
    assert isinstance(comparison, dict)
    assert comparison["treatment_arm_id"] == ROLLOVER_HANDOFF.arm_id
    assert comparison["treatment_selected"] is True
    assert comparison["treatment_recorded"] is True
    assert comparison["baselines_recorded"] == [
        FULL_TRANSCRIPT.arm_id,
        COMPACTED_TRANSCRIPT.arm_id,
        INFORMAL_SUMMARY.arm_id,
    ]
    assert comparison["compared_pairs"] == [["t-audit", HOST], ["t-doc", HOST]]
    assert comparison["compared_pair_count"] == 2


def test_an_assembly_only_run_holds_no_comparison(tmp_path: Path) -> None:
    run = run_work_continuity(task_lock=analysis_lock(tmp_path), run_id="assembly-only")
    comparison = run_summary(run)["comparison"]
    assert isinstance(comparison, dict)

    assert comparison["treatment_selected"] is True
    assert comparison["treatment_recorded"] is False
    assert comparison["baselines_recorded"] == []
    assert comparison["compared_pair_count"] == 0


def test_a_recording_without_the_treatment_holds_no_comparison(lock: Path, tmp_path: Path) -> None:
    """Every baseline recorded and no treatment is coverage without a comparison."""

    entries = [entry for entry in attempts_entries(lock) if entry["arm_id"] != ROLLOVER_HANDOFF.arm_id]
    path = write_isolated_attempts(tmp_path, lock, "baselines-only", entries)

    run = recorded_run(lock, path)
    comparison = run_summary(run)["comparison"]
    assert isinstance(comparison, dict)

    assert comparison["treatment_selected"] is True
    assert comparison["treatment_recorded"] is False
    assert comparison["compared_pair_count"] == 0


def test_a_treatment_and_a_baseline_on_different_hosts_hold_no_comparison(lock: Path, tmp_path: Path) -> None:
    """Comparisons are scoped per host, so two hosts are not one comparison."""

    entries = [
        attempt(lock, "t-audit", ROLLOVER_HANDOFF.arm_id, step(1, ["h1", "h2"], performed="c1"), host="host-b"),
        attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1")),
    ]
    path = write_isolated_attempts(tmp_path, lock, "split-hosts", entries)

    comparison = run_summary(recorded_run(lock, path))["comparison"]
    assert isinstance(comparison, dict)

    assert comparison["treatment_recorded"] is True
    assert comparison["baselines_recorded"] == [FULL_TRANSCRIPT.arm_id]
    assert comparison["compared_pair_count"] == 0


def test_a_treatment_and_a_baseline_on_the_same_host_but_different_tasks_hold_no_comparison(
    lock: Path, tmp_path: Path
) -> None:
    """Coverage is read per task and host, so a host that split the tasks is not a comparison.

    Grouping only by host would report this run as holding one comparison, although
    no task ever had both the treatment and a baseline recorded for it.
    """

    entries = [
        attempt(lock, "t-audit", ROLLOVER_HANDOFF.arm_id, step(1, ["h1", "h2"], performed="c1")),
        attempt(lock, "t-doc", FULL_TRANSCRIPT.arm_id, step(1, ["g1", "g2"], performed="b1")),
    ]
    path = write_isolated_attempts(tmp_path, lock, "split-tasks", entries)

    comparison = run_summary(recorded_run(lock, path))["comparison"]
    assert isinstance(comparison, dict)

    assert comparison["treatment_recorded"] is True
    assert comparison["baselines_recorded"] == [FULL_TRANSCRIPT.arm_id]
    assert comparison["compared_pairs"] == []
    assert comparison["compared_pair_count"] == 0


def test_a_run_refuses_recordings_made_under_a_different_byte_ceiling(lock: Path, attempts: Path) -> None:
    """The reproduction from review: reusing a recording under another ceiling.

    Contexts are a deterministic function of the task lock and the byte ceiling, so
    a recording made at the default ceiling says nothing about a one-byte one. It
    used to be scored against it anyway, reporting successes for contexts that held
    a single truncated header.
    """

    with pytest.raises(WorkContinuityRunError, match="made under a 16000 byte ceiling"):
        recorded_run(lock, attempts, max_bytes=1)


def test_a_run_refuses_recordings_made_against_a_different_task_lock(
    lock: Path, attempts: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other-lock"
    other.mkdir()
    revised = audit_task()
    revised["objective"] = "Audit the retry helper, revised."
    changed = write_lock(other, [revised, documentation_task()])

    with pytest.raises(WorkContinuityRunError, match="different task lock"):
        recorded_run(changed, attempts)


def test_a_run_refuses_a_recording_bound_to_a_different_context(lock: Path, tmp_path: Path) -> None:
    entries = [
        attempt(
            lock,
            "t-audit",
            FULL_TRANSCRIPT.arm_id,
            step(1, ["h1", "h2"], performed="c1"),
            context_sha256="b" * 64,
        )
    ]
    path = write_isolated_attempts(tmp_path, lock, "rebound", entries)

    with pytest.raises(WorkContinuityRunError, match="but this run delivers"):
        run_work_continuity(task_lock=lock, run_id="rebound", attempts_path=path)


def test_the_manifest_names_the_ground_truth_it_scored_against(lock: Path, attempts: Path, tmp_path: Path) -> None:
    whole = recorded_run(lock, attempts).manifest["task_evidence_digest"]
    narrow = write_isolated_attempts(
        tmp_path,
        lock,
        "ground-truth",
        [attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1"))],
    )

    narrowed = run_work_continuity(
        task_lock=lock, run_id="run-2", task_ids=("t-audit",), attempts_path=narrow
    ).manifest["task_evidence_digest"]

    assert whole != narrowed


def test_the_run_summary_keeps_injected_bytes_and_outcome_in_separate_blocks(lock: Path, attempts: Path) -> None:
    for entry in summary_arms(recorded_run(lock, attempts)).values():
        injected = nested(entry, "injected_bytes")
        outcome = nested(entry, "outcome")

        assert injected["total"] > 0
        assert set(outcome) == {
            "recorded_attempts",
            "task_success",
            "incorrect_assumptions",
            "missing_evidence",
            "unverifiable_claims",
            "user_correction_burden",
            "mean_time_to_recover_state",
        }
        # No outcome metric is derived from a byte count.
        assert "injected_bytes" not in outcome


def test_the_run_reports_the_treatment_as_underperforming_on_exactly_one_task(lock: Path, attempts: Path) -> None:
    run = recorded_run(lock, attempts)
    assert run.analysis is not None

    assert run.analysis.underperforming_task_ids == ("t-audit",)
    entry = run.analysis.underperformance[0]
    assert (entry.task_id, entry.host) == ("t-audit", HOST)
    assert entry.beaten_by == (FULL_TRANSCRIPT.arm_id, COMPACTED_TRANSCRIPT.arm_id)
    assert entry.treatment_failure_class == "vague_next_action"


def test_an_assembly_only_run_has_no_failure_block(lock: Path) -> None:
    run = run_work_continuity(task_lock=lock, run_id="assembly-only")
    summary = run_summary(run)

    assert "failure_analysis" not in summary
    for entry in summary_arms(run).values():
        assert nested(entry, "outcome")["recorded_attempts"] == 0


def test_a_smaller_context_is_not_thereby_a_better_continuation(lock: Path, attempts: Path) -> None:
    """The handoff injects fewer bytes than the full transcript and still loses on t-audit."""

    arms = summary_arms(recorded_run(lock, attempts))
    handoff = arms[ROLLOVER_HANDOFF.arm_id]
    full = arms[FULL_TRANSCRIPT.arm_id]

    assert nested(handoff, "injected_bytes")["total"] < nested(full, "injected_bytes")["total"]
    assert nested(handoff, "outcome")["task_success"] < nested(full, "outcome")["task_success"]


def test_run_artifacts_are_written_into_a_fresh_directory(lock: Path, attempts: Path, tmp_path: Path) -> None:
    run = recorded_run(lock, attempts)
    target = tmp_path / "run-out"

    artifacts = write_run_artifacts(run, target)

    assert artifacts.output_dir == target
    for path in (artifacts.manifest_path, artifacts.assembly_path, artifacts.scores_path, artifacts.summary_path):
        assert path.exists() and path.stat().st_size > 0

    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == RUN_MANIFEST_SCHEMA
    rows = [json.loads(line) for line in artifacts.assembly_path.read_text(encoding="utf-8").splitlines()]
    assert {row["arm_id"] for row in rows} == set(supported_continuation_arm_ids())
    scored = [json.loads(line) for line in artifacts.scores_path.read_text(encoding="utf-8").splitlines()]
    assert len(scored) == len(attempts_entries(lock))


def test_writing_run_artifacts_refuses_to_overwrite_an_existing_directory(
    lock: Path, attempts: Path, tmp_path: Path
) -> None:
    run = recorded_run(lock, attempts)
    target = tmp_path / "run-out"
    write_run_artifacts(run, target)

    with pytest.raises(WorkContinuityRunError, match="output directory already exists"):
        write_run_artifacts(run, target)


def test_a_run_refuses_attempts_that_name_a_task_it_did_not_select(lock: Path, attempts: Path) -> None:
    with pytest.raises(WorkContinuityRunError, match=r"attempts name task t-doc but the run selects \['t-audit'\]"):
        run_work_continuity(task_lock=lock, run_id="narrow", task_ids=("t-audit",), attempts_path=attempts)


def test_a_run_refuses_attempts_that_name_an_arm_it_did_not_select(lock: Path, attempts: Path) -> None:
    with pytest.raises(
        WorkContinuityRunError,
        match=r"attempts name arm compacted-transcript-v1 but the run selects \['full-transcript-v1'\]",
    ):
        run_work_continuity(
            task_lock=lock,
            run_id="narrow",
            arm_ids=(FULL_TRANSCRIPT.arm_id,),
            attempts_path=attempts,
        )


def test_an_unrecorded_combination_is_a_recording_gap_rather_than_a_failure(lock: Path, tmp_path: Path) -> None:
    partial = write_attempts(
        tmp_path,
        [attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1"))],
        lock=lock,
    )

    run = run_work_continuity(task_lock=lock, run_id="partial", attempts_path=partial)
    assert run.analysis is not None

    assert run.analysis.unrecorded_keys
    assert {finding.failure_class for finding in run.analysis.findings} == {NO_RECORDING}
    # A recording gap is deliberately kept out of the failure-class histogram, so
    # an incomplete run never reads as a finding about a method.
    assert set(run.analysis.findings_by_class) == set(FAILURE_CLASSES)
    assert sum(run.analysis.findings_by_class.values()) == 0


def test_selecting_one_arm_and_one_task_narrows_the_run(lock: Path, tmp_path: Path) -> None:
    narrow = write_isolated_attempts(
        tmp_path,
        lock,
        "narrow",
        [attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1"))],
    )

    run = run_work_continuity(
        task_lock=lock,
        run_id="narrow",
        arm_ids=(FULL_TRANSCRIPT.arm_id,),
        task_ids=("t-audit",),
        attempts_path=narrow,
    )

    assert [context.task_id for context in run.contexts] == ["t-audit"]
    assert [context.arm_id for context in run.contexts] == [FULL_TRANSCRIPT.arm_id]


@pytest.mark.parametrize("max_bytes", [0, -5])
def test_a_non_positive_budget_is_refused(lock: Path, max_bytes: int) -> None:
    with pytest.raises(WorkContinuityRunError, match="assembly budget must be positive"):
        run_work_continuity(task_lock=lock, run_id="bad", max_bytes=max_bytes)


def test_the_context_and_task_accessors_report_unknown_identifiers(lock: Path) -> None:
    run = run_work_continuity(task_lock=lock, run_id="assembly-only")

    with pytest.raises(WorkContinuityRunError, match="no assembled context for task t-audit arm nope"):
        run.context("t-audit", "nope")
    with pytest.raises(WorkContinuityRunError, match="no selected task nope"):
        run.task("nope")


def test_revisions_are_recorded_verbatim_when_supplied(lock: Path) -> None:
    run = run_work_continuity(
        task_lock=lock,
        run_id="revisions",
        powercontext_revision="pc-abc",
        integration_revision="int-def",
    )

    assert run.manifest["revisions"] == {"powercontext": "pc-abc", "integration": "int-def"}
