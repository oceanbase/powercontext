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

"""Independent work-continuity command-line application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, cast

import typer

from powercontext_eval_work_continuity.arms import (
    DEFAULT_ASSEMBLY_MAX_BYTES,
    ContinuationArmError,
    declared_run_arm_ids,
    ensure_comparable_work_continuity_runs,
    supported_continuation_arm_ids,
)
from powercontext_eval_work_continuity.attempts import (
    AttemptInputError,
    load_attempts,
)
from powercontext_eval_work_continuity.catalog import (
    TaskCatalog,
    WorkContinuityCatalogError,
    WorkContinuityInputError,
)
from powercontext_eval_work_continuity.report import (
    ReportError as WorkContinuityReportError,
)
from powercontext_eval_work_continuity.report import (
    build_report as build_work_continuity_report,
)
from powercontext_eval_work_continuity.runner import (
    WorkContinuityRunError,
    execution_configuration,
    require_recording_bindings,
    run_summary,
    run_work_continuity,
    write_run_artifacts,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Continuation-method evaluation for Rollover Handoff and its alternatives.",
)


@app.command("validate")
def work_continuity_validate(
    task_lock: Annotated[Path, typer.Option("--task-lock")],
    attempts: Annotated[Path | None, typer.Option("--attempts")] = None,
) -> None:
    """Validate the pinned task lock and any recorded attempts without writing anything."""

    try:
        catalog = TaskCatalog.load(task_lock)
        recorded = load_attempts(attempts, catalog=catalog) if attempts is not None else None
        if recorded is not None:
            # The documented preflight has to reject what `run` would reject:
            # a recording bound to another task lock or another context is not
            # scoreable, and finding that out here is the point of the command.
            require_recording_bindings(catalog=catalog, attempts=recorded)
    except (WorkContinuityInputError, AttemptInputError) as error:
        raise typer.BadParameter(str(error)) from None
    except (WorkContinuityCatalogError, WorkContinuityRunError) as error:
        typer.echo(f"Work-continuity validation failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "work-continuity-input-validation",
                "task_set_id": catalog.task_set_id,
                "task_count": len(catalog.tasks),
                "task_ids": list(catalog.task_ids),
                "task_lock_sha256": catalog.content_sha256,
                "supported_arms": list(supported_continuation_arm_ids()),
                "attempt_count": None if recorded is None else len(recorded.attempts),
                "hosts": None if recorded is None else list(recorded.hosts),
                "recording_protocol": (
                    None
                    if recorded is None
                    else {
                        "task_set_id": recorded.protocol.task_set_id,
                        "task_lock_sha256": recorded.protocol.task_lock_sha256,
                        "assembly_max_bytes": recorded.protocol.assembly_max_bytes,
                    }
                ),
                "execution_configuration": None if recorded is None else execution_configuration(recorded),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _run_manifest(path: Path) -> dict[str, object]:
    """Read one run manifest from a run directory or from a manifest file.

    A published run is a directory, so accepting the directory is what makes the
    command usable on the artifacts a run actually writes.
    """

    candidate = path / "run-manifest.json" if path.is_dir() else path
    if not candidate.is_file():
        raise WorkContinuityRunError(f"no run manifest at {candidate}")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkContinuityRunError(f"cannot read run manifest {candidate}: {error}") from None
    if not isinstance(payload, dict):
        raise WorkContinuityRunError(f"run manifest {candidate} is not a JSON object")
    return cast("dict[str, object]", payload)


@app.command("compare")
def work_continuity_compare(
    baseline: Annotated[Path, typer.Option("--baseline")],
    treatment: Annotated[Path, typer.Option("--treatment")],
) -> None:
    """Decide whether two recorded runs may be compared, and refuse when they may not."""

    try:
        first = _run_manifest(baseline)
        second = _run_manifest(treatment)
        # The gate is the whole point of the command: it is the only supported way
        # to have the documented comparison rules applied to two published runs.
        ensure_comparable_work_continuity_runs(first, second)
    except ContinuationArmError as error:
        typer.echo(f"Work-continuity comparison refused: {error}", err=True)
        raise typer.Exit(code=1) from None
    except WorkContinuityRunError as error:
        typer.echo(f"Work-continuity comparison failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    first_arms = declared_run_arm_ids(first) or ()
    second_arms = declared_run_arm_ids(second) or ()
    typer.echo(
        json.dumps(
            {
                "classification": "work-continuity-comparability",
                "comparable": True,
                "baseline_run_id": first.get("run_id"),
                "treatment_run_id": second.get("run_id"),
                "task_set_id": first.get("task_set_id"),
                "baseline_arms": list(first_arms),
                "treatment_arms": list(second_arms),
                "shared_arms": sorted(set(first_arms) & set(second_arms)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _method_rows(summary: dict[str, object]) -> list[dict[str, object]]:
    """Return one per-method echo row, rejecting a summary that lost its shape."""

    arms = summary.get("arms")
    if not isinstance(arms, list):
        raise WorkContinuityRunError("work-continuity run summary is missing its per-method rows")
    rows: list[dict[str, object]] = []
    for arm in arms:
        if not isinstance(arm, dict):
            raise WorkContinuityRunError("work-continuity run summary holds a malformed method row")
        method = cast("dict[str, object]", arm)
        injected = method.get("injected_bytes")
        outcome = method.get("outcome")
        if not isinstance(injected, dict) or not isinstance(outcome, dict):
            raise WorkContinuityRunError("work-continuity run summary holds a malformed method row")
        rows.append(
            {
                "arm_id": method["arm_id"],
                "injected_bytes_total": cast("dict[str, object]", injected)["total"],
                "task_success": cast("dict[str, object]", outcome)["task_success"],
            }
        )
    return rows


@app.command("run")
def work_continuity_run(
    task_lock: Annotated[Path, typer.Option("--task-lock")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    run_id: Annotated[str, typer.Option("--run-id")],
    max_bytes: Annotated[int, typer.Option("--max-bytes", min=1)] = DEFAULT_ASSEMBLY_MAX_BYTES,
    arm: Annotated[list[str] | None, typer.Option("--arm")] = None,
    task_id: Annotated[list[str] | None, typer.Option("--task-id")] = None,
    attempts: Annotated[Path | None, typer.Option("--attempts")] = None,
    powercontext_revision: Annotated[str | None, typer.Option("--powercontext-revision")] = None,
    integration_revision: Annotated[str | None, typer.Option("--integration-revision")] = None,
) -> None:
    """Assemble every selected method, score any recorded attempts, and write one run directory."""

    try:
        result = run_work_continuity(
            task_lock=task_lock,
            run_id=run_id,
            max_bytes=max_bytes,
            arm_ids=tuple(arm) if arm else None,
            task_ids=tuple(task_id) if task_id else None,
            attempts_path=attempts,
            powercontext_revision=powercontext_revision,
            integration_revision=integration_revision,
        )
        artifacts = write_run_artifacts(result, output_dir)
        report = build_work_continuity_report(result, output_dir=artifacts.output_dir)
        methods = _method_rows(run_summary(result))
    except ContinuationArmError as error:
        raise typer.BadParameter(str(error)) from None
    except (WorkContinuityInputError, AttemptInputError) as error:
        raise typer.BadParameter(str(error)) from None
    except (WorkContinuityRunError, WorkContinuityReportError, WorkContinuityCatalogError) as error:
        typer.echo(f"Work-continuity run failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": result.classification,
                "run_id": result.run_id,
                "manifest": str(artifacts.manifest_path),
                "assembly": str(artifacts.assembly_path),
                "scores": str(artifacts.scores_path),
                "summary": str(artifacts.summary_path),
                "report": str(report.report_path),
                "markdown": str(report.markdown_path),
                "methods": methods,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def main() -> None:
    """Run the work-continuity command-line application."""

    app()


if __name__ == "__main__":
    main()
