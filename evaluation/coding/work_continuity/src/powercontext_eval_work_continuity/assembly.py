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

"""Deterministic continuation-context assembly and injected-byte measurement.

Every method renders the same task material into an ordered list of items, under
the same byte ceiling, with no model in the loop. That makes injected bytes a
property of the method and its budget rather than of a provider, and it lets the
same assembled context be compared byte for byte across hosts.

Rendering is deliberately plain. A richer presentation would make injected bytes
depend on formatting choices instead of on what the method chose to carry.

Quality is checked twice for a rollover draft. ``draft_quality`` describes the
material the method intended to deliver, and ``quality`` describes only what
survived the byte ceiling. The delivered report is the one a reader must trust,
because a budget that drops the next action cannot be allowed to certify the
context it emptied.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from powercontext_eval_work_continuity.arms import ROLLOVER_HANDOFF, ContinuationArm
from powercontext_eval_work_continuity.catalog import ContinuationTask, StateFact
from powercontext_eval_work_continuity.quality import (
    HandoffContent,
    HandoffStatement,
    QualityReport,
    check_rollover_quality,
)

# Section labels are ordinary items, so a method that runs out of budget can drop
# a label together with its content instead of leaving an empty heading behind.
TRANSCRIPT_LABEL = "TRANSCRIPT:"
STATE_LABEL = "STATE:"
NEXT_ACTION_LABEL = "NEXT ACTION:"
OMISSIONS_LABEL = "OMISSIONS:"


@dataclass(frozen=True)
class AssembledItem:
    """One droppable unit of the delivered continuation context."""

    item_id: str
    kind: str
    text: str


@dataclass(frozen=True)
class ContinuationContext:
    """The continuation context one arm delivers for one task."""

    task_id: str
    arm_id: str
    method: str
    max_bytes: int
    text: str
    truncated: bool
    delivered_item_ids: tuple[str, ...]
    dropped_item_ids: tuple[str, ...]
    delivered_turn_numbers: tuple[int, ...]
    delivered_fact_ids: tuple[str, ...]
    unavailable_fact_ids: tuple[str, ...]
    delivered_superseded_turns: tuple[int, ...]
    carries_next_action: bool
    quality: QualityReport | None
    draft_quality: QualityReport | None

    @property
    def injected_bytes(self) -> int:
        """Return the exact UTF-8 size of what the fresh session would receive."""

        return len(self.text.encode("utf-8"))

    @property
    def content_sha256(self) -> str:
        """Return the digest of the delivered bytes a recording is bound to."""

        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def line_count(self) -> int:
        return len(self.text.splitlines())

    @property
    def next_action_lost_to_the_budget(self) -> bool:
        """Return whether the byte ceiling removed the next action the arm carried.

        The next action is an ordinary droppable item, so a ceiling that runs out
        before reaching it leaves a context whose state survived and whose action
        did not. That loss belongs to the ceiling, and reading only the facts the
        action depends on would report it as a vague action instead.
        """

        return "next_action" in self.dropped_item_ids


def assemble_context(task: ContinuationTask, arm: ContinuationArm, *, max_bytes: int) -> ContinuationContext:
    """Render one task for one arm under one byte ceiling."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    items = build_items(task, arm)
    kept, dropped, text, truncated = _fit(items, max_bytes=max_bytes)
    kept_ids = {item.item_id for item in kept}
    delivered_turns = tuple(turn for turn in task.turn_numbers if f"turn:{turn}" in kept_ids)
    # A fact counts as delivered when either the method carried its state item or
    # it carried the turn the fact is drawn from. Measuring only the state items
    # would credit a transcript method with delivering nothing, which would turn a
    # presentation difference into a fabricated evidence gap.
    delivered_facts = tuple(
        fact.fact_id
        for fact in task.required_state_facts
        if fact.evidence is not None
        and (f"state:{fact.fact_id}" in kept_ids or int(fact.evidence.split(":", 1)[1]) in delivered_turns)
    )
    superseded = tuple(turn for turn in task.obsolete_source_turns if turn in delivered_turns)
    is_handoff = arm.method == "rollover-handoff"
    return ContinuationContext(
        task_id=task.task_id,
        arm_id=arm.arm_id,
        method=arm.method,
        max_bytes=max_bytes,
        text=text,
        truncated=truncated,
        delivered_item_ids=tuple(item.item_id for item in kept),
        dropped_item_ids=tuple(item.item_id for item in dropped),
        delivered_turn_numbers=delivered_turns,
        delivered_fact_ids=delivered_facts,
        unavailable_fact_ids=task.unavailable_fact_ids,
        delivered_superseded_turns=superseded,
        carries_next_action=arm.carries_next_action and "next_action" in kept_ids,
        quality=_handoff_quality(task, kept_ids) if is_handoff else None,
        draft_quality=_handoff_quality(task, (item.item_id for item in items)) if is_handoff else None,
    )


def handoff_content(task: ContinuationTask, delivered_item_ids: Iterable[str]) -> HandoffContent:
    """Project the delivered items onto the RFC 0048 Handoff fields they fill in.

    Only the fields that survived assembly are reported, so a byte ceiling that
    drops the state or the next action shows up as missing content rather than as
    a draft that was merely written and never delivered. The disposition stays the
    draft's own claim, because that claim is exactly what the delivered fields are
    then checked against.
    """

    kept = set(delivered_item_ids)
    statements = tuple(
        HandoffStatement(text=fact.text, evidence=(fact.evidence,))
        for fact in task.required_state_facts
        if fact.evidence is not None and f"state:{fact.fact_id}" in kept
    )
    omissions = tuple(omission for index, omission in enumerate(handoff_omissions(task)) if f"omission:{index}" in kept)
    return HandoffContent(
        objective=task.objective if "objective" in kept else "",
        state_statements=statements,
        disposition="continuable",
        next_action=(
            HandoffStatement(
                text=task.expected_next_action.text,
                evidence=tuple(f"fact:{fact_id}" for fact_id in task.expected_next_action.required_fact_ids),
            )
            if "next_action" in kept
            else None
        ),
        omissions=omissions,
    )


