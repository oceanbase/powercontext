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

"""Adapters from existing Runtime behavior into Handoff Report read ports."""

from __future__ import annotations

from typing import Protocol

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import (
    Handoff,
    HandoffEvidenceCheck,
)
from powercontext.builtin.handoff_report.errors import HandoffReportEvidenceCheckUnavailableError
from powercontext.builtin.work import WorkContinuity


class _ScopedHandoffReader(Protocol):
    async def latest(self) -> Handoff | None: ...

    async def revision(self, reference: ArtifactRef, /) -> Handoff: ...

    async def revisions(self) -> tuple[Handoff, ...]: ...


class _HandoffApplicationReader(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedHandoffReader: ...


class _ScopedWorkReader(Protocol):
    async def continuity(self, selected_handoff: ArtifactRef | None = None) -> WorkContinuity: ...


class _WorkApplicationReader(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedWorkReader: ...


class RuntimeHandoffReadAdapter:
    """Use the existing public Runtime Handoff application as a read-only source."""

    def __init__(self, application: _HandoffApplicationReader, /) -> None:
        self._application = application

    async def latest(self, scope_id: str, /) -> Handoff | None:
        return await self._application.for_scope(scope_id).latest()

    async def get(self, scope_id: str, reference: ArtifactRef, /) -> Handoff:
        return await self._application.for_scope(scope_id).revision(reference)

    async def revisions(self, scope_id: str, /) -> tuple[Handoff, ...]:
        return await self._application.for_scope(scope_id).revisions()

    async def check_evidence(
        self,
        scope_id: str,
        reference: ArtifactRef,
        /,
    ) -> tuple[HandoffEvidenceCheck, ...]:
        del scope_id, reference
        # The current Runtime exposes evidence checks through Continue only.
        # Report must not enter that control flow, so it degrades explicitly
        # until an independent read-only capability exists.
        raise HandoffReportEvidenceCheckUnavailableError


class RuntimeWorkContinuityReadAdapter:
    """Project Work continuity through the Runtime's high-level read application."""

    def __init__(self, application: _WorkApplicationReader, /) -> None:
        self._application = application

    async def get(self, scope_id: str, reference: ArtifactRef | None, /) -> WorkContinuity:
        return await self._application.for_scope(scope_id).continuity(reference)


__all__ = ["RuntimeHandoffReadAdapter", "RuntimeWorkContinuityReadAdapter"]
