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
from dataclasses import replace

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult

from powercontext_eval_evidence_admission.cli import execute, manifest_for
from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import Sample, digest
from powercontext_eval_evidence_admission.report import aggregate, load_run
from powercontext_eval_evidence_admission.runner import run_sample


@pytest.fixture(scope="module")
def decision_run(suite):
    cases = tuple(c for c in suite.cases if c.case_id in {"small-complete", "single-source-overflow"})
    minimal = replace(
        suite, cases=cases, experiment=suite.experiment.model_copy(update={"budgets": (4096,), "live_repetitions": 2})
    )
    manifest = manifest_for(minimal, "live")
    # Construct gate inputs locally; this is not a committed freeze or a provider execution.
    manifest["code"] = {**manifest["code"], "frozen_committed": True}

    class Extractor:
        async def generate(self, value, /):
            claim = cases[0].required[0].text
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text=claim,
                            description=(claim)[:200],
                            evidence_ids=tuple(item.evidence_id for item in value.evidence),
                        ),
                    )
                )
            )

    async def run():
        return tuple(
            [
                await run_sample(minimal, case, arm, 4096, repetition, generator=Extractor())
                for case in cases
                for arm in minimal.experiment.arms
                for repetition in range(2)
            ]
        )

    return minimal, manifest, asyncio.run(run())


def test_complete_controlled_generation_can_recommend_a_follow_up(decision_run):
    suite, manifest, samples = decision_run
    report = aggregate(suite, samples, manifest)
    assert report["decision"] == "bounded_projection_follow_up_required"
    assert report["coverage_complete"] and report["live_coverage_complete"]
    assert report["one_source_improvement_samples"]


@pytest.mark.parametrize("failure", ["failed", "prepared", "partial_window_unscored", "unresolved"])
@pytest.mark.parametrize("arm", ["complete-projection-v1", "window-reduction-v1", "bounded-fragments-v1"])
def test_incomplete_generation_or_audit_in_any_arm_blocks_a_decision(decision_run, failure, arm):
    suite, manifest, samples = decision_run
    small_hash = next(s.case_hash for s in samples if s.status == "generated" and s.arm == "complete-projection-v1")
    target = next(s for s in samples if s.case_hash == small_hash and s.arm == arm)
    if failure == "unresolved":
        changed = target.model_copy(update={"generation": target.generation.model_copy(update={"unresolved": 1})})
    elif failure == "partial_window_unscored":
        changed = target.model_copy(update={"status": "budget_rejected", "reason": "input_budget", "generation": None})
    else:
        changed = target.model_copy(update={"status": failure, "generation": None})
    rows = tuple(changed if s.sample_id == target.sample_id else s for s in samples)
    report = aggregate(suite, rows, manifest)
    assert report["coverage_complete"]
    assert report["decision"] == "insufficient_evidence"
    assert target.sample_id in report["unresolved_samples" if failure == "unresolved" else "live_incomplete_samples"]


@pytest.mark.parametrize(
    "failure", ["unsupported", "false_attribution", "invalid_support", "required_loss", "literal_loss"]
)
def test_completed_candidate_with_unacceptable_loss_or_risk_recommends_no_change(decision_run, failure):
    suite, manifest, samples = decision_run
    target = next(s for s in samples if s.arm == "bounded-fragments-v1")
    if failure == "required_loss":
        changed = target.model_copy(
            update={"preparation": target.preparation.model_copy(update={"missing_claim_hashes": ("missing",)})}
        )
    elif failure == "literal_loss":
        changed = target.model_copy(
            update={"preparation": target.preparation.model_copy(update={"exact_literals_total": 1})}
        )
    else:
        changed = target.model_copy(update={"generation": target.generation.model_copy(update={failure: 1})})
    report = aggregate(suite, tuple(changed if s.sample_id == target.sample_id else s for s in samples), manifest)
    assert report["live_coverage_complete"] and report["decision"] == "no_production_change"


def test_missing_sample_or_uncommitted_freeze_cannot_authorize_a_decision(decision_run):
    suite, manifest, samples = decision_run
    assert aggregate(suite, samples[:-1], manifest)["decision"] == "insufficient_evidence"
    manifest = {**manifest, "code": {**manifest["code"], "frozen_committed": False}}
    assert aggregate(suite, samples, manifest)["decision"] == "insufficient_evidence"


