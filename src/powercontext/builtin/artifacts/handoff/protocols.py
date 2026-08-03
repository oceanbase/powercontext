"""Family-level ports used by the Handoff service."""

from __future__ import annotations

from typing import Protocol

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff.models import (
    Handoff,
    HandoffArtifactDraft,
    HandoffCitation,
    HandoffDraft,
    HandoffGenerationEvidence,
    HandoffGenerationRequest,
)


class HandoffEvidenceResolver(Protocol):
    """Resolve exact evidence in one scope."""

    async def resolve(self, citation: HandoffCitation, /) -> HandoffGenerationEvidence:
        """Return the canonical value addressed by one exact citation."""

        ...

    async def validate(self, citation: HandoffCitation, /) -> None:
        """Raise when the citation is unavailable or invalid."""

        ...


class HandoffGenerationPipeline(Protocol):
    """Generate an untrusted Handoff Draft from canonical bounded evidence."""

    async def generate(self, request: HandoffGenerationRequest, /) -> HandoffDraft:
        """Return a Draft that still requires service validation and inspection."""

        ...


class HandoffBackend(Protocol):
    """Persist and read one scope-bound Handoff Artifact lifecycle."""

    async def create(self, artifact_id: str, draft: HandoffArtifactDraft, /) -> Handoff:
        """Commit the first Revision."""

        ...

    async def revise(self, base: Handoff, draft: HandoffArtifactDraft, /) -> Handoff:
        """Commit a Revision only while ``base`` remains current."""

        ...

    async def get(self, reference: ArtifactRef, /) -> Handoff:
        """Read one exact Revision."""

        ...

    async def latest(self, artifact_id: str, /) -> Handoff | None:
        """Read the current Revision, or ``None`` when no milestone exists."""

        ...

    async def revisions(self, artifact_id: str, /) -> tuple[Handoff, ...]:
        """Read the immutable history in ascending order."""

        ...
