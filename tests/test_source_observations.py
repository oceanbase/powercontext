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

from __future__ import annotations

import asyncio
import hashlib
import urllib.request
from decimal import Decimal
from typing import Self

import pytest
import rfc8785
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer
from pydantic.alias_generators import to_camel
from referencing.exceptions import Unresolvable

from powercontext import InvalidSourceObservationError
from powercontext.builtin.persistence.source_definitions import SourceDefinitionManifestRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime import SubmitSourceObservation
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import CONTENT_SOURCE_DEFINITION, ContentCapture
from powercontext.builtin.sources.observation_validation import json_schema_validator
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceCatalog,
    SourceDefinitionManifest,
    SourceDefinitionRegistry,
    SourceMaterialization,
    SourceObservation,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)


class EmptySourceBackend:
    async def list(self) -> tuple[Source, ...]:
        return ()

    async def get(self, source: Source, /) -> Source:
        raise AssertionError(source)


def test_definition_manifest_has_a_stable_content_addressed_identity() -> None:
    first = manifest_for_definition(CONTENT_SOURCE_DEFINITION)
    second = manifest_for_definition(CONTENT_SOURCE_DEFINITION)

    assert first == second
    assert first.fingerprint.startswith("sha256:")
    with pytest.raises(ValidationError, match="fingerprint does not match"):
        SourceDefinitionManifest.model_validate(first.model_dump(mode="json", by_alias=True) | {"version": "2"})


