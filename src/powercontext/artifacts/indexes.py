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

"""Declarative, persistent search projections for ordinary Artifact families."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext.builtin.artifacts.memory.models import EmbeddingProfile

Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9-]*$")]
FieldName = Annotated[str, Field(min_length=1, max_length=128)]
Version = Annotated[str, Field(min_length=1, max_length=128, pattern=r".*\S.*")]
SearchMode = Literal["text", "vector", "hybrid"]


class _Declaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FragmentProjection(_Declaration):
    """Project an explicit persistent array; the framework does not split content."""

    array_field: FieldName
    id_field: FieldName


class TextIndex(_Declaration):
    name: Name
    kind: Literal["text"] = "text"
    projection_version: Version
    fields: tuple[FieldName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_fields(self):
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("text fields must be distinct")  # noqa: TRY003
        return self


class VectorIndex(_Declaration):
    """Index persistent vectors or encode persistent text in one exact profile.

    For content vectors, profile_field names a persistent EmbeddingProfile object
    alongside the vector. This prevents a producer replacing P1 with P2 in place.
    Fragment projections resolve fields within each persistent array item.
    """

    name: Name
    kind: Literal["vector"] = "vector"
    projection_version: Version
    source: Literal["text", "content"]
    profile: EmbeddingProfile
    fields: tuple[FieldName, ...] = ()
    vector_field: FieldName | None = None
    profile_field: FieldName | None = None
    fragments: FragmentProjection | None = None

    @model_validator(mode="after")
    def coherent_source(self):
        if not self.profile.profile_id.strip() or not self.profile.model.strip() or self.profile.dimension < 1:
            raise ValueError("an explicit model, profile identity and positive dimension are required")  # noqa: TRY003
        if self.source == "text":
            if not self.fields or self.vector_field is not None or self.profile_field is not None:
                raise ValueError("text vectors require text fields and cannot declare content vector fields")  # noqa: TRY003
            if len(set(self.fields)) != len(self.fields):
                raise ValueError("text fields must be distinct")  # noqa: TRY003
        elif self.fields or self.vector_field is None or self.profile_field is None:
            raise ValueError("content vectors require vector_field and profile_field, without text fields")  # noqa: TRY003
        return self


SearchIndex = Annotated[TextIndex | VectorIndex, Field(discriminator="kind")]


class ArtifactSearchDeclaration(_Declaration):
    version: Version
    indexes: tuple[SearchIndex, ...] = Field(min_length=1)
    modes: tuple[SearchMode, ...] = Field(min_length=1)
    default_mode: SearchMode
    defaults: dict[SearchMode, dict[Literal["text", "vector"], tuple[Name, ...]]] = Field(default_factory=dict)
    candidate_limit: int = Field(default=200, ge=1, le=2000, strict=True)
    rrf_k: int = Field(default=60, gt=0, strict=True)
    text_weight: float = Field(default=1, ge=0)
    vector_weight: float = Field(default=1, ge=0)

    @model_validator(mode="after")
    def coherent_modes(self):
        by_name = {index.name: index for index in self.indexes}
        if len(by_name) != len(self.indexes) or len(set(self.modes)) != len(self.modes):
            raise ValueError("index names and modes must be distinct")  # noqa: TRY003
        if self.default_mode not in self.modes or set(self.defaults) - set(self.modes):
            raise ValueError("defaults must refer to supported modes")  # noqa: TRY003
        if self.text_weight + self.vector_weight <= 0:
            raise ValueError("at least one fusion channel must have positive weight")  # noqa: TRY003
        for mode in self.modes:
            self.select_indexes(mode, self.defaults.get(mode))
        return self

    def select_indexes(self, mode, selection=None):  # noqa: C901 - Keep the complete projection or publication invariant together.
        if mode not in self.modes:
            raise ValueError("search mode is not declared")  # noqa: TRY003
        channels = {"text", "vector"} if mode == "hybrid" else {mode}
        selection = self.defaults.get(mode) if selection is None else selection
        if selection is None:
            selection = {}
            for channel in channels:
                names = tuple(index.name for index in self.indexes if index.kind == channel)
                if len(names) != 1:
                    raise ValueError("multiple indexes require explicit mode defaults")  # noqa: TRY003
                selection[channel] = names
        if set(selection) != channels:
            raise ValueError("index selection must match the requested channels")  # noqa: TRY003
        by_name = {index.name: index for index in self.indexes}
        result = []
        for channel, names in selection.items():
            if not names or len(set(names)) != len(names):
                raise ValueError("each channel requires distinct index names")  # noqa: TRY003
            for name in names:
                if name not in by_name or by_name[name].kind != channel:
                    raise ValueError("index is not declared for this channel")  # noqa: TRY003
                result.append(by_name[name])
        profiles = [index.profile for index in result if isinstance(index, VectorIndex)]
        if profiles and any(profile != profiles[0] for profile in profiles):
            raise ValueError("selected vector indexes require the same complete EmbeddingProfile")  # noqa: TRY003
        return tuple(result)


def validate_index_schema(index: TextIndex | VectorIndex, schema: dict[str, Any]) -> None:  # noqa: C901 - Keep the complete projection or publication invariant together.
    """Validate explicit required fields; no JSONPath or producer code is executed."""

    def resolve(value):
        seen = set()
        while "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/$defs/") or ref in seen:
                raise ValueError("index fields require local, nonrecursive schema definitions")  # noqa: TRY003
            seen.add(ref)
            value = schema["$defs"][ref.removeprefix("#/$defs/")]
        return value

    def field(obj, name, kind):
        obj = resolve(obj)
        value = resolve(obj.get("properties", {}).get(name, {}))
        if name not in obj.get("required", ()) or value.get("type") != kind:
            raise ValueError(f"index field {name!r} must be a required {kind}")  # noqa: TRY003
        return value

    obj = schema
    if isinstance(index, VectorIndex) and index.fragments is not None:
        array = field(obj, index.fragments.array_field, "array")
        obj = resolve(array.get("items", {}))
        if obj.get("type") != "object":
            raise ValueError("fragments must be persistent objects")  # noqa: TRY003
        field(obj, index.fragments.id_field, "string")
    if isinstance(index, TextIndex) or index.source == "text":
        for name in index.fields:
            field(obj, name, "string")
    else:
        vector = field(obj, index.vector_field, "array")
        if resolve(vector.get("items", {})).get("type") not in {"number", "integer"}:
            raise ValueError("vectors must contain numbers")  # noqa: TRY003
        field(obj, index.profile_field, "object")
