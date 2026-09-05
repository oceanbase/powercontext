# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.topic_memory import TopicMemoryContent
from powercontext.builtin.artifacts.topic_memory.generation import (
    BudgetedTopicMemoryGenerator,
    TopicMemoryEvidence,
    TopicMemoryGenerationError,
    TopicMemoryGlobalOutput,
    TopicMemoryHistoricalSlot,
    TopicMemoryPlanItem,
    TopicMemoryPlannerOutput,
    TopicMemoryProbe,
    TopicMemoryProbeInput,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryReconcileInput,
    TopicMemoryTemporaryOutput,
    topic_memory_stage_budget,
)
from powercontext.builtin.artifacts.topic_memory.relatedness import (
    topic_memory_lexical_signature,
    topic_memory_related_components,
    topic_memory_vector_centroid,
)
from powercontext.builtin.inference import GenerationResult, TokenEstimator, character_token_estimator


class _Generator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, value: TopicMemoryProbeInput, /) -> GenerationResult[TopicMemoryProbeOutput]:
        self.calls += 1
        return GenerationResult(output=TopicMemoryProbeOutput(probes=()))


def _content(title: str, detail: str) -> TopicMemoryContent:
    return TopicMemoryContent(title=title, summary=f"Summary for {title}", detail=detail)


def test_stage_budget_applies_run_level_reserve_and_user_wire_cap() -> None:
    budget = topic_memory_stage_budget(
        context_window_tokens=10_000,
        max_requests=4,
        model_settings={"max_tokens": 200},
    )

    assert budget.input_tokens_limit == 8_000
    assert budget.transcript_reserve == 2_000
    assert budget.output_tokens_limit == 926
    assert budget.max_output_tokens_per_request == 200
    assert budget.output_tokens_limit + (budget.max_output_tokens_per_request + 128) * 3 <= budget.transcript_reserve

    with pytest.raises(TopicMemoryGenerationError, match="invalid_max_tokens"):
        topic_memory_stage_budget(context_window_tokens=10_000, max_requests=2, model_settings={"max_tokens": True})
    with pytest.raises(TopicMemoryGenerationError, match="output_budget_exceeded"):
        topic_memory_stage_budget(context_window_tokens=100, max_requests=2, model_settings={})


def test_budgeted_generator_rejects_before_provider_call() -> None:
    async def scenario() -> None:
        delegate = _Generator()
        budget = topic_memory_stage_budget(context_window_tokens=1_000, max_requests=1, model_settings={})
        generator = BudgetedTopicMemoryGenerator(
            delegate,
            estimator=TokenEstimator(character_token_estimator().profile, lambda value: len(value)),
            budget=budget,
            fixed_prompt="fixed",
        )
        evidence = TopicMemoryEvidence(evidence_id="e1", source_type="content", content="x" * 1_000)

        with pytest.raises(TopicMemoryGenerationError, match="input_budget_exceeded"):
            await generator.generate(TopicMemoryProbeInput(evidence=(evidence,)))
        assert delegate.calls == 0

    asyncio.run(scenario())


def test_schema_caps_and_opaque_evidence_are_fail_closed() -> None:
    with pytest.raises(ValidationError):
        TopicMemoryProbeOutput(
            probes=tuple(TopicMemoryProbe(query=str(index), evidence_ids=("e1",)) for index in range(21))
        )
    with pytest.raises(ValidationError):
        TopicMemoryProposal(
            content=_content("topic", "detail"),
            evidence_ids=("e1", "e1"),
        )
    with pytest.raises(ValidationError, match=r"artifact_id|revision"):
        TopicMemoryProposal.model_validate({
            "artifact_id": "model-owned-id",
            "content": {"title": "topic", "summary": "summary", "detail": "detail", "revision": 4},
            "evidence_ids": ["e1"],
        })
    proposals = tuple(
        TopicMemoryProposal(content=_content(f"topic-{index}", "detail"), evidence_ids=("e1",))
        for index in range(21)
    )
    with pytest.raises(ValidationError):
        TopicMemoryGlobalOutput(proposals=proposals)
    with pytest.raises(ValidationError):
        TopicMemoryPlannerOutput(
            items=tuple(TopicMemoryPlanItem(probe_ids=(f"probe-{index}",)) for index in range(21))
        )
    with pytest.raises(ValidationError):
        TopicMemoryTemporaryOutput(proposals=proposals)
    with pytest.raises(ValidationError):
        TopicMemoryReconcileInput(component_id="component", proposals=proposals)
    with pytest.raises(ValidationError):
        TopicMemoryReconcileInput(
            component_id="component",
            proposals=proposals[:1],
            historical=tuple(
                TopicMemoryHistoricalSlot(
                    candidate_id=f"candidate-{index}",
                    title="title",
                    summary="summary",
                    detail="detail",
                )
                for index in range(21)
            ),
        )


