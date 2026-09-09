# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Native database substring predicates for Scope discovery."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func


def literal_contains(column: Any, query: str, dialect: str) -> ColumnElement[bool]:
    """Build one bound literal-substring predicate for a supported SQL dialect."""

    if dialect == "sqlite":
        return func.instr(column, query) > 0
    if dialect == "mysql":
        return func.locate(query, column) > 0
    raise ValueError(f"unsupported Scope discovery dialect: {dialect}")  # noqa: TRY003


__all__ = ["literal_contains"]
