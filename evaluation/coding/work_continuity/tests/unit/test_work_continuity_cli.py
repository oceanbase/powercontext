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

"""The `work-continuity` CLI subcommands.

The commands are the only supported entry point, so these tests drive them the
way a contributor would: validate first, then run, and read the JSON the command
prints to stdout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from powercontext_eval_work_continuity.arms import (
    COMPACTED_TRANSCRIPT,
    FULL_TRANSCRIPT,
    INFORMAL_SUMMARY,
    ROLLOVER_HANDOFF,
    supported_continuation_arm_ids,
)
from powercontext_eval_work_continuity.cli import app

from .work_continuity_fixtures import analysis_lock, context_digest, write_attempts

HOST = "fixture-host"


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


def full_coverage(lock: Path) -> list[dict[str, Any]]:
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


def payload(result: Any) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output.strip().splitlines()[-1])
    assert isinstance(parsed, dict)
    return parsed


def test_validate_describes_the_pinned_inputs_without_writing_anything(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)

    body = payload(CliRunner().invoke(app, ["validate", "--task-lock", str(lock)]))

    assert body["classification"] == "work-continuity-input-validation"
    assert body["task_set_id"] == "test-set"
    assert body["task_count"] == 2
    assert body["task_ids"] == ["t-audit", "t-doc"]
    assert len(body["task_lock_sha256"]) == 64
    assert body["attempt_count"] is None
    assert body["hosts"] is None
    assert body["supported_arms"] == [
        "full-transcript-v1",
        "compacted-transcript-v1",
        "informal-summary-v1",
        "rollover-handoff-v1",
    ]
    assert sorted(candidate.name for candidate in tmp_path.iterdir()) == ["tasks.json"]


def test_validate_also_checks_recorded_attempts(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)

    body = payload(
        CliRunner().invoke(
            app,
            ["validate", "--task-lock", str(lock), "--attempts", str(attempts)],
        )
    )

    assert body["attempt_count"] == len(full_coverage(lock))
    assert body["hosts"] == [HOST]
    # The protocol and the execution configuration are surfaced, not only validated.
    assert body["recording_protocol"]["task_lock_sha256"] == body["task_lock_sha256"]
    assert body["recording_protocol"]["assembly_max_bytes"] == 16_000
    assert body["execution_configuration"] == [
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


def test_validate_reports_an_unusable_lock_without_a_traceback(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["validate", "--task-lock", str(tmp_path / "absent.json")])

    assert result.exit_code == 1
    assert "Work-continuity validation failed" in result.output
    assert not isinstance(result.exception, json.JSONDecodeError)


def test_validate_rejects_an_attempts_artifact_that_cannot_be_scored(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    entry = attempt(lock, "t-audit", FULL_TRANSCRIPT.arm_id, step(1, ["h1"]))
    entry["arm_id"] = "nope"
    attempts = write_attempts(tmp_path, [entry], lock=lock)

    result = CliRunner().invoke(app, ["validate", "--task-lock", str(lock), "--attempts", str(attempts)])

    assert result.exit_code == 2
    assert "unknown continuation arm" in result.output


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_set_id", "another-task-set"),
        ("task_lock_sha256", "b" * 64),
    ],
)
def test_validate_rejects_a_recording_bound_to_another_protocol(tmp_path: Path, field: str, value: str) -> None:
    """The documented preflight must reject what `run` rejects.

    The artifact stays well formed — a plausible task set, a hex digest — so only
    the binding check can tell that these recordings cannot be scored here.
    """

    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    document = json.loads(attempts.read_text(encoding="utf-8"))
    document["protocol"][field] = value
    attempts.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", "--task-lock", str(lock), "--attempts", str(attempts)])

    assert result.exit_code == 1
    assert "Work-continuity validation failed" in result.output


def test_validate_rejects_a_context_digest_this_project_cannot_assemble(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    document = json.loads(attempts.read_text(encoding="utf-8"))
    document["attempts"][0]["context_sha256"] = "c" * 64
    attempts.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", "--task-lock", str(lock), "--attempts", str(attempts)])

    assert result.exit_code == 1
    assert "Work-continuity validation failed" in result.output
    assert "declares context" in result.output


def test_run_assembles_scores_and_writes_one_run_directory(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    output = tmp_path / "run"

    body = payload(
        CliRunner().invoke(
            app,
            [
                "run",
                "--task-lock",
                str(lock),
                "--attempts",
                str(attempts),
                "--output-dir",
                str(output),
                "--run-id",
                "cli-run",
                "--powercontext-revision",
                "pc-1",
            ],
        )
    )

    assert body["classification"] == "recorded-attempts-not-a-complete-benchmark-result"
    assert body["run_id"] == "cli-run"
    for name in (
        "run-manifest.json",
        "assembly.jsonl",
        "scores.jsonl",
        "run-summary.json",
        "report.json",
        "report.md",
    ):
        assert (output / name).exists(), name

    methods = body["methods"]
    assert isinstance(methods, list)
    by_arm = {entry["arm_id"]: entry for entry in methods}
    assert set(by_arm) == {
        "full-transcript-v1",
        "compacted-transcript-v1",
        "informal-summary-v1",
        "rollover-handoff-v1",
    }
    # A method that injected fewer bytes is not thereby reported as more successful.
    assert (
        by_arm[ROLLOVER_HANDOFF.arm_id]["injected_bytes_total"] < by_arm[FULL_TRANSCRIPT.arm_id]["injected_bytes_total"]
    )
    assert by_arm[ROLLOVER_HANDOFF.arm_id]["task_success"] < by_arm[FULL_TRANSCRIPT.arm_id]["task_success"]

    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["failure_analysis"]["underperforming_task_ids"] == ["t-audit"]


def test_run_assembles_without_attempts_and_says_so(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    output = tmp_path / "assembly-only"

    body = payload(
        CliRunner().invoke(
            app,
            [
                "run",
                "--task-lock",
                str(lock),
                "--output-dir",
                str(output),
                "--run-id",
                "assembly-only",
            ],
        )
    )

    assert body["classification"] == "assembly-only-no-recorded-attempts"
    assert (output / "report.md").read_text(encoding="utf-8").count("assembly only, no recorded attempt") >= 1


def test_run_narrows_to_one_arm_and_one_task(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    output = tmp_path / "narrow"

    body = payload(
        CliRunner().invoke(
            app,
            [
                "run",
                "--task-lock",
                str(lock),
                "--output-dir",
                str(output),
                "--run-id",
                "narrow",
                "--arm",
                FULL_TRANSCRIPT.arm_id,
                "--task-id",
                "t-audit",
                "--max-bytes",
                "4096",
            ],
        )
    )

    methods = body["methods"]
    assert isinstance(methods, list)
    assert [entry["arm_id"] for entry in methods] == [FULL_TRANSCRIPT.arm_id]
    manifest = json.loads((output / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["task_ids"] == ["t-audit"]
    assert manifest["assembly"] == {"max_bytes": 4096}


def test_run_refuses_to_reuse_an_output_directory(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    output = tmp_path / "reused"
    output.mkdir()

    result = CliRunner().invoke(
        app,
        ["run", "--task-lock", str(lock), "--output-dir", str(output), "--run-id", "again"],
    )

    assert result.exit_code == 1
    assert "Work-continuity run failed" in result.output
    assert "already exists" in result.output


def test_run_refuses_to_rescore_recordings_under_a_smaller_ceiling(tmp_path: Path) -> None:
    """The reproduction from review, driven through the CLI entry point."""

    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    output = tmp_path / "tiny"

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--task-lock",
            str(lock),
            "--attempts",
            str(attempts),
            "--output-dir",
            str(output),
            "--run-id",
            "tiny",
            "--max-bytes",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert "made under a 16000 byte ceiling" in result.output
    assert not output.exists()


def test_run_rejects_an_unknown_arm_as_a_usage_error(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--task-lock",
            str(lock),
            "--output-dir",
            str(tmp_path / "bad-arm"),
            "--run-id",
            "bad-arm",
            "--arm",
            "nope",
        ],
    )

    assert result.exit_code == 2
    assert "unknown continuation arm" in result.output
    assert not (tmp_path / "bad-arm").exists()


def record_run(tmp_path: Path, lock: Path, attempts: Path, name: str, *extra: str) -> Path:
    """Run the benchmark once and return the run directory it wrote.

    The revisions are declared because the gate refuses a run that leaves them out
    rather than treating two unknowns as equal.
    """

    output = tmp_path / name
    payload(
        CliRunner().invoke(
            app,
            [
                "run",
                "--task-lock",
                str(lock),
                "--attempts",
                str(attempts),
                "--output-dir",
                str(output),
                "--run-id",
                name,
                "--powercontext-revision",
                "pc-1",
                "--integration-revision",
                "int-1",
                *extra,
            ],
        )
    )
    return output


def test_compare_accepts_two_runs_that_differ_only_by_their_identity(tmp_path: Path) -> None:
    """The gate runs on published runs, not only from a test."""

    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    first = record_run(tmp_path, lock, attempts, "run-a")
    second = record_run(tmp_path, lock, attempts, "run-b")

    body = payload(
        CliRunner().invoke(
            app,
            ["compare", "--baseline", str(first), "--treatment", str(second)],
        )
    )

    assert body["classification"] == "work-continuity-comparability"
    assert body["comparable"] is True
    assert body["baseline_run_id"] == "run-a"
    assert body["treatment_run_id"] == "run-b"
    assert body["task_set_id"] == "test-set"
    assert body["shared_arms"] == sorted(supported_continuation_arm_ids())


def test_compare_refuses_a_run_recorded_under_another_ceiling(tmp_path: Path) -> None:
    """A refused comparison says which pinned field disagreed."""

    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    first = record_run(tmp_path, lock, attempts, "run-a")
    second = record_run(tmp_path, lock, attempts, "run-b")
    manifest = second / "run-manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["assembly"]["max_bytes"] = 4_000
    manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["compare", "--baseline", str(first), "--treatment", str(second)],
    )

    assert result.exit_code == 1
    assert "Work-continuity comparison refused" in result.output
    assert "assembly.max_bytes" in result.output


def test_compare_reports_a_manifest_it_cannot_read(tmp_path: Path) -> None:
    lock = analysis_lock(tmp_path)
    attempts = write_attempts(tmp_path, full_coverage(lock), lock=lock)
    first = record_run(tmp_path, lock, attempts, "run-a")

    result = CliRunner().invoke(
        app,
        ["compare", "--baseline", str(first), "--treatment", str(tmp_path / "absent")],
    )

    assert result.exit_code == 1
    assert "Work-continuity comparison failed" in result.output
    assert "no run manifest" in result.output
