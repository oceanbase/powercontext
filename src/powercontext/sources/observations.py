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

"""Worker-owned Source Definition manifests and projected observations."""

from __future__ import annotations

import hashlib
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

import rfc8785
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

from powercontext.errors import (
    InvalidSourceDefinitionError,
    InvalidSourceObservationError,
    InvalidSourceProjectionError,
)
from powercontext.limits import MAX_SOURCE_TYPE_LENGTH
from powercontext.sources.definitions import SourceDefinition, SourceDefinitionRegistry
from powercontext.sources.models import Source, SourceMaterialization, SourceProjectionKey

_JSON_VALUE = TypeAdapter(JsonValue)


class SourceProjectionManifest(BaseModel):
    """Declarative schema for one worker-computed named projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: SourceProjectionKey
    schema_: dict[str, JsonValue] = Field(alias="schema")

    @field_validator("schema_")
    @classmethod
    def validate_schema_references(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _reject_remote_schema_references(value)
        return value


class SourceDefinitionManifest(BaseModel):
    """Immutable declarative identity registered by a remote worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    fingerprint: str
    source_schema: dict[str, JsonValue]
    projections: tuple[SourceProjectionManifest, ...] = ()

    @field_validator("name", "version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value or value.strip() != value or len(value) > MAX_SOURCE_TYPE_LENGTH:
            raise ValueError("manifest identity must be a bounded non-empty trimmed string")  # noqa: TRY003
        return value

    @field_validator("projections")
    @classmethod
    def validate_projection_limit(
        cls,
        value: tuple[SourceProjectionManifest, ...],
    ) -> tuple[SourceProjectionManifest, ...]:
        if len(value) > 16:
            raise ValueError("manifest must not declare more than 16 projections")  # noqa: TRY003
        return value

    @field_validator("source_schema")
    @classmethod
    def validate_source_schema_references(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _reject_remote_schema_references(value)
        return value

    @field_validator("fingerprint")
    @classmethod
    def validate_fingerprint_shape(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("manifest fingerprint must use sha256:<hex>")  # noqa: TRY003
        try:
            int(value.removeprefix("sha256:"), 16)
        except ValueError as error:
            raise ValueError("manifest fingerprint must contain lowercase hexadecimal") from error  # noqa: TRY003
        if value != value.lower():
            raise ValueError("manifest fingerprint must contain lowercase hexadecimal")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> SourceDefinitionManifest:
        keys = tuple(projection.key for projection in self.projections)
        if len(set(keys)) != len(keys):
            raise ValueError("manifest projection keys must be unique")  # noqa: TRY003
        expected = _source_definition_fingerprint(
            name=self.name,
            version=self.version,
            source_schema=self.source_schema,
            projections=self.projections,
        )
        if self.fingerprint != expected:
            raise ValueError("manifest fingerprint does not match its declaration")  # noqa: TRY003
        return self


class SourceProjectionValue(BaseModel):
    """One named projection computed by the worker that owns the Definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: SourceProjectionKey
    value: JsonValue


class SourceObservation(Source):
    """Canonical captured observation stored without loading worker plugin code."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_type: str
    definition_fingerprint: str
    payload: dict[str, JsonValue]
    projections: tuple[SourceProjectionValue, ...] = ()

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        if not value or value.strip() != value or len(value) > MAX_SOURCE_TYPE_LENGTH:
            raise ValueError("source_type must be a bounded non-empty trimmed string")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_envelope_identity(self) -> SourceObservation:
        expected = {
            "name": self.name,
            "definition_version": self.definition_version,
            "materialization": self.materialization.value,
            "description": self.description,
        }
        for field, value in expected.items():
            if self.payload.get(field) != value:
                raise ValueError(f"projected Source payload {field} does not match its envelope")  # noqa: TRY003
        keys = tuple(projection.key for projection in self.projections)
        if len(set(keys)) != len(keys):
            raise ValueError("projected Source projection keys must be unique")  # noqa: TRY003
        return self

    def projection(self, key: SourceProjectionKey, /) -> JsonValue:
        for projection in self.projections:
            if projection.key == key:
                return projection.value
        raise InvalidSourceProjectionError(key.name, "key", "was not supplied by the worker")


def manifest_for_definition(definition: SourceDefinition[Any, Any, Any], /) -> SourceDefinitionManifest:
    """Build a declaration matching the canonical remote payload representation."""

    return _manifest_for_definition(definition, transport=True)


def _manifest_for_definition(
    definition: SourceDefinition[Any, Any, Any],
    /,
    *,
    transport: bool,
) -> SourceDefinitionManifest:
    if transport and _validation_schema_matches_payload(definition.source_class):
        source_schema = _json_object(definition.source_class.model_json_schema())
    elif transport:
        source_schema = _json_object(
            definition.source_class.model_json_schema(
                mode="serialization",
                by_alias=bool(definition.source_class.model_config.get("serialize_by_alias")),
            )
        )
    else:
        source_schema = _json_object(definition.source_class.model_json_schema(mode="serialization"))
    projections = tuple(
        SourceProjectionManifest(
            key=SourceProjectionKey(name=projection.name, version=projection.version),
            schema=projection.output_class.model_json_schema(),
        )
        for projection in definition.projections
    )
    return SourceDefinitionManifest(
        name=definition.name,
        version=definition.version,
        fingerprint=_source_definition_fingerprint(
            name=definition.name,
            version=definition.version,
            source_schema=source_schema,
            projections=projections,
        ),
        source_schema=source_schema,
        projections=projections,
    )


def project_source_for_transport(
    registry: SourceDefinitionRegistry,
    source: Source,
    /,
) -> SourceObservation:
    """Execute one worker-owned Definition and serialize its durable result."""

    definition = registry.definition_for_source(source)
    if source.materialization is not SourceMaterialization.CAPTURED:
        raise InvalidSourceObservationError(
            "materialization",
            "remote Source observations must retain their canonical value",
        )
    manifest = manifest_for_definition(definition)
    payload = _json_object(source.model_dump(mode="json"))
    projections = tuple(
        SourceProjectionValue(key=key, value=registry.project(source, key)) for key in registry.projection_keys(source)
    )
    return SourceObservation(
        name=source.name,
        definition_version=source.definition_version,
        materialization=SourceMaterialization.CAPTURED,
        description=source.description,
        source_type=definition.name,
        definition_fingerprint=manifest.fingerprint,
        payload=payload,
        projections=projections,
    )


def _validation_schema_matches_payload(
    source_class: type[BaseModel],
    seen: set[type[BaseModel]] | None = None,
) -> bool:
    """Return whether validation-mode keys accept the serialized payload."""

    visited = set() if seen is None else seen
    if source_class in visited:
        return True
    visited.add(source_class)
    serialize_by_alias = bool(source_class.model_config.get("serialize_by_alias"))
    # Preserve existing permissive manifests; optional renamed keys only fail when extras are forbidden.
    forbid_extra = source_class.model_config.get("extra") == "forbid"
    for name, field in source_class.model_fields.items():
        if field.exclude:
            continue
        if field.is_required() or forbid_extra:
            serialization_key = (field.serialization_alias or field.alias or name) if serialize_by_alias else name
            if _validation_schema_key(name, field) != serialization_key:
                return False
        # A null default means this field can reach the payload as null, so a renamed
        # shape below it need not appear and cannot justify replacing a manifest a
        # deployment already accepted. Populating one instead requires the nested
        # model to serialize by alias, which keeps the validation keys authoritative,
        # or a new Definition version.
        if field.default is None and not forbid_extra:
            continue
        if not _validation_schema_matches_annotation(field.annotation, visited):
            return False
    return True


def _validation_schema_matches_annotation(annotation: Any, seen: set[type[BaseModel]]) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _validation_schema_matches_payload(annotation, seen)
    arguments = get_args(annotation)
    if get_origin(annotation) in (Union, UnionType):
        return _union_validation_covers_payloads(arguments, seen)
    return all(_validation_schema_matches_annotation(argument, seen) for argument in arguments)


def _union_validation_covers_payloads(arguments: tuple[Any, ...], seen: set[type[BaseModel]]) -> bool:
    # Every possible output needs coverage, not merely one compatible branch.
    for branch in arguments:
        if _validation_schema_matches_annotation(branch, set(seen)):
            continue
        if not isinstance(branch, type) or not issubclass(branch, BaseModel):
            return False
        serialized = branch.model_json_schema(
            mode="serialization", by_alias=bool(branch.model_config.get("serialize_by_alias"))
        )
        if not any(
            isinstance(candidate, type)
            and issubclass(candidate, BaseModel)
            and _same_flat_schema(serialized, candidate.model_json_schema())
            for candidate in arguments
        ):
            return False
    return True


def _same_flat_schema(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Prove flat model equivalence without interpreting references or nested schemas."""
    supported = {"title", "type", "properties", "required", "additionalProperties"}
    if left.keys() - supported or right.keys() - supported:
        return False
    left_properties = left.get("properties", {})
    right_properties = right.get("properties", {})
    if left_properties.keys() != right_properties.keys():
        return False
    for name, schema in left_properties.items():
        other = right_properties[name]
        # Only primitive properties: no refs, unions, containers or nested schemas.
        if schema.get("type") not in {"string", "integer", "number", "boolean", "null"}:
            return False
        if {key: value for key, value in schema.items() if key != "title"} != {
            key: value for key, value in other.items() if key != "title"
        }:
            return False
    return all(left.get(key) == right.get(key) for key in supported - {"title", "properties"})


def _validation_schema_key(name: str, field: Any) -> str | None:
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias
    if isinstance(alias, AliasChoices):
        return next((choice for choice in alias.choices if isinstance(choice, str)), None)
    if isinstance(alias, AliasPath):
        return name  # Pydantic keeps the field name for path-based validation.
    if alias is not None:
        return None
    return field.alias if field.alias is not None else name


def _source_definition_fingerprint(
    *,
    name: str,
    version: str,
    source_schema: dict[str, JsonValue],
    projections: tuple[SourceProjectionManifest, ...],
) -> str:
    declaration = {
        "name": name,
        "version": version,
        "source_schema": source_schema,
        "projections": [projection.model_dump(mode="json", by_alias=True) for projection in projections],
    }
    encoded = rfc8785.dumps(_JSON_VALUE.validate_python(declaration))
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_object(value: object) -> dict[str, JsonValue]:
    validated = _JSON_VALUE.validate_python(value)
    if not isinstance(validated, dict):
        raise InvalidSourceDefinitionError(type(value), "schema", "must be a JSON object")
    return validated


def _reject_remote_schema_references(schema: JsonValue) -> None:
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {"$ref", "$dynamicRef"} and isinstance(nested, str) and not nested.startswith("#"):
                    raise ValueError("remote schema references are not allowed")  # noqa: TRY003
                pending.append(nested)
        elif isinstance(value, list):
            pending.extend(value)


__all__ = [
    "SourceDefinitionManifest",
    "SourceObservation",
    "SourceProjectionManifest",
    "SourceProjectionValue",
    "manifest_for_definition",
    "project_source_for_transport",
]
