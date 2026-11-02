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
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from powercontext_eval_artifact_evolution.bundle import load_bundle, write_bundle
from powercontext_eval_artifact_evolution.cli import app
from powercontext_eval_artifact_evolution.models import (
    ArtifactEvolutionBundle,
    Budget,
    CapturePolicy,
    TrajectoryRequest,
)
from powercontext_eval_artifact_evolution.privacy import canonical_json, digest_json
from powercontext_eval_artifact_evolution.recording import (
    DreamRecordingError,
    DreamRunSnapshot,
    DreamUsageSnapshot,
    decision_for_dream_run,
    record_dream_attempt,
    snapshot_from_dream_run,
)
from powercontext_eval_artifact_evolution.replay import ReplayEngine

from .test_bundle import bundle

FIXTURES = Path(__file__).parents[1] / "fixtures"


def dream_run() -> DreamRunSnapshot:
    return DreamRunSnapshot(
        scope_id="scope-1",
        run_id="dream-run-1",
        operation="refine_experience",
        status="succeeded",
        outcome="proposed",
        input_manifest={
            "transform_version": "powercontext.dream.evidence.v1",
            "artifacts": ({"family": "experience", "artifact_id": "exp-1", "revision": 3},),
            "sources": ({"source_type": "message", "source_id": "source-1"},),
            "nodes": (),
            "edges": (),
            "root_groups": (),
            "projection_digest": "sha256:" + "a" * 64,
            "projection_bytes": 128,
            "incomplete": False,
        },
        candidate={"kind": "artifact", "candidate_id": "candidate-1", "version": 2},
        started_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 10, 17, 12, 0, 1, 500_000, tzinfo=UTC),
        attempt_count=2,
        usage=DreamUsageSnapshot(model_calls=2, input_tokens=100, output_tokens=20),
        prompt_version="powercontext.dream.v1.4",
        model_config_id="model-1",
    )


def bound_bundle(
    run: DreamRunSnapshot,
    *,
    payloads: Literal["none", "redacted", "approved"] = "none",
) -> ArtifactEvolutionBundle:
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    target_ref = None if run.target is None else canonical_json(run.target).decode("utf-8")
    manifest = run.input_manifest
    assert manifest is not None
    refs = tuple(canonical_json(value).decode("utf-8") for value in (*manifest.artifacts, *manifest.sources))
    base = bundle()
    return base.model_copy(
        update={
            "workload": base.workload.model_copy(
                update={
                    "scope_digest": digest_json(run.scope_id),
                    "fixture_digest": digest_json(manifest),
                    "target_artifact_ref": target_ref,
                    "expected_head": target_ref,
                    "evidence_refs": refs,
                    "declared_budget": Budget(max_proposal_attempts=1, max_model_calls=2),
                }
            ),
            "capture_policy": CapturePolicy(mode="controlled", payloads=payloads, retention_class="test"),
            "requested_decisions": (decision,),
            "requested_trajectories": (
                TrajectoryRequest(trajectory_id=decision.trajectory_id, decision_ids=(decision.decision_id,)),
            ),
        }
    )


def test_controlled_dream_recording_round_trips_into_exact_replay(tmp_path: Path) -> None:
    run = dream_run()
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    base = bound_bundle(run, payloads="redacted")

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
    assert replayed.attempt.nomination.status == "nominated"


