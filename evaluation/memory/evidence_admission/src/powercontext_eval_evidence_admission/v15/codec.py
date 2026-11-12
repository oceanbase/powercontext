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

"""Label-free inline copy units with request-bound evidence aliases."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
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
ATOMICITY_EXAMPLES = """For example, "the service listens on port 8080" and "the service requires Node 18" are two
independently changeable facts about one subject; emit two self-contained candidates, not one combined candidate.
"the config path is /etc/app.conf" and "the retry limit is 3" are likewise two separate candidates. A candidate may
use several copy parts when one fact needs several exact values; the number of copy parts never decides atomicity.
""".strip()
COPY_RULES = """Strings may be encoded as {s: [prose, {c: cell_id, v: exact_value}, ...]}.
Read the segments in order. A copy part names a c-id and copies its entire v byte-for-byte; never compute
byte offsets. Use copy parts for supplied code literals, combining sequences, emoji and raw CRLF rather
than rewriting or normalizing them. Preserve meaningful original line breaks within retained evidence.
Several copy parts may support one fact; do not merge independent assertions to collect copy parts.
Cite evidence aliases e0, e1, ...; c-ids belong only in copy parts. Return TemplateOutput. Do not backfill
omitted evidence. An exact quote alone does not establish a fact. No normalization or newline conversion.
""".strip()

INSTRUCTIONS = (
    BASE_INSTRUCTIONS.replace(
        "Split facts that can change independently.", ATOMICITY_RULES + "\n" + ATOMICITY_EXAMPLES
    ).replace(
        "Each candidate must cite one or more supplied evidence_ids and contain kind, text and description.",
        "Each candidate must cite supplied evidence_id aliases and render self-contained text from its parts; provide a separate description.",
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
    description: str = Field(min_length=1, max_length=200)
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
    value_byte_start: int
    value_byte_end: int

    def identity(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "pointer": self.pointer,
            "support": self.support.model_dump(mode="json"),
            "value_hash": digest(self.value),
            "source_bytes": [self.source_byte_start, self.source_byte_end],
            "protocol": "v15-inline-cell",
            "cell_id": self.cell_id,
            "evidence_index": self.evidence_index,
            "value_bytes": [self.value_byte_start, self.value_byte_end],
        }

    def index(self) -> dict[str, Any]:
        # Model-facing representation: id + the cell's verbatim value, no byte offsets.
        return {"c": self.cell_id, "v": self.value}


MAX_UNICODE_RUN = 64


def cluster_end(text: str, start: int) -> int:
    """Keep CRLF, marks, modifiers, variation selectors, ZWJ chains and flag pairs whole.

    This is a bounded preservation rule, not a claim of complete UAX #29 segmentation.
    """
    if text.startswith("\r\n", start):
        return start + 2
    end = start + 1
    if "\U0001f1e6" <= text[start] <= "\U0001f1ff" and end < len(text) and "\U0001f1e6" <= text[end] <= "\U0001f1ff":
        end += 1
    while end < len(text):
        ch = text[end]
        if unicodedata.category(ch).startswith("M") or "\U0001f3fb" <= ch <= "\U0001f3ff":
            end += 1
        elif ch == "\u200d" and end + 1 < len(text):
            end += 2
        else:
            break
    return end


def split_cells(text: str) -> list[tuple[str, int, int]]:
    """Code spans, protected clusters and bounded non-ASCII runs, without fixture labels.

    ASCII prose stays literal. Unicode is never copied as a whole unbounded leaf.
    Delimiters and all text outside cells remain in the encoded string, exactly once.
    """
    result = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] == "`":
            close = text.find("`", cursor + 1)
            if close > cursor + 1:
                result.append((text[cursor + 1 : close], cursor + 1, close))
                cursor = close + 1
                continue
        end = cluster_end(text, cursor)
        if end > cursor + 1 or ord(text[cursor]) > 127 or text[cursor] == "\r":
            # Group ordinary adjacent non-ASCII letters; protected clusters stay selectable.
            if end == cursor + 1 and unicodedata.category(text[cursor]).startswith("L"):
                while end < len(text) and end - cursor < MAX_UNICODE_RUN:
                    next_end = cluster_end(text, end)
                    if (
                        ord(text[end]) <= 127
                        or not unicodedata.category(text[end]).startswith("L")
                        or next_end != end + 1
                    ):
                        break
                    end = next_end
            result.append((text[cursor:end], cursor, end))
        cursor = end
    return result


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
                    value_byte_start=quote.value_byte_start + value_byte_start,
                    value_byte_end=quote.value_byte_start + value_byte_start + len(cell_text.encode()),
                )
            )
    assigned = tuple(replace(cell, cell_id=f"{CELL_ID_PREFIX}{index}") for index, cell in enumerate(result))
    if len({digest({k: v for k, v in c.identity().items() if k != "cell_id"}) for c in assigned}) != len(assigned):
        raise EvaluationError("duplicate_cell_identity")
    return assigned


def cell_binding(cells: tuple[Cell, ...]) -> str:
    return digest(tuple(c.identity() for c in cells))


def render(
    output: TemplateOutput, cells: tuple[Cell, ...], aliases: tuple[str, ...], *, request_hash: str
) -> AtomicMemoryExtractionOutput:
    if cell_binding(cells) != request_hash:
        raise EvaluationError("cell_request_binding_mismatch")
    available = {c.cell_id: c for c in cells}
    alias_to_original = {f"e{i}": original for i, original in enumerate(aliases)}
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
        AtomicMemoryContent(kind=candidate.kind, text=text, description=candidate.description)
        candidates.append(
            AtomicMemoryCandidate(
                kind=candidate.kind, text=text, description=candidate.description, evidence_ids=original_ids
            )
        )
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
