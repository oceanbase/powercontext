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

from powercontext_eval_evidence_admission.admission import token_count
from powercontext_eval_evidence_admission.bounded_v2 import hybrid_admission, record_ranges
from powercontext_eval_evidence_admission.manifests import EvaluationError, field_value, load_suite
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.replay import replay_case
from powercontext_eval_evidence_admission.runner import INSTRUCTIONS, run_sample
from powercontext_eval_evidence_admission.scoring import score_generation, score_preparation


@pytest.fixture(scope="module")
def v2_suite():
    return load_suite(experiment_version="v2")


@pytest.mark.parametrize(
    "case_id,budget",
    [
        ("many-source-overflow", 2048),
        ("many-source-overflow", 4096),
        ("positional-facts", 2048),
        ("repeated-history", 2048),
        ("mixed-scaffolding", 2048),
        ("mixed-scaffolding", 4096),
        ("mixed-scaffolding", 8192),
        ("observation-overflow", 2048),
    ],
)
def test_v2_retains_all_jointly_feasible_facts_in_v1_regressions(v2_suite, case_id, budget, historical_budget_context):
    case = next(c for c in v2_suite.cases if c.case_id == case_id)
    result = asyncio.run(run_sample(v2_suite, case, "bounded-fragments-v2", budget, 0))
    assert result.status == "prepared" and result.preparation.required_content_fits
    assert not result.preparation.missing_claim_hashes
    assert result.preparation.exact_literals_retained == result.preparation.exact_literals_total
    assert result.metrics.prepared_tokens <= budget
    assert result.metrics.prepared_requests <= v2_suite.experiment.v2.max_calls_per_sample
    assert result.metrics.prepared_tokens_total <= v2_suite.experiment.v2.total_input_budget_multiplier * budget
    assert result.memory_mutations == result.published_cursor == result.requests == 0


@pytest.mark.parametrize(
    "text,limit", [("", 7), ("one\r\ntwo\n", 7), ("中文🙂é\r\n" * 40, 7), ("x" * 512 + "\r\nlast", 512)]
)
def test_record_ranges_preserve_original_bytes_codepoints_and_crlf(text, limit):
    ranges = tuple(record_ranges(text, limit))
    assert "".join(text[left:right] for left, right, _, _ in ranges) == text
    for left, right, start, end in ranges:
        assert text.encode()[start:end].decode() == text[left:right]
        assert end - start <= limit
        assert not (right < len(text) and text[right - 1 : right + 1] == "\r\n")


@pytest.mark.parametrize("resource", ["calls", "total_tokens"])
def test_resource_exhaustion_never_claims_complete_coverage(v2_suite, resource):
    case = next(c for c in v2_suite.cases if c.case_id == "many-source-overflow")
    policy = v2_suite.experiment.v2.model_copy(
        update={
            "max_calls_per_sample": 1 if resource == "calls" else 16,
            "total_input_budget_multiplier": 1 if resource == "total_tokens" else 8,
        }
    )
    suite = replace(v2_suite, experiment=v2_suite.experiment.model_copy(update={"v2": policy}))
    result = asyncio.run(run_sample(suite, case, "bounded-fragments-v2", 2048, 0))
    assert result.status == "budget_rejected" and result.reason == "resource_budget"
    assert result.completeness == "bounded" and result.preparation.missing_claim_hashes
    assert any(o.reason == "resource_budget" for o in result.omissions)
    assert result.metrics.prepared_requests <= policy.max_calls_per_sample
    assert result.metrics.prepared_tokens_total <= policy.total_input_budget_multiplier * 2048
    assert result.memory_mutations == result.published_cursor == 0


def test_single_source_fallback_continues_to_later_normal_sources(v2_suite, small_case):
    large = next(c for c in v2_suite.cases if c.case_id == "single-source-overflow")
    case = large.model_copy(
        update={
            "sources": (*large.sources, *small_case.sources),
            "required": (large.required[0], small_case.required[0].model_copy(update={"claim_id": "later-approval"})),
        }
    )
    result = asyncio.run(run_sample(v2_suite, case, "bounded-fragments-v2", 2048, 0))
    assert result.status == "prepared" and not result.preparation.missing_claim_hashes
    assert result.metrics.prepared_requests == 2
    assert any(s.evidence_id.startswith("fragment-bundle:") for s in result.supports)
    assert any(s.evidence_id.startswith("source:") for s in result.supports)