def rollover_handoff_content(task: ContinuationTask) -> HandoffContent:
    """Project a task onto the complete Handoff draft the treatment intends to send."""

    return handoff_content(task, (item.item_id for item in build_items(task, ROLLOVER_HANDOFF)))


def handoff_omissions(task: ContinuationTask) -> tuple[str, ...]:
    """Return the omissions a rollover draft discloses, the unverifiable ones included.

    Facts whose evidence cannot be produced are not asserted as current state;
    RFC 1783 asks the draft to record an omission instead of presenting
    unverifiable material as fact.
    """

    by_id = {fact.fact_id: fact for fact in task.required_state_facts}
    omissions = list(task.known_omissions)
    omissions.extend(
        f'unavailable evidence for "{by_id[entry.fact_id].text}": {entry.reason} (pointer: {entry.pointer})'
        for entry in task.unavailable_evidence
    )
    return tuple(omissions)


def build_items(task: ContinuationTask, arm: ContinuationArm) -> tuple[AssembledItem, ...]:
    """Return the ordered, droppable items one arm renders for one task."""

    items: list[AssembledItem] = [
        AssembledItem(
            item_id="header",
            kind="header",
            text=f"CONTINUATION METHOD: {arm.arm_id} ({arm.method})",
        ),
        AssembledItem(item_id="objective", kind="objective", text=f"OBJECTIVE: {task.objective}"),
    ]
    selected_turns = _selected_turns(task, arm)
    if selected_turns:
        items.append(AssembledItem(item_id="label:transcript", kind="label", text=TRANSCRIPT_LABEL))
        for number in selected_turns:
            turn = task.turn(number)
            if turn is None:
                continue
            items.append(
                AssembledItem(
                    item_id=f"turn:{number}",
                    kind="transcript",
                    text=f"[turn {number}] {turn.role}: {turn.text}",
                )
            )
    if arm.carries_state_facts:
        deliverable = [fact for fact in task.required_state_facts if fact.evidence is not None]
        if deliverable:
            items.append(AssembledItem(item_id="label:state", kind="label", text=STATE_LABEL))
            items.extend(_state_item(fact) for fact in deliverable)
    if arm.carries_next_action:
        items.append(AssembledItem(item_id="label:next_action", kind="label", text=NEXT_ACTION_LABEL))
        items.append(
            AssembledItem(
                item_id="next_action",
                kind="next_action",
                text=_next_action_text(task),
            )
        )
    if arm.carries_omissions:
        omissions = handoff_omissions(task)
        if omissions:
            items.append(AssembledItem(item_id="label:omissions", kind="label", text=OMISSIONS_LABEL))
            items.extend(
                AssembledItem(item_id=f"omission:{index}", kind="omission", text=f"- {omission}")
                for index, omission in enumerate(omissions)
            )
    return tuple(items)


def _handoff_quality(task: ContinuationTask, item_ids: Iterable[str]) -> QualityReport:
    return check_rollover_quality(handoff_content(task, item_ids), caller_objective=task.objective)


def _selected_turns(task: ContinuationTask, arm: ContinuationArm) -> tuple[int, ...]:
    if not arm.carries_transcript:
        return ()
    numbers = task.turn_numbers
    if arm.transcript_tail_turns is None:
        return numbers
    head = numbers[: arm.transcript_head_turns]
    tail_count = arm.transcript_tail_turns
    tail = numbers[len(numbers) - tail_count :] if tail_count else ()
    return tuple(dict.fromkeys((*head, *tail)))


def _state_item(fact: StateFact) -> AssembledItem:
    return AssembledItem(
        item_id=f"state:{fact.fact_id}",
        kind="state",
        text=f"- {fact.text} [evidence {fact.evidence}]",
    )


def _next_action_text(task: ContinuationTask) -> str:
    evidence = ", ".join(f"fact:{fact_id}" for fact_id in task.expected_next_action.required_fact_ids)
    return f"- {task.expected_next_action.text} [evidence {evidence}]"


def _fit(
    items: tuple[AssembledItem, ...], *, max_bytes: int
) -> tuple[tuple[AssembledItem, ...], list[AssembledItem], str, bool]:
    """Drop whole items from the end until the body fits, then cut the remainder.

    The front item is always delivered, so a ceiling smaller than one item still
    produces a measurable, deterministic context instead of an empty one. The
    cut lands on a UTF-8 boundary, so injected bytes stay exact.
    """

    kept: list[AssembledItem] = list(items)
    dropped: list[AssembledItem] = []
    while len(kept) > 1 and _body_bytes(kept) > max_bytes:
        dropped.append(kept.pop())
    text = _body_text(kept)
    truncated = bool(dropped)
    if len(text.encode("utf-8")) > max_bytes:
        text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        truncated = True
    return tuple(kept), list(reversed(dropped)), text, truncated


def _body_text(items: Sequence[AssembledItem]) -> str:
    return "\n\n".join(item.text for item in items)


def _body_bytes(items: Sequence[AssembledItem]) -> int:
    return len(_body_text(items).encode("utf-8"))
