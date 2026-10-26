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

"""Window-first admission with range bundles; selection receives no fixture labels."""

from __future__ import annotations

import asyncio
import heapq
from collections import defaultdict, deque
from dataclasses import dataclass

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.builtin.persistence.memory_windows import MemorySourceWindowRepository
from powercontext.builtin.triggers import SourceHighWatermark, SourceWindowTrigger

from .admission import Admission, Fragment, leaves, summarize, text_ranges, token_count
from .manifests import EvaluationError
from .models import V2Policy, canonical, digest
from .replay import Replay, checkpoint

POLICY = "bounded-fragments-v2"


@dataclass(frozen=True)
class BoundaryFragment(Fragment):
    @property
    def identity(self) -> str:
        return digest(
            {
                "source": self.source.source_ref.model_dump(),
                "materialization": self.materialization,
                "projection": self.projection_hash,
                "field": self.field,
                "bytes": [self.byte_start, self.byte_end],
                "characters": [self.char_start, self.char_end],
                "policy": POLICY,
            }
        )


def record_ranges(text: str, limit: int):
    """Prefer whole LF/CRLF-delimited records; never normalize or discard bytes."""
    start = byte_start = end = byte_end = 0
    for record in text.splitlines(keepends=True):
        # splitlines recognizes more separators than LF; all separators remain verbatim data.
        for left, right, first, last in text_ranges(record, limit):
            width = last - first
            if byte_end - byte_start + width > limit and end > start:
                yield start, end, byte_start, byte_end
                start, byte_start = end, byte_end
            end += right - left
            byte_end += width
    if end > start or not text:
        yield start, end, byte_start, byte_end


async def boundary_fragments(value: AtomicMemoryExtractionInput, materials: tuple[str, ...], limit: int):
    if limit < 4 or len(materials) != len(value.evidence):
        raise EvaluationError("invalid_fragment_configuration")
    result = []
    for source, material in zip(value.evidence, materials, strict=True):
        projection = digest(source.model_dump(mode="json"))
        for order, (field, leaf) in enumerate(
            leaves({"content": source.content, "source_metadata": source.source_metadata})
        ):
            ranges = record_ranges(leaf, limit) if isinstance(leaf, str) else [(0, 0, 0, len(canonical(leaf)))]
            for start, end, byte_start, byte_end in ranges:
                result.append(
                    BoundaryFragment(
                        source,
                        material,
                        projection,
                        field,
                        order,
                        leaf[start:end] if isinstance(leaf, str) else leaf,
                        byte_start,
                        byte_end,
                        start,
                        end,
                    )
                )
                if len(result) % 64 == 0:
                    await asyncio.sleep(0)
    return tuple(result)


def gap_order(items: list[BoundaryFragment]):
    if not items:
        return
    yield items[0]
    if len(items) == 1:
        return
    yield items[-1]
    gaps = []

    def push(start: int, end: int):
        if start < end:
            heapq.heappush(gaps, (-(end - start), start, end))

    push(1, len(items) - 1)
    while gaps:
        _, start, end = heapq.heappop(gaps)
        # Keep v1's positional and tie-breaking convention, now over whole-record fragments.
        middle = (start + end - 1) // 2
        yield items[middle]
        push(start, middle)
        push(middle + 1, end)


def coverage_order(items: tuple[BoundaryFragment, ...]):
    fields = defaultdict(list)
    for item in items:
        fields[(item.source.journal_position, item.field_order)].append(item)
    sources = defaultdict(deque)
    for (source, _), group in sorted(fields.items()):
        sources[source].append(deque(gap_order(group)))
    active = deque(sources.values())
    while active:
        source = active.popleft()
        field = source.popleft()
        yield field.popleft()
        if field:
            source.append(field)
        if source:
            active.append(source)


def bundle_input(items: tuple[BoundaryFragment, ...]) -> AtomicMemoryExtractionInput:
    sources = defaultdict(list)
    for item in sorted(items, key=lambda f: (f.source.journal_position, f.field_order, f.byte_start)):
        sources[item.source.journal_position].append(item)
    evidence = []
    for group in sources.values():
        first = group[0]
        identity = digest({"policy": POLICY, "fragments": [item.identity for item in group]})
        evidence.append(
            AtomicMemoryEvidence(
                evidence_id=f"fragment-bundle:{identity}",
                source_ref=first.source.source_ref,
                journal_position=first.source.journal_position,
                content={
                    "incomplete": True,
                    "ranges": [
                        {
                            "id": item.identity,
                            "field": item.field,
                            "value": item.value,
                            "bytes": [item.byte_start, item.byte_end],
                            "characters": [item.char_start, item.char_end],
                        }
                        for item in group
                    ],
                },
                source_metadata={
                    "materialization_hash": first.materialization,
                    "projection_hash": first.projection_hash,
                    "admission_policy": POLICY,
                    "source_declaration_changed": False,
                },
                via_artifact=first.source.via_artifact,
            )
        )
    return AtomicMemoryExtractionInput(evidence=tuple(evidence))


