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

import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult, InferenceUsage
from typer.testing import CliRunner

from powercontext_eval_evidence_admission import cli
from powercontext_eval_evidence_admission.cli import Provider, app, live_generator
from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.report import load_run


@pytest.mark.parametrize("experiment_version", ["v1", "v2", "v3"])
def test_controlled_live_command_round_trips_private_outputs_audit_and_report(
    monkeypatch, tmp_path, experiment_version
):
    suite = load_suite(experiment_version=experiment_version)
    cases = tuple(c for c in suite.cases if c.case_id in {"small-complete", "single-source-overflow"})
    minimal = replace(
        suite, cases=cases, experiment=suite.experiment.model_copy(update={"budgets": (4096,), "live_repetitions": 2})
    )
    actual_identity = cli.code_identity

    def test_identity(selected):
        return {**actual_identity(selected), "frozen_committed": True}

    class ControlledGenerator:
        async def generate(self, value, /):
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text="Two maintainers must approve a release.",
                            description=("Two maintainers must approve a release.")[:200],
                            evidence_ids=tuple(item.evidence_id for item in value.evidence),
                        ),
                    )
                ),
                usage=InferenceUsage(requests=1, input_tokens=11, output_tokens=7),
            )

    @asynccontextmanager
    async def controlled_provider(*args, **kwargs):
        yield ControlledGenerator()

    monkeypatch.setattr(cli, "selected_suite", lambda *_: minimal)
    monkeypatch.setattr(cli, "code_identity", test_identity)
    monkeypatch.setattr(cli, "live_generator", controlled_provider)
    output = tmp_path / "run"
    private = tmp_path / "private"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-live",
            "--output-dir",
            str(output),
            "--audit-dir",
            str(private),
            "--provider",
            "openai-compatible",
            "--model",
            "controlled-no-network",
            "--token-env",
            "UNUSED_TEST_CREDENTIAL",
            "--base-url",
            "https://example.invalid/v1",
            "--max-requests",
            "50",
        ],
    )
    assert result.exit_code == 0, result.exception
    manifest, samples, report = load_run(output)
    assert report["decision"] == "insufficient_evidence" and report["unresolved_samples"]
    raw = json.loads((private / "outputs.json").read_text())
    assert json.loads((private / "STATE.json").read_text())["status"] == "complete"
    assert (private.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in private.iterdir())
    events = [json.loads(line) for line in (private / "events.jsonl").read_text().splitlines()]
    assert sum(e["kind"] == "sample" for e in events) == len(samples)
    assert sum(e["kind"] == "output" for e in events) == len(raw["outputs"])
    reviews = {
        "manifest_hash": digest(manifest),
        "reviews": {
            sample.sample_id: {
                candidate: {"verdict": "supported", "claim_hashes": [digest(cases[0].required[0].claim_id)]}
                for candidate in sample.generation.candidate_hashes
            }
            for sample in samples
            if sample.generation
        },
    }
    review_file = private / "reviews.json"
    review_file.write_text(json.dumps(reviews))
    audited = tmp_path / "audited"
    args = [
        "audit",
        "--run-dir",
        str(output),
        "--private-outputs",
        str(private / "outputs.json"),
        "--reviews",
        str(review_file),
        "--output-dir",
        str(audited),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.exception
    _, updated, audited_report = load_run(audited)
    expected_decision = (
        "insufficient_evidence" if experiment_version == "v3" else "bounded_projection_follow_up_required"
    )
    assert audited_report["decision"] == expected_decision
    assert not audited_report["unresolved_samples"]
    assert (audited / "FOLLOW_UP.md").is_file() == (experiment_version != "v3")
    for original, reviewed in zip(samples, updated, strict=True):
        assert original.model_dump(exclude={"generation"}) == reviewed.model_dump(exclude={"generation"})
    candidate = audited_report["arms"][minimal.experiment.candidate_arm]
    assert candidate["generation_retention_rate"] == 1.0
    assert candidate["generated_required_claims"] == candidate["generation_required_claims"] == 4
    assert candidate["provider_input_tokens"] == 44 and candidate["provider_output_tokens"] == 28
    assert candidate["requests"] == 4 and candidate["usage_complete"]
    assert candidate["exact_duplicates"] == candidate["near_duplicates"] == 0
    result = runner.invoke(app, ["report", "--run-dir", str(audited)])
    assert result.exit_code == 0 and json.loads(result.output) == audited_report

    # Private raw output and annotation mistakes must be detected before publishing another report.
    for mistake in ("modified_output", "duplicate_call", "unknown_sample", "unknown_review"):
        modified_raw = json.loads(json.dumps(raw))
        modified_reviews = json.loads(json.dumps(reviews))
        if mistake == "modified_output":
            modified_raw["outputs"][0]["output"]["candidates"][0]["text"] = "Tampered output"
        elif mistake == "duplicate_call":
            modified_raw["outputs"].append(modified_raw["outputs"][0])
        elif mistake == "unknown_sample":
            modified_raw["outputs"][0]["sample_id"] = "unknown"
        else:
            modified_reviews["reviews"]["unknown"] = {}
        (private / "outputs.json").write_text(json.dumps(modified_raw))
        review_file.write_text(json.dumps(modified_reviews))
        result = runner.invoke(app, args[:-1] + [str(tmp_path / mistake)])
        assert isinstance(result.exception, EvaluationError)
        assert str(result.exception) == (
            "audit_unknown_sample"
            if mistake == "unknown_sample"
            else "audit_unknown_review"
            if mistake == "unknown_review"
            else "audit_output_hash_mismatch"
        )
        assert not (tmp_path / mistake / "COMPLETE.json").exists()


@pytest.mark.parametrize("interruption", ["cancelled", "request_cap"])
def test_interrupted_sample_preserves_earlier_call_output_without_completed_report(suite, tmp_path, interruption):
    case = next(c for c in suite.cases if c.case_id == "many-source-overflow")
    minimal = replace(
        suite,
        cases=(case,),
        experiment=suite.experiment.model_copy(
            update={
                "arms": ("window-reduction-v1",),
                "budgets": (2048,),
                "live_repetitions": 2,
            }
        ),
    )

    class InterruptedGenerator:
        calls = 0

        async def generate(self, value, /):
            self.calls += 1
            if self.calls == 2:
                if interruption == "cancelled":
                    raise asyncio.CancelledError
                raise EvaluationError("live_request_budget_exhausted")
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(candidates=()),
                usage=InferenceUsage(requests=1, input_tokens=11, output_tokens=7),
            )

    private, output = tmp_path / "private", tmp_path / "run"
    with pytest.raises(asyncio.CancelledError if interruption == "cancelled" else EvaluationError):
        asyncio.run(
            cli.execute(
                minimal,
                cli.manifest_for(minimal, "live"),
                output,
                generator=InterruptedGenerator(),
                raw_dir=private,
            )
        )
    events = [json.loads(line) for line in (private / "events.jsonl").read_text().splitlines()]
    assert len(events) == 1 and events[0]["kind"] == "output" and events[0]["call_index"] == 0
    assert digest(events[0]["output"]) == events[0]["output_hash"]
    assert events[0]["usage"]["input_tokens"] == 11
    assert json.loads((private / "STATE.json").read_text())["status"] == (
        "cancelled" if interruption == "cancelled" else "failed"
    )
    assert not (private / "outputs.json").exists()
    assert not (output / "COMPLETE.json").exists()
    with pytest.raises(EvaluationError):
        load_run(output)


