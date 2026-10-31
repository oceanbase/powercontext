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

"""Compare recorded processing results with the artifact store they name.

The ledger is written in the domain commit transaction, so a disagreement means
storage was changed outside that path. The comparison is limited to what the
records actually claim; an artifact with no ledger entry is not reported because
direct saves also create artifacts and lineage carries no processing origin.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.builtin.memory_processing import MemoryProcessingRecord
from powercontext.builtin.persistence.memory_processing import _decode
from powercontext.builtin.persistence.tables import (
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    MEMORY_PROCESSING_ARTIFACTS_TABLE,
    MEMORY_PROCESSING_RECORDS_TABLE,
)


@dataclass(frozen=True)
class ProcessingMismatch:
    """One disagreement between a record and stored artifacts."""

    check: str
    scope_id: str
    attempt_id: str
    reference: str

    @override
    def __str__(self) -> str:
        return f"{self.check}: scope {self.scope_id} attempt {self.attempt_id} -> {self.reference}"


def _key(ref) -> tuple[str, str, int]:
    return ref.family, ref.artifact_id, ref.revision


async def _existing(connection, scope_id: str, keys: set[tuple[str, str, int]]) -> set[tuple[str, str, int]]:
    if not keys:
        return set()
    rows = await connection.execute(
        select(ARTIFACTS_TABLE.c.family, ARTIFACTS_TABLE.c.artifact_id, ARTIFACTS_TABLE.c.revision).where(
            ARTIFACTS_TABLE.c.scope_id == scope_id,
            tuple_(ARTIFACTS_TABLE.c.family, ARTIFACTS_TABLE.c.artifact_id, ARTIFACTS_TABLE.c.revision).in_(keys),
        )
    )
    return {(row[0], row[1], row[2]) for row in rows}


async def _lineage_sources(connection, scope_id: str, key: tuple[str, str, int]) -> set[tuple[str, str]]:
    family, artifact_id, revision = key
    rows = await connection.execute(
        select(
            ARTIFACT_LINEAGE_SOURCES_TABLE.c.source_type,
            ARTIFACT_LINEAGE_SOURCES_TABLE.c.source_id,
        ).where(
            ARTIFACT_LINEAGE_SOURCES_TABLE.c.scope_id == scope_id,
            ARTIFACT_LINEAGE_SOURCES_TABLE.c.family == family,
            ARTIFACT_LINEAGE_SOURCES_TABLE.c.artifact_id == artifact_id,
            ARTIFACT_LINEAGE_SOURCES_TABLE.c.revision == revision,
        )
    )
    return {(row[0], row[1]) for row in rows}


async def _indexed(connection, scope_id: str, attempt_id: str) -> set[tuple[str, str, int]]:
    rows = await connection.execute(
        select(
            MEMORY_PROCESSING_ARTIFACTS_TABLE.c.artifact_id,
            MEMORY_PROCESSING_ARTIFACTS_TABLE.c.revision,
        ).where(
            MEMORY_PROCESSING_ARTIFACTS_TABLE.c.scope_id == scope_id,
            MEMORY_PROCESSING_ARTIFACTS_TABLE.c.attempt_id == attempt_id,
        )
    )
    return {("atomic-memory", row[0], row[1]) for row in rows}


def _report(record: MemoryProcessingRecord, check: str, reference: str) -> ProcessingMismatch:
    return ProcessingMismatch(check, record.scope_id, record.attempt_id, reference)


async def _check_results(connection, record, existing) -> list[ProcessingMismatch]:
    problems = []
    for result in record.results:
        if _key(result.artifact) not in existing:
            problems.append(
                _report(record, "result_missing_artifact", f"{result.artifact.family}/{result.artifact.artifact_id}")
            )
            continue
        lineage = await _lineage_sources(connection, record.scope_id, _key(result.artifact))
        for source in result.source_refs:
            if (source.source_type, source.source_id) not in lineage:
                problems.append(
                    _report(record, "result_source_not_in_lineage", f"{source.source_type}/{source.source_id}")
                )
    for ref in record.retained_artifacts:
        if _key(ref) not in existing:
            problems.append(
                _report(record, "retained_missing_artifact", f"{ref.family}/{ref.artifact_id}@{ref.revision}")
            )
    return problems


async def _check_record(connection, record: MemoryProcessingRecord) -> list[ProcessingMismatch]:
    expected = {_key(result.artifact) for result in record.results} | {_key(ref) for ref in record.retained_artifacts}
    existing = await _existing(connection, record.scope_id, expected)
    problems = await _check_results(connection, record, existing)
    if record.status != "succeeded":
        return problems
    indexed = await _indexed(connection, record.scope_id, record.attempt_id)
    if indexed != expected:
        problems.append(_report(record, "index_mismatch", f"expected {sorted(expected)} recorded {sorted(indexed)}"))
    if (record.outcome == "changed") != bool(record.results):
        problems.append(
            _report(record, "outcome_mismatch", f"outcome {record.outcome} with {len(record.results)} results")
        )
    return problems


async def reconcile(
    connection: AsyncConnection,
    *,
    scope_id: str | None = None,
) -> list[ProcessingMismatch]:
    """Compare every recorded result claim with the stored artifacts."""
    statement = select(MEMORY_PROCESSING_RECORDS_TABLE)
    if scope_id is not None:
        statement = statement.where(MEMORY_PROCESSING_RECORDS_TABLE.c.scope_id == scope_id)
    rows = (await connection.execute(statement)).mappings().all()
    problems: list[ProcessingMismatch] = []
    for row in rows:
        problems.extend(await _check_record(connection, _decode(row)))
    return problems
