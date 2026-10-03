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

"""Deterministic continuation assembly and injected-byte measurement.

Assembly is the half of the benchmark that needs no host, so these tests pin the
properties a reader has to trust: which turns each method retains, that a fact
counts as delivered when the method carried it in any form, that injected bytes
are exact, and that dropping happens at item boundaries rather than mid-sentence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from work_continuity_fixtures import analysis_lock, budget_through

from powercontext_eval.benchmarks.work_continuity.arms import (
    COMPACTED_TRANSCRIPT,
    FULL_TRANSCRIPT,
    INFORMAL_SUMMARY,
    ROLLOVER_HANDOFF,
    ContinuationArm,
)
from powercontext_eval.benchmarks.work_continuity.assembly import (
    NEXT_ACTION_LABEL,
    OMISSIONS_LABEL,
    STATE_LABEL,
    TRANSCRIPT_LABEL,
    assemble_context,
)
from powercontext_eval.benchmarks.work_continuity.catalog import ContinuationTask, TaskCatalog
from powercontext_eval.benchmarks.work_continuity.quality import NEXT_ACTION_REQUIREMENT

GENEROUS_BUDGET = 16_000


@pytest.fixture
def audit(tmp_path: Path) -> ContinuationTask:
    return TaskCatalog.load(analysis_lock(tmp_path)).require("t-audit")


def test_full_transcript_delivers_every_turn(audit: ContinuationTask) -> None:
    context = assemble_context(audit, FULL_TRANSCRIPT, max_bytes=GENEROUS_BUDGET)

    assert context.delivered_turn_numbers == (1, 2, 3, 4, 5, 6, 7, 8)
    assert context.dropped_item_ids == ()
    assert context.truncated is False
    assert "CONTINUATION METHOD: full-transcript-v1 (full-transcript)" in context.text
    assert TRANSCRIPT_LABEL in context.text


def test_compacted_transcript_keeps_the_opening_and_the_tail(audit: ContinuationTask) -> None:
    context = assemble_context(audit, COMPACTED_TRANSCRIPT, max_bytes=GENEROUS_BUDGET)

    assert context.delivered_turn_numbers == (1, 2, 5, 6, 7, 8)
    assert 3 not in context.delivered_turn_numbers
    assert 4 not in context.delivered_turn_numbers


def test_informal_summary_keeps_only_the_final_turn(audit: ContinuationTask) -> None:
    context = assemble_context(audit, INFORMAL_SUMMARY, max_bytes=GENEROUS_BUDGET)

    assert context.delivered_turn_numbers == (8,)


def test_rollover_handoff_carries_state_and_next_action_without_a_transcript(audit: ContinuationTask) -> None:
    context = assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=GENEROUS_BUDGET)

    assert context.delivered_turn_numbers == ()
    assert TRANSCRIPT_LABEL not in context.text
    assert STATE_LABEL in context.text
    assert NEXT_ACTION_LABEL in context.text
    assert OMISSIONS_LABEL in context.text
    assert context.carries_next_action is True
    assert context.delivered_fact_ids == ("h1", "h2", "h3")


def test_a_transcript_method_never_carries_a_next_action(audit: ContinuationTask) -> None:
    for arm in (FULL_TRANSCRIPT, COMPACTED_TRANSCRIPT, INFORMAL_SUMMARY):
        context = assemble_context(audit, arm, max_bytes=GENEROUS_BUDGET)
        assert context.carries_next_action is False
        assert NEXT_ACTION_LABEL not in context.text


def test_only_the_treatment_attaches_a_rollover_quality_report(audit: ContinuationTask) -> None:
    transcript = assemble_context(audit, FULL_TRANSCRIPT, max_bytes=GENEROUS_BUDGET)

    assert transcript.quality is None
    assert transcript.draft_quality is None

    context = assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=GENEROUS_BUDGET)

    assert context.quality is not None
    assert context.quality.satisfied is True
    assert context.draft_quality is not None
    assert context.draft_quality.satisfied is True


def test_a_ceiling_that_empties_the_context_cannot_certify_it(audit: ContinuationTask) -> None:
    """The quality verdict must describe what was delivered, not what was drafted.

    Rating the draft let a one-byte ceiling report content that satisfies RFC 1783
    while the session received a truncated header and nothing else.
    """

    context = assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=1)

    assert context.delivered_item_ids == ("header",)
    assert context.quality is not None
    assert context.quality.satisfied is False
    assert context.draft_quality is not None
    assert context.draft_quality.satisfied is True


def test_a_ceiling_that_drops_only_the_next_action_fails_the_next_action_requirement(
    audit: ContinuationTask,
) -> None:
    ceiling = budget_through(audit, ROLLOVER_HANDOFF, "state:h3")
    context = assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=ceiling)

    assert "next_action" in context.dropped_item_ids
    assert context.carries_next_action is False
    assert context.quality is not None
    assert NEXT_ACTION_REQUIREMENT in context.quality.violations_by_requirement
    assert context.quality.satisfied is False


def test_a_fact_counts_as_delivered_when_the_turn_it_comes_from_is_delivered(audit: ContinuationTask) -> None:
    """A transcript method carries no state items, yet it did deliver the facts.

    Crediting only state items would turn a presentation difference into a
    fabricated evidence gap for every transcript method.
    """

    context = assemble_context(audit, FULL_TRANSCRIPT, max_bytes=GENEROUS_BUDGET)

    assert all(not item_id.startswith("state:") for item_id in context.delivered_item_ids)
    assert context.delivered_fact_ids == ("h1", "h2", "h3")


def test_a_fact_whose_turn_was_dropped_is_not_counted_as_delivered(audit: ContinuationTask) -> None:
    context = assemble_context(audit, COMPACTED_TRANSCRIPT, max_bytes=GENEROUS_BUDGET)

    assert context.delivered_fact_ids == ("h1", "h2")
    assert "h3" not in context.delivered_fact_ids


def test_superseded_turns_are_reported_when_a_method_still_carries_them(audit: ContinuationTask) -> None:
    assert assemble_context(audit, FULL_TRANSCRIPT, max_bytes=GENEROUS_BUDGET).delivered_superseded_turns == (1,)
    assert assemble_context(audit, COMPACTED_TRANSCRIPT, max_bytes=GENEROUS_BUDGET).delivered_superseded_turns == (1,)
    assert assemble_context(audit, INFORMAL_SUMMARY, max_bytes=GENEROUS_BUDGET).delivered_superseded_turns == ()
    assert assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=GENEROUS_BUDGET).delivered_superseded_turns == ()


def test_the_handoff_excludes_a_superseded_fact_from_its_state(audit: ContinuationTask) -> None:
    context = assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=GENEROUS_BUDGET)

    assert "o1" not in context.delivered_fact_ids
    assert "30 second base" not in context.text


@pytest.mark.parametrize("arm", [FULL_TRANSCRIPT, COMPACTED_TRANSCRIPT, INFORMAL_SUMMARY, ROLLOVER_HANDOFF])
def test_injected_bytes_are_the_exact_utf8_size_of_the_delivered_text(
    audit: ContinuationTask, arm: ContinuationArm
) -> None:
    context = assemble_context(audit, arm, max_bytes=GENEROUS_BUDGET)

    assert context.injected_bytes == len(context.text.encode("utf-8"))
    assert context.line_count == len(context.text.splitlines())


def test_a_tight_ceiling_drops_whole_items_from_the_end_and_reports_them(audit: ContinuationTask) -> None:
    ceiling = budget_through(audit, ROLLOVER_HANDOFF, "state:h1")
    context = assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=ceiling)

    assert context.truncated is True
    assert "state:h2" in context.dropped_item_ids
    assert "state:h1" in context.delivered_item_ids
    assert context.injected_bytes <= ceiling
    # A label is an ordinary droppable item, so no empty heading survives a drop.
    assert not context.text.rstrip().endswith(STATE_LABEL)


def test_a_ceiling_smaller_than_one_item_still_produces_a_measurable_context(audit: ContinuationTask) -> None:
    context = assemble_context(audit, FULL_TRANSCRIPT, max_bytes=10)

    assert context.delivered_item_ids == ("header",)
    assert context.truncated is True
    assert context.injected_bytes <= 10
    assert context.text  # never empty, so an injected-byte number always exists


def test_the_cut_lands_on_a_utf8_boundary(audit: ContinuationTask) -> None:
    """A partial multi-byte character must not be counted as delivered bytes."""

    for ceiling in range(1, 200):
        context = assemble_context(audit, ROLLOVER_HANDOFF, max_bytes=ceiling)
        assert context.injected_bytes == len(context.text.encode("utf-8"))
        assert context.text == context.text.encode("utf-8").decode("utf-8")
        assert context.injected_bytes <= ceiling


def test_assembly_is_deterministic(audit: ContinuationTask) -> None:
    first = assemble_context(audit, COMPACTED_TRANSCRIPT, max_bytes=1_000)
    second = assemble_context(audit, COMPACTED_TRANSCRIPT, max_bytes=1_000)

    assert (first.text, first.injected_bytes) == (second.text, second.injected_bytes)
    assert first.delivered_item_ids == second.delivered_item_ids
    assert first.dropped_item_ids == second.dropped_item_ids


def test_the_header_names_the_method_so_a_context_is_self_describing(audit: ContinuationTask) -> None:
    context = assemble_context(audit, INFORMAL_SUMMARY, max_bytes=GENEROUS_BUDGET)

    assert "CONTINUATION METHOD: informal-summary-v1 (informal-summary)" in context.text
    assert context.method == "informal-summary"
    assert context.arm_id == "informal-summary-v1"
    assert context.task_id == "t-audit"


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_assembly_refuses_a_non_positive_budget(audit: ContinuationTask, max_bytes: int) -> None:
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        assemble_context(audit, FULL_TRANSCRIPT, max_bytes=max_bytes)
