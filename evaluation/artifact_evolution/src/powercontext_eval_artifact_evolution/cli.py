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

"""CLI for validating and replaying Artifact-evolution bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from powercontext_eval_artifact_evolution.bundle import (
    BundleIOError,
    load_bundle,
    validation_error_summary,
    write_bundle,
)
from powercontext_eval_artifact_evolution.models import DecisionRequest
from powercontext_eval_artifact_evolution.recording import (
    DreamRunSnapshot,
    decision_for_dream_run,
    record_dream_attempt,
)
from powercontext_eval_artifact_evolution.replay import ReplayEngine

app = typer.Typer(no_args_is_help=True, help="Validate and faithfully replay Artifact-evolution bundles.")


@app.command("validate")
def validate_bundle(path: Annotated[Path, typer.Argument()]) -> None:
    """Validate one bundle without calling a model or writing any Runtime state."""

    try:
        bundle = load_bundle(path)
    except BundleIOError as error:
        typer.echo(f"Artifact-evolution validation failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "artifact-evolution-bundle-validation",
                "schema": bundle.schema_version,
                "bundle_id": bundle.bundle_id,
                "operation": bundle.workload.operation,
                "requested_decisions": len(bundle.requested_decisions),
                "recorded_attempts": len(bundle.attempts),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@app.command("replay")
def replay_bundle(
    path: Annotated[Path, typer.Argument()],
    decision_id: Annotated[str, typer.Option("--decision-id")],
    trajectory_id: Annotated[str, typer.Option("--trajectory-id")],
    observations_digest: Annotated[str, typer.Option("--observations-digest")],
    action_signature: Annotated[str, typer.Option("--action-signature")],
    ordering: Annotated[int, typer.Option("--ordering", min=0)],
) -> None:
    """Return one exact realized result or ``out_of_support``."""

    try:
        bundle = load_bundle(path)
    except BundleIOError as error:
        typer.echo(f"Artifact-evolution replay failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    try:
        request = DecisionRequest(
            decision_id=decision_id,
            trajectory_id=trajectory_id,
            observations_digest=observations_digest,
            action_signature=action_signature,
            ordering=ordering,
        )
        result = ReplayEngine(bundle).replay(request)
    except ValidationError as error:
        typer.echo(
            f"Artifact-evolution replay failed: invalid decision request ({validation_error_summary(error)})",
            err=True,
        )
        raise typer.Exit(code=1) from None
    payload: dict[str, object] = {"status": result.status}
    if result.reason is not None:
        payload["reason"] = result.reason
    if result.attempt is not None:
        payload["attempt"] = result.attempt.model_dump(mode="json", exclude_none=True)
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


@app.command("coverage")
def bundle_coverage(path: Annotated[Path, typer.Argument()]) -> None:
    """Report basic exact-replay coverage and saved generation cost."""

    try:
        bundle = load_bundle(path)
    except BundleIOError as error:
        typer.echo(f"Artifact-evolution coverage failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    coverage = ReplayEngine(bundle).coverage()
    typer.echo(
        json.dumps(
            {
                "classification": "artifact-evolution-exact-replay-coverage",
                "requested_decisions": coverage.requested_decisions,
                "supported_decisions": coverage.supported_decisions,
                "decision_ratio": coverage.decision_ratio,
                "requested_trajectories": coverage.requested_trajectories,
                "complete_trajectories": coverage.complete_trajectories,
                "trajectory_ratio": coverage.trajectory_ratio,
                "saved_model_calls": coverage.saved_model_calls,
                "saved_retries": coverage.saved_retries,
                "saved_input_tokens": coverage.saved_input_tokens,
                "saved_output_tokens": coverage.saved_output_tokens,
                "input_tokens_complete": coverage.input_tokens_complete,
                "output_tokens_complete": coverage.output_tokens_complete,
                "unsupported_decision_ids": coverage.unsupported_decision_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@app.command("record")
def record_bundle(
    bundle_path: Annotated[Path, typer.Argument(help="Existing recording bundle to extend.")],
    dream_run_path: Annotated[Path, typer.Argument(help="JSON snapshot of one terminal DreamRun.")],
    output_path: Annotated[Path, typer.Argument(help="New output bundle path.")],
    action_signature: Annotated[str, typer.Option("--action-signature")],
    policy_id: Annotated[str, typer.Option("--policy-id")],
    generator_id: Annotated[str, typer.Option("--generator-id")],
    runtime_version: Annotated[str, typer.Option("--runtime-version")],
    decision_id: Annotated[str | None, typer.Option("--decision-id")] = None,
    trajectory_id: Annotated[str | None, typer.Option("--trajectory-id")] = None,
    ordering: Annotated[int, typer.Option("--ordering", min=0)] = 0,
) -> None:
    """Record one controlled terminal refine_experience Dream snapshot."""

    try:
        bundle = load_bundle(bundle_path)
        snapshot = DreamRunSnapshot.model_validate_json(dream_run_path.read_bytes())
        decision = decision_for_dream_run(
            snapshot,
            action_signature=action_signature,
            decision_id=decision_id,
            trajectory_id=trajectory_id,
            ordering=ordering,
        )
        recorded = record_dream_attempt(
            bundle,
            snapshot,
            decision=decision,
            policy_id=policy_id,
            generator_id=generator_id,
            runtime_version=runtime_version,
        )
        path = write_bundle(recorded, output_path)
    except ValidationError as error:
        typer.echo(f"Artifact-evolution record failed: {validation_error_summary(error)}", err=True)
        raise typer.Exit(code=1) from None
    except (ValueError, OSError) as error:
        typer.echo(f"Artifact-evolution record failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps({"status": "recorded", "output": str(path)}, ensure_ascii=False, sort_keys=True))


def main() -> None:
    """Run the Artifact-evolution CLI."""

    app()


if __name__ == "__main__":
    main()
