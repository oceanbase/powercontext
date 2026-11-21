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

"""Family allocation and exact-content scheduling without fixture labels or support aliases."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from functools import cached_property

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.builtin.persistence.memory_windows import MemorySourceWindowRepository
from powercontext.builtin.triggers import SourceHighWatermark, SourceWindowTrigger

from .admission import summarize, token_count
from .bounded_v2 import BoundaryFragment, V2Admission, boundary_fragments, gap_order
from .manifests import EvaluationError
from .models import V3Policy, canonical, digest
from .replay import Replay, checkpoint

POLICY = "bounded-fragments-v3"


@dataclass(frozen=True)
class V3Fragment(BoundaryFragment):
    @cached_property
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


def family(field: str) -> str:
    if field == "/content/metadata" or field.startswith("/content/metadata/"):
        return "metadata"
    return "source_metadata" if field.startswith("/source_metadata") else "content"


def distinct_order(items: list[V3Fragment]):
    seen = set()
    deferred = []
    for item in gap_order(list(items)):
        # Canonical typed JSON keeps strings verbatim and distinguishes true, 1 and "1".
        value = canonical(item.value)
        if value in seen:
            deferred.append(item)
        else:
            seen.add(value)
            yield item
    yield from deferred


def family_order(fields: dict[int, list[V3Fragment]]):
    groups = defaultdict(deque)
    for _, items in sorted(fields.items()):
        groups[family(items[0].field)].append(deque(distinct_order(items)))
    while groups:
        for name in ("content", "content", "metadata", "source_metadata"):
            fields_in_group = groups.get(name)
            if not fields_in_group:
                continue
            field = fields_in_group.popleft()
            yield field.popleft()
            if field:
                fields_in_group.append(field)
            if not fields_in_group:
                del groups[name]


def coverage_order(items: tuple[V3Fragment, ...]):
    sources = defaultdict(lambda: defaultdict(list))
    for item in items:
        sources[item.source.journal_position][item.field_order].append(item)
    active = deque(deque(family_order(fields)) for _, fields in sorted(sources.items()))
    while active:
        source = active.popleft()
        yield source.popleft()
        if source:
            active.append(source)


def bundle_input(items: tuple[V3Fragment, ...], *, citation_only: bool = False) -> AtomicMemoryExtractionInput:
    sources = defaultdict(list)
    for item in sorted(items, key=lambda f: (f.source.journal_position, f.field_order, f.byte_start)):
        sources[item.source.journal_position].append(item)
    evidence = []
    for group in sources.values():
        first = group[0]
        identity_data = {"policy": POLICY, "fragments": [item.identity for item in group]}
        if citation_only:
            identity_data["encoding"] = "source-range-bundle-citation-v2"
        identity = digest(identity_data)
        evidence.append(
            AtomicMemoryEvidence(
                evidence_id=f"fragment-bundle:{identity}",
                source_ref=first.source.source_ref,
                journal_position=first.source.journal_position,
                content={
                    "incomplete": True,
                    "ranges": [
                        {
                            **({} if citation_only else {"id": item.identity}),
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


async def select_bundle(
    inventory: tuple[V3Fragment, ...], budget: int, instructions: str, *, citation_only: bool = False
):
    selected = []
    for index, fragment in enumerate(coverage_order(inventory)):
        candidate = bundle_input((*selected, fragment), citation_only=citation_only)
        if token_count(candidate, instructions) <= budget:
            selected.append(fragment)
        if index % 32 == 0:
            await checkpoint("v3_selection")
    selected.sort(key=lambda f: (f.source.journal_position, f.field_order, f.byte_start))
    return tuple(selected), bundle_input(tuple(selected), citation_only=citation_only)


def estimator_units(text: str) -> int:
    return sum(1 if character.isascii() else 4 for character in text)


def cost_components(metrics, inputs, admitted, instructions):
    value_bytes = defaultdict(int)
    units = defaultdict(int)
    for item in admitted:
        name = family(item.field)
        value_bytes[name] += item.byte_end - item.byte_start
        units[name] += estimator_units(canonical(item.value).decode())
    total = sum(estimator_units(f"{instructions}\n{value.model_dump_json()}") for value in inputs)
    units["instructions"] = len(inputs) * estimator_units(instructions)
    units["envelope"] = total - sum(units.values())
    return metrics.model_copy(
        update={
            "value_bytes_by_family": dict(value_bytes),
            "input_estimator_units_by_family": dict(units),
        }
    )


@dataclass(frozen=True)
class V3Admission(V2Admission):
    """V3 retains the v2 resource-exhaustion and partial-progress contract."""


async def hybrid_admission(
    replay: Replay,
    budget: int,
    fragment_bytes: int,
    instructions: str,
    policy: V3Policy,
) -> tuple[V3Admission, tuple]:
    if token_count(AtomicMemoryExtractionInput(evidence=()), instructions) > budget:
        raise EvaluationError("instructions_exceed_budget")
    inventory = tuple(
        V3Fragment(**vars(item))
        for item in await boundary_fragments(replay.value, replay.materializations, fragment_bytes)
    )
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
            await checkpoint("v3_window_retry")
            continue
        window_fragments = tuple(f for f in inventory if action.after < f.source.journal_position <= action.through)
        if fits:
            chosen, prepared = window_fragments, value
            references = tuple(f.support(f.source.evidence_id) for f in chosen)
        else:
            chosen, prepared = await select_bundle(
                window_fragments,
                budget,
                instructions,
                citation_only=policy.encoding == "source-range-bundle-citation-v2",
            )
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
    result = replace(result, metrics=cost_components(result.metrics, tuple(inputs), tuple(admitted), instructions))
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
    return V3Admission(
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
