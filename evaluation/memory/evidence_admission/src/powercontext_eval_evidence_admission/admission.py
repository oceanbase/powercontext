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

"""Deterministic evaluation-only fragment selection; no access to fixture labels."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Literal

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.builtin.inference import TokenEstimator, character_token_estimator

from .manifests import EvaluationError
from .models import InputMetrics, Omission, Support, canonical, digest


def token_count(value: AtomicMemoryExtractionInput, instructions: str, estimator: TokenEstimator | None = None) -> int:
    estimator = estimator or character_token_estimator()
    return estimator.estimate(f"{instructions}\n{value.model_dump_json()}")


@dataclass(frozen=True)
class Fragment:
    source: AtomicMemoryEvidence
    materialization: str
    projection_hash: str
    field: str
    field_order: int
    value: Any
    byte_start: int
    byte_end: int
    char_start: int
    char_end: int

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
                "policy": "bounded-fragments-v1",
            }
        )

    def support(self, evidence_id: str | None = None) -> Support:
        return Support(
            evidence_id=evidence_id or f"fragment:{self.identity}",
            source_hash=digest(self.source.source_ref.source_id),
            materialization_hash=self.materialization,
            projection_hash=self.projection_hash,
            field_hash=digest(self.field),
            byte_start=self.byte_start,
            byte_end=self.byte_end,
            char_start=self.char_start,
            char_end=self.char_end,
        )

    def evidence(self) -> AtomicMemoryEvidence:
        return AtomicMemoryEvidence(
            evidence_id=f"fragment:{self.identity}",
            source_ref=self.source.source_ref,
            journal_position=self.source.journal_position,
            content={
                "field": self.field,
                "value": self.value,
                "byte_range": [self.byte_start, self.byte_end],
                "character_range": [self.char_start, self.char_end],
                "incomplete": True,
            },
            source_metadata={
                "materialization_hash": self.materialization,
                "projection_hash": self.projection_hash,
                "admission_policy": "bounded-fragments-v1",
                "source_declaration_changed": False,
            },
            via_artifact=self.source.via_artifact,
        )


def leaves(value: Any, pointer: str = ""):
    if isinstance(value, dict) and value:
        for key in sorted(value):
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from leaves(value[key], f"{pointer}/{escaped}")
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from leaves(item, f"{pointer}/{index}")
    else:
        yield pointer, value


def text_ranges(text: str, limit: int):
    start = byte_start = size = 0
    for index, character in enumerate(text):
        width = len(character.encode("utf-8"))
        crlf = character == "\n" and index > start and text[index - 1] == "\r"
        if size + width > limit and index > start:
            cut = index - 1 if crlf else index
            prefix_size = size - 1 if crlf else size
            yield start, cut, byte_start, byte_start + prefix_size
            start, byte_start, size = cut, byte_start + prefix_size, 1 if crlf else 0
        size += width
    yield start, len(text), byte_start, byte_start + size


async def fragments(
    value: AtomicMemoryExtractionInput, materializations: tuple[str, ...], limit: int
) -> tuple[Fragment, ...]:
    if limit < 4 or len(materializations) != len(value.evidence):
        raise EvaluationError("invalid_fragment_configuration")
    result = []
    for evidence, material in zip(value.evidence, materializations, strict=True):
        projection_hash = digest(evidence.model_dump(mode="json"))
        fields = leaves({"content": evidence.content, "source_metadata": evidence.source_metadata})
        for order, (pointer, leaf) in enumerate(fields):
            if isinstance(leaf, str):
                for start, end, byte_start, byte_end in text_ranges(leaf, limit):
                    result.append(
                        Fragment(
                            evidence,
                            material,
                            projection_hash,
                            pointer,
                            order,
                            leaf[start:end],
                            byte_start,
                            byte_end,
                            start,
                            end,
                        )
                    )
                    if len(result) % 64 == 0:
                        await asyncio.sleep(0)
            else:
                length = len(canonical(leaf))
                result.append(Fragment(evidence, material, projection_hash, pointer, order, leaf, 0, length, 0, length))
    return tuple(result)


def positional_order(length: int) -> tuple[int, ...]:
    if not length:
        return ()
    chosen = [0] if length == 1 else [0, length - 1]
    gaps = [(1, length - 1)] if length > 2 else []
    while gaps:
        gaps.sort(key=lambda span: (-(span[1] - span[0]), span[0]))
        start, end = gaps.pop(0)
        middle = (start + end - 1) // 2
        chosen.append(middle)
        gaps.extend(span for span in ((start, middle), (middle + 1, end)) if span[0] < span[1])
    return tuple(chosen)


def selection_order(items: tuple[Fragment, ...]):
    fields = defaultdict(list)
    for item in items:
        fields[(item.source.journal_position, item.field_order)].append(item)
    sources = defaultdict(list)
    for (position, _), group in sorted(fields.items()):
        sources[position].append(deque(group[index] for index in positional_order(len(group))))
    while any(any(group for group in fields) for fields in sources.values()):
        for groups in sources.values():
            for group in groups:
                if group:
                    yield group.popleft()


@dataclass(frozen=True)
class Admission:
    inputs: tuple[AtomicMemoryExtractionInput, ...]
    inventory: tuple[Fragment, ...]
    admitted: tuple[Fragment, ...]
    supports: tuple[Support, ...]
    omissions: tuple[Omission, ...]
    metrics: InputMetrics
    completeness: Literal["complete", "bounded"]
    accepted: bool


def summarize(
    inputs: tuple[AtomicMemoryExtractionInput, ...],
    inventory: tuple[Fragment, ...],
    admitted: tuple[Fragment, ...],
    instructions: str,
    *,
    bounded: bool,
    accepted: bool,
    reason: Literal["input_budget", "source_window", "unavailable"] = "input_budget",
) -> Admission:
    identities = {item.identity for item in admitted}
    omitted = tuple(item for item in inventory if item.identity not in identities)
    omission_ranges = []
    for item in sorted(omitted, key=lambda part: (part.source.journal_position, part.field_order, part.byte_start)):
        omission = Omission(
            source_hash=digest(item.source.source_ref.source_id),
            field_hash=digest(item.field),
            byte_start=item.byte_start,
            byte_end=item.byte_end,
            reason=reason,
        )
        if (
            omission_ranges
            and omission_ranges[-1].source_hash == omission.source_hash
            and omission_ranges[-1].field_hash == omission.field_hash
            and omission_ranges[-1].byte_end == omission.byte_start
        ):
            omission = omission_ranges.pop().model_copy(update={"byte_end": omission.byte_end})
        omission_ranges.append(omission)
    omissions = tuple(omission_ranges)
    support_ranges = []
    for item in admitted:
        support = item.support(None if bounded else item.source.evidence_id)
        if (
            not bounded
            and support_ranges
            and support_ranges[-1].evidence_id == support.evidence_id
            and support_ranges[-1].field_hash == support.field_hash
            and support_ranges[-1].byte_end == support.byte_start
        ):
            support = support_ranges.pop().model_copy(
                update={"byte_end": support.byte_end, "char_end": support.char_end}
            )
        support_ranges.append(support)
    supports = tuple(support_ranges)
    categories = defaultdict(int)
    for item in admitted:
        categories[
            "metadata" if item.field.startswith("/source_metadata/") or "/metadata/" in item.field else "body"
        ] += item.byte_end - item.byte_start
    estimator = character_token_estimator()
    metrics = InputMetrics(
        projected_bytes=sum(item.byte_end - item.byte_start for item in inventory),
        admitted_bytes=sum(item.byte_end - item.byte_start for item in admitted),
        omitted_bytes=sum(item.byte_end - item.byte_start for item in omitted),
        projected_tokens=sum(
            estimator.estimate(item.value if isinstance(item.value, str) else canonical(item.value).decode())
            for item in inventory
        ),
        prepared_tokens=max((token_count(value, instructions) for value in inputs), default=0),
        omitted_tokens=sum(
            estimator.estimate(item.value if isinstance(item.value, str) else canonical(item.value).decode())
            for item in omitted
        ),
        evidence_items=sum(len(value.evidence) for value in inputs),
        fragments=len(admitted),
        projected_fragments=len(inventory),
        omitted_fragments=len(omitted),
        categories=dict(categories),
        serialized_input_bytes=sum(len(value.model_dump_json().encode()) for value in inputs),
    )
    return Admission(
        inputs, inventory, admitted, supports, omissions, metrics, "bounded" if omitted else "complete", accepted
    )


async def admit(
    value: AtomicMemoryExtractionInput,
    materials: tuple[str, ...],
    *,
    budget: int,
    fragment_bytes: int,
    instructions: str,
    bounded: bool,
) -> Admission:
    empty = AtomicMemoryExtractionInput(evidence=())
    if token_count(empty, instructions) > budget:
        raise EvaluationError("instructions_exceed_budget")
    inventory = await fragments(value, materials, fragment_bytes)
    if token_count(value, instructions) <= budget:
        return summarize((value,), inventory, inventory, instructions, bounded=False, accepted=True)
    if not bounded:
        return summarize((), inventory, (), instructions, bounded=False, accepted=False)
    admitted = []
    evidence = []
    for index, item in enumerate(selection_order(inventory)):
        candidate = AtomicMemoryExtractionInput(evidence=(*evidence, item.evidence()))
        if token_count(candidate, instructions) <= budget:
            admitted.append(item)
            evidence.append(candidate.evidence[-1])
        if index % 32 == 0:
            await asyncio.sleep(0)
    admitted.sort(key=lambda item: (item.source.journal_position, item.field_order, item.byte_start))
    prepared = AtomicMemoryExtractionInput(evidence=tuple(item.evidence() for item in admitted))
    return summarize(
        (prepared,) if admitted else (), inventory, tuple(admitted), instructions, bounded=True, accepted=bool(admitted)
    )
