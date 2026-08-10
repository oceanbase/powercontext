"""Read-only ports consumed by the optional Handoff Report feature."""

from __future__ import annotations

from typing import Protocol

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import Handoff, HandoffEvidenceCheck


class HandoffReadAdapter(Protocol):
    """Read committed Handoffs without extending their persistence protocol."""

    async def latest(self, scope_id: str, /) -> Handoff | None:
        """Return one scope's current committed Handoff, if it exists."""

        ...

    async def get(self, scope_id: str, reference: ArtifactRef, /) -> Handoff:
        """Return the exact committed Handoff addressed by ``reference``."""

        ...

    async def revisions(self, scope_id: str, /) -> tuple[Handoff, ...]:
        """Return one scope's committed Handoffs in ascending Revision order."""

        ...

    async def check_evidence(
        self,
        scope_id: str,
        reference: ArtifactRef,
        /,
    ) -> tuple[HandoffEvidenceCheck, ...]:
        """Recheck evidence readability for one exact committed Handoff."""

        ...


__all__ = ["HandoffReadAdapter"]
