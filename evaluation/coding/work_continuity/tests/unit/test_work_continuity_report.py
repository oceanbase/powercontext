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

"""The unified work-continuity report.

Two properties carry the issue's acceptance criteria: injected bytes are never
merged into a success number, and the report states in its own words what it is
not evidence about. Both are asserted on the rendered payload and on the
markdown a reader actually sees.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from powercontext_eval_work_continuity.arms import (
    COMPACTED_TRANSCRIPT,
    FULL_TRANSCRIPT,
    INFORMAL_SUMMARY,
    ROLLOVER_HANDOFF,
    TREATMENT_ARM_ID,
)
from powercontext_eval_work_continuity.quality import NEXT_ACTION_REQUIREMENT
from powercontext_eval_work_continuity.report import (
    NOT_A_BENCHMARK_BANNER,
    REPORT_SCHEMA,
    build_report,
    render_markdown,
    report_payload,
)
from powercontext_eval_work_continuity.runner import WorkContinuityRun, run_work_continuity

from .work_continuity_fixtures import analysis_lock, context_digest, write_attempts

HOST = "fixture-host"


def step(number: int, relied_on: list[str], *, performed: str | None = None) -> dict[str, Any]:
    return {
        "step": number,
        "action_text": f"action {number}",
        "relied_on": relied_on,
        "performed_action_id": performed,
    }


def attempt(lock: Path, task_id: str, arm_id: str, *steps: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "arm_id": arm_id,
        "host": HOST,
        "host_revision": f"{HOST}@1",
        "model": "declared-model",
        "context_sha256": context_digest(lock, task_id, arm_id),
        "steps": list(steps),
    }


def full_coverage(lock: Path) -> list[dict[str, Any]]:
    """Every arm on both tasks, with the treatment failing only on t-audit."""

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
def recorded(tmp_path: Path) -> WorkContinuityRun:
    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    return run_work_continuity(task_lock=lock, run_id="report-run", attempts_path=attempts)


@pytest.fixture
def assembly_only(tmp_path: Path) -> WorkContinuityRun:
    return run_work_continuity(task_lock=analysis_lock(tmp_path), run_id="assembly-run")


def block(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload[key]
    assert isinstance(value, dict)
    return value


def rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload[key]
    assert isinstance(value, list)
    for entry in value:
        assert isinstance(entry, dict)
    return value


def sections(markdown: str) -> tuple[str, str]:
    """Split the rendered report into its injected-byte and outcome sections."""

    before, _, after = markdown.partition("## Continuation outcome")
    assert after, "the report must render a continuation-outcome section"
    _, _, byte_section = before.partition("## Injected bytes")
    assert byte_section, "the report must render an injected-bytes section"
    return byte_section, after


def test_injected_bytes_and_task_success_live_in_two_tables_that_are_never_merged(recorded: WorkContinuityRun) -> None:
    byte_section, outcome_section = sections(render_markdown(report_payload(recorded)))

    assert "Total bytes" in byte_section
    assert "Recovered" not in byte_section
    assert "Recovered" in outcome_section
    assert "Total bytes" not in outcome_section
    for arm_id in ("full-transcript-v1", "compacted-transcript-v1", "informal-summary-v1", TREATMENT_ARM_ID):
        assert arm_id in byte_section
        assert arm_id in outcome_section


def test_the_payload_declares_which_measurement_is_which(recorded: WorkContinuityRun) -> None:
    payload = report_payload(recorded)
    separated = block(payload, "separated_measurements")

    assert "never combined with an outcome metric" in str(separated["injected_bytes"])
    assert "success, recovery cost, and conflict counts" in str(separated["outcome"])


def test_the_report_states_that_it_is_not_a_complete_benchmark_result(recorded: WorkContinuityRun) -> None:
    payload = report_payload(recorded)
    markdown = render_markdown(payload)

    assert payload["banner"] == NOT_A_BENCHMARK_BANNER
    assert payload["schema"] == REPORT_SCHEMA
    assert "Not a complete benchmark result" in markdown
    assert "does not establish that Rollover Handoff outperforms" in markdown


def test_the_report_enumerates_its_own_boundaries(recorded: WorkContinuityRun) -> None:
    boundaries = report_payload(recorded)["boundaries"]
    assert isinstance(boundaries, list)
    joined = " ".join(str(boundary) for boundary in boundaries)

    assert "authored fixture material" in joined
    assert "runs no model" in joined
    assert "Injected bytes count the assembled continuation context only" in joined
    assert "one declared next action per task" in joined
    assert HOST in joined


def test_the_boundaries_change_when_no_attempts_were_supplied(assembly_only: WorkContinuityRun) -> None:
    boundaries = report_payload(assembly_only)["boundaries"]
    assert isinstance(boundaries, list)

    assert any("reports assembly only" in str(boundary) for boundary in boundaries)


def test_the_report_names_the_treatment_underperformance_and_the_requirement_it_argues_about(
    recorded: WorkContinuityRun,
) -> None:
    payload = report_payload(recorded)
    failure = block(payload, "failure_analysis")

    assert failure["available"] is True
    assert failure["underperforming_task_ids"] == ["t-audit"]
    entries = failure["treatment_underperformance"]
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    assert entry["host"] == HOST
    assert entry["treatment_failure_class"] == "vague_next_action"
    assert entry["requirement"] == NEXT_ACTION_REQUIREMENT
    assert entry["beaten_by"] == [FULL_TRANSCRIPT.arm_id, COMPACTED_TRANSCRIPT.arm_id]

    markdown = render_markdown(payload)
    assert "### Where the treatment underperformed" in markdown
    assert NEXT_ACTION_REQUIREMENT in markdown


def test_recommendations_use_failure_classes_and_leave_the_recording_gap_out(
    recorded: WorkContinuityRun,
) -> None:
    recommendations = rows(report_payload(recorded), "recommendations")

    assert [entry["failure_class"] for entry in recommendations] == [
        "context_absent",
        "vague_next_action",
    ]
    assert all(entry["requirement"] is not None for entry in recommendations)
    assert all(entry["occurrences"] >= 1 for entry in recommendations)


def test_a_recording_gap_produces_no_recommendation(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    partial = write_attempts(
        tmp_path,
        [attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1"))],
        lock=lock,
    )

    payload = report_payload(run_work_continuity(task_lock=lock, run_id="partial", attempts_path=partial))
    failure = block(payload, "failure_analysis")

    assert rows(payload, "recommendations") == []
    assert failure["unrecorded_keys"]
    assert len(failure["findings"]) == len(failure["unrecorded_keys"])
    assert all(finding["failure_class"] == "no_recording" for finding in failure["findings"])


def test_an_assembly_only_report_says_why_no_outcome_exists(assembly_only: WorkContinuityRun) -> None:
    payload = report_payload(assembly_only)
    failure = block(payload, "failure_analysis")
    markdown = render_markdown(payload)

    assert failure["available"] is False
    assert "no recorded attempts were supplied" in str(failure["reason"])
    assert failure["findings"] == []
    assert "assembly only, no recorded attempt" in markdown


def test_an_assembly_only_report_does_not_claim_unmeasured_coverage_or_comparison(
    assembly_only: WorkContinuityRun,
) -> None:
    """Empty coverage and an empty comparison are absences, not clean bills of health."""

    markdown = render_markdown(report_payload(assembly_only))

    assert "Recording coverage is unavailable" in markdown
    assert "No comparison was performed" in markdown
    assert "Every selected task and method has a recorded attempt." not in markdown
    assert "The treatment did not rank below a baseline for any task and host in this run." not in markdown
    assert "Findings by class: unavailable" in markdown


def test_a_recorded_run_reports_its_coverage_and_comparison(recorded: WorkContinuityRun) -> None:
    markdown = render_markdown(report_payload(recorded))

    assert "Every selected task and method has a recorded attempt." in markdown
    assert "Recording coverage is unavailable" not in markdown
    assert "No comparison was performed" not in markdown


def test_the_report_separates_delivered_quality_from_draft_quality(recorded: WorkContinuityRun) -> None:
    payload = report_payload(recorded)
    methods = {method["arm_id"]: method for method in rows(payload, "methods")}
    treatment = block(methods[TREATMENT_ARM_ID], "context_quality")

    assert treatment["applicable"] is True
    assert treatment["checked_task_count"] == 2
    assert treatment["satisfied_task_count"] == 2
    assert treatment["draft_checked_task_count"] == 2
    assert treatment["draft_satisfied_task_count"] == 2
    # A transcript method carries no rollover draft, so nothing is checked for it.
    # Its zeros are the absence of a check, and the table has to say so rather than
    # render a measured "0 of 0 satisfied".
    baseline = block(methods[FULL_TRANSCRIPT.arm_id], "context_quality")
    assert baseline["applicable"] is False
    assert baseline["checked_task_count"] == 0

    markdown = render_markdown(payload)
    assert "## Delivered context quality" in markdown
    assert f"| `{FULL_TRANSCRIPT.arm_id}` | n/a | n/a | n/a | n/a |" in markdown
    assert f"| `{TREATMENT_ARM_ID}` | 2 | 2 | 2 | 2 |" in markdown


def test_the_report_renders_the_recorded_execution_configuration(recorded: WorkContinuityRun) -> None:
    payload = report_payload(recorded)

    assert payload["execution_configuration"] == [
        {
            "host": HOST,
            "host_revision": f"{HOST}@1",
            "model": "declared-model",
            "attempt_count": 8,
            "tasks": {
                "t-audit": {
                    "compacted-transcript-v1": 1,
                    "full-transcript-v1": 1,
                    "informal-summary-v1": 1,
                    "rollover-handoff-v1": 1,
                },
                "t-doc": {
                    "compacted-transcript-v1": 1,
                    "full-transcript-v1": 1,
                    "informal-summary-v1": 1,
                    "rollover-handoff-v1": 1,
                },
            },
        }
    ]
    markdown = render_markdown(payload)
    assert "## Recorded execution configuration" in markdown
    assert "declared-model" in markdown
    # The share of the recordings and the per-task, per-method split are rendered
    # too, because the comparison gate weighs a configuration by which task under
    # which method it recorded rather than only noticing that it appears somewhere.
    assert (
        "| `fixture-host` | `fixture-host@1` | `declared-model` | 8 | "
        "`t-audit` `compacted-transcript-v1` x1, `full-transcript-v1` x1, `informal-summary-v1` x1, "
        "`rollover-handoff-v1` x1; "
        "`t-doc` `compacted-transcript-v1` x1, `full-transcript-v1` x1, `informal-summary-v1` x1, "
        "`rollover-handoff-v1` x1 |" in markdown
    )


def test_a_recording_without_the_treatment_reports_no_comparison(tmp_path: Path) -> None:
    """The reproduction from review: coverage without a pair to compare.

    Filtering the recording to one baseline and running that arm alone leaves the
    run with an available analysis and no treatment at all, so "the treatment did
    not rank below a baseline" would describe a measurement that never happened.
    """

    lock = analysis_lock(tmp_path)
    entries = [
        attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1", "h2"], performed="c1")),
        attempt(lock, "t-doc", FULL_TRANSCRIPT.arm_id, step(1, ["g1", "g2"], performed="b1")),
    ]
    path = write_attempts(tmp_path, entries, lock=lock)
    run = run_work_continuity(
        task_lock=lock,
        run_id="baseline-only",
        arm_ids=(FULL_TRANSCRIPT.arm_id,),
        attempts_path=path,
    )

    payload = report_payload(run)
    comparison = block(payload, "comparison")
    markdown = render_markdown(payload)

    assert comparison["treatment_selected"] is False
    assert comparison["compared_pair_count"] == 0
    assert "Every selected task and method has a recorded attempt." in markdown
    missing = "No comparison was performed: the treatment arm `rollover-handoff-v1` was not selected in this run."
    assert missing in markdown
    assert "The treatment did not rank below a baseline for any task and host in this run." not in markdown


def test_a_selected_treatment_without_a_recording_reports_no_comparison(tmp_path: Path) -> None:
    """Every arm selected, only the baselines recorded: still nothing to compare."""

    lock = analysis_lock(tmp_path)
    entries = [entry for entry in full_coverage(lock) if entry["arm_id"] != TREATMENT_ARM_ID]
    path = write_attempts(tmp_path, entries, lock=lock)
    run = run_work_continuity(task_lock=lock, run_id="no-treatment-recording", attempts_path=path)

    payload = report_payload(run)
    markdown = render_markdown(payload)

    assert block(payload, "comparison")["treatment_recorded"] is False
    assert "No comparison was performed: the treatment arm `rollover-handoff-v1` has no recorded attempt" in markdown
    assert "The treatment did not rank below a baseline for any task and host in this run." not in markdown


def test_a_treatment_and_baselines_on_different_hosts_report_no_shared_unit(tmp_path: Path) -> None:
    """Both sides recorded, never on one host: the per-host scope is what blocks it."""

    lock = analysis_lock(tmp_path)
    entries = full_coverage(lock)
    for entry in entries:
        if entry["arm_id"] == TREATMENT_ARM_ID:
            entry["host"] = "other-host"
            entry["host_revision"] = "other-host@1"
    path = write_attempts(tmp_path, entries, lock=lock)
    run = run_work_continuity(task_lock=lock, run_id="split-hosts", attempts_path=path)

    payload = report_payload(run)
    markdown = render_markdown(payload)

    assert block(payload, "comparison")["compared_pair_count"] == 0
    assert (
        "No comparison was performed: the treatment and its baselines were never recorded on the same task "
        "and host, and this benchmark only compares one task on one host at a time." in markdown
    )
    assert "The treatment did not rank below a baseline for any task and host in this run." not in markdown


def test_a_treatment_and_a_baseline_on_one_host_but_different_tasks_report_no_comparison(tmp_path: Path) -> None:
    """The reproduction from review: one host is not one comparison.

    The treatment and a baseline are both recorded on the one host, but they cover
    different tasks, so no task ever had both. Counting coverage per host would
    report one comparison and describe an underperformance ranking that was never
    computed.
    """

    lock = analysis_lock(tmp_path)
    entries = [
        attempt(lock, "t-audit", TREATMENT_ARM_ID, step(1, ["h1", "h2"], performed="c1")),
        attempt(lock, "t-doc", FULL_TRANSCRIPT.arm_id, step(1, ["g1", "g2"], performed="b1")),
    ]
    path = write_attempts(tmp_path, entries, lock=lock)
    run = run_work_continuity(task_lock=lock, run_id="split-tasks", attempts_path=path)

    payload = report_payload(run)
    comparison = block(payload, "comparison")
    markdown = render_markdown(payload)

    assert comparison["compared_pairs"] == []
    assert comparison["compared_pair_count"] == 0
    assert comparison["treatment_recorded"] is True
    assert comparison["baselines_recorded"] == [FULL_TRANSCRIPT.arm_id]
    assert (
        "No comparison was performed: the treatment and its baselines were never recorded on the same task "
        "and host, and this benchmark only compares one task on one host at a time." in markdown
    )
    assert "The treatment did not rank below a baseline for any task and host in this run." not in markdown


def test_the_report_writes_json_and_markdown_together(recorded: WorkContinuityRun, tmp_path: Path) -> None:
    target = tmp_path / "report-out"

    result = build_report(recorded, output_dir=target)

    assert result.report_path == target / "report.json"
    assert result.markdown_path == target / "report.md"
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["schema"] == REPORT_SCHEMA
    assert payload["workload"] == "work-continuity"
    assert payload["run_id"] == "report-run"
    assert payload["assembly_max_bytes"] == 16_000
    assert payload["hosts"] == [HOST]
    assert result.markdown_path.read_text(encoding="utf-8").startswith("# Work-continuity report: report-run")


def test_each_task_row_keeps_its_injected_bytes_next_to_its_treatment_outcome(recorded: WorkContinuityRun) -> None:
    tasks = rows(report_payload(recorded), "tasks")
    by_id = {row["task_id"]: row for row in tasks}

    assert set(by_id) == {"t-audit", "t-doc"}
    for row in tasks:
        injected = row["injected_bytes"]
        assert isinstance(injected, dict)
        assert set(injected) == {
            "full-transcript-v1",
            "compacted-transcript-v1",
            "informal-summary-v1",
            "rollover-handoff-v1",
        }
    assert by_id["t-audit"]["underperforming_baselines"] == [
        f"{FULL_TRANSCRIPT.arm_id}@{HOST}",
        f"{COMPACTED_TRANSCRIPT.arm_id}@{HOST}",
    ]
    assert by_id["t-doc"]["underperforming_baselines"] == []


def test_the_report_is_deterministic(recorded: WorkContinuityRun) -> None:
    first = report_payload(recorded)
    second = report_payload(recorded)

    assert first == second
    assert render_markdown(first) == render_markdown(second)


def test_every_arm_appears_once_in_the_method_tables(recorded: WorkContinuityRun) -> None:
    methods = rows(report_payload(recorded), "methods")

    assert [method["arm_id"] for method in methods] == [
        "full-transcript-v1",
        "compacted-transcript-v1",
        "informal-summary-v1",
        "rollover-handoff-v1",
    ]
    for method in methods:
        injected = block(method, "injected_bytes")
        assert injected["mean"] <= injected["max"] <= injected["total"]
        assert injected["truncated_task_count"] <= method["task_count"]
        assert block(method, "outcome")["recorded_attempts"] == method["task_count"]
