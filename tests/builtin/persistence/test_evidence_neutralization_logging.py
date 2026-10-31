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

"""Neutral evidence facts report a bounded, content-free operational reason."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from pydantic import field_serializer, field_validator
from sqlalchemy import update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.atomic_memory_evidence import AtomicMemoryEvidenceRepository
from powercontext.builtin.persistence.atomic_memory_evidence_schema import SOURCE_EVIDENCE_BINDINGS_TABLE
from powercontext.builtin.persistence.source_definitions import SourceDefinitionManifestRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.server.logging import JsonFormatter, OperationalContextFilter
from powercontext.sources import (
    AdapterSourceDefinition,
    MemoryEvidenceDeclaration,
    SourceDefinitionRegistry,
    SourceMaterialization,
    project_source_for_transport,
)
from tests.builtin.persistence.contract import NoteAdapter, NoteSource, repository_profile


def _neutral_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == "atomic_memory.evidence.neutral"]


def test_undecodable_snapshot_reports_decode_failed(caplog) -> None:
    reference = ArtifactRef(family="atomic-memory", artifact_id="memory", revision=1)

    with caplog.at_level(logging.DEBUG, logger="powercontext.sources.evidence"):
        assert AtomicMemoryEvidenceRepository._decode("scope", reference, b"\xffnot-json") is None

    record = _neutral_records(caplog)[-1]
    assert record.getMessage() == "Memory evidence resolved to neutral"
    assert (
        getattr(record, "operation", None),
        getattr(record, "unit", None),
        getattr(record, "outcome", None),
        getattr(record, "reason", None),
    ) == (
        "atomic_memory.evidence",
        "declaration",
        "neutral",
        "decode_failed",
    )


def test_neutral_reason_is_serialized_without_evidence_content(caplog) -> None:
    reference = ArtifactRef(family="atomic-memory", artifact_id="memory", revision=1)

    with caplog.at_level(logging.DEBUG, logger="powercontext.sources.evidence"):
        AtomicMemoryEvidenceRepository._decode("scope", reference, b"\xffnot-json")

    record = _neutral_records(caplog)[-1]
    OperationalContextFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "atomic_memory.evidence.neutral"
    assert (payload["operation"], payload["unit"], payload["outcome"], payload["reason"]) == (
        "atomic_memory.evidence",
        "declaration",
        "neutral",
        "decode_failed",
    )
    assert set(payload) <= {
        "timestamp",
        "level",
        "logger",
        "message",
        "event",
        "operation",
        "unit",
        "outcome",
        "reason",
        "request_id",
        "trace_id",
        "span_id",
        "service_name",
        "service_version",
        "service_role",
        "service_instance_id",
    }


def test_observation_fingerprint_mismatch_reports_digest_mismatch(caplog) -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry.from_adapters((NoteAdapter(),))
        source = NoteSource(name="observation", materialization=SourceMaterialization.CAPTURED, body="Retained text.")
        definition = registry.definition_for_name("note")
        async with repository_profile() as (profile, _), profile.database.transaction() as connection:
            await SourceDefinitionManifestRepository().register_native(connection, definition)
            observation = project_source_for_transport(registry, source).model_copy(
                update={"definition_fingerprint": "sha256:" + "0" * 64}
            )
            with caplog.at_level(logging.DEBUG, logger="powercontext.sources.evidence"):
                await SourceRepository(registry).add(connection, "scope", observation)
            records = _neutral_records(caplog)
            assert [getattr(record, "reason", None) for record in records] == ["digest_mismatch"]
            assert records[0].levelno == logging.WARNING

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_binding", [False, True])
def test_schema_neutral_evidence_does_not_report_digest_corruption(caplog, legacy_binding: bool) -> None:
    class SerializedNote(NoteSource):
        @field_serializer("body")
        def serialize_body(self, value: str):
            return {"text": value}

        @field_validator("body", mode="before")
        @classmethod
        def deserialize_body(cls, value):
            return value["text"] if isinstance(value, dict) else value

    class SerializedNoteAdapter(NoteAdapter):
        source_class = SerializedNote

    async def scenario() -> None:
        declaration = MemoryEvidenceDeclaration(authority="repository_attested", verification="verified")
        definition = AdapterSourceDefinition(SerializedNoteAdapter(), memory_evidence=declaration)
        repository = SourceRepository(SourceDefinitionRegistry((definition,)))
        source = SerializedNote(
            name="serialized", materialization=SourceMaterialization.CAPTURED, body="Retained text."
        )
        async with repository_profile() as (profile, _), profile.database.transaction() as connection:
            with caplog.at_level(logging.DEBUG, logger="powercontext.sources.evidence"):
                stored = await repository.add(connection, "scope", source)
            assert [getattr(record, "reason", None) for record in _neutral_records(caplog)] == ["schema_mismatch"]
            if legacy_binding:
                manifest = await SourceDefinitionManifestRepository().get(connection, "note", "1")
                await connection.execute(
                    update(SOURCE_EVIDENCE_BINDINGS_TABLE).values(
                        manifest_fingerprint=manifest.fingerprint,
                        declaration=declaration.model_dump_json().encode("utf-8"),
                    )
                )
            caplog.clear()
            with caplog.at_level(logging.DEBUG, logger="powercontext.sources.evidence"):
                evidence = await repository.evidence(connection, "scope", (stored.ref,))
            assert evidence[0].declaration == MemoryEvidenceDeclaration()
            assert not any(record.levelno >= logging.WARNING for record in _neutral_records(caplog))
            if legacy_binding:
                assert [getattr(record, "reason", None) for record in _neutral_records(caplog)] == ["schema_mismatch"]

    asyncio.run(scenario())
