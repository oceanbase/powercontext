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

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from powercontext_eval_artifact_evolution.bundle import load_bundle, write_bundle
from powercontext_eval_artifact_evolution.cli import app
from powercontext_eval_artifact_evolution.models import CapturePolicy, TrajectoryRequest
from powercontext_eval_artifact_evolution.privacy import digest_json
from powercontext_eval_artifact_evolution.recording import (
    DreamRunSnapshot,
    DreamUsageSnapshot,
    decision_for_dream_run,
    record_dream_attempt,
)
from powercontext_eval_artifact_evolution.replay import ReplayEngine

from .test_bundle import bundle


def dream_run() -> DreamRunSnapshot:
    return DreamRunSnapshot(
        scope_id="scope-1",
        run_id="dream-run-1",
        operation="refine_experience",
        status="succeeded",
        outcome="proposed",
        input_manifest={"projection_digest": "manifest-1", "nodes": [{"evidence_id": "e-1"}]},
        candidate={"kind": "artifact", "candidate_id": "candidate-1", "version": 2},
        started_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 10, 17, 12, 0, 1, 500_000, tzinfo=UTC),
        attempt_count=2,
        usage=DreamUsageSnapshot(model_calls=1, input_tokens=100, output_tokens=20),
        prompt_version="powercontext.dream.v1.4",
        model_config_id="model-1",
    )


def test_controlled_dream_recording_round_trips_into_exact_replay(tmp_path: Path) -> None:
    run = dream_run()
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    base = bundle().model_copy(
        update={
            "capture_policy": CapturePolicy(mode="controlled", payloads="redacted", retention_class="test"),
            "requested_decisions": (decision,),
            "requested_trajectories": (
                TrajectoryRequest(trajectory_id=decision.trajectory_id, decision_ids=(decision.decision_id,)),
            ),
        }
    )

    recorded = record_dream_attempt(
        base,
        run,
        decision=decision,
        policy_id="baseline-policy-v1",
        generator_id="dream.refine_experience",
        runtime_version="runtime-v1",
    )
    path = write_bundle(recorded, tmp_path / "recorded.json")
    loaded = load_bundle(path)
    replayed = ReplayEngine(loaded).replay(decision)

    assert replayed.status == "replayed"
    assert replayed.attempt is not None
    assert replayed.attempt.parent_dream_run_ref.run_id == "dream-run-1"
    assert replayed.attempt.cost.retries == 1
    assert replayed.attempt.cost.wall_time_ms == 1500
    assert replayed.attempt.result.output_digest == digest_json(replayed.attempt.result.output_payload)


def test_recorder_rejects_non_terminal_or_wrong_operation() -> None:
    run = dream_run()
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    running = run.model_copy(update={"status": "running"})

    import pytest

    with pytest.raises(ValueError, match="terminal"):
        record_dream_attempt(
            bundle(),
            running,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


def test_cli_records_a_controlled_dream_snapshot(tmp_path: Path) -> None:
    run = dream_run()
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    base = bundle().model_copy(
        update={
            "capture_policy": CapturePolicy(mode="controlled", payloads="redacted", retention_class="test"),
            "requested_decisions": (decision,),
            "requested_trajectories": (
                TrajectoryRequest(trajectory_id=decision.trajectory_id, decision_ids=(decision.decision_id,)),
            ),
        }
    )
    source = write_bundle(base, tmp_path / "base.json")
    dream_path = tmp_path / "dream.json"
    dream_path.write_text(run.model_dump_json(), encoding="utf-8")
    output = tmp_path / "recorded.json"

    result = CliRunner().invoke(
        app,
        [
            "record",
            str(source),
            str(dream_path),
            str(output),
            "--action-signature",
            "refine-experience:v1",
            "--policy-id",
            "baseline-policy-v1",
            "--generator-id",
            "dream.refine_experience",
            "--runtime-version",
            "runtime-v1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_bundle(output).attempts[0].parent_dream_run_ref.run_id == "dream-run-1"
