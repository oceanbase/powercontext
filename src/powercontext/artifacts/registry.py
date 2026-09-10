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

"""Registry values shared by Artifact-family composition and readers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from powercontext.artifacts.models import Artifact

ArtifactListOrder = Literal["artifact_id:asc", "published_at:desc"]


@dataclass(frozen=True, slots=True)
class ArtifactFamilyDefinition:
    """Static capabilities and identity for one Artifact family."""

    artifact_type: type[Artifact[Any]]
    standard_read: bool = True
    standard_write: bool = False
    supports_tags: bool = False
    list_order: ArtifactListOrder = "artifact_id:asc"

    @property
    def family(self) -> str:
        """Return the stable wire family name declared by the Artifact type."""

        return self.artifact_type.family


class ArtifactFamilyRegistry:
    """Provide one immutable routing view for Artifact-family registration."""

    def __init__(self, definitions: Iterable[ArtifactFamilyDefinition], /) -> None:
        registered = tuple(definitions)
        by_family: dict[str, ArtifactFamilyDefinition] = {}
        for definition in registered:
            family = definition.family
            if family in by_family:
                raise ValueError(f"Artifact families must be unique: {family}")  # noqa: TRY003
            if definition.list_order not in {"artifact_id:asc", "published_at:desc"}:
                raise ValueError(f"Unsupported Artifact list order: {definition.list_order}")  # noqa: TRY003
            by_family[family] = definition
        if not registered:
            raise ValueError("Artifact family registry must not be empty")  # noqa: TRY003
        self._definitions = registered
        self._by_family = by_family

    @property
    def definitions(self) -> tuple[ArtifactFamilyDefinition, ...]:
        """Return definitions in registration order."""

        return self._definitions

    @property
    def artifact_types(self) -> tuple[type[Artifact[Any]], ...]:
        """Return the registered Artifact types for repository composition."""

        return tuple(definition.artifact_type for definition in self._definitions)

    @property
    def families(self) -> frozenset[str]:
        """Return all registered family wire names."""

        return frozenset(self._by_family)

    def definition_for(self, family: str, /) -> ArtifactFamilyDefinition:
        """Return the definition for one family or raise ``KeyError``."""

        return self._by_family[family]


__all__ = ["ArtifactFamilyDefinition", "ArtifactFamilyRegistry", "ArtifactListOrder"]