def test_recorder_rejects_non_terminal_run() -> None:
    run = dream_run()
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    running = run.model_copy(update={"status": "running"})

    with pytest.raises(ValueError, match="terminal"):
        record_dream_attempt(
            bound_bundle(run),
            running,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


def test_cli_accepts_real_runtime_json_and_defaults_to_digest_only(tmp_path: Path) -> None:
    dream_path = FIXTURES / "refine_experience_dream_run.json"
    run = DreamRunSnapshot.model_validate_json(dream_path.read_bytes())
    base = bound_bundle(run)
    source = write_bundle(base, tmp_path / "base.json")
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
    recorded = load_bundle(output).attempts[0]
    assert recorded.parent_dream_run_ref.run_id == "dream-run-1"
    assert recorded.result.output_digest == digest_json({"candidate_id": "candidate-1", "candidate_version": 2})
    assert recorded.result.output_payload is None
    assert recorded.cost.retries == 1


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"attempt_count": 0}, "no execution attempt"),
        ({"reused": True}, "reused"),
        ({"outcome": None}, "no outcome"),
        ({"model_config_id": None}, "model_config_id"),
        ({"candidate": {"kind": "artifact", "candidate_id": "", "version": 0}}, "candidate.candidate_id"),
        ({"status": "failed"}, "must not contain"),
        ({"outcome": "no_change"}, "non-proposed"),
    ],
)
def test_recorder_rejects_runs_that_cannot_be_replayed_faithfully(update: dict[str, object], message: str) -> None:
    original = dream_run()
    malformed = original.model_copy(update=update)
    decision = decision_for_dream_run(malformed, action_signature="refine-experience:v1")

    with pytest.raises(DreamRecordingError, match=message):
        record_dream_attempt(
            bound_bundle(malformed),
            malformed,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


def test_recorder_rejects_empty_manifest() -> None:
    original = dream_run()
    malformed = original.model_copy(update={"input_manifest": {}})
    decision = decision_for_dream_run(malformed, action_signature="refine-experience:v1")

    with pytest.raises(DreamRecordingError, match="invalid Dream run snapshot"):
        record_dream_attempt(
            bound_bundle(original),
            malformed,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", True),
        ("revision", "3"),
        ("projection_bytes", "128"),
        ("incomplete", "false"),
    ],
)
def test_recorder_rejects_coerced_manifest_scalars(field: str, value: object) -> None:
    original = dream_run()
    manifest = original.input_manifest
    assert manifest is not None
    raw_manifest = manifest.model_dump(mode="python")
    if field == "revision":
        raw_manifest["artifacts"][0][field] = value
    else:
        raw_manifest[field] = value
    malformed = original.model_copy(update={"input_manifest": raw_manifest})
    decision = decision_for_dream_run(original, action_signature="refine-experience:v1")

    with pytest.raises(DreamRecordingError, match="invalid Dream run snapshot"):
        record_dream_attempt(
            bound_bundle(original),
            malformed,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


@pytest.mark.parametrize(
    ("update", "field"),
    [
        ({"target": {}}, "target"),
        (
            {"target": {"family": "experience", "artifact_id": "exp-1", "revision": True}},
            "target.revision",
        ),
        ({"candidate": {"kind": "tag", "candidate_id": "candidate-1", "version": 2}}, "candidate.kind"),
    ],
)
def test_recorder_rejects_malformed_target_or_candidate(update: dict[str, object], field: str) -> None:
    original = dream_run()
    malformed = original.model_copy(update=update)
    decision = decision_for_dream_run(original, action_signature="refine-experience:v1")

    with pytest.raises(DreamRecordingError, match=field):
        record_dream_attempt(
            bound_bundle(original),
            malformed,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


def test_recorder_rejects_structurally_incomplete_evidence_node() -> None:
    original = dream_run()
    raw = original.model_dump(mode="python")
    raw["input_manifest"]["nodes"] = ({},)
    malformed = SimpleNamespace(**raw)
    decision = decision_for_dream_run(original, action_signature="refine-experience:v1")

    with pytest.raises(DreamRecordingError, match="input_manifest.nodes.0"):
        record_dream_attempt(
            bound_bundle(original),
            malformed,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


@pytest.mark.parametrize(
    ("workload_update", "message"),
    [
        ({"scope_digest": "0" * 64}, "scope_digest"),
        ({"fixture_digest": "0" * 64}, "fixture_digest"),
        ({"target_artifact_ref": "unrelated"}, "target_artifact_ref"),
        ({"expected_head": "unrelated"}, "expected_head"),
        ({"evidence_refs": ("unrelated",)}, "evidence_refs"),
    ],
)
def test_recorder_rejects_workload_mismatch(workload_update: dict[str, object], message: str) -> None:
    run = dream_run()
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    base = bound_bundle(run)
    mismatched = base.model_copy(update={"workload": base.workload.model_copy(update=workload_update)})

    with pytest.raises(DreamRecordingError, match=message):
        record_dream_attempt(
            mismatched,
            run,
            decision=decision,
            policy_id="p",
            generator_id="g",
            runtime_version="r",
        )


def test_recorder_binds_exact_target_and_expected_head() -> None:
    run = dream_run().model_copy(update={"target": {"family": "experience", "artifact_id": "exp-1", "revision": 3}})
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")

    recorded = record_dream_attempt(
        bound_bundle(run),
        run,
        decision=decision,
        policy_id="p",
        generator_id="g",
        runtime_version="r",
    )

    expected = canonical_json(run.target).decode("utf-8")
    assert recorded.workload.target_artifact_ref == expected
    assert recorded.workload.expected_head == expected


def test_object_and_json_paths_have_the_same_observation_digest() -> None:
    class ManifestNode(BaseModel):
        evidence_id: str
        kind: str
        digest: str
        source: dict[str, object] | None = None
        artifact: dict[str, object] | None = None
        role: str
        historical: bool = False

    class RuntimeManifest(BaseModel):
        transform_version: str
        artifacts: tuple[dict[str, object], ...]
        sources: tuple[dict[str, object], ...]
        nodes: tuple[ManifestNode, ...]
        edges: tuple[dict[str, object], ...]
        root_groups: tuple[dict[str, object], ...]
        projection_digest: str
        projection_bytes: int
        incomplete: bool

    raw = json.loads((FIXTURES / "refine_experience_dream_run.json").read_text(encoding="utf-8"))
    json_snapshot = DreamRunSnapshot.model_validate_json((FIXTURES / "refine_experience_dream_run.json").read_bytes())
    object_fields = json_snapshot.model_dump(mode="python")
    object_fields["input_manifest"] = RuntimeManifest.model_validate(raw["input_manifest"])
    object_snapshot = snapshot_from_dream_run(SimpleNamespace(**object_fields))

    assert object_snapshot.input_manifest == json_snapshot.input_manifest
    assert (
        decision_for_dream_run(object_snapshot, action_signature="refine-experience:v1").observations_digest
        == decision_for_dream_run(json_snapshot, action_signature="refine-experience:v1").observations_digest
    )


def test_output_digest_is_independent_of_capture_policy() -> None:
    run = dream_run().model_copy(update={"candidate": {"kind": "artifact", "candidate_id": "sk-test", "version": 2}})
    decision = decision_for_dream_run(run, action_signature="refine-experience:v1")
    arguments = {
        "decision": decision,
        "policy_id": "p",
        "generator_id": "g",
        "runtime_version": "r",
    }

    digest_only = record_dream_attempt(bound_bundle(run), run, **arguments)
    redacted = record_dream_attempt(bound_bundle(run, payloads="redacted"), run, **arguments)

    digest_only_result = digest_only.attempts[0].result
    redacted_result = redacted.attempts[0].result
    assert digest_only_result.output_digest == redacted_result.output_digest
    assert digest_only_result.output_payload is None
    assert digest_only_result.payload_digest is None
    assert redacted_result.output_payload == {
        "candidate_id": "[REDACTED]",
        "candidate_version": 2,
    }
    assert redacted_result.payload_digest == digest_json(redacted_result.output_payload)


def test_record_cli_does_not_echo_rejected_input(tmp_path: Path) -> None:
    run = dream_run()
    source = write_bundle(bound_bundle(run), tmp_path / "base.json")
    dream_path = tmp_path / "dream.json"
    dream_path.write_text(run.model_dump_json().replace('"succeeded"', '"SECRET-TASK-BODY"'), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "record",
            str(source),
            str(dream_path),
            str(tmp_path / "recorded.json"),
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

    assert result.exit_code == 1
    assert "status" in result.output
    assert "SECRET-TASK-BODY" not in result.output
    assert "input_value" not in result.output
