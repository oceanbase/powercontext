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

import pytest

from powercontext.builtin.artifacts.generation import MAX_GENERATION_EVIDENCE_CHARS, GenerationEvidenceKind
from powercontext.builtin.inference.errors import InferenceBudgetExceededError
from powercontext.builtin.review.generation import _bounded_evidence


def test_oversized_evidence_is_rejected_instead_of_being_shortened() -> None:
    with pytest.raises(InferenceBudgetExceededError) as rejected:
        _bounded_evidence(
            evidence_id="source:memory/1",
            kind=GenerationEvidenceKind.SOURCE,
            content="x" * (MAX_GENERATION_EVIDENCE_CHARS + 1),
            operation="skill.generate",
        )

    decision = rejected.value.decision
    assert decision.operation == "skill.generate"
    assert decision.dimension == "input_bytes"
    assert decision.estimated_value == MAX_GENERATION_EVIDENCE_CHARS + 1
    assert decision.limit_value == MAX_GENERATION_EVIDENCE_CHARS
    assert decision.unit == "characters"


def test_evidence_at_the_contract_bound_is_kept_intact() -> None:
    evidence = _bounded_evidence(
        evidence_id="source:memory/1",
        kind=GenerationEvidenceKind.SOURCE,
        content="x" * MAX_GENERATION_EVIDENCE_CHARS,
        operation="experience.generate",
    )

    assert len(evidence.content) == MAX_GENERATION_EVIDENCE_CHARS
