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

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError
from powercontext.builtin.runtime.atomic_memory_processing import AtomicMemoryProcessingConfig

from powercontext_eval_evidence_admission.admission import token_count
from powercontext_eval_evidence_admission.replay import replay_case
from powercontext_eval_evidence_admission.runner import INSTRUCTIONS, pipeline_for, run_sample, window_admission


@pytest.mark.parametrize("case_id", ["many-source-overflow", "single-source-overflow"])
def test_window_control_matches_real_processor_budget_attempts_without_memory_writes(suite, case_id):
    case = next(case for case in suite.cases if case.case_id == case_id)

    class EmptyExtractor:
        def __init__(self):
            self.inputs = []

        async def generate(self, value, /):
            self.inputs.append(value)
            return GenerationResult(output=AtomicMemoryExtractionOutput())

    async def scenario():
        async with replay_case(case) as replay:
            expected, attempts = await window_admission(replay, 2048, 512)
        extractor = EmptyExtractor()
        async with replay_case(case) as replay:
            processor = replay.contexts.atomic_memory_processor
            processor.pipeline = pipeline_for(extractor)
            processor.config = AtomicMemoryProcessingConfig(input_tokens_limit=2048, related_mode="fts")
            context = await replay.contexts.get(replay.scope_id)
            observed = []
            for after, through, estimated, fits in attempts:
                assert (await context.triggers.cursor()).sequence == after
                try:
                    result = await replay.contexts.process_memory(replay.scope_id, len(case.sources))
                except InvalidInferenceOutputError as error:
                    assert error.operation == "atomic-memory-input-budget" and not fits
                    observed.append(False)
                else:
                    assert fits and result.current_cursor == through
                    observed.append(True)
                    assert token_count(extractor.inputs[-1], INSTRUCTIONS) == estimated
            assert observed == [row[3] for row in attempts]
            assert bool(expected.accepted) == (observed[-1] is True)
            memories, _ = await replay.mutations()
            assert memories == 0

    asyncio.run(scenario())


def test_controls_reject_single_source_but_candidate_prepares_generation(suite):
    case = next(case for case in suite.cases if case.case_id == "single-source-overflow")

    async def scenario():
        for arm in suite.experiment.arms:
            result = await run_sample(suite, case, arm, 4096, 0)
            assert result.requests == 0 and result.memory_mutations == result.published_cursor == 0
            assert result.status == ("prepared" if arm == "bounded-fragments-v1" else "budget_rejected")

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["missing_source", "invalid_projection"])
def test_unavailable_evidence_reports_identity_and_failed_preparation_cost(suite, fault):
    case = next(case for case in suite.cases if case.fault == fault)
    result = asyncio.run(run_sample(suite, case, "complete-projection-v1", 4096, 0))
    assert result.status == result.completeness == "unavailable"
    assert result.source_hashes and result.materialization_seconds > 0
    assert result.requests == 0 and result.generation is None
