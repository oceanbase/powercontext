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
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate

from powercontext_eval_evidence_admission.admission import token_count
from powercontext_eval_evidence_admission.bounded_v3 import hybrid_admission
from powercontext_eval_evidence_admission.manifests import EvaluationError, field_value, load_suite
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.replay import replay_case
from powercontext_eval_evidence_admission.runner import INSTRUCTIONS, run_sample
from powercontext_eval_evidence_admission.scoring import score_generation, score_preparation


@pytest.fixture(scope="module")
def v3_suite():
    return load_suite(experiment_version="v3")


@pytest.mark.parametrize(
    "case_id,budget",
    [
        ("validation-off-center-facts", 2048),
        ("validation-off-center-facts", 4096),
        ("validation-off-center-facts", 8192),
        ("validation-metadata-pressure", 2048),
    ],
)
def test_v3_repairs_v2_known_losses_under_the_same_budget(case_id, budget):
    suite = load_suite(experiment_version="v3", dataset="validation-v2")
    case = next(c for c in suite.cases if c.case_id == case_id)

    async def scenario():
        before = await run_sample(suite, case, "bounded-fragments-v2", budget, 0)
        after = await run_sample(suite, case, "bounded-fragments-v3", budget, 0)
        assert before.preparation.missing_claim_hashes
        assert after.status == "prepared" and after.preparation.required_content_fits
        assert not after.preparation.missing_claim_hashes
        assert set(before.preparation.retained_claim_hashes) <= set(after.preparation.retained_claim_hashes)
        assert after.metrics.prepared_requests == before.metrics.prepared_requests
        assert after.metrics.prepared_tokens <= budget
        assert after.memory_mutations == after.published_cursor == after.requests == 0
        assert sum(after.metrics.value_bytes_by_family.values()) == after.metrics.admitted_bytes
        units = sum(after.metrics.input_estimator_units_by_family.values())
        assert (units + 3) // 4 == after.metrics.prepared_tokens_total
        assert after.metrics.input_estimator_units_by_family["envelope"] >= 0

    asyncio.run(scenario())


@pytest.mark.parametrize("resource", ["calls", "total_tokens"])
def test_v3_resource_cap_is_not_reported_as_complete(v3_suite, resource):
    case = next(c for c in v3_suite.cases if c.case_id == "many-source-overflow")
    policy = v3_suite.experiment.v3.model_copy(
        update={
            "max_calls_per_sample": 1 if resource == "calls" else 16,
            "total_input_budget_multiplier": 1 if resource == "total_tokens" else 8,
        }
    )
    suite = replace(v3_suite, experiment=v3_suite.experiment.model_copy(update={"v3": policy}))
    sample = asyncio.run(run_sample(suite, case, "bounded-fragments-v3", 2048, 0))
    assert sample.status == "budget_rejected" and sample.reason == "resource_budget"
    assert sample.completeness == "bounded" and sample.preparation.missing_claim_hashes
    assert any(o.reason == "resource_budget" for o in sample.omissions)
    assert sample.metrics.prepared_requests <= policy.max_calls_per_sample
    assert sample.metrics.prepared_tokens_total <= 2048 * policy.total_input_budget_multiplier
    assert sample.memory_mutations == sample.published_cursor == 0


@pytest.mark.parametrize("phase", ["before_read", "projection", "generation"])
def test_v3_cancellation_keeps_no_published_state(v3_suite, phase):
    case = next(c for c in v3_suite.cases if c.cancel_phase == phase)
    sample = asyncio.run(run_sample(v3_suite, case, "bounded-fragments-v3", 2048, 0))
    assert sample.status == sample.completeness == "cancelled"
    assert sample.memory_mutations == sample.published_cursor == 0
    assert sample.preparation is sample.generation is None


def test_v3_duplicate_representative_cannot_cite_other_occurrences(v3_suite):
    case = next(c for c in v3_suite.cases if c.case_id == "repeated-history")

    async def scenario():
        async with replay_case(case) as replay:
            admission, _ = await hybrid_admission(replay, 2048, 512, INSTRUCTIONS, v3_suite.experiment.v3)
            assert admission.completeness == "bounded" and admission.omissions
            admitted_ids = {f.identity for f in admission.admitted}
            repeated_omitted = [
                f
                for f in admission.inventory
                if f.identity not in admitted_ids
                and any(f.field == a.field and f.value == a.value for a in admission.admitted)
            ]
            assert repeated_omitted
            for omitted in repeated_omitted:
                assert not any(
                    s.field_hash == digest(omitted.field)
                    and s.source_hash == digest(omitted.source.source_ref.source_id)
                    and s.byte_start <= omitted.byte_start
                    and omitted.byte_end <= s.byte_end
                    for s in admission.supports
                )
            for fragment in admission.admitted:
                original = field_value(
                    {"content": fragment.source.content, "source_metadata": fragment.source.source_metadata},
                    fragment.field,
                )
                if isinstance(original, str):
                    assert original.encode()[fragment.byte_start : fragment.byte_end] == fragment.value.encode()
            candidate = AtomicMemoryCandidate(
                kind="fact", text=case.required[0].text, evidence_ids=(admission.inputs[0].evidence[0].evidence_id,)
            )
            score = score_generation(case, replay.value, admission, (candidate,))
            assert score.retained_claim_hashes == (digest(case.required[0].claim_id),)
            assert score.unsupported == score.invalid_support == 0
            assert token_count(admission.inputs[0], INSTRUCTIONS) <= 2048

    asyncio.run(scenario())


def test_v3_labels_do_not_change_input_and_missing_spans_cannot_be_audited_into_support(v3_suite):
    case = next(c for c in v3_suite.cases if c.case_id == "impossible-completeness")

    async def scenario():
        a = await run_sample(v3_suite, case, "bounded-fragments-v3", 2048, 0)
        b = await run_sample(
            v3_suite, case.model_copy(update={"required": (), "forbidden": ()}), "bounded-fragments-v3", 2048, 0
        )
        assert a.input_hashes == b.input_hashes
        async with replay_case(case) as replay:
            admission, _ = await hybrid_admission(replay, 2048, 512, INSTRUCTIONS, v3_suite.experiment.v3)
            preparation = score_preparation(case, replay.value, admission, 2048, INSTRUCTIONS)
            missing = next(c for c in case.required if digest(c.claim_id) in preparation.missing_claim_hashes)
            candidate = AtomicMemoryCandidate(
                kind="fact", text=missing.text, evidence_ids=(admission.inputs[0].evidence[0].evidence_id,)
            )
            assert score_generation(case, replay.value, admission, (candidate,)).unsupported == 1
            paraphrase = candidate.model_copy(update={"text": "An unprovided claim."})
            review = {
                digest(paraphrase.model_dump(mode="json")): {
                    "verdict": "supported",
                    "claim_hashes": [digest(missing.claim_id)],
                }
            }
            with pytest.raises(EvaluationError, match="without_admitted_support"):
                score_generation(case, replay.value, admission, (paraphrase,), audit=review)

    asyncio.run(scenario())