def test_nonrepeatable_input_cannot_be_recommended(decision_run):
    suite, manifest, samples = decision_run
    target = next(s for s in samples if s.arm == "bounded-fragments-v1")
    changed = target.model_copy(update={"input_hashes": ("different-preparation",)})
    report = aggregate(suite, tuple(changed if s.sample_id == target.sample_id else s for s in samples), manifest)
    assert report["live_coverage_complete"] and not report["deterministic_preparation"]
    assert report["decision"] == "no_production_change"


def test_failure_without_one_source_improvement_does_not_warrant_a_follow_up(decision_run):
    suite, manifest, samples = decision_run
    small = next(s for s in samples if s.arm == "complete-projection-v1" and s.status == "generated")
    suite = replace(suite, cases=tuple(c for c in suite.cases if c.case_id == "small-complete"))
    rows = tuple(s for s in samples if s.case_hash == small.case_hash)
    report = aggregate(suite, rows, manifest)
    assert report["coverage_complete"] and report["live_coverage_complete"]
    assert not report["one_source_improvement_samples"]
    assert report["decision"] == "no_production_change"


def test_offline_run_is_replayable_aggregate_only_and_cannot_authorize_production(suite, small_case, tmp_path):
    cases = (small_case, next(c for c in suite.cases if c.cancel_phase == "generation"))
    minimal = replace(
        suite, cases=cases, experiment=suite.experiment.model_copy(update={"budgets": (2048,), "repetitions": 2})
    )
    output = tmp_path / "run"
    manifest = manifest_for(minimal, "prepare")
    report = asyncio.run(execute(minimal, manifest, output))
    stored_manifest, samples, stored_report = load_run(output)
    assert stored_manifest == manifest and stored_report == report
    assert report["decision"] == "insufficient_evidence"
    assert report["coverage_complete"] and report["deterministic_preparation"] and report["cancellation_valid"]
    assert all(isinstance(sample, Sample) for sample in samples)
    public = "\n".join(path.read_text() for path in output.iterdir())
    assert small_case.sources[0].capture()[0] not in public
    assert '"source_id"' not in public and '"source_ref"' not in public
    assert all(sample.requests == 0 for sample in samples)
    for arm in report["arms"].values():
        assert arm["generated_required_claims"] is arm["generation_retention_rate"] is None
        assert arm["provider_input_tokens"] is arm["provider_output_tokens"] is arm["usage_complete"] is None
    with pytest.raises(EvaluationError, match="existing_output"):
        asyncio.run(execute(minimal, manifest, output))


def test_modified_or_incomplete_reports_are_not_consumable(tmp_path):
    with pytest.raises(EvaluationError):
        load_run(tmp_path)


def test_a_critical_failure_cannot_be_averaged_away(suite, small_case):
    sample = Sample(
        sample_id="a" * 64,
        case_hash="b" * 64,
        arm="bounded-fragments-v1",
        budget=2048,
        repetition=0,
        completeness="cancelled",
        status="cancelled",
        memory_mutations=1,
    )
    report = aggregate(suite, (sample,), manifest_for(suite, "prepare"))
    assert report["critical_failures"] == [sample.sample_id]
    assert report["decision"] == "insufficient_evidence"


def test_prepared_fact_without_generated_fact_cannot_authorize_follow_up(decision_run):
    suite, manifest, samples = decision_run
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}

    class EmptyGenerator:
        async def generate(self, value, /):
            return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=()))

    async def replace_candidates():
        result = []
        for s in samples:
            if s.arm == suite.experiment.candidate_arm:
                s = await run_sample(
                    suite, cases[s.case_hash], s.arm, s.budget, s.repetition, generator=EmptyGenerator()
                )
            result.append(s)
        return tuple(result)

    rows = asyncio.run(replace_candidates())
    report = aggregate(suite, rows, manifest)
    assert report["live_coverage_complete"] and not report["required_retention_failures"]
    assert report["generation_retention_failures"] and report["decision"] == "no_production_change"
    assert not report["one_source_improvement_samples"]