def test_source_observation_remains_usable_without_worker_definition_code() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,))
        source = await registry.resolve(
            ContentCapture(source_id="turn-1", content="Keep the remote contract declarative.")
        )
        projected = project_source_for_transport(registry, source)
        catalog = SourceCatalog(backend=EmptySourceBackend())
        payload = await catalog.read(projected)
        projection = catalog.project(projected, TEXT_EVIDENCE_PROJECTION_KEY)

        assert isinstance(projected, SourceObservation)
        assert catalog.as_ref(projected).model_dump() == {"source_type": "content", "source_id": "turn-1"}
        assert payload == projected.payload
        assert catalog.projection_keys(projected) == (TEXT_EVIDENCE_PROJECTION_KEY,)
        assert registry.project(projected, TEXT_EVIDENCE_PROJECTION_KEY) == projection
        assert TextEvidence.model_validate(projection).content == "Keep the remote contract declarative."

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name_alias", "body_alias"),
    [("source_name", "wire_text"), ("body", "wire_text"), ("source_name", "name"), ("wire_text", "wire_text")],
)
def test_serialization_aliases_preserve_the_worker_envelope_during_ingestion(
    name_alias: str, body_alias: str, tmp_path
) -> None:
    class AliasedSource(Source):
        name: str = Field(serialization_alias=name_alias)
        definition_version: str = Field(default="1", serialization_alias="wire_version")
        materialization: SourceMaterialization = Field(serialization_alias="wire_materialization")
        description: str | None = Field(default=None, serialization_alias="wire_description")
        body: str = Field(serialization_alias=body_alias)

    class AliasedAdapter:
        name = "aliased"
        input_class = str
        source_class = AliasedSource

        async def resolve(self, value: str, /) -> AliasedSource:
            return AliasedSource(
                name="remote-aliased",
                definition_version="2",
                materialization=SourceMaterialization.CAPTURED,
                description="Aliased description.",
                body=value,
            )

        async def read(self, source: AliasedSource, /) -> str:
            return source.body

    async def scenario() -> None:
        definition = AdapterSourceDefinition(AliasedAdapter(), version="2")
        worker = SourceDefinitionRegistry((definition,))
        source = await worker.resolve("Retain the aliased business value.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(worker, source)
        expected = {
            "name": "remote-aliased",
            "definition_version": "2",
            "materialization": "captured",
            "description": "Aliased description.",
            "body": "Retain the aliased business value.",
        }
        assert manifest.source_schema == AliasedSource.model_json_schema()
        baseline_declaration = {
            "name": definition.name,
            "version": definition.version,
            "source_schema": AliasedSource.model_json_schema(),
            "projections": [],
        }
        baseline_fingerprint = f"sha256:{hashlib.sha256(rfc8785.dumps(baseline_declaration)).hexdigest()}"
        assert manifest.fingerprint == baseline_fingerprint
        assert observation.payload == expected
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'remote-manifest.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest

        current_manifest = manifest_for_definition(definition)
        assert current_manifest == manifest
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(current_manifest) == manifest
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            stored = await (await contexts.get("scope-a")).sources.get(observation)

        assert receipt.source_ref.source_id == "remote-aliased"
        assert isinstance(stored, SourceObservation)
        assert stored.payload == expected

        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as native_profile:
            async with native_profile.database.transaction() as connection:
                native_manifest = await SourceDefinitionManifestRepository().register_native(connection, definition)
            assert native_manifest is not None
            assert native_manifest.source_schema == AliasedSource.model_json_schema(mode="serialization")
            assert native_manifest.fingerprint != manifest.fingerprint

    asyncio.run(scenario())


def test_alias_serialization_remote_manifest_and_payload_remain_upgrade_stable(tmp_path) -> None:
    class AliasedSource(Source):
        model_config = ConfigDict(serialize_by_alias=True, populate_by_name=True)
        body: str = Field(alias="wire_text")

    class AliasedAdapter:
        name = "aliased-upgrade"
        input_class = str
        source_class = AliasedSource

        async def resolve(self, value: str, /) -> AliasedSource:
            return AliasedSource(
                name="remote-aliased",
                definition_version="2",
                materialization=SourceMaterialization.CAPTURED,
                wire_text=value,
            )

        async def read(self, source: AliasedSource, /) -> str:
            return source.body

    async def scenario() -> None:
        definition = AdapterSourceDefinition(AliasedAdapter(), version="2")
        registry = SourceDefinitionRegistry((definition,))
        source = await registry.resolve("Retained alias.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(registry, source)
        expected = AliasedSource.model_json_schema()
        expected_fingerprint = hashlib.sha256(
            rfc8785.dumps({
                "name": definition.name,
                "version": definition.version,
                "source_schema": expected,
                "projections": [],
            })
        ).hexdigest()
        assert manifest.source_schema == expected
        assert manifest.fingerprint == f"sha256:{expected_fingerprint}"
        assert observation.payload["wire_text"] == "Retained alias."

        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'remote-alias-upgrade.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            assert receipt.source_ref.source_id == "remote-aliased"

    asyncio.run(scenario())


def test_optional_alias_with_forbidden_extras_accepts_the_worker_payload() -> None:
    class OptionalAliasSource(Source):
        model_config = ConfigDict(extra="forbid")
        body: str = Field(default="Default body.", alias="wire_text")

    class OptionalAliasAdapter:
        name = "optional-alias"
        input_class = str
        source_class = OptionalAliasSource

        async def resolve(self, value: str, /) -> OptionalAliasSource:
            return OptionalAliasSource.model_validate({
                "name": "optional",
                "materialization": SourceMaterialization.CAPTURED,
                "body": value,
            })

        async def read(self, source: OptionalAliasSource, /) -> str:
            return source.body

    async def scenario() -> None:
        definition = AdapterSourceDefinition(OptionalAliasAdapter(), version="1")
        registry = SourceDefinitionRegistry((definition,))
        source = await registry.resolve("Retain the optional value.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(registry, source)

        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            await contexts.register_source_definition(manifest)
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            stored = await (await contexts.get("scope-a")).sources.get(observation)

        assert receipt.source_ref.source_id == "optional"
        assert isinstance(stored, SourceObservation)
        assert stored.payload["body"] == "Retain the optional value."

    asyncio.run(scenario())


def test_optional_alias_keeps_the_preexisting_manifest_fingerprint(tmp_path) -> None:
    class VersionedSource(Source):
        model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
        body: str

    class VersionedAdapter:
        name = "versioned-source"
        input_class = str
        source_class = VersionedSource

        async def resolve(self, value: str, /) -> VersionedSource:
            return VersionedSource(
                name="versioned",
                definition_version="2",
                materialization=SourceMaterialization.CAPTURED,
                body=value,
            )

        async def read(self, source: VersionedSource, /) -> str:
            return source.body

    async def scenario() -> None:
        definition = AdapterSourceDefinition(VersionedAdapter(), version="2")
        registry = SourceDefinitionRegistry((definition,))
        source = await registry.resolve("Retained.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(registry, source)
        baseline = {
            "name": definition.name,
            "version": definition.version,
            "source_schema": VersionedSource.model_json_schema(),
            "projections": [],
        }
        assert manifest.source_schema == VersionedSource.model_json_schema()
        assert manifest.fingerprint == f"sha256:{hashlib.sha256(rfc8785.dumps(baseline)).hexdigest()}"
        assert observation.payload["definition_version"] == "2"

        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'optional-alias.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            assert receipt.source_ref.source_id == "versioned"

    asyncio.run(scenario())


@pytest.mark.parametrize("wrapper", ["default", "list"])
def test_populated_optional_nested_alias_accepts_the_worker_payload(wrapper: str) -> None:
    # A field with a concrete default always reaches the payload, so a renamed nested
    # shape below it must be described by the serialization schema. A null default is
    # covered by test_nullable_nested_alias_upgrade_keeps_the_accepted_manifest.
    class Detail(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        label: str = Field(alias="wire_label")

    class DefaultNestedSource(Source):
        detail: Detail = Field(default_factory=lambda: Detail.model_validate({"label": "Default label."}))

    class ListNestedSource(Source):
        detail: list[Detail] = Field(default_factory=list)

    source_model = {
        "default": DefaultNestedSource,
        "list": ListNestedSource,
    }[wrapper]

    class NestedAliasAdapter:
        name = "optional-nested-alias"
        input_class = str
        source_class = source_model

        async def resolve(self, value: str, /) -> Source:
            detail = Detail.model_validate({"label": value})
            return source_model.model_validate({
                "name": "optional-nested",
                "materialization": SourceMaterialization.CAPTURED,
                "detail": [detail] if wrapper == "list" else detail,
            })

        async def read(self, source: Source, /) -> str:
            return source.name

    async def scenario() -> None:
        definition = AdapterSourceDefinition(NestedAliasAdapter(), version="1")
        registry = SourceDefinitionRegistry((definition,))
        source = await registry.resolve("Retain the nested value.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(registry, source)

        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            await contexts.register_source_definition(manifest)
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            stored = await (await contexts.get("scope-a")).sources.get(observation)

        expected_detail = {"label": "Retain the nested value."}
        assert receipt.source_ref.source_id == "optional-nested"
        assert isinstance(stored, SourceObservation)
        assert stored.payload["detail"] == ([expected_detail] if wrapper == "list" else expected_detail)

    asyncio.run(scenario())


def test_nullable_nested_alias_upgrade_keeps_the_accepted_manifest(tmp_path) -> None:
    """A payload that can only carry null cannot justify replacing an accepted manifest."""

    class Detail(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        label: str = Field(alias="wire_label")

    class NullableSource(Source):
        detail: Detail | None = None

    class NullableAdapter:
        name = "nullable-nested"
        input_class = str
        source_class = NullableSource

        async def resolve(self, value: str, /) -> NullableSource:
            return NullableSource(
                name="nullable",
                definition_version="2",
                materialization=SourceMaterialization.CAPTURED,
                detail=None,
            )

        async def read(self, source: NullableSource, /) -> str:
            return source.name

    async def scenario() -> None:
        definition = AdapterSourceDefinition(NullableAdapter(), version="2")
        registry = SourceDefinitionRegistry((definition,))
        source = await registry.resolve("Retained.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(registry, source)
        baseline = {
            "name": definition.name,
            "version": definition.version,
            "source_schema": NullableSource.model_json_schema(),
            "projections": [],
        }
        # Renaming the nested key only matters to material this Definition never emits.
        assert manifest.source_schema == NullableSource.model_json_schema()
        assert manifest.fingerprint == f"sha256:{hashlib.sha256(rfc8785.dumps(baseline)).hexdigest()}"
        assert observation.payload["detail"] is None

        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'nullable-nested.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            assert receipt.source_ref.source_id == "nullable"
            # Populating the field needs a representation the accepted manifest describes,
            # so the flat field-name payload is rejected instead of silently accepted.
            populated = project_source_for_transport(
                registry,
                NullableSource(
                    name="nullable",
                    definition_version="2",
                    materialization=SourceMaterialization.CAPTURED,
                    detail=Detail.model_validate({"label": "Populated."}),
                ),
            )
            with pytest.raises(InvalidSourceObservationError, match="schema"):
                await contexts.submit_source_observation(
                    SubmitSourceObservation(scope_id="scope-a", observation=populated)
                )

    asyncio.run(scenario())


def test_nullable_nested_alias_serializing_by_alias_accepts_populated_payloads(tmp_path) -> None:
    """A nested model that serializes by alias keeps the accepted manifest and populated values."""

    class Detail(BaseModel):
        model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
        label: str = Field(alias="wire_label")

    class NullableSource(Source):
        detail: Detail | None = None

    class PopulatedAdapter:
        name = "nullable-nested-alias"
        input_class = str
        source_class = NullableSource

        async def resolve(self, value: str, /) -> NullableSource:
            return NullableSource(
                name="populated",
                definition_version="1",
                materialization=SourceMaterialization.CAPTURED,
                detail=Detail.model_validate({"label": value}),
            )

        async def read(self, source: NullableSource, /) -> str:
            return source.name

    async def scenario() -> None:
        definition = AdapterSourceDefinition(PopulatedAdapter(), version="1")
        registry = SourceDefinitionRegistry((definition,))
        source = await registry.resolve("Retain the nested value.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(registry, source)
        assert manifest.source_schema == NullableSource.model_json_schema()
        assert observation.payload["detail"] == {"wire_label": "Retain the nested value."}

        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'nullable-alias.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            await contexts.register_source_definition(manifest)
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            stored = await (await contexts.get("scope-a")).sources.get(observation)

        assert receipt.source_ref.source_id == "populated"
        assert isinstance(stored, SourceObservation)
        assert stored.payload["detail"] == {"wire_label": "Retain the nested value."}

    asyncio.run(scenario())


def test_decimal_source_manifest_fingerprint_survives_upgrade(tmp_path) -> None:
    class AmountSource(Source):
        amount: Decimal

    class AmountAdapter:
        name = "amount-source"
        input_class = str
        source_class = AmountSource

        async def resolve(self, value: str, /) -> AmountSource:
            return AmountSource(
                name="amount",
                definition_version="1",
                materialization=SourceMaterialization.CAPTURED,
                amount=Decimal(value),
            )

        async def read(self, source: AmountSource, /) -> str:
            return str(source.amount)

    async def scenario() -> None:
        definition = AdapterSourceDefinition(AmountAdapter(), version="1")
        registry = SourceDefinitionRegistry((definition,))
        source = await registry.resolve("1.25")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(registry, source)
        baseline = {
            "name": definition.name,
            "version": definition.version,
            "source_schema": AmountSource.model_json_schema(),
            "projections": [],
        }
        baseline_fingerprint = f"sha256:{hashlib.sha256(rfc8785.dumps(baseline)).hexdigest()}"
        assert manifest.source_schema == AmountSource.model_json_schema()
        assert manifest.fingerprint == baseline_fingerprint
        assert observation.payload["amount"] == "1.25"

        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'decimal-upgrade.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            assert await contexts.register_source_definition(manifest) == manifest
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            assert receipt.source_ref.source_id == "amount"

    asyncio.run(scenario())


def test_recursive_source_transport_preserves_canonical_nested_payload() -> None:
    class RecursiveSource(Source):
        name: str = Field(serialization_alias="source_name")
        definition_version: str = Field(default="1", serialization_alias="wire_version")
        body: str = Field(serialization_alias="wire_text")
        children: list[Self] = Field(default_factory=list)

    class RecursiveAdapter:
        name = "recursive"
        input_class = str
        source_class = RecursiveSource

        async def resolve(self, value: str, /) -> RecursiveSource:
            leaf = RecursiveSource(name="leaf", materialization=SourceMaterialization.CAPTURED, body="Nested text.")
            return RecursiveSource(
                name="root",
                definition_version="2",
                materialization=SourceMaterialization.CAPTURED,
                body=value,
                children=[leaf],
            )

        async def read(self, source: RecursiveSource, /) -> str:
            return source.body

    async def scenario() -> None:
        definition = AdapterSourceDefinition(RecursiveAdapter(), version="2")
        worker = SourceDefinitionRegistry((definition,))
        source = await worker.resolve("Root text.")
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(worker, source)
        expected = {
            "name": "root",
            "definition_version": "2",
            "materialization": "captured",
            "description": None,
            "body": "Root text.",
            "children": [
                {
                    "name": "leaf",
                    "definition_version": "1",
                    "materialization": "captured",
                    "description": None,
                    "body": "Nested text.",
                    "children": [],
                }
            ],
        }

        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            await contexts.register_source_definition(manifest)
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            stored = await (await contexts.get("scope-a")).sources.get(observation)

        assert receipt.source_ref.source_id == "root"
        assert isinstance(stored, SourceObservation)
        assert stored.payload == expected

    asyncio.run(scenario())


def test_remote_source_observation_requires_captured_materialization() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,))
        source = await registry.resolve(ContentCapture(source_id="turn-1", content="Retain this value."))
        observation = project_source_for_transport(registry, source)

        with pytest.raises(ValidationError):
            SourceObservation.model_validate(observation.model_dump(mode="json") | {"materialization": "referenced"})

    asyncio.run(scenario())


def test_remote_identity_exclusions_and_serializers_fail_observation_validation() -> None:
    class ExcludedIdentitySource(Source):
        definition_version: str = Field(default="1", exclude=True)

    class TransformedIdentitySource(Source):
        @field_serializer("name")
        def encode_name(self, value: str) -> str:
            return f"wire:{value}"

    class Adapter:
        input_class = str
        name = "remote-identity"

        def __init__(self, source_class: type[Source]) -> None:
            self.source_class = source_class

        async def resolve(self, value: str, /) -> Source:
            return self.source_class(name=value, materialization=SourceMaterialization.CAPTURED)

        async def read(self, source: Source, /) -> str:
            return source.name

    async def scenario() -> None:
        for source_class in (ExcludedIdentitySource, TransformedIdentitySource):
            definition = AdapterSourceDefinition(Adapter(source_class), version="1")
            registry = SourceDefinitionRegistry((definition,))
            assert manifest_for_definition(definition).source_schema == source_class.model_json_schema()
            source = await registry.resolve("identity")
            with pytest.raises(ValidationError):
                project_source_for_transport(registry, source)

    asyncio.run(scenario())


def test_remote_schema_references_are_rejected_without_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted = False

    def reject_request(*_args: object, **_kwargs: object) -> None:
        nonlocal attempted
        attempted = True
        raise OSError("network access is forbidden")  # noqa: TRY003

    monkeypatch.setattr(urllib.request, "urlopen", reject_request)
    validator = json_schema_validator("remote", {"$ref": "http://127.0.0.1:9/schema"})

    with pytest.raises(Unresolvable):
        validator.validate({})
    assert not attempted

    with pytest.raises(ValidationError, match="remote schema references are not allowed"):
        SourceDefinitionManifest(
            name="remote",
            version="1",
            fingerprint=f"sha256:{'0' * 64}",
            source_schema={"$dynamicRef": "https://example.invalid/schema"},
        )


def test_covered_union_retains_registered_manifest_and_accepts_both_branches() -> None:
    class Aliased(BaseModel):
        model_config = ConfigDict(populate_by_name=True, extra="forbid")
        label: str = Field(validation_alias="wire_label")

    class Canonical(BaseModel):
        model_config = ConfigDict(extra="forbid")
        label: str

    class UnionSource(Source):
        item: Aliased | Canonical

    class UnionAdapter:
        name = "covered-union"
        input_class = str
        source_class = UnionSource

        async def resolve(self, value: str, /) -> UnionSource:
            return UnionSource.model_validate({
                "name": value,
                "definition_version": "1",
                "materialization": "captured",
                "item": {"label": value},
            })

        async def read(self, source: UnionSource, /) -> str:
            return source.item.label

    async def scenario() -> None:
        definition = AdapterSourceDefinition(UnionAdapter(), version="1")
        declaration = {
            "name": definition.name,
            "version": definition.version,
            "source_schema": UnionSource.model_json_schema(),
            "projections": [],
        }
        historical = SourceDefinitionManifest.model_validate(
            declaration
            | {
                "fingerprint": "sha256:" + hashlib.sha256(rfc8785.dumps(declaration)).hexdigest(),
            }
        )
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, source_registry=SourceDefinitionRegistry(()))
            await contexts.register_source_definition(historical)
            await contexts.register_source_definition(manifest_for_definition(definition))
            for index, branch in enumerate((Aliased.model_validate({"label": "a"}), Canonical(label="b"))):
                source = UnionSource.model_validate({
                    "name": f"branch-{index}",
                    "definition_version": "1",
                    "materialization": "captured",
                    "item": branch,
                })
                observation = project_source_for_transport(SourceDefinitionRegistry((definition,)), source)
                receipt = await contexts.submit_source_observation(
                    SubmitSourceObservation(scope_id="scope-a", observation=observation)
                )
                assert receipt.source_ref.source_id == source.name

    asyncio.run(scenario())