def test_cli_exposes_versioned_commands_and_validates_frozen_inputs():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("validate", "prepare", "run-live", "audit", "report"):
        assert command in result.output
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 0 and '"fixtures": 20' in result.output
    result = runner.invoke(app, ["validate", "--experiment-version", "v2", "--dataset", "validation-v2"])
    assert result.exit_code == 0 and '"fixtures": 8' in result.output


def test_invalid_cli_inputs_do_not_echo_private_paths_or_contents(tmp_path):
    private = tmp_path / "PRIVATE_CREDENTIAL_PATH"
    result = subprocess.run(
        [sys.executable, "-m", "powercontext_eval_evidence_admission.cli", "validate", "--suite-root", str(private)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1
    assert "PRIVATE_CREDENTIAL_PATH" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("provider", [Provider.OPENAI, Provider.ANTHROPIC])
def test_live_adapter_configuration_does_not_make_a_model_request(suite, monkeypatch, provider):
    monkeypatch.setenv("EVIDENCE_TEST_KEY", "synthetic-unused-key")

    async def scenario():
        async with live_generator(
            suite, provider, "synthetic-model", "EVIDENCE_TEST_KEY", "https://example.invalid/v1", 1
        ) as generator:
            assert generator.calls == 0

    asyncio.run(scenario())
