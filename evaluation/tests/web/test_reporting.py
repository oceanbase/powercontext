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

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from powercontext_eval.artifacts import ArmState
from powercontext_eval.report import ArmReport, MetricSet, ReportBundle
from powercontext_eval.web.reporting import InvalidReportArtifact, UnsafeReportPath, load_raw_report, load_report

POWERCONTEXT_SHA = "a" * 40
HARNESS_SHA = "b" * 40
PLUGIN_ID = "powercontext@powercontext"
PLUGIN_VERSION = "0.1.0"


def _bundle() -> ReportBundle:
    return ReportBundle(
        title="SWE-bench Pro evaluation",
        revisions={"harness": HARNESS_SHA, "powercontext": POWERCONTEXT_SHA},
        configuration={
            "codex": "0.145.0",
            "instance": "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
        },
        off=ArmReport(
            arm="off",
            state=ArmState.TREATMENT_VALIDATED,
            resolved=True,
            passed=True,
            treatment_valid=True,
            metrics=MetricSet(
                patch_bytes=100,
                input_tokens=1_963_194,
                output_tokens=20_000,
                elapsed_seconds=120.0,
            ),
        ),
        on=ArmReport(
            arm="on",
            state=ArmState.TREATMENT_VALIDATED,
            resolved=True,
            passed=True,
            treatment_valid=True,
            metrics=MetricSet(
                patch_bytes=90,
                input_tokens=1_122_180,
                output_tokens=18_000,
                elapsed_seconds=100.0,
            ),
        ),
    )


def _evidence(run_id: str, arm: str) -> dict[str, object]:
    return {
        "mcp_requests": 0 if arm == "off" else 2,
        "plugin_checkout_sha": POWERCONTEXT_SHA,
        "plugin_id": PLUGIN_ID,
        "plugin_installed": True,
        "plugin_version": PLUGIN_VERSION,
        "prompt_sources": 0 if arm == "off" else 1,
        "scope_id": f"eval:{run_id}:{arm}",
        "server_ready": True,
    }


def _write_run(runs_root: Path, run_id: str = "run-123") -> Path:
    run_dir = runs_root / run_id
    for arm in ("off", "on"):
        evidence_dir = run_dir / "arms" / arm / "powercontext"
        evidence_dir.mkdir(parents=True)
        (evidence_dir / "treatment.json").write_text(json.dumps(_evidence(run_id, arm)))
    (run_dir / "report.json").write_text(_bundle().model_dump_json())
    (run_dir / "report.md").write_text("# safe\n")
    return run_dir


def test_loads_validated_report_and_derives_exact_comparisons(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)

    response = load_report(run_dir, runs_root)

    assert response.task_id == "run-123"
    assert response.acceptance_valid is True
    assert response.off.resolution == "resolved"
    assert response.off.state is ArmState.TREATMENT_VALIDATED
    assert response.off.passed is True
    assert response.off.treatment_valid is True
    assert response.on.resolution == "resolved"
    assert response.off.input_tokens == 1_963_194
    assert response.on.input_tokens == 1_122_180
    assert response.comparison.input_tokens is not None
    assert response.comparison.input_tokens.delta == -841_014
    assert response.comparison.input_tokens.percent == pytest.approx(-42.839, abs=0.001)
    assert response.evidence.off == response.evidence.off.model_copy(update={"scope_id": "eval:run-123:off"})
    assert response.evidence.on.mcp_requests == 2
    assert dict(response.revisions) == {"harness": HARNESS_SHA, "powercontext": POWERCONTEXT_SHA}
    assert dict(response.configuration) == dict(_bundle().configuration)


