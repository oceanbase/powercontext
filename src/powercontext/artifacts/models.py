"""Domain values shared by artifact families."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Generic, TypeVar

from powercontext.sources import Source

ContentT = TypeVar("ContentT", covariant=True)


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A stable reference to one exact artifact revision."""

    artifact_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class ArtifactLineage:
    """The direct evidence used to produce one artifact revision."""

    sources: tuple[Source, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactDraft(Generic[ContentT]):
    """Content and complete evidence supplied for one Artifact write."""

    family: ClassVar[str] = "artifact"

    content: ContentT
    sources: tuple[Source, ...] = ()
    artifacts: tuple[Artifact[object], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Artifact(Generic[ContentT]):
    """An immutable snapshot in an artifact lifecycle."""

    family: ClassVar[str] = "artifact"

    artifact_id: str
    revision: int
    content: ContentT
    lineage: ArtifactLineage = field(default_factory=ArtifactLineage)

    @property
    def ref(self) -> ArtifactRef:
        """Return an exact reference to this revision."""

        return ArtifactRef(self.artifact_id, self.revision)
