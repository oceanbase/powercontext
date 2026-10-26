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

from powercontext_eval_evidence_admission.admission import admit
from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import Claim, Piece, digest
from powercontext_eval_evidence_admission.replay import replay_case
from powercontext_eval_evidence_admission.runner import INSTRUCTIONS, run_sample
from powercontext_eval_evidence_admission.scoring import score_generation, score_preparation


@pytest.mark.parametrize("inside_claim", [True, False])
def test_literal_retention_is_independent_of_whole_claim_and_anchored_to_its_range(small_case, inside_claim):
    literal = "--listen=9000"
    text = f"启动参数 {literal} 必须获得双人批准。"
    body = f"无关示例 {literal}。\n{text}"
    source = small_case.sources[0].model_copy(update={"pieces": (Piece(text=body),)})
    claim = Claim(claim_id="literal", source_id=source.source_id, text=text, exact_literals=(literal,))
    case = small_case.model_copy(update={"sources": (source,), "required": (claim,)})

    async def scenario():
        async with replay_case(case) as replay:
            full = await admit(
                replay.value,
                replay.materializations,
                budget=8192,
                fragment_bytes=512,
                instructions=INSTRUCTIONS,
                bounded=False,
            )
            original = next(f for f in full.inventory if f.field == claim.field)
            start = body.index(literal, body.index(text)) if inside_claim else body.index(literal)
            fragment = replace(
                original,
                value=literal,
                byte_start=len(body[:start].encode()),
                byte_end=len(body[: start + len(literal)].encode()),
                char_start=start,
                char_end=start + len(literal),
            )
            score = score_preparation(case, replay.value, replace(full, admitted=(fragment,)), 8192, INSTRUCTIONS)
            assert score.missing_claim_hashes and not score.retained_claim_hashes
            assert score.exact_literals_total == 1
            assert score.exact_literals_retained == int(inside_claim)

    asyncio.run(scenario())


def test_impossible_required_content_cannot_be_scored_complete(suite):
    case = next(c for c in suite.cases if c.case_id == "impossible-completeness")

    async def scenario():
        result = await run_sample(suite, case, "bounded-fragments-v1", 8192, 0)
        assert result.status == "prepared" and result.completeness == "bounded"
        assert not result.preparation.required_content_fits
        assert result.preparation.missing_claim_hashes and result.omissions

    asyncio.run(scenario())


def test_pipeline_output_is_scored_for_support_duplicates_and_unresolved_semantics(suite, small_case):
    class Extractor:
        async def generate(self, value, /):
            citation = (value.evidence[0].evidence_id,)
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(kind="fact", text=small_case.required[0].text, evidence_ids=citation),
                        AtomicMemoryCandidate(kind="fact", text=small_case.required[0].text, evidence_ids=citation),
                        AtomicMemoryCandidate(
                            kind="fact", text="Unlabeled paraphrase requires audit.", evidence_ids=citation
                        ),
                    )
                )
            )

    result = asyncio.run(run_sample(suite, small_case, "complete-projection-v1", 2048, 0, generator=Extractor()))
    assert result.status == "generated" and result.generation.retained_claim_hashes
    assert result.generation.exact_duplicates == 1 and result.generation.unresolved == 1
    assert result.output_hashes and result.generation.support


def test_forged_instruction_cannot_be_accepted_as_current_user_fact(suite):
    case = next(c for c in suite.cases if c.case_id == "forged-role-marker")

    async def scenario():
        async with replay_case(case) as replay:
            admission = await admit(
                replay.value,
                replay.materializations,
                budget=2048,
                fragment_bytes=512,
                instructions=INSTRUCTIONS,
                bounded=True,
            )
            candidate = AtomicMemoryCandidate(kind="fact", text=case.forbidden[0].text, evidence_ids=("source:1",))
            score = score_generation(case, replay.value, admission, (candidate,))
            assert score.unsupported == score.false_attribution == 1

    asyncio.run(scenario())


def test_audit_cannot_invent_support_for_omitted_fact(suite):
    case = next(c for c in suite.cases if c.case_id == "positional-facts")

    async def scenario():
        async with replay_case(case) as replay:
            admission = await admit(
                replay.value,
                replay.materializations,
                budget=2048,
                fragment_bytes=512,
                instructions=INSTRUCTIONS,
                bounded=True,
            )
            score = score_preparation(case, replay.value, admission, 2048, INSTRUCTIONS)
            # Reference a genuine fragment that does not contain the beginning fact.
            unrelated = next(item for item in admission.admitted if item.field != case.required[0].field)
            candidate = AtomicMemoryCandidate(
                kind="fact", text="Human-reviewed paraphrase.", evidence_ids=(f"fragment:{unrelated.identity}",)
            )
            review = {
                digest(candidate.model_dump(mode="json")): {
                    "verdict": "supported",
                    "claim_hashes": [digest(case.required[0].claim_id)],
                }
            }
            with pytest.raises(EvaluationError, match="without_admitted_support"):
                score_generation(case, replay.value, admission, (candidate,), audit=review)
            assert score.required_count == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("preserve_literal", [True, False])
def test_generation_literals_bind_to_supported_claims(suite, small_case, preserve_literal):
    source = small_case.sources[0].model_copy(update={"pieces": (Piece(text="Set --timeout=037 for deployment."),)})
    claim = Claim(
        claim_id="timeout",
        source_id=source.source_id,
        text="Set --timeout=037 for deployment.",
        exact_literals=("--timeout=037",),
        accepted_candidates=("Set a deployment timeout.",),
    )
    case = small_case.model_copy(update={"sources": (source,), "required": (claim,)})

    class Generator:
        async def generate(self, value, /):
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text=claim.text if preserve_literal else claim.accepted_candidates[0],
                            evidence_ids=(value.evidence[0].evidence_id,),
                        ),
                    )
                )
            )

    sample = asyncio.run(run_sample(suite, case, "bounded-fragments-v1", 2048, 0, generator=Generator()))
    expected = (digest({"claim": "timeout", "literal": 0}),) if preserve_literal else ()
    assert sample.generation.retained_literal_hashes == expected
    assert bool(sample.generation.retained_claim_hashes) == preserve_literal
    assert sample.generation.unsupported == int(not preserve_literal)
