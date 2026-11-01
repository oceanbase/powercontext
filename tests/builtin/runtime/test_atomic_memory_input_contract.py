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

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryComparisonItem,
    AtomicMemoryReconciliationInput,
)
from powercontext.builtin.inference.input_encoding import (
    InputEncoding,
    InputEncodingMode,
    decode_input,
)
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, PrivacyPolicy
from powercontext.builtin.inference.tokens import character_token_estimator
from powercontext.builtin.inference.usage import collect_input_encodings
from powercontext.builtin.runtime.atomic_memory_input import ATOMIC_MEMORY_INPUT_SCHEMA, render_atomic_memory_input
from powercontext.sources import SourceRef

_DETAIL = "Keep independent source identities, exact dates, conditions, and repeated occurrences. " * 8


def _render(
    value: AtomicMemoryExtractionInput | AtomicMemoryReconciliationInput,
    *,
    mode: InputEncodingMode,
) -> InputEncoding:
    with collect_input_encodings() as reports:
        prompt = render_atomic_memory_input(
            value,
            modes={"atomic_memory.extract": mode, "atomic_memory.reconcile": mode},
            estimator=character_token_estimator(),
            policy=PrivacyPolicy(InferenceProtectionConfig()),
            observe=True,
        )
    assert len(reports) == 1
    return InputEncoding(prompt=prompt, report=reports[0])


def _input() -> AtomicMemoryExtractionInput:
    metadata = {"session": "session-42", "conditions": _DETAIL}
    return AtomicMemoryExtractionInput(
        evidence=tuple(
            AtomicMemoryEvidence(
                evidence_id=f"source:{index}",
                source_ref=SourceRef(source_type="content", source_id=f"turn-{index}"),
                journal_position=index,
                content={"source_id": f"turn-{index}", "content": f"Independent fact {index}", "metadata": metadata},
                source_metadata={"name": f"turn-{index}", "metadata": metadata},
                via_artifact=(
                    ArtifactRef(family="atomic-memory", artifact_id="memory-1", revision=3) if index == 2 else None
                ),
            )
            for index in range(1, 9)
        )
    )


def test_active_saves_complete_prompt_and_restores_all_ordered_evidence() -> None:
    value = _input()
    original = value.model_dump_json()
    result = _render(value, mode="active")

    assert result.report.reason == "applied"
    assert result.report.replacements >= 2
    assert result.report.shared_values >= 1
    assert result.report.selected_bytes < result.report.original_bytes
    assert result.report.selected_estimated_tokens < result.report.original_estimated_tokens
    assert decode_input(result.prompt, schema=ATOMIC_MEMORY_INPUT_SCHEMA, stage="atomic_memory.extract") == original
    assert value.model_dump_json() == original
    restored = AtomicMemoryExtractionInput.model_validate_json(
        decode_input(result.prompt, schema=ATOMIC_MEMORY_INPUT_SCHEMA, stage="atomic_memory.extract")
    )
    assert [item.evidence_id for item in restored.evidence] == [f"source:{index}" for index in range(1, 9)]
    assert restored.evidence[1].via_artifact == value.evidence[1].via_artifact


def test_reconciliation_retains_proposal_related_order_and_evidence_bindings() -> None:
    evidence = _input().evidence
    proposal = AtomicMemoryComparisonItem(
        item_id="proposal",
        kind="constraint",
        text=_DETAIL,
        description="保留独立约束与原始引用。",
        original_refs=(),
        evidence_ids=("source:1", "source:2"),
    )
    related = (
        AtomicMemoryComparisonItem(
            item_id="related-2",
            kind="fact",
            text=_DETAIL,
            description="Historical fact with its own source.",
            original_refs=(),
            evidence_ids=("source:5",),
        ),
        AtomicMemoryComparisonItem(
            item_id="related-1", kind="fact", text="A different fact", original_refs=(), evidence_ids=("source:3",)
        ),
    )
    value = AtomicMemoryReconciliationInput(proposal=proposal, related=related, evidence=evidence)
    result = _render(value, mode="active")

    assert result.report.stage == "atomic_memory.reconcile"
    assert result.report.reason == "applied"
    assert (
        decode_input(result.prompt, schema=ATOMIC_MEMORY_INPUT_SCHEMA, stage="atomic_memory.reconcile")
        == value.model_dump_json()
    )


@pytest.mark.parametrize("mode", ["shadow", "active"])
def test_renderer_rejects_invalid_typed_input_even_when_json_is_reversible(mode: InputEncodingMode) -> None:
    original = _input()
    value = original.model_copy(
        update={
            "evidence": (original.evidence[0].model_copy(update={"journal_position": 0}), *original.evidence[1:]),
        }
    )
    result = _render(value, mode=mode)

    assert result.prompt == value.model_dump_json()
    assert result.report.reason == "roundtrip_failed"
    assert result.report.candidate_bytes is not None
    assert result.report.selected_estimated_tokens == result.report.original_estimated_tokens
