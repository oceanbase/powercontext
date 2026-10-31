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
from contextlib import asynccontextmanager

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult, InferenceUsage
from powercontext.builtin.inference.errors import InferenceUnavailableError
from typer.testing import CliRunner

from powercontext_eval_evidence_admission import cli, live_evaluation
from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.report import aggregate, load_run
from powercontext_eval_evidence_admission.runner import run_sample

# Actual outputs from the frozen 2026-10-27 DeepSeek smoke. Keep the failures, not a repaired citation.
RECORDED_RANGE_IDS = {
    "bounded-fragments-v2": "8949192149f8af58464f8157895549c51e9ab0b4fc9fb256f45d2aa7de96bace",
    "bounded-fragments-v3": "bfab8fc2ab997b08fd317ce6f75d20856a0c3f907fafe0a2cb5b9418b498b3ea",
}
PARAPHRASE = "Release approval requires two maintainers."


class Generator:
    def __init__(self, citation=None, text=PARAPHRASE):
        self.citation, self.text = citation, text
        self.inputs = []

    async def generate(self, value, /):
        self.inputs.append(value)
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=(
                    AtomicMemoryCandidate(
                        kind="constraint",
                        text=self.text,
                        evidence_ids=(self.citation or value.evidence[0].evidence_id,),
                    ),
                )
            ),
            usage=InferenceUsage(requests=1, input_tokens=17, output_tokens=11),
        )


@pytest.mark.parametrize("arm", RECORDED_RANGE_IDS)
@pytest.mark.parametrize("version", ["v3", "v4", "v5"])
def test_recorded_range_citations_stay_failed_and_returned_usage_is_independent(arm, version):
    suite = load_suite(experiment_version=version)
    case = next(c for c in suite.cases if c.case_id == "single-source-overflow")
    generator = Generator(RECORDED_RANGE_IDS[arm])
    sample = asyncio.run(run_sample(suite, case, arm, 4096, 0, generator=generator))
    assert sample.status == "failed" and sample.generation is None
    assert sample.requests == sample.generation_calls == 1
    assert sample.provider_input_tokens == 17 and sample.provider_output_tokens == 11
    ranges = generator.inputs[0].evidence[0].content["ranges"]
    assert all(("id" not in item) == (version != "v3") for item in ranges)
    if version == "v3":
        assert generator.citation in {item["id"] for item in ranges}
        assert not sample.usage_complete
    else:
        assert sample.usage_complete
        assert sample.failure_phase == "output_validation"
        assert sample.failure_kind == "invalid_evidence_reference"
        report = aggregate(suite, (sample,), cli.manifest_for(suite, "live"))
        assert sample.sample_id in report["critical_failures"]
        assert report["generation_failures"][0]["kind"] == "invalid_evidence_reference"


@pytest.mark.parametrize("arm", RECORDED_RANGE_IDS)
@pytest.mark.parametrize("version", ["v4", "v5"])
def test_new_bundle_accepts_only_its_supplied_evidence_id_and_preserves_support(arm, version):
    suite = load_suite(experiment_version=version)
    case = next(c for c in suite.cases if c.case_id == "single-source-overflow")
    generator = Generator(text=case.required[0].text)
    sample = asyncio.run(run_sample(suite, case, arm, 4096, 0, generator=generator))
    assert sample.status == "generated" and sample.usage_complete
    assert sample.generation.retained_claim_hashes == (digest(case.required[0].claim_id),)
    assert sample.generation.invalid_support == 0
    assert sample.preparation.missing_claim_hashes == ()
    assert sample.metrics.prepared_tokens <= sample.budget
    assert all("id" not in item for value in generator.inputs for e in value.evidence for item in e.content["ranges"])
    assert sample.memory_mutations == sample.published_cursor == 0


def test_failed_request_without_returned_usage_is_unknown_not_zero_cost():
    suite = load_suite(experiment_version="v4")

    class Unavailable:
        async def generate(self, value, /):
            raise InferenceUnavailableError("test")

    sample = asyncio.run(run_sample(suite, suite.cases[0], "bounded-fragments-v3", 4096, 0, generator=Unavailable()))
    assert sample.status == "failed" and sample.failure_kind == "inference_failed"
    assert sample.generation_calls == 1 and not sample.usage_complete
    assert sample.provider_input_tokens is sample.provider_output_tokens is None


