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

"""Protect byte-sensitive sequences without wrapping ordinary Unicode prose."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput

from ..manifests import EvaluationError
from ..models import Support, digest
from ..v7.fidelity import quote_inventory
from ..v15.codec import INSTRUCTIONS, TemplateOutput, _boundary, cell_binding, cluster_end, render
from ..v15.codec import Cell as PreviousCell

__all__ = ["INSTRUCTIONS", "Cell", "TemplateOutput", "build_cells", "cell_binding", "render", "split_cells"]


@dataclass(frozen=True)
class Cell(PreviousCell):
    def identity(self) -> dict[str, Any]:
        return {**super().identity(), "protocol": "v16-protected-inline-cell"}


def split_cells(text: str) -> list[tuple[str, int, int]]:
    """Select code spans, protected clusters and symbols without consulting labels.

    Ordinary letters and punctuation remain verbatim prose. This deliberately does
    not claim complete UAX #29 or complete Unicode Emoji property classification.
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
        category = unicodedata.category(text[cursor])
        if (
            end > cursor + 1
            or category.startswith("M")
            or category == "So"
            or text[cursor] in {"\r", "\u200d"}
            or "\U0001f3fb" <= text[cursor] <= "\U0001f3ff"
        ):
            result.append((text[cursor:end], cursor, end))
        cursor = end
    return result


def build_cells(value: AtomicMemoryExtractionInput, supports: tuple[Support, ...]) -> tuple[Cell, ...]:
    result = []
    for quote in quote_inventory(value, supports):
        for text, start, end in split_cells(quote.value):
            if not _boundary(quote.value, start) or not _boundary(quote.value, end):
                raise EvaluationError("cell_splits_protected_sequence")
            offset = len(quote.value[:start].encode())
            result.append(
                Cell(
                    cell_id="",
                    evidence_index=quote.evidence_index,
                    evidence_id=quote.evidence_id,
                    pointer=quote.pointer,
                    value=text,
                    support=quote.support,
                    source_byte_start=quote.source_byte_start + offset,
                    source_byte_end=quote.source_byte_start + offset + len(text.encode()),
                    value_byte_start=quote.value_byte_start + offset,
                    value_byte_end=quote.value_byte_start + offset + len(text.encode()),
                )
            )
    cells = tuple(replace(cell, cell_id=f"c{index}") for index, cell in enumerate(result))
    identities = {digest({k: v for k, v in c.identity().items() if k != "cell_id"}) for c in cells}
    if len(identities) != len(cells):
        raise EvaluationError("duplicate_cell_identity")
    return cells
