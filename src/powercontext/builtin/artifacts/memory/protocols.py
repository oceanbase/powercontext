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

"""Family-level ports used by the Memory service and SQL adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.memory.models import MemoryEntryInput, MemoryEntryVersion
from powercontext.sources import Source


class MemoryCandidateRequest(BaseModel):
    """Canonical evidence and bounded current entries offered to a pipeline."""

    sources: tuple[Source, ...]
    artifacts: tuple[Artifact[object], ...]
    current_entries: tuple[MemoryEntryVersion, ...]


class MemoryWriteRejectionCode(StrEnum):
    """Structured, caller-visible vocabulary for a held Memory write.

    Names mirror the evidence-selection vocabulary so a host can branch on one stable set of
    codes instead of parsing prose.
    """

    NEEDS_EVIDENCE = "needs_evidence"
    EVIDENCE_LIMIT_EXCEEDED = "evidence_limit_exceeded"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


class MemoryWriteVerdict(StrEnum):
    """The complete verdict vocabulary a Memory write gate may produce."""

    ACCEPT = "accept"
    FLAG = "flag"
    HOLD = "hold"


class MemoryWriteAssessment(BaseModel):
    """One gate verdict with the structured refusal a caller can observe.

    ``HOLD`` always carries both a ``code`` and a ``reason``: a refused write is visible to
    its caller, never silently dropped. ``ACCEPT``/``FLAG`` leave ``code`` unset.
    """

    model_config = ConfigDict(frozen=True)

    verdict: MemoryWriteVerdict
    policy_id: str
    code: MemoryWriteRejectionCode | None = None
    reason: str | None = None
    used_fallback: bool = False


@dataclass(frozen=True, slots=True)
class MemoryWriteGateRequest:
    """A bounded projection of one pending Memory write for sufficiency judgement."""

    candidates: tuple[str, ...]
    evidence: tuple[str, ...]
    expected_revision: int | None = None


class MemoryWriteGate(Protocol):
    """Judge whether a pending Memory write is supported by its cited evidence.

    A gate only classifies: it never writes, approves, rejects, or deletes anything. A missing
    or failing gate must be treated by callers as a pass-through, never as a hold.
    """

    policy_id: str

    async def assess(self, request: MemoryWriteGateRequest, /) -> MemoryWriteAssessment:
        """Return one verdict for a candidate set and its bounded evidence projection."""

        ...


class CandidatePipeline(Protocol):
    """Produce untrusted Memory candidates from canonical bounded evidence."""

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        """Return candidates that still require all service validations."""

        ...