def test_bundle_citations_cover_only_admitted_ranges_and_replay_deterministically(v2_suite, historical_budget_context):
    case = next(c for c in v2_suite.cases if c.case_id == "mixed-scaffolding")

    async def scenario():
        hashes = []
        for _ in range(2):
            async with replay_case(case) as replay:
                admission, _ = await hybrid_admission(
                    replay, 2048, 512, historical_budget_context, v2_suite.experiment.v2
                )
                value = admission.inputs[0]
                assert token_count(value, historical_budget_context) <= 2048
                hashes.append(digest(value.model_dump(mode="json")))
                evidence = value.evidence[0]
                assert evidence.evidence_id.startswith("fragment-bundle:")
                for fragment in admission.admitted:
                    original = field_value(
                        {"content": fragment.source.content, "source_metadata": fragment.source.source_metadata},
                        fragment.field,
                    )
                    if isinstance(original, str):
                        assert original.encode()[fragment.byte_start : fragment.byte_end] == fragment.value.encode()
                candidate = AtomicMemoryCandidate(
                    kind="fact",
                    text=case.required[0].text,
                    description=(case.required[0].text)[:200],
                    evidence_ids=(evidence.evidence_id,),
                )
                score = score_generation(case, replay.value, admission, (candidate,))
                assert score.retained_claim_hashes == (digest(case.required[0].claim_id),)
                assert score.invalid_support == score.unsupported == 0
                assert all(s in admission.supports for s in score.support[0])
        assert hashes[0] == hashes[1]

    asyncio.run(scenario())


def test_bundle_citation_and_human_review_cannot_supply_omitted_content(v2_suite):
    case = next(c for c in v2_suite.cases if c.case_id == "impossible-completeness")

    async def scenario():
        async with replay_case(case) as replay:
            admission, _ = await hybrid_admission(replay, 2048, 512, INSTRUCTIONS, v2_suite.experiment.v2)
            preparation = score_preparation(case, replay.value, admission, 2048, INSTRUCTIONS)
            missing = next(c for c in case.required if digest(c.claim_id) in preparation.missing_claim_hashes)
            evidence_id = admission.inputs[0].evidence[0].evidence_id
            candidate = AtomicMemoryCandidate(
                kind="fact", text=missing.text, description=(missing.text)[:200], evidence_ids=(evidence_id,)
            )
            assert score_generation(case, replay.value, admission, (candidate,)).unsupported == 1
            paraphrase = candidate.model_copy(update={"text": "Human-reviewed unprovided statement."})
            review = {
                digest(paraphrase.model_dump(mode="json")): {
                    "verdict": "supported",
                    "claim_hashes": [digest(missing.claim_id)],
                }
            }
            with pytest.raises(EvaluationError, match="without_admitted_support"):
                score_generation(case, replay.value, admission, (paraphrase,), audit=review)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["before_read", "projection", "generation"])
def test_v2_preserves_real_cancellation_and_no_publication(v2_suite, phase):
    case = next(c for c in v2_suite.cases if c.cancel_phase == phase)
    result = asyncio.run(run_sample(v2_suite, case, "bounded-fragments-v2", 2048, 0))
    assert result.status == result.completeness == "cancelled"
    assert result.memory_mutations == result.published_cursor == 0
    assert result.preparation is result.generation is None


def test_v2_pipeline_scores_real_bundle_ids(v2_suite, historical_budget_context):
    case = next(c for c in v2_suite.cases if c.case_id == "mixed-scaffolding")

    class Extractor:
        async def generate(self, value, /):
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text=case.required[0].text,
                            description=(case.required[0].text)[:200],
                            evidence_ids=(value.evidence[0].evidence_id,),
                        ),
                    )
                )
            )

    result = asyncio.run(run_sample(v2_suite, case, "bounded-fragments-v2", 2048, 0, generator=Extractor()))
    assert result.status == "generated" and result.generation.retained_claim_hashes
    assert result.generation.invalid_support == result.generation.unsupported == 0