@pytest.fixture(params=["v4", "v5"])
def smoke_chain(tmp_path, monkeypatch, request):
    suite = load_suite(experiment_version=request.param)
    identity = {**cli.code_identity(suite), "frozen_committed": True}
    monkeypatch.setattr(cli, "code_identity", lambda _: identity)
    monkeypatch.setattr(live_evaluation, "code_identity", lambda _: identity)

    @asynccontextmanager
    async def provider(*args, **kwargs):
        yield Generator()

    monkeypatch.setattr(cli, "live_generator", provider)
    original, private, audited = tmp_path / "smoke", tmp_path / "private", tmp_path / "audited"
    args = [
        "run-smoke",
        "--experiment-version",
        suite.version,
        "--provider",
        "openai-compatible",
        "--model",
        "controlled-no-network",
        "--base-url",
        "https://example.invalid",
        "--token-env",
        "UNUSED",
        "--max-requests",
        "8",
        "--output-dir",
        str(original),
        "--audit-dir",
        str(private),
    ]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.exception
    manifest, samples, report = load_run(original)
    reviews = {
        "manifest_hash": digest(manifest),
        "reviews": {
            s.sample_id: {
                candidate: {"verdict": "supported", "claim_hashes": [digest("approval")]}
                for candidate in s.generation.candidate_hashes
            }
            for s in samples
            if s.generation
        },
    }
    review_path = private / "reviews.json"
    review_path.write_text(json.dumps(reviews))
    result = CliRunner().invoke(
        cli.app,
        [
            "audit",
            "--experiment-version",
            suite.version,
            "--run-dir",
            str(original),
            "--private-outputs",
            str(private / "outputs.json"),
            "--reviews",
            str(review_path),
            "--output-dir",
            str(audited),
        ],
    )
    assert result.exit_code == 0, result.exception
    return suite, original, audited, report


def test_smoke_audit_resolves_semantics_without_changing_original_or_entering_formal(smoke_chain):
    suite, original, audited, before = smoke_chain
    manifest, samples, report = load_run(audited)
    assert manifest["mode"] == "audited_smoke"
    assert before["smoke"]["execution_complete"] and not before["smoke"]["ready_for_formal"]
    assert before["smoke"]["unresolved_samples"] == 8
    assert report["smoke"]["unresolved_samples"] == 0 and report["smoke"]["ready_for_formal"]
    assert report["decision"] == "insufficient_evidence"
    assert load_run(original)[2] == before
    for candidate in samples:
        if candidate.arm == suite.experiment.candidate_arm:
            assert candidate.generation.retained_claim_hashes == (digest("approval"),)
    assert live_evaluation.verify_smoke(suite, original, audited, manifest["provider"])
    result = CliRunner().invoke(cli.app, ["report", "--experiment-version", suite.version, "--run-dir", str(audited)])
    assert result.exit_code == 0, result.exception
    with pytest.raises(EvaluationError, match="unexpected_run_mode"):
        live_evaluation.combined_live(suite, (original,), (original,), (audited,))
    with pytest.raises(EvaluationError, match="smoke_provider_model_or_endpoint_mismatch"):
        live_evaluation.verify_smoke(suite, original, audited, {**manifest["provider"], "model": "different"})


def test_smoke_audit_cannot_change_original_output_identity(smoke_chain):
    _suite, original, audited, _ = smoke_chain
    parent, rows, _ = load_run(original)
    manifest, samples, _ = load_run(audited)
    changed = (samples[0].model_copy(update={"output_hashes": ("0" * 64,)}), *samples[1:])
    with pytest.raises(EvaluationError, match="audit_changed_execution_or_usage"):
        live_evaluation.verify_audit_derivation(parent, rows, manifest, changed, mode="audited_smoke")