async def select_bundle(inventory: tuple[BoundaryFragment, ...], budget: int, instructions: str):
    selected = []
    for index, fragment in enumerate(coverage_order(inventory)):
        candidate = bundle_input((*selected, fragment))
        if token_count(candidate, instructions) <= budget:
            selected.append(fragment)
        if index % 32 == 0:
            await checkpoint("v2_selection")
    selected.sort(key=lambda f: (f.source.journal_position, f.field_order, f.byte_start))
    return tuple(selected), bundle_input(tuple(selected))


@dataclass(frozen=True)
class V2Admission(Admission):
    limit_exhausted: bool = False


async def hybrid_admission(
    replay: Replay,
    budget: int,
    fragment_bytes: int,
    instructions: str,
    policy: V2Policy,
) -> tuple[V2Admission, tuple]:
    if token_count(AtomicMemoryExtractionInput(evidence=()), instructions) > budget:
        raise EvaluationError("instructions_exceed_budget")
    inventory = await boundary_fragments(replay.value, replay.materializations, fragment_bytes)
    windows, trigger = MemorySourceWindowRepository(), SourceWindowTrigger()
    state = trigger.initial_state()
    inputs, admitted, supports, attempts = [], [], [], []
    total_tokens = 0
    exhausted = False
    while state.sequence < len(replay.value.evidence):
        if len(inputs) >= policy.max_calls_per_sample or total_tokens >= policy.total_input_budget_multiplier * budget:
            exhausted = True
            break
        async with replay.contexts.database.transaction() as connection:
            watermark, limit = await windows.signal(
                connection, replay.scope_id, state.sequence, len(replay.value.evidence)
            )
        transition = trigger.activate(SourceHighWatermark(sequence=watermark, limit=limit), state)
        action = transition.actions[0]
        value = AtomicMemoryExtractionInput(
            evidence=tuple(
                source for source in replay.value.evidence if action.after < source.journal_position <= action.through
            )
        )
        if tuple(s.journal_position for s in value.evidence) != tuple(range(action.after + 1, action.through + 1)):
            raise EvaluationError("noncontiguous_source_window")
        estimated = token_count(value, instructions)
        fits = estimated <= budget
        attempts.append((action.after, action.through, estimated, fits))
        if not fits and action.through - action.after > 1:
            async with replay.contexts.database.transaction() as connection:
                await windows.reduce(
                    connection,
                    replay.scope_id,
                    source_through=watermark,
                    window_limit=max(1, (action.through - action.after) // 2),
                )
            await checkpoint("v2_window_retry")
            continue
        window_fragments = tuple(f for f in inventory if action.after < f.source.journal_position <= action.through)
        if fits:
            chosen, prepared = window_fragments, value
            references = tuple(f.support(f.source.evidence_id) for f in chosen)
        else:
            chosen, prepared = await select_bundle(window_fragments, budget, instructions)
            ids = {item.journal_position: item.evidence_id for item in prepared.evidence}
            references = tuple(f.support(ids[f.source.journal_position]) for f in chosen)
        if not chosen:
            break
        cost = token_count(prepared, instructions)
        if total_tokens + cost > policy.total_input_budget_multiplier * budget:
            exhausted = True
            break
        inputs.append(prepared)
        admitted.extend(chosen)
        supports.extend(references)
        total_tokens += cost
        state = transition.state
        async with replay.contexts.database.transaction() as connection:
            await windows.clear_consumed(connection, replay.scope_id, state.sequence)
    result = summarize(
        tuple(inputs),
        inventory,
        tuple(admitted),
        instructions,
        bounded=False,
        accepted=state.sequence == len(replay.value.evidence),
    )
    omissions = tuple(
        o.model_copy(update={"reason": "resource_budget"})
        if exhausted
        and any(
            digest(s.source_ref.source_id) == o.source_hash and s.journal_position > state.sequence
            for s in replay.value.evidence
        )
        else o
        for o in result.omissions
    )
    return V2Admission(
        result.inputs,
        result.inventory,
        result.admitted,
        tuple(supports),
        omissions,
        result.metrics,
        result.completeness,
        result.accepted,
        exhausted,
    ), tuple(attempts)
