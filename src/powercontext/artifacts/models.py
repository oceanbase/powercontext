"""Domain values shared by artifact families."""

from __future__ import annotations

from typing import ClassVar, Generic, TypeVar

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from powercontext.errors import InvalidArtifactReferenceError
from powercontext.limits import MAX_ARTIFACT_FAMILY_LENGTH, MAX_ARTIFACT_ID_LENGTH
from powercontext.sources.models import SourceRef

ContentT = TypeVar("ContentT", covariant=True)


class ArtifactRef(BaseModel):
    """A stable reference to one exact artifact revision."""

    family: str
    artifact_id: str
    revision: StrictInt = Field(ge=1)

    @field_validator("family", "artifact_id")
    @classmethod
    def validate_identity(cls, value: str, info) -> str:
        _validate_reference_part(info.field_name, value)
        maximum = MAX_ARTIFACT_FAMILY_LENGTH if info.field_name == "family" else MAX_ARTIFACT_ID_LENGTH
        if len(value) > maximum:
            raise InvalidArtifactReferenceError(info.field_name, f"must not exceed {maximum} characters")
        return value


class ArtifactLineage(BaseModel):
    """The direct evidence used to produce one artifact revision."""

    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()


class ArtifactDraft(BaseModel, Generic[ContentT]):
    """Content and complete evidence supplied for one Artifact write."""

    family: ClassVar[str] = "artifact"

    content: ContentT
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def validate_family(self):
        _validate_reference_part("family", self.family)
        return self


class Artifact(BaseModel, Generic[ContentT]):
    """An immutable snapshot in an artifact lifecycle."""

    family: ClassVar[str] = "artifact"

    artifact_id: str
    revision: StrictInt = Field(ge=1)
    content: ContentT
    lineage: ArtifactLineage = Field(default_factory=ArtifactLineage)

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        _validate_reference_part("artifact_id", value)
        if len(value) > MAX_ARTIFACT_ID_LENGTH:
            raise InvalidArtifactReferenceError(
                "artifact_id",
                f"must not exceed {MAX_ARTIFACT_ID_LENGTH} characters",
            )
        return value

    def as_ref(self) -> ArtifactRef:
        """Return an exact reference to this revision."""

        return ArtifactRef(family=self.family, artifact_id=self.artifact_id, revision=self.revision)


def _validate_reference_part(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidArtifactReferenceError(field_name, "must be a non-empty string")
    if value != value.strip():
        raise InvalidArtifactReferenceError(field_name, "must not contain leading or trailing whitespace")
