"""Adapters from existing Runtime behavior into Handoff Report read ports."""

from __future__ import annotations

from typing import Protocol

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import (
    Handoff,
    HandoffEvidenceCheck,
)
from powercontext.builtin.handoff_report.errors import HandoffReportEvidenceCheckUnavailableError


class _ScopedHandoffReader(Protocol):
    async def latest(self) -> Handoff | None: ...

    async def revision(self, reference: ArtifactRef, /) -> Handoff: ...

    async def revisions(self) -> tuple[Handoff, ...]: ...


class _HandoffApplicationReader(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedHandoffReader: ...


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


__all__ = ["RuntimeHandoffReadAdapter"]
