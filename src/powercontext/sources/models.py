from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from powercontext.errors import SourceDefinitionError


class SourceMaterialization(StrEnum):
    """Describe where the value read for a Source comes from."""

    CAPTURED = "captured"
    REFERENCED = "referenced"


@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """Base value for a named, adapter-owned Source description."""

    source_type: ClassVar[str]

    name: str
    materialization: SourceMaterialization
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise SourceDefinitionError("name", self.name, "must be a non-empty string")
        if not isinstance(self.materialization, SourceMaterialization):
            raise SourceDefinitionError(
                "materialization",
                self.materialization,
                f"must be a {SourceMaterialization.__name__}",
            )
        if self.description is not None and not isinstance(self.description, str):
            raise SourceDefinitionError("description", self.description, "must be a string or None")

        source_type = getattr(type(self), "source_type", None)
        if not isinstance(source_type, str) or not source_type.strip():
            raise SourceDefinitionError("source_type", source_type, "must be a non-empty class attribute")
