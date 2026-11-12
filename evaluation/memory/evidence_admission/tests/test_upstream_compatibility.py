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

"""Current-runtime outcomes remain separate from the frozen historical measurements."""

from __future__ import annotations

import asyncio

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
)
from pydantic import ValidationError

from powercontext_eval_evidence_admission.manifests import load_suite
from powercontext_eval_evidence_admission.models import Support
from powercontext_eval_evidence_admission.runner import run_sample
from powercontext_eval_evidence_admission.v18 import core
from powercontext_eval_evidence_admission.v18.scoring import score_output


def test_native_output_without_description_is_not_repaired():
    with pytest.raises(ValidationError, match="description"):
        AtomicMemoryExtractionOutput.model_validate(
            {"candidates": [{"kind": "fact", "text": "A release requires two approvals.", "evidence_ids": ["e0"]}]}
        )


def test_exact_text_does_not_auto_approve_an_unreviewed_description():
    async def scenario():
        case = core.suite_for("synthetic-v1").cases[0]
        row, values = await core.prepare_one("synthetic-v1", case, core.ARMS[0], 4096, 0)
        candidate = AtomicMemoryCandidate(
            kind="fact",
            text=case.required[0].text,
            description="The current user owns every repository.",
            evidence_ids=(values[0].evidence[0].evidence_id,),
        )
        result, _ = score_output(
            case,
            values[0],
            tuple(Support.model_validate(s) for s in row["sample"]["supports"]),
            AtomicMemoryExtractionOutput(candidates=(candidate,)),
        )
        assert result["unresolved_candidates"] == 1
        assert result["retained_atoms"] == 0
        assert not result["diagnostic_success"]

    asyncio.run(scenario())


@pytest.mark.parametrize("case_id", ["positional-facts", "observation-overflow"])
def test_current_native_instruction_cost_keeps_new_v2_losses_visible(case_id):
    suite = load_suite(experiment_version="v2")
    case = next(c for c in suite.cases if c.case_id == case_id)
    result = asyncio.run(run_sample(suite, case, "bounded-fragments-v2", 2048, 0))
    assert result.status == "prepared"
    assert result.completeness == "bounded"
    assert result.preparation.required_content_fits
    assert len(result.preparation.missing_claim_hashes) == 1
    assert result.omissions
    assert result.metrics.prepared_tokens <= 2048
    assert result.requests == result.memory_mutations == result.published_cursor == 0


def test_current_native_cost_does_not_force_the_legacy_cell_plan_inside_2k():
    from powercontext_eval_evidence_admission.v6.workflow import InputProbe
    from powercontext_eval_evidence_admission.v16.admission import pack

    async def scenario():
        suite = load_suite(experiment_version="v5")
        case = next(c for c in suite.cases if c.case_id == "many-source-overflow")
        probe = InputProbe()
        sample = await run_sample(suite, case, "bounded-fragments-v3", 2048, 0, generator=probe)
        plan = pack(probe.inputs, sample.supports, 2048, suite.experiment.v3)
        assert not plan.accepted
        assert plan.reason == "indivisible_range_over_budget"
        assert sample.requests == sample.memory_mutations == sample.published_cursor == 0

    asyncio.run(scenario())
