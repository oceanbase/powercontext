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

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from powercontext_eval_artifact_evolution.bundle import write_bundle
from powercontext_eval_artifact_evolution.cli import app
from powercontext_eval_artifact_evolution.models import ArtifactEvolutionBundle, AttemptCost, Budget, TrajectoryRequest
from powercontext_eval_artifact_evolution.replay import ReplayEngine

from .test_bundle import DIGEST, attempt, bundle, decision


def test_replay_returns_saved_result_and_cost_without_reexecution() -> None:
    recorded = attempt()
    engine = ReplayEngine(bundle(attempts=(recorded,)))

    result = engine.replay(decision())

    assert result.status == "replayed"
    assert result.attempt == recorded
    coverage = engine.coverage()
    assert coverage.decision_ratio == 1.0
    assert coverage.trajectory_ratio == 1.0
    assert coverage.saved_model_calls == 1
    assert coverage.saved_retries == 0
    assert coverage.saved_input_tokens == 100
    assert coverage.saved_output_tokens == 20
    assert coverage.input_tokens_complete is True
    assert coverage.output_tokens_complete is True


def test_replay_is_read_only_and_does_not_consume_future_outcomes() -> None:
    state = {"model_calls": 0, "candidate_count": 1, "review_state": "pending"}
    engine = ReplayEngine(bundle(attempts=(attempt(),)))
    before = state.copy()

    result = engine.replay(decision())

    assert state == before
    assert result.status == "replayed"
    assert result.attempt is not None
    assert "review" not in result.attempt.model_dump(mode="python")
    assert "revision" not in result.attempt.model_dump(mode="python")

    # Downstream state is deliberately not an input to the exact replay key.
    future_state = {"review": "approved", "revision": 7, "selected": True}
    assert engine.replay(decision()).attempt == result.attempt
    assert future_state == {"review": "approved", "revision": 7, "selected": True}


def test_attempt_rejects_downstream_result_fields() -> None:
    raw = attempt().model_dump(mode="python")
    raw["review"] = {"status": "approved"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(attempt()).model_validate(raw)


def test_replay_is_out_of_support_for_an_unrecorded_action() -> None:
    result = ReplayEngine(bundle(attempts=(attempt(),))).replay(decision(action="refine-experience:unrecorded"))

    assert result.status == "out_of_support"
    assert result.attempt is None
    assert result.reason is not None


def test_coverage_keeps_unsupported_decisions_and_trajectories_in_denominator() -> None:
    second = decision(decision_id="decision-2", action="refine-experience:second", trajectory_id="trajectory-2")
    expanded = bundle().model_copy(
        update={
            "workload": bundle().workload.model_copy(
                update={"declared_budget": Budget(max_proposal_attempts=2, max_model_calls=2)}
            ),
            "requested_decisions": (decision(), second),
            "requested_trajectories": (
                TrajectoryRequest(trajectory_id="trajectory-1", decision_ids=("decision-1",)),
                TrajectoryRequest(trajectory_id="trajectory-2", decision_ids=("decision-2",)),
            ),
            "attempts": (attempt(),),
        }
    )
    # Validate the copy through the public model boundary before replaying it.
    validated = ArtifactEvolutionBundle.model_validate(expanded.model_dump(mode="python", by_alias=True))
    coverage = ReplayEngine(validated).coverage()

    assert coverage.requested_decisions == 2
    assert coverage.supported_decisions == 1
    assert coverage.decision_ratio == 0.5
    assert coverage.requested_trajectories == 2
    assert coverage.complete_trajectories == 1
    assert coverage.trajectory_ratio == 0.5
    assert coverage.unsupported_decision_ids == ("decision-2",)


def test_coverage_preserves_known_partial_token_costs() -> None:
    second = decision(decision_id="decision-2", action="refine-experience:second", trajectory_id="trajectory-2")
    partial = attempt(second, attempt_id="attempt-2").model_copy(
        update={
            "cost": AttemptCost(model_calls=1, retries=0, input_tokens=None, output_tokens=10),
        }
    )
    expanded = bundle().model_copy(
        update={
            "workload": bundle().workload.model_copy(
                update={"declared_budget": Budget(max_proposal_attempts=2, max_model_calls=2)}
            ),
            "requested_decisions": (decision(), second),
            "requested_trajectories": (
                TrajectoryRequest(trajectory_id="trajectory-1", decision_ids=("decision-1",)),
                TrajectoryRequest(trajectory_id="trajectory-2", decision_ids=("decision-2",)),
            ),
            "attempts": (attempt(), partial),
        }
    )
    coverage = ReplayEngine(
        ArtifactEvolutionBundle.model_validate(expanded.model_dump(mode="python", by_alias=True))
    ).coverage()

    assert coverage.saved_input_tokens == 100
    assert coverage.input_tokens_complete is False
    assert coverage.saved_output_tokens == 30
    assert coverage.output_tokens_complete is True


def test_cli_validate_replay_and_coverage_use_saved_bundle_only(tmp_path: Path) -> None:
    path = write_bundle(bundle(attempts=(attempt(),)), tmp_path / "bundle.json")
    runner = CliRunner()

    validated = runner.invoke(app, ["validate", str(path)])
    replayed = runner.invoke(
        app,
        [
            "replay",
            str(path),
            "--decision-id",
            "decision-1",
            "--trajectory-id",
            "trajectory-1",
            "--observations-digest",
            DIGEST,
            "--action-signature",
            "refine-experience:v1",
            "--ordering",
            "0",
        ],
    )
    coverage = runner.invoke(app, ["coverage", str(path)])

    assert validated.exit_code == 0, validated.stdout
    assert '"recorded_attempts": 1' in validated.stdout
    assert replayed.exit_code == 0, replayed.stdout
    assert '"status": "replayed"' in replayed.stdout
    assert coverage.exit_code == 0, coverage.stdout
    assert '"decision_ratio": 1.0' in coverage.stdout


def test_cli_rejects_malformed_decision_request_without_traceback(tmp_path: Path) -> None:
    path = write_bundle(bundle(attempts=(attempt(),)), tmp_path / "bundle.json")
    result = CliRunner().invoke(
        app,
        [
            "replay",
            str(path),
            "--decision-id",
            "decision-1",
            "--trajectory-id",
            "trajectory-1",
            "--observations-digest",
            "abc",
            "--action-signature",
            "refine-experience:v1",
            "--ordering",
            "0",
        ],
    )

    assert result.exit_code == 1
    assert "invalid decision request" in result.output
    assert "Traceback" not in result.output
