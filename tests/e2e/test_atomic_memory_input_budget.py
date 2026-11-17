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

"""Input fitting preserves complete comparisons, rejection diagnostics and publication."""

from __future__ import annotations

import asyncio
import json

import pytest

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import (
    GenerationResult,
    InvalidInferenceOutputError,
    TokenEstimator,
    TokenEstimatorProfile,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.processing_diagnostics import diagnostics_of
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts

TEXT = "Rollout strategy requires an independent approval."


def render_input(value: AtomicMemoryExtractionInput | AtomicMemoryReconciliationInput) -> str:
    return json.dumps({"tokens": 1 if isinstance(value, AtomicMemoryExtractionInput) else 8 + 16 * len(value.related)})


class Extractor:
    async def generate(self, value: AtomicMemoryExtractionInput, /):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=(
                    AtomicMemoryCandidate(
                        kind="fact", text=TEXT, description=TEXT, evidence_ids=(value.evidence[0].evidence_id,)
                    ),
                )
            )
        )


class Reconciler:
    def __init__(self) -> None:
        self.inputs: list[AtomicMemoryReconciliationInput] = []

    async def generate(self, value: AtomicMemoryReconciliationInput, /):
        self.inputs.append(value)
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create",
                compared_ids=tuple(item.item_id for item in value.related),
                content=AtomicMemoryReconciliationContent(
                    kind=value.proposal.kind,
                    text=value.proposal.text,
                    description=TEXT,
                ),
                evidence_ids=value.proposal.evidence_ids,
                reason="Keep an independent fact.",
            )
        )


@pytest.mark.parametrize(
    ("related_count", "budget", "accepted"), [(8, 72, True), (8, 24, True), (8, 23, False), (0, 8, True), (0, 7, False)]
)
def test_custom_rendered_budget_keeps_all_comparisons_or_rejects_without_publication(
    tmp_path, related_count: int, budget: int, accepted: bool
) -> None:
    async def scenario() -> None:
        reconciler = Reconciler()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=Extractor(),
            reconciler=reconciler,
            estimator=TokenEstimator(
                TokenEstimatorProfile(estimator_id="rendered-budget-fixture", version="1"),
                lambda text: json.loads(text.rsplit("\n", 1)[-1])["tokens"],
            ),
            input_renderer=render_input,
        )
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}"),
            runtime=RuntimeConfig(atomic_memory_related_mode="fts", atomic_memory_input_tokens_limit=budget),
        )
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            context = await contexts.get("budget")
            memory = contexts.atomic_memory.for_scope("budget")
            if related_count:
                await contexts.records.create_atomic_memories(
                    "budget",
                    tuple(
                        {"kind": "fact", "text": f"Rollout strategy requires approval {index}."}
                        for index in range(related_count)
                    ),
                )
                await contexts.process_memory("budget", related_count)
            before = (await memory.list(limit=100)).items
            await contexts.records.capture_source("budget", "content", "new-fact", TEXT, {})
            if accepted:
                result = await contexts.process_memory("budget", 1)
                assert result.current_cursor == related_count + 1
                after = (await memory.list(limit=100)).items
                assert len(after) == related_count + 1
                assert any(item.artifact.content.text == TEXT for item in after)
                compared = {
                    (read.ref.artifact_id, read.ref.revision)
                    for value in reconciler.inputs
                    for item in value.related
                    for read in item.original_refs
                }
                assert compared == {(item.ref.artifact_id, item.ref.revision) for item in before}
                assert all(json.loads(render_input(value))["tokens"] <= budget for value in reconciler.inputs)
            else:
                with pytest.raises(InvalidInferenceOutputError) as captured:
                    await contexts.process_memory("budget", 1)
                assert captured.value.operation == "atomic-memory-input-budget"
                diagnostic = diagnostics_of(captured.value)
                assert diagnostic is not None
                assert diagnostic.effective_budget == budget
                assert diagnostic.estimated_input_tokens == (24 if related_count else 8)
                assert diagnostic.failure_location == "atomic_memory.reconcile"
                assert (await context.triggers.cursor()).sequence == related_count
                assert (await memory.list(limit=100)).items == before

    asyncio.run(scenario())
