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

"""Validate transported Sources against their immutable definition manifests."""

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.protocols import Validator
from referencing import Registry
from referencing.exceptions import Unresolvable

from powercontext.errors import InvalidSourceDefinitionError, InvalidSourceObservationError
from powercontext.limits import MAX_SOURCE_OBSERVATION_BYTES
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionManifest,
    SourceObservation,
    TextEvidence,
    TextEvidenceV1,
)


def validate_source_definition_manifest(manifest: SourceDefinitionManifest, /) -> None:
    """Validate the bounded, network-independent schemas declared by a worker."""

    if len(manifest.model_dump_json(by_alias=True).encode()) > 64 * 1024:
        raise InvalidSourceDefinitionError(type(manifest), "manifest", "must not exceed 64 KiB")
    json_schema_validator(manifest.name, manifest.source_schema)
    standard_text_schema = TextEvidence.model_json_schema()
    for projection in manifest.projections:
        json_schema_validator(projection.key.name, projection.schema_)
        if projection.key == TEXT_EVIDENCE_PROJECTION_KEY and projection.schema_ not in (
            standard_text_schema,
            TextEvidenceV1.model_json_schema(),
        ):
            raise InvalidSourceDefinitionError(
                type(manifest), "projection", f"{projection.key.name!r} must use the standard schema"
            )


def validate_source_observation(source: SourceObservation, manifest: SourceDefinitionManifest, /) -> None:
    """Check captured payloads and every projection using the owning declaration."""

    if source.source_type != manifest.name or source.definition_version != manifest.version:
        raise InvalidSourceObservationError("definition", "does not match the registered manifest identity")
    if source.definition_fingerprint != manifest.fingerprint:
        raise InvalidSourceObservationError("fingerprint", "does not match the registered manifest")
    if len(source.model_dump_json().encode()) > MAX_SOURCE_OBSERVATION_BYTES:
        raise InvalidSourceObservationError("size", "must not exceed 4 MiB")
    _validate_schema_value(manifest.name, manifest.source_schema, source.payload)
    declarations = {projection.key: projection for projection in manifest.projections}
    supplied = {projection.key: projection.value for projection in source.projections}
    if declarations.keys() != supplied.keys():
        raise InvalidSourceObservationError("projections", "must exactly match the registered manifest")
    for key, declaration in declarations.items():
        value = supplied[key]
        _validate_schema_value(key.name, declaration.schema_, value)
        if key == TEXT_EVIDENCE_PROJECTION_KEY:
            evidence = TextEvidence.model_validate(value)
            if evidence.source_type != source.source_type or evidence.source_id != source.name:
                raise InvalidSourceObservationError(
                    "text-evidence", "source identity does not match the observation envelope"
                )


def json_schema_validator(name: str, schema: Mapping[str, Any], /) -> Validator:
    """Build a JSON Schema validator with no network-backed reference registry."""

    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, registry=Registry())
    except SchemaError as error:
        raise InvalidSourceDefinitionError(type(schema), "schema", f"{name!r} is not valid JSON Schema") from error


def _validate_schema_value(name: str, schema: Mapping[str, Any], value: object) -> None:
    try:
        json_schema_validator(name, schema).validate(value)
    except (JsonSchemaValidationError, Unresolvable) as error:
        raise InvalidSourceObservationError("schema", f"value does not match {name!r}") from error