def test_full_content_signature_scans_tail_and_remains_bounded() -> None:
    content = _content("front", f"{' '.join(f'term{index}' for index in range(80))} tailwinner " + "tailwinner " * 20)

    signature = topic_memory_lexical_signature(content)

    assert "tailwinner" in signature.split()
    assert len(signature) <= 8_192
    assert len(signature.split()) <= 64

    low_weight_tail = _content(
        "front",
        " ".join(f"frequent{index} frequent{index}" for index in range(70)) + " unique-low-weight-tail",
    )
    assert "unique-low-weight-tail" not in topic_memory_lexical_signature(low_weight_tail).split()
    with pytest.raises(ValueError, match="no bounded lexical signature"):
        topic_memory_lexical_signature(TopicMemoryContent(title="---", summary="***", detail="###"))


def test_related_components_share_lexical_and_vector_admission() -> None:
    proposals = (
        TopicMemoryProposal(proposal_id="p1", content=_content("alpha", "shared durable signal"), evidence_ids=("e1",)),
        TopicMemoryProposal(proposal_id="p2", content=_content("beta", "shared durable signal"), evidence_ids=("e2",)),
        TopicMemoryProposal(
            proposal_id="p3",
            content=TopicMemoryContent(title="isolated", summary="orthogonal", detail="unrelated"),
            evidence_ids=("e3",),
        ),
    )

    components = topic_memory_related_components(
        proposals,
        secondary_candidates=(frozenset(), frozenset(), frozenset()),
        vectors=((1.0, 0.0), (1.0, 0.0), (-1.0, 0.0)),
    )

    assert components == ((0, 1), (2,))
    assert topic_memory_vector_centroid(
        (1.0, 0.0),
        ((10, (1.0, 0.0)),),
        topic_length=5,
    ) == (1.0, 0.0)

    weighted = topic_memory_vector_centroid(
        (1.0, 0.0),
        ((1, (0.0, 1.0)),),
        topic_length=3,
    )
    assert weighted == pytest.approx((0.948683298, 0.316227766))


def test_related_components_connect_new_topics_to_exact_history_without_merging_slots() -> None:
    proposals = (
        TopicMemoryProposal(
            proposal_id="update-a",
            candidate_id="history-a",
            content=TopicMemoryContent(title="alpha", summary="bravo", detail="charlie"),
            evidence_ids=("e1",),
        ),
        TopicMemoryProposal(
            proposal_id="update-b",
            candidate_id="history-b",
            content=TopicMemoryContent(title="delta", summary="echo", detail="foxtrot"),
            evidence_ids=("e2",),
        ),
        TopicMemoryProposal(
            proposal_id="new",
            content=TopicMemoryContent(title="golf", summary="hotel", detail="india"),
            evidence_ids=("e3",),
        ),
        TopicMemoryProposal(
            proposal_id="isolated",
            content=TopicMemoryContent(title="juliet", summary="kilo", detail="lima"),
            evidence_ids=("e4",),
        ),
    )

    components = topic_memory_related_components(
        proposals,
        secondary_candidates=(
            frozenset({"history-a"}),
            frozenset({"history-b"}),
            frozenset({"history-a", "history-b"}),
            frozenset(),
        ),
    )

    assert components == ((0, 1, 2), (3,))
