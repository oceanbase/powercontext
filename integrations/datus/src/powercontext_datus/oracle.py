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

"""Full-row result comparison; never independently sort result columns."""

# ruff: noqa: TRY003 - bounded validation errors are part of this bridge's diagnostics.

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias

Cell: TypeAlias = str | int | float | bool | Decimal | None
TypedCell: TypeAlias = tuple[str, Cell]


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]

    def __post_init__(self) -> None:
        if not self.columns or len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must be nonempty and unique")
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("row width does not match columns")
            for cell in row:
                _typed_cell(cell)


@dataclass(frozen=True)
class ComparisonPolicy:
    """Fix this policy and any column/units mapping before seeing predictions."""

    ordered: bool = False
    absolute_tolerance: Decimal = Decimal(0)
    relative_tolerance: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        for value in (self.absolute_tolerance, self.relative_tolerance):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("tolerances must be finite nonnegative Decimals")


def _typed_cell(cell: Cell) -> TypedCell:
    if cell is None:
        return ("null", None)
    if isinstance(cell, bool):
        return ("bool", cell)
    if isinstance(cell, str):
        return ("str", cell)
    if isinstance(cell, (int, float, Decimal)):
        if isinstance(cell, float) and not math.isfinite(cell):
            raise ValueError("nonfinite result cell")
        numeric = Decimal(str(cell))
        if not numeric.is_finite():
            raise ValueError("nonfinite result cell")
        return ("number", numeric)
    raise ValueError("unsupported result cell type")


def compare_tables(actual: Table, expected: Table, policy: ComparisonPolicy | None = None) -> bool:
    """Compare complete rows, including duplicates, nulls and required column order.

    Tolerant unordered comparison uses a perfect bipartite matching, not greedy
    pairing (which can reject valid duplicate/near-equal result rows).
    """
    policy = policy or ComparisonPolicy()
    if actual.columns != expected.columns or len(actual.rows) != len(expected.rows):
        return False
    left = [tuple(_typed_cell(cell) for cell in row) for row in actual.rows]
    right = [tuple(_typed_cell(cell) for cell in row) for row in expected.rows]
    if policy.absolute_tolerance == 0 and policy.relative_tolerance == 0:
        return left == right if policy.ordered else Counter(left) == Counter(right)

    if policy.ordered:
        return all(_rows_match(a, b, policy) for a, b in zip(left, right, strict=True))
    edges = [[j for j, b in enumerate(right) if _rows_match(a, b, policy)] for a in left]
    assigned: dict[int, int] = {}
    return all(_augment(start, edges, assigned) for start in range(len(left)))


def _rows_match(a: tuple[TypedCell, ...], b: tuple[TypedCell, ...], policy: ComparisonPolicy) -> bool:
    for (at, av), (bt, bv) in zip(a, b, strict=True):
        if at != bt:
            return False
        if at == "number":
            if not isinstance(av, Decimal) or not isinstance(bv, Decimal):
                raise ValueError("invalid numeric cell")
            if abs(av - bv) > max(policy.absolute_tolerance, policy.relative_tolerance * abs(bv)):
                return False
        elif av != bv:
            return False
    return True


def _augment(start: int, edges: list[list[int]], assigned: dict[int, int]) -> bool:
    queue = deque([start])
    visited_left = {start}
    predecessors: dict[int, int] = {}
    while queue:
        node = queue.popleft()
        for target in edges[node]:
            if target in predecessors:
                continue
            predecessors[target] = node
            if target not in assigned:
                _assign_path(start, target, predecessors, assigned)
                return True
            previous = assigned[target]
            if previous not in visited_left:
                visited_left.add(previous)
                queue.append(previous)
    return False


def _assign_path(start: int, free: int, predecessors: dict[int, int], assigned: dict[int, int]) -> None:
    while True:
        node = predecessors[free]
        previous_target = next((j for j, i in assigned.items() if i == node), None)
        assigned[free] = node
        if node == start:
            return
        if previous_target is None:
            raise ValueError("invalid matching path")
        free = previous_target
