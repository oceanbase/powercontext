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

"""Storage contracts and values for the built-in scoped Source journal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import Source, SourceRef


def validate_scope_id(value: str) -> str:
    """Validate an opaque built-in family scope."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("scope_id must not be empty")  # noqa: TRY003
    if len(value) > MAX_SCOPE_ID_LENGTH:
        raise ValueError(f"scope_id must not exceed {MAX_SCOPE_ID_LENGTH} characters")  # noqa: TRY003
    return value


class SourceCursor(BaseModel):
    """The last Source sequence consumed by one family trigger."""

    sequence: int = 0


@dataclass(frozen=True, slots=True)
class SourceJournalEntry:
    """One canonical Source paired with its stable scoped journal position."""

    source_ref: SourceRef
    source: Source
    position: int


@runtime_checkable
class SourceJournal(Protocol):
    """Read stable positions from one scoped Source catalog."""

    async def position(self, source: Source, /) -> int: ...

    async def entries(self) -> tuple[SourceJournalEntry, ...]: ...
