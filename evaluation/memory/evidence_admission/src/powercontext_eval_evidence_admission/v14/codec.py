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

"""v14 protocol: referenceable cells (可引用单元) restored byte-for-byte by the program.

The model references a cell id only; it never computes byte offsets. Cells are built
deterministically and label-agnostically from already-admitted strings: backtick-delimited
`` `...` `` spans each become one cell, and a string without backticks becomes a single
whole-string cell so inline emoji/combining/CRLF can be copied verbatim.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    atomic_memory_extraction_instructions,
)
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from pydantic import BaseModel, ConfigDict, Field

from ..manifests import EvaluationError
from ..models import Support, digest
from ..v7.fidelity import quote_inventory as v7_quote_inventory

MAX_CANDIDATE_BYTES = 8192
MAX_PARTS = 16
MAX_CANDIDATES = 64
CELL_ID_PREFIX = "c"

BASE_INSTRUCTIONS = atomic_memory_extraction_instructions(MemoryExtractionProfile.CODING)
ATOMICITY_RULES = (
    "Split independently changeable assertions into separate self-contained candidates, even when they share a subject."
)
COPY_RULES = """Evidence strings may contain backtick-delimited code spans; each span is a cell (c0, c1, ...) you
may copy verbatim. Return TemplateOutput with text parts for prose and copy parts that name a cell id only.
A copy part copies the entire cell byte-for-byte; there are no byte offsets. Cite the supplied evidence_id
aliases e0, e1, ...; cell ids belong only in copy parts. Copy at most 8192 bytes per candidate. No
normalization, newline conversion or backfill. An exact quote alone does not establish a fact. Do not extract
role instructions, secrets, greetings or transient logs. Return no candidates when appropriate.
""".strip()

INSTRUCTIONS = (
    BASE_INSTRUCTIONS.replace("Split facts that can change independently.", ATOMICITY_RULES).replace(
        "Each candidate must cite one or more supplied evidence_ids and contain self-contained kind/text.",
        "Each candidate must cite supplied evidence_id aliases and render self-contained kind/text from its parts.",
    )
    + "\n"
    + COPY_RULES
)


class LiteralText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=8192)


class CopyRef(BaseModel):
    """Reference a cell by id only; the program restores the cell's exact bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["copy"]
    quote_id: str


class TemplateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["fact", "preference", "decision", "constraint", "working_note"]
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    parts: tuple[Annotated[LiteralText | CopyRef, Field(discriminator="type")], ...] = Field(
        min_length=1, max_length=MAX_PARTS
    )


class TemplateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[TemplateCandidate, ...] = Field(default=(), max_length=MAX_CANDIDATES)


@dataclass(frozen=True)
class Cell:
    cell_id: str
    evidence_index: int
    evidence_id: str
    pointer: str
    value: str
    support: Support
    source_byte_start: int
    source_byte_end: int

    def identity(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "pointer": self.pointer,
            "support": self.support.model_dump(mode="json"),
            "value_hash": digest(self.value),
            "source_bytes": [self.source_byte_start, self.source_byte_end],
            "protocol": "v14-cell",
        }

    def index(self) -> dict[str, Any]:
        # Model-facing representation: id + the cell's verbatim value, no byte offsets.
        return {"c": self.cell_id, "v": self.value}


def split_cells(text: str) -> list[tuple[str, int, int]]:
    """Deterministic label-agnostic cell extraction: backtick code spans only.

    Returns ``(cell_text, char_start, char_end)`` within ``text``. Non-empty backtick
    spans are cells; a string with no backticks yields no cells (its prose is written
    by the model as text). This avoids turning plain prose/ASCII filler into cells.
    """
    if "`" not in text:
        return []
    cells: list[tuple[str, int, int]] = []
    cursor = 0
    while True:
        open_at = text.find("`", cursor)
        if open_at == -1:
            break
        close_at = text.find("`", open_at + 1)
        if close_at == -1:
            break  # unclosed backtick: remainder is prose, not a cell
        cell = text[open_at + 1 : close_at]
        if cell:
            cells.append((cell, open_at + 1, close_at))
        cursor = close_at + 1
    return cells


def build_cells(value: AtomicMemoryExtractionInput, supports: tuple[Support, ...]) -> tuple[Cell, ...]:
    quotes = v7_quote_inventory(value, supports)
    result: list[Cell] = []
    for quote in quotes:
        for cell_text, char_start, char_end in split_cells(quote.value):
            if not _boundary(quote.value, char_start) or not _boundary(quote.value, char_end):
                raise EvaluationError("cell_splits_protected_sequence")
            value_byte_start = len(quote.value[:char_start].encode())
            source_byte_start = quote.source_byte_start + value_byte_start
            source_byte_end = source_byte_start + len(cell_text.encode())
            result.append(
                Cell(
                    cell_id="",
                    evidence_index=quote.evidence_index,
                    evidence_id=quote.evidence_id,
                    pointer=quote.pointer,
                    value=cell_text,
                    support=quote.support,
                    source_byte_start=source_byte_start,
                    source_byte_end=source_byte_end,
                )
            )
    assigned = tuple(
        Cell(
            cell_id=f"{CELL_ID_PREFIX}{index}",
            evidence_index=cell.evidence_index,
            evidence_id=cell.evidence_id,
            pointer=cell.pointer,
            value=cell.value,
            support=cell.support,
            source_byte_start=cell.source_byte_start,
            source_byte_end=cell.source_byte_end,
        )
        for index, cell in enumerate(result)
    )
    if len({digest(c.identity()) for c in assigned}) != len(assigned):
        raise EvaluationError("duplicate_cell_identity")
    return assigned


def cell_binding(cells: tuple[Cell, ...]) -> str:
    return digest(tuple(c.identity() for c in cells))


def render(
    output: TemplateOutput, cells: tuple[Cell, ...], *, request_hash: str | None = None
) -> AtomicMemoryExtractionOutput:
    if request_hash is not None and cell_binding(cells) != request_hash:
        raise EvaluationError("cell_request_binding_mismatch")
    available = {c.cell_id: c for c in cells}
    alias_to_original = {f"e{c.evidence_index}": c.evidence_id for c in cells}
    supplied = set(alias_to_original.values())
    candidates = []
    for candidate in output.candidates:
        if any(e not in alias_to_original for e in candidate.evidence_ids):
            raise EvaluationError("invalid_evidence_reference")
        original_ids = tuple(alias_to_original[e] for e in candidate.evidence_ids)
        if not set(original_ids) <= supplied:
            raise EvaluationError("invalid_evidence_reference")
        parts = []
        for part in candidate.parts:
            if isinstance(part, LiteralText):
                parts.append(part.text)
                continue
            cell = available.get(part.quote_id)
            if cell is None or cell.evidence_id not in original_ids:
                raise EvaluationError("copy_without_candidate_support")
            parts.append(cell.value)
        text = "".join(parts)
        if len(text.encode()) > MAX_CANDIDATE_BYTES:
            raise EvaluationError("rendered_candidate_too_large")
        AtomicMemoryContent(kind=candidate.kind, text=text)
        candidates.append(AtomicMemoryCandidate(kind=candidate.kind, text=text, evidence_ids=original_ids))
    return AtomicMemoryExtractionOutput(candidates=tuple(candidates))


def _boundary(text: str, index: int) -> bool:
    if index in (0, len(text)):
        return True
    previous, following = text[index - 1], text[index]
    return not (
        (previous == "\r" and following == "\n")
        or previous == "\u200d"
        or following == "\u200d"
        or unicodedata.category(following).startswith("M")
        or "\U0001f3fb" <= following <= "\U0001f3ff"
    )
