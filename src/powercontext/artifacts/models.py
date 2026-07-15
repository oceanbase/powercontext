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

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str):
            raise TypeError("artifact_id must be a string")  # noqa: TRY003
        if not self.artifact_id.strip():
            raise ValueError("artifact_id must not be empty")  # noqa: TRY003
        if type(self.revision) is not int:
            raise TypeError("revision must be an integer")  # noqa: TRY003
        if self.revision < 1:
            raise ValueError("revision must be positive")  # noqa: TRY003


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

    def __post_init__(self) -> None:
        _validate_family(self.family)


@dataclass(frozen=True, slots=True, kw_only=True)
class Artifact(Generic[ContentT]):
    """An immutable snapshot in an artifact lifecycle."""

    family: ClassVar[str] = "artifact"

    artifact_id: str
    revision: int
    content: ContentT
    lineage: ArtifactLineage = field(default_factory=ArtifactLineage)

    def __post_init__(self) -> None:
        _validate_family(self.family)
        ArtifactRef(self.artifact_id, self.revision)

    @property
    def ref(self) -> ArtifactRef:
        """Return an exact reference to this revision."""

        return ArtifactRef(self.artifact_id, self.revision)


def _validate_family(family: object) -> None:
    if not isinstance(family, str) or not family.strip():
        raise ValueError("artifact family must not be empty")  # noqa: TRY003