def test_formal_v4_requires_successful_audited_smoke_before_provider_initialization(tmp_path):
    result = CliRunner().invoke(
        cli.app,
        [
            "run-live",
            "--experiment-version",
            "v4",
            "--provider",
            "openai-compatible",
            "--model",
            "controlled-no-network",
            "--base-url",
            "https://example.invalid",
            "--token-env",
            "UNUSED",
            "--max-requests",
            "1",
            "--output-dir",
            str(tmp_path / "run"),
            "--audit-dir",
            str(tmp_path / "private"),
        ],
    )
    # A committed freeze is checked before the smoke proof; both fail before provider initialization.
    assert result.exit_code != 0
    assert isinstance(result.exception, EvaluationError)
    assert str(result.exception) in {
        "successful_audited_smoke_required",
        "live_requires_committed_fixture_policy_and_code_freeze",
    }
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize("defect", ["unsupported", "invalid_support", "required_loss", "usage", "cap"])
def test_audited_smoke_quality_or_usage_failure_never_allows_formal(smoke_chain, defect):
    suite, _original, audited, _ = smoke_chain
    manifest, samples, _ = load_run(audited)
    cases = {case.case_id: case for case in suite.cases}
    from dataclasses import replace

    selected = replace(suite, cases=tuple(cases[label] for label in manifest["smoke"]["cases"]))
    rows = list(samples)
    index = next(i for i, s in enumerate(rows) if s.arm == suite.experiment.candidate_arm)
    sample = rows[index]
    if defect in {"unsupported", "invalid_support"}:
        rows[index] = sample.model_copy(update={"generation": sample.generation.model_copy(update={defect: 1})})
    elif defect == "required_loss":
        rows[index] = sample.model_copy(
            update={"generation": sample.generation.model_copy(update={"retained_claim_hashes": ()})}
        )
    elif defect == "usage":
        rows[index] = sample.model_copy(update={"usage_complete": False})
    else:
        manifest = {**manifest, "provider": {**manifest["provider"], "max_requests": 1}}
    report = aggregate(selected, tuple(rows), manifest)
    assert not report["smoke"]["ready_for_formal"]
    assert report["decision"] == "insufficient_evidence"


def test_audit_rejects_reviews_for_failed_generation(tmp_path, monkeypatch):
    suite = load_suite(experiment_version="v4")
    identity = {**cli.code_identity(suite), "frozen_committed": True}
    monkeypatch.setattr(cli, "code_identity", lambda _: identity)

    @asynccontextmanager
    async def provider(*args, **kwargs):
        yield Generator(citation="nested-range-id-is-not-an-evidence-id")

    monkeypatch.setattr(cli, "live_generator", provider)
    original, private = tmp_path / "original", tmp_path / "private"
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "run-smoke",
            "--experiment-version",
            "v4",
            "--provider",
            "openai-compatible",
            "--model",
            "controlled-no-network",
            "--base-url",
            "https://example.invalid",
            "--token-env",
            "UNUSED",
            "--max-requests",
            "8",
            "--output-dir",
            str(original),
            "--audit-dir",
            str(private),
        ],
    )
    assert result.exit_code == 0, result.exception
    manifest, samples, report = load_run(original)
    failed = next(s for s in samples if s.status == "failed")
    assert not report["smoke"]["execution_complete"]
    raw = json.loads((private / "outputs.json").read_text())
    candidate = next(row for row in raw["outputs"] if row["sample_id"] == failed.sample_id)["output"]["candidates"][0]
    reviews = {
        "manifest_hash": digest(manifest),
        "reviews": {
            failed.sample_id: {digest(candidate): {"verdict": "supported", "claim_hashes": [digest("approval")]}}
        },
    }
    path = private / "reviews.json"
    path.write_text(json.dumps(reviews))
    result = runner.invoke(
        cli.app,
        [
            "audit",
            "--experiment-version",
            "v4",
            "--run-dir",
            str(original),
            "--private-outputs",
            str(private / "outputs.json"),
            "--reviews",
            str(path),
            "--output-dir",
            str(tmp_path / "audited"),
        ],
    )
    assert result.exit_code != 0
    assert str(result.exception) == "audit_unknown_review"


@pytest.mark.parametrize("arm", RECORDED_RANGE_IDS)
def test_v5_fixes_v4_control_regression_by_preserving_original_selected_ranges(arm):
    original = load_suite(experiment_version="v3", dataset="validation-v3")
    revised = load_suite(experiment_version="v5", dataset="validation-v3")
    case = next(c for c in original.cases if c.case_id == "probe-shifted-middle")

    async def scenario():
        before = await run_sample(original, case, arm, 4096, 0)
        after = await run_sample(revised, case, arm, 4096, 0)
        assert len(after.preparation.retained_claim_hashes) == len(before.preparation.retained_claim_hashes) == 3
        assert before.preparation == after.preparation
        assert [s.model_dump(exclude={"evidence_id"}) for s in before.supports] == [
            s.model_dump(exclude={"evidence_id"}) for s in after.supports
        ]
        assert after.window_attempts == before.window_attempts
        assert after.metrics.selection_prepared_tokens_total == before.metrics.prepared_tokens_total
        assert after.metrics.prepared_tokens_total <= after.metrics.selection_prepared_tokens_total
        assert after.status == before.status and after.completeness == before.completeness

    asyncio.run(scenario())