@pytest.mark.parametrize(
    ("relative_path", "contents"),
    [
        ("report.json", None),
        ("report.json", b"{"),
        ("report.json", b"\xff"),
        ("report.json", b"x" * (1024 * 1024 + 1)),
        ("report.json", b'{"title":"missing strict fields"}'),
        ("arms/off/powercontext/treatment.json", None),
        ("arms/off/powercontext/treatment.json", b"{"),
        ("arms/off/powercontext/treatment.json", b"\xff"),
        ("arms/off/powercontext/treatment.json", b"x" * (64 * 1024 + 1)),
    ],
)
def test_rejects_missing_oversized_or_malformed_artifacts(
    tmp_path: Path, relative_path: str, contents: bytes | None
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    target = run_dir / relative_path
    target.unlink()
    if contents is not None:
        target.write_bytes(contents)

    with pytest.raises(InvalidReportArtifact, match="Evaluation report artifacts are invalid"):
        load_report(run_dir, runs_root)


def test_rejects_wrong_arm_roles(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    bundle = _bundle()
    wrong = bundle.model_copy(
        update={
            "off": bundle.off.model_copy(update={"arm": "on"}),
            "on": bundle.on.model_copy(update={"arm": "off"}),
        }
    )
    (run_dir / "report.json").write_text(wrong.model_dump_json())

    with pytest.raises(InvalidReportArtifact):
        load_report(run_dir, runs_root)


def test_rejects_run_outside_root_and_symlink_escape(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    outside = _write_run(tmp_path / "outside")
    runs_root.mkdir()
    escaped = runs_root / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeReportPath, match="Evaluation run path is unsafe"):
        load_report(outside, runs_root)
    with pytest.raises(UnsafeReportPath, match="Evaluation run path is unsafe"):
        load_report(escaped, runs_root)


@pytest.mark.parametrize(
    ("arm", "update"),
    [
        ("off", {"extra": "forbidden"}),
        ("off", {"plugin_checkout_sha": "c" * 40}),
        ("off", {"plugin_id": "other@plugin"}),
        ("off", {"plugin_installed": False}),
        ("off", {"server_ready": False}),
        ("off", {"scope_id": "eval:other:off"}),
        ("off", {"prompt_sources": 1}),
        ("off", {"mcp_requests": 1}),
        ("on", {"plugin_version": "9.9.9"}),
        ("on", {"prompt_sources": 0}),
        ("on", {"mcp_requests": 0}),
        ("on", {"plugin_installed": False}),
        ("on", {"server_ready": False}),
        ("on", {"scope_id": "eval:run-123:off"}),
    ],
)
def test_rejects_incoherent_treatment_evidence(tmp_path: Path, arm: str, update: dict[str, object]) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    evidence = _evidence(run_dir.name, arm)
    evidence.update(update)
    (run_dir / "arms" / arm / "powercontext" / "treatment.json").write_text(json.dumps(evidence))

    with pytest.raises(InvalidReportArtifact, match="Evaluation report artifacts are invalid"):
        load_report(run_dir, runs_root)


@pytest.mark.parametrize(
    ("off_update", "on_update"),
    [
        ({"treatment_valid": False}, {}),
        ({}, {"treatment_valid": False}),
        ({"state": ArmState.REPORTED}, {"state": ArmState.TREATMENT_VALIDATED}),
        ({"resolved": True, "passed": False}, {}),
        ({}, {"resolved": False, "passed": True}),
    ],
)
def test_acceptance_is_false_without_coherent_official_outcomes(
    tmp_path: Path, off_update: dict[str, object], on_update: dict[str, object]
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    bundle = _bundle()
    changed = bundle.model_copy(
        update={
            "off": bundle.off.model_copy(update=off_update),
            "on": bundle.on.model_copy(update=on_update),
        }
    )
    (run_dir / "report.json").write_text(changed.model_dump_json())

    assert load_report(run_dir, runs_root).acceptance_valid is False


def test_acceptance_requires_both_official_arms_to_pass_and_resolve(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    bundle = _bundle()
    unresolved = bundle.model_copy(
        update={
            "off": bundle.off.model_copy(update={"resolved": False, "passed": False}),
            "on": bundle.on.model_copy(update={"resolved": False, "passed": False}),
        }
    )
    (run_dir / "report.json").write_text(unresolved.model_dump_json())

    response = load_report(run_dir, runs_root)

    assert response.acceptance_valid is False
    assert response.off.resolution == "unresolved"
    assert response.off.state is ArmState.TREATMENT_VALIDATED
    assert response.off.passed is False
    assert response.off.treatment_valid is True
    assert response.on.resolution == "unresolved"
    assert response.on.state is ArmState.TREATMENT_VALIDATED
    assert response.on.passed is False
    assert response.on.treatment_valid is True
    assert response.comparison.input_tokens is not None
    assert response.comparison.input_tokens.delta == -841_014


@pytest.mark.parametrize(
    ("off_update", "on_update"),
    [
        ({"treatment_valid": False}, {}),
        ({}, {"treatment_valid": False}),
        ({"state": ArmState.INVALID_TREATMENT}, {}),
        ({}, {"state": ArmState.INFRASTRUCTURE_ERROR}),
    ],
)
def test_invalid_or_noncomparable_treatments_hide_all_comparisons(
    tmp_path: Path, off_update: dict[str, object], on_update: dict[str, object]
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    bundle = _bundle()
    invalid = bundle.model_copy(
        update={
            "off": bundle.off.model_copy(update=off_update),
            "on": bundle.on.model_copy(update=on_update),
        }
    )
    (run_dir / "report.json").write_text(invalid.model_dump_json())

    response = load_report(run_dir, runs_root)

    assert response.comparison.input_tokens is None
    assert response.comparison.output_tokens is None
    assert response.comparison.elapsed_seconds is None
    assert response.comparison.patch_bytes is None


def test_missing_metrics_and_zero_off_denominator_preserve_na(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    bundle = _bundle()
    changed = bundle.model_copy(
        update={
            "off": bundle.off.model_copy(update={"metrics": MetricSet(input_tokens=0, output_tokens=None)}),
            "on": bundle.on.model_copy(update={"metrics": MetricSet(input_tokens=10, output_tokens=5)}),
        }
    )
    (run_dir / "report.json").write_text(changed.model_dump_json())

    response = load_report(run_dir, runs_root)
    assert response.comparison.input_tokens is not None
    assert response.comparison.input_tokens.delta == 10
    assert response.comparison.input_tokens.percent is None
    assert response.comparison.output_tokens is None
    assert response.comparison.patch_bytes is None


def test_raw_markdown_is_bounded_utf8_literal_text(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    literal = "# result\n<script>alert('literal')</script>\n"
    (run_dir / "report.md").write_text(literal)

    assert load_raw_report(run_dir, runs_root) == literal

    (run_dir / "report.md").write_bytes(b"\xff")
    with pytest.raises(InvalidReportArtifact):
        load_raw_report(run_dir, runs_root)


def test_report_json_rejects_secret_shaped_configuration(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    payload = _bundle().model_dump(mode="json")
    payload["configuration"]["authorization"] = "must-not-appear"
    (run_dir / "report.json").write_text(json.dumps(payload))

    with pytest.raises(InvalidReportArtifact) as caught:
        load_report(run_dir, runs_root)
    assert "must-not-appear" not in str(caught.value)


def test_rejects_symlinked_artifact_file(tmp_path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("host lacks O_NOFOLLOW")
    runs_root = tmp_path / "runs"
    run_dir = _write_run(runs_root)
    outside = tmp_path / "outside.json"
    outside.write_text(_bundle().model_dump_json())
    (run_dir / "report.json").unlink()
    (run_dir / "report.json").symlink_to(outside)

    with pytest.raises(InvalidReportArtifact):
        load_report(run_dir, runs_root)
