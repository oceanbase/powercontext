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

"""Portable, verified logical bundles for built-in relational storage.

The archive deliberately contains domain rows only.  Database files, search
indexes, trigger cursors, usage statistics, and host-local skill registrations
are deployment details and are not portable state.
"""

# Error reason strings are the public inspection/validation contract.
# ruff: noqa: TRY003

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import insert, select, update
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_PUBLICATIONS_TABLE,
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    PORTABLE_RESTORE_RECEIPTS_TABLE,
    SCOPE_CONTEXT_REFERENCES_TABLE,
    SCOPE_CREATION_REQUESTS_TABLE,
    SCOPE_EXTERNAL_REFERENCES_TABLE,
    SCOPES_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)

FORMAT_VERSION = 1
_RECORDS_NAME = "records.ndjson"
_MANIFEST_NAME = "manifest.json"
_SHA256 = "sha256:"
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_RECORDS_BYTES = 2 * 1024 * 1024 * 1024
_MAX_RECORDS = 2_000_000
_MAX_LINE_BYTES = 4 * 1024 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024
_HEX_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ABSOLUTE_HOST_PATH = re.compile(r"^(?:/|file:/+|[A-Za-z]:[\\/]|\\\\)")
_SENSITIVE_KEYS = frozenset({
    "access_token",
    "api_key",
    "apikey",
    "bearer_token",
    "credential",
    "credentials",
    "password",
    "provider_secret",
    "refresh_token",
    "secret",
    "token",
})
_EXCLUDED_RECORD_CLASSES = (
    "access_bindings",
    "audit_records",
    "credentials_and_provider_secrets",
    "evaluation_receipts",
    "external_skill_registrations",
    "host_local_installation_state",
    "restore_receipts",
    "scope_default_selection",
    "search_projections",
    "source_cursors",
    "usage_statistics",
)

RecordType = Literal[
    "scope",
    "scope_context_reference",
    "scope_external_reference",
    "scope_creation_request",
    "source_journal_head",
    "source",
    "artifact_revision",
    "artifact_lineage_source",
    "artifact_lineage_artifact",
    "artifact_publication",
    "artifact_head",
    "memory_entry_version",
    "memory_entry_head",
    "candidate_version",
    "candidate_head",
]
ProjectionRebuilder = Callable[[tuple[str, ...]], Awaitable[None]]
ExportAuthorizer = Callable[[tuple[str, ...]], Awaitable[None]]
ProgressObserver = Callable[["BundleProgress"], None]


class BundleFormatError(ValueError):
    """The supplied archive is malformed, corrupt, or unsupported."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class BundleConflictError(ValueError):
    """A target immutable identity exists with different canonical content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class BundleInspection:
    """Content-free facts established by parsing and verifying a bundle."""

    bundle_id: str
    scopes: tuple[str, ...]
    record_count: int
    records_by_type: Mapping[str, int]
    total_digest: str
    format_version: int
    producer_version: str


@dataclass(frozen=True, slots=True)
class BundleValidation(BundleInspection):
    """Target compatibility established without performing writes."""

    already_present: int
    conflicts: int
    projections_supported: bool
    compatible: bool
    required_source_types: tuple[str, ...]
    unsupported_source_types: tuple[str, ...]
    required_artifact_families: tuple[str, ...]
    unsupported_artifact_families: tuple[str, ...]
    reader_version: str


@dataclass(frozen=True, slots=True)
class BundleReceipt:
    """Content-free result of an export or restore."""

    bundle_id: str
    record_count: int
    total_digest: str
    inserted: int = 0
    already_present: int = 0
    projections_ready: bool = False
    status: Literal["exported", "authoritative_restored", "ready"] = "exported"


@dataclass(frozen=True, slots=True)
class BundleProgress:
    """Content-free progress suitable for terminals and automation logs."""

    phase: Literal["export", "inspect", "validate", "target", "restore", "projections"]
    processed: int
    total: int | None


@dataclass(frozen=True, slots=True)
class _Record:
    record_type: RecordType
    identity: Mapping[str, str | int]
    payload: Mapping[str, Any]
    digest: str


@dataclass(frozen=True, slots=True)
class _ParsedBundle:
    inspection: BundleInspection


@dataclass(frozen=True, slots=True)
class _RecordSpec:
    """Stable logical fields and their current relational persistence adapter."""

    table: Any
    identity: tuple[str, ...]
    payload: tuple[str, ...]
    scope_field: str = "scope_id"


_SPECS: dict[RecordType, _RecordSpec] = {
    "scope": _RecordSpec(SCOPES_TABLE, ("scope_id",), ("title", "summary", "parent_scope_id", "version")),
    "scope_context_reference": _RecordSpec(SCOPE_CONTEXT_REFERENCES_TABLE, ("scope_id", "referenced_scope_id"), ()),
    "scope_external_reference": _RecordSpec(
        SCOPE_EXTERNAL_REFERENCES_TABLE,
        ("scope_id", "ordinal"),
        ("kind", "value", "value_digest"),
    ),
    "scope_creation_request": _RecordSpec(
        SCOPE_CREATION_REQUESTS_TABLE,
        ("idempotency_key",),
        ("request_digest", "scope_id"),
    ),
    "source_journal_head": _RecordSpec(SOURCE_JOURNAL_HEADS_TABLE, ("scope_id",), ("position",)),
    "source": _RecordSpec(
        SOURCES_TABLE,
        ("scope_id", "source_type", "source_id"),
        ("payload", "journal_position"),
    ),
    "artifact_revision": _RecordSpec(
        ARTIFACTS_TABLE,
        ("scope_id", "family", "artifact_id", "revision"),
        ("content", "memory_citations"),
    ),
    "artifact_lineage_source": _RecordSpec(
        ARTIFACT_LINEAGE_SOURCES_TABLE,
        ("scope_id", "family", "artifact_id", "revision", "ordinal"),
        ("source_type", "source_id"),
    ),
    "artifact_lineage_artifact": _RecordSpec(
        ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
        ("scope_id", "family", "artifact_id", "revision", "ordinal"),
        ("upstream_family", "upstream_artifact_id", "upstream_revision"),
    ),
    "artifact_publication": _RecordSpec(
        ARTIFACT_PUBLICATIONS_TABLE,
        ("target_scope_id", "target_family", "target_artifact_id", "target_revision"),
        (
            "source_scope_id",
            "source_family",
            "source_artifact_id",
            "source_revision",
            "content_digest",
            "idempotency_key",
        ),
        scope_field="target_scope_id",
    ),
    "artifact_head": _RecordSpec(
        ARTIFACT_HEADS_TABLE,
        ("scope_id", "family", "artifact_id"),
        ("revision", "lifecycle_state", "replacement_artifact_id", "governance_generation"),
    ),
    "memory_entry_version": _RecordSpec(
        MEMORY_ENTRY_VERSIONS_TABLE,
        ("scope_id", "memory_artifact_id", "entry_version_id"),
        (
            "family",
            "entry_id",
            "version",
            "previous_version_id",
            "kind",
            "text",
            "source_refs",
            "artifact_refs",
            "entry_content_hash",
            "created_in_revision",
        ),
    ),
    "memory_entry_head": _RecordSpec(
        MEMORY_ENTRY_HEADS_TABLE,
        ("scope_id", "memory_artifact_id", "entry_id"),
        ("family", "head_revision", "entry_version_id", "entry_content_hash"),
    ),
    "candidate_version": _RecordSpec(
        ARTIFACT_CANDIDATE_VERSIONS_TABLE,
        ("scope_id", "candidate_id", "version"),
        (
            "family",
            "proposal",
            "source_refs",
            "artifact_refs",
            "memory_citations",
            "target_family",
            "target_artifact_id",
            "target_revision",
            "reason",
        ),
    ),
    "candidate_head": _RecordSpec(
        ARTIFACT_CANDIDATE_HEADS_TABLE,
        ("scope_id", "candidate_id"),
        (
            "family",
            "version",
            "status",
            "result_family",
            "result_artifact_id",
            "result_revision",
            "decision_reason",
        ),
    ),
}

# Parent rows always precede their dependent rows.  This matters for SQLite as
# well as for MySQL/OceanBase foreign-key enforcement.
_EXPORT_ORDER: tuple[RecordType, ...] = tuple(_SPECS)


class PortableBundleService:
    """Export and restore complete scopes without depending on a DB dialect.

    The service is intentionally application-facing rather than a database-file
    utility.  Callers must perform authorization before calling :meth:`export`;
    records are never enumerated before the scope arguments are validated.
    """

    def __init__(
        self,
        database: AsyncDatabase,
        /,
        *,
        projection_rebuilder: ProjectionRebuilder | None = None,
        supported_source_types: Iterable[str] | None = None,
        supported_artifact_families: Iterable[str] | None = None,
    ) -> None:
        self._database = database
        self._projection_rebuilder = projection_rebuilder
        self._supported_source_types = None if supported_source_types is None else frozenset(supported_source_types)
        self._supported_artifact_families = (
            None if supported_artifact_families is None else frozenset(supported_artifact_families)
        )

    async def export(
        self,
        scopes: Iterable[str],
        output: Path,
        /,
        *,
        authorize: ExportAuthorizer,
        progress: ProgressObserver | None = None,
        compress: bool = True,
    ) -> BundleReceipt:
        """Write a verified archive containing all portable records for scopes.

        A single read transaction provides a consistent snapshot on supported
        relational backends.  The resulting ZIP is written only after the
        snapshot has been completely read, so a failed export never presents a
        partially finalized archive at ``output``.
        """

        selected = _validate_scopes(scopes)
        # Authorization is deliberately outside the snapshot transaction and
        # before the first database query or pagination operation.
        await authorize(selected)
        output.parent.mkdir(parents=True, exist_ok=True)
        records_path = _temporary_path(output.parent, suffix=".records.ndjson")
        archive_path = _temporary_path(output.parent, suffix=".pcb")
        try:
            with records_path.open("wb") as stream:
                async with self._database.transaction() as connection:
                    await _establish_export_snapshot(connection)
                    await _validate_selected_scopes(connection, selected)
                    count, records_by_type, digest = await self._stream_export(
                        connection, selected, stream, progress=progress
                    )
            bundle_id = str(uuid5(NAMESPACE_URL, f"powercontext:portable:v{FORMAT_VERSION}:{digest}"))
            manifest = {
                "format_version": FORMAT_VERSION,
                "bundle_id": bundle_id,
                "producer": {"name": "powercontext", "version": _producer_version()},
                "scopes": list(selected),
                "record_count": count,
                "records_by_type": dict(sorted(records_by_type.items())),
                "excluded_record_classes": list(_EXCLUDED_RECORD_CLASSES),
                "total_digest": digest,
            }
            compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
            with zipfile.ZipFile(
                archive_path, "w", compression=compression, compresslevel=9 if compress else None
            ) as archive:
                with (
                    records_path.open("rb") as source,
                    archive.open(_zip_info(_RECORDS_NAME, compression), "w") as target,
                ):
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                archive.writestr(_zip_info(_MANIFEST_NAME, compression), _canonical_json(manifest))
            archive_path.replace(output)
            return BundleReceipt(bundle_id=bundle_id, record_count=count, total_digest=digest)
        finally:
            records_path.unlink(missing_ok=True)
            archive_path.unlink(missing_ok=True)

    @staticmethod
    async def inspect(source: Path, /, *, progress: ProgressObserver | None = None) -> BundleInspection:
        """Verify structure and checksums without touching the target database."""

        return _parse_bundle(source, progress=progress, phase="inspect").inspection

    async def validate(
        self,
        source: Path,
        /,
        *,
        supported_source_types: Iterable[str] | None = None,
        progress: ProgressObserver | None = None,
    ) -> BundleValidation:
        """Verify bundle integrity, dependencies, and optional adapter support."""

        parsed = _parse_bundle(source, progress=progress, phase="validate")
        _validate_dependencies(source)
        required_sources = {
            str(record.identity["source_type"]) for record in _iter_records(source) if record.record_type == "source"
        }
        configured = (
            self._supported_source_types if supported_source_types is None else frozenset(supported_source_types)
        )
        missing_sources = () if configured is None else tuple(sorted(required_sources - configured))
        required_families = _required_artifact_families(_iter_records(source))
        missing_families = (
            ()
            if self._supported_artifact_families is None
            else tuple(sorted(required_families - self._supported_artifact_families))
        )
        already_present, conflicts = await self._target_compatibility(
            source, total=parsed.inspection.record_count, progress=progress
        )
        return BundleValidation(
            bundle_id=parsed.inspection.bundle_id,
            scopes=parsed.inspection.scopes,
            record_count=parsed.inspection.record_count,
            records_by_type=parsed.inspection.records_by_type,
            total_digest=parsed.inspection.total_digest,
            format_version=parsed.inspection.format_version,
            producer_version=parsed.inspection.producer_version,
            already_present=already_present,
            conflicts=conflicts,
            projections_supported=self._projection_rebuilder is not None,
            compatible=not missing_sources and not missing_families and conflicts == 0,
            required_source_types=tuple(sorted(required_sources)),
            unsupported_source_types=missing_sources,
            required_artifact_families=tuple(sorted(required_families)),
            unsupported_artifact_families=missing_families,
            reader_version=_producer_version(),
        )

    async def restore(
        self,
        source: Path,
        /,
        *,
        supported_source_types: Iterable[str] | None = None,
        progress: ProgressObserver | None = None,
    ) -> BundleReceipt:
        """Restore a validated bundle atomically, skipping identical records.

        Existing rows are compared using the same portable record encoding.  A
        difference at the same primary identity aborts the entire transaction;
        no existing revision or head is overwritten.
        """

        parsed = _parse_bundle(source)
        validation = await self.validate(source, supported_source_types=supported_source_types, progress=progress)
        if validation.conflicts:
            raise BundleConflictError(f"target contains {validation.conflicts} conflicting record identities")
        if not validation.compatible:
            raise BundleFormatError("target is not compatible with bundle requirements")
        try:
            async with self._database.transaction() as connection:
                inserted, existing = await self._write_records(
                    connection, source, total=parsed.inspection.record_count, progress=progress
                )
                await self._write_restore_receipt(
                    connection,
                    parsed.inspection,
                    inserted=inserted,
                    already_present=existing,
                    projections_ready=False,
                )
        except IntegrityError as error:
            raise BundleConflictError("target constraint conflict during transactional restore") from error
        projections_ready = False
        if self._projection_rebuilder is not None:
            _report_progress(progress, "projections", 0, 1)
            await self._projection_rebuilder(parsed.inspection.scopes)
            projections_ready = True
            _report_progress(progress, "projections", 1, 1)
            async with self._database.transaction() as connection:
                await self._write_restore_receipt(
                    connection,
                    parsed.inspection,
                    inserted=inserted,
                    already_present=existing,
                    projections_ready=True,
                )
        return BundleReceipt(
            bundle_id=parsed.inspection.bundle_id,
            record_count=parsed.inspection.record_count,
            total_digest=parsed.inspection.total_digest,
            inserted=inserted,
            already_present=existing,
            projections_ready=projections_ready,
            status="ready" if projections_ready else "authoritative_restored",
        )

    async def _write_restore_receipt(
        self,
        connection: AsyncConnection,
        inspection: BundleInspection,
        /,
        *,
        inserted: int,
        already_present: int,
        projections_ready: bool,
    ) -> None:
        row = (
            (
                await connection.execute(
                    select(PORTABLE_RESTORE_RECEIPTS_TABLE).where(
                        PORTABLE_RESTORE_RECEIPTS_TABLE.c.bundle_id == inspection.bundle_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None and (
            str(row["total_digest"]) != inspection.total_digest
            or int(row["format_version"]) != inspection.format_version
            or int(row["record_count"]) != inspection.record_count
        ):
            raise BundleConflictError("restore receipt bundle identity conflict")
        values = {
            "format_version": inspection.format_version,
            "total_digest": inspection.total_digest,
            "record_count": inspection.record_count,
            "status": "ready" if projections_ready else "authoritative_restored",
            "inserted": inserted,
            "already_present": already_present,
            "projections_ready": projections_ready,
            "updated_at": datetime.now(UTC),
        }
        if row is None:
            await connection.execute(
                insert(PORTABLE_RESTORE_RECEIPTS_TABLE).values(bundle_id=inspection.bundle_id, **values)
            )
        else:
            await connection.execute(
                update(PORTABLE_RESTORE_RECEIPTS_TABLE)
                .where(PORTABLE_RESTORE_RECEIPTS_TABLE.c.bundle_id == inspection.bundle_id)
                .values(**values)
            )

    async def _target_compatibility(
        self,
        source: Path,
        /,
        *,
        total: int,
        progress: ProgressObserver | None,
    ) -> tuple[int, int]:
        already_present = 0
        conflicts = 0
        async with self._database.transaction() as connection:
            existing_tables = await connection.run_sync(
                lambda sync_connection: {
                    spec.table.name
                    for spec in _SPECS.values()
                    if sqlalchemy_inspect(sync_connection).has_table(spec.table.name)
                }
            )
            for processed, record in enumerate(_iter_records(source), start=1):
                table = _SPECS[record.record_type].table
                if table.name not in existing_tables:
                    _report_progress(progress, "target", processed, total)
                    continue
                where = [table.c[key] == _database_value(value) for key, value in record.identity.items()]
                row = (await connection.execute(select(table).where(*where))).mappings().one_or_none()
                if row is None:
                    _report_progress(progress, "target", processed, total)
                    continue
                present = _record_from_row(record.record_type, cast(Mapping[str, Any], dict(row)))
                if present.digest == record.digest:
                    already_present += 1
                else:
                    conflicts += 1
                _report_progress(progress, "target", processed, total)
        return already_present, conflicts

    async def _write_records(
        self,
        connection: AsyncConnection,
        source: Path,
        /,
        *,
        total: int,
        progress: ProgressObserver | None,
    ) -> tuple[int, int]:
        """Replay records in dependency order inside the caller transaction."""
        inserted = 0
        existing = 0
        processed = 0
        for record in _ordered_scope_records(source):
            was_inserted = await self._write_record(connection, record)
            inserted += int(was_inserted)
            existing += int(not was_inserted)
            processed += 1
            _report_progress(progress, "restore", processed, total)
        # A portable bundle may be produced by another implementation, so do
        # not trust its physical order.  Re-scan once per dependency level
        # instead of materializing its records merely to sort them.
        for record_type in _EXPORT_ORDER:
            if record_type == "scope":
                continue
            for record in _iter_records(source):
                if record.record_type != record_type:
                    continue
                was_inserted = await self._write_record(connection, record)
                inserted += int(was_inserted)
                existing += int(not was_inserted)
                processed += 1
                _report_progress(progress, "restore", processed, total)
        return inserted, existing

    async def _write_record(self, connection: AsyncConnection, record: _Record, /) -> bool:
        table = _SPECS[record.record_type].table
        where = [table.c[key] == _database_value(value) for key, value in record.identity.items()]
        row = (await connection.execute(select(table).where(*where))).mappings().one_or_none()
        if row is not None:
            present = _record_from_row(record.record_type, cast(Mapping[str, Any], dict(row)))
            if present.digest != record.digest:
                raise BundleConflictError(f"immutable identity conflict: {record.record_type}")
            return False
        await connection.execute(insert(table).values(**_row_values(record)))
        return True

    async def _stream_export(
        self,
        connection: AsyncConnection,
        scopes: tuple[str, ...],
        stream: Any,
        *,
        progress: ProgressObserver | None,
    ) -> tuple[int, Counter[str], str]:
        count = 0
        records_by_type: Counter[str] = Counter()
        digest = _DigestAccumulator()
        for record_type in _EXPORT_ORDER:
            spec = _SPECS[record_type]
            table = spec.table
            statement = (
                select(table)
                .where(table.c[spec.scope_field].in_(scopes))
                .order_by(*(table.c[field] for field in spec.identity))
            )
            rows = await connection.stream(statement)
            async for row in rows.mappings():
                record = _record_from_row(record_type, cast(Mapping[str, Any], dict(row)))
                _validate_record_safe(record)
                stream.write(_canonical_json(_record_document(record)) + b"\n")
                digest.add(record.digest)
                count += 1
                records_by_type[record.record_type] += 1
                _report_progress(progress, "export", count, None)
        _report_progress(progress, "export", count, count)
        return count, records_by_type, digest.value()


def _record_from_row(record_type: RecordType, row: Mapping[str, Any]) -> _Record:
    spec = _SPECS[record_type]
    identity = {field: _json_value(row[field]) for field in spec.identity}
    payload = {field: _json_value(row[field]) for field in spec.payload}
    canonical = {"record_type": record_type, "schema_version": 1, "identity": identity, "payload": payload}
    return _Record(record_type=record_type, identity=identity, payload=payload, digest=_digest(canonical))


async def _establish_export_snapshot(connection: AsyncConnection, /) -> None:
    """Pin all export reads to one database snapshot before enumeration."""

    if connection.dialect.name == "mysql":
        # AsyncDatabase has already issued START TRANSACTION for MySQL-mode
        # engines. Replacing that empty transaction with a consistent snapshot
        # is safe because authorization has completed and no query has run yet.
        await connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")


def _record_document(record: _Record) -> dict[str, Any]:
    return {
        "record_type": record.record_type,
        "schema_version": 1,
        "identity": dict(record.identity),
        "payload": dict(record.payload),
        "digest": record.digest,
    }


def _row_values(record: _Record) -> dict[str, Any]:
    values = {key: _database_value(value) for key, value in record.identity.items()}
    values.update({key: _database_value(value) for key, value in record.payload.items()})
    # Projections are intentionally excluded from logical bundles.  Heads may
    # use NULL searchable text; Memory projections are rebuilt by the runtime.
    if record.record_type == "artifact_head":
        values["searchable_text"] = None
    elif record.record_type == "memory_entry_head":
        values["searchable_text"] = str(record.payload["entry_content_hash"])
    elif record.record_type == "scope":
        values["scope_id_search"] = str(record.identity["scope_id"])
        values["title_search"] = str(record.payload["title"])
        values["summary_search"] = str(record.payload["summary"])
    elif record.record_type == "scope_external_reference":
        values["value_search"] = str(record.payload["value"])
    return values


def _parse_bundle(
    source: Path,
    *,
    progress: ProgressObserver | None = None,
    phase: Literal["inspect", "validate"] = "inspect",
) -> _ParsedBundle:
    try:
        with zipfile.ZipFile(source) as archive:
            _validate_container(archive)
            with archive.open(_MANIFEST_NAME) as stream:
                manifest = json.loads(stream.read(_MAX_MANIFEST_BYTES + 1), parse_constant=_reject_json_constant)
            _validate_manifest(manifest)
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise BundleFormatError("cannot read portable bundle") from error
    count = 0
    counts: Counter[str] = Counter()
    digest = _DigestAccumulator()
    for record in _iter_records(source):
        count += 1
        counts[record.record_type] += 1
        digest.add(record.digest)
        _report_progress(progress, phase, count, manifest["record_count"])
    expected_count = manifest["record_count"]
    if count != expected_count:
        raise BundleFormatError("manifest record count does not match records")
    if digest.value() != manifest["total_digest"]:
        raise BundleFormatError("bundle total digest does not match records")
    sorted_counts = dict(sorted(counts.items()))
    if sorted_counts != manifest["records_by_type"]:
        raise BundleFormatError("manifest record classes do not match records")
    inspection = BundleInspection(
        bundle_id=manifest["bundle_id"],
        scopes=tuple(manifest["scopes"]),
        record_count=count,
        records_by_type=sorted_counts,
        total_digest=digest.value(),
        format_version=manifest["format_version"],
        producer_version=manifest["producer"]["version"],
    )
    scope_ids = tuple(
        str(record.identity["scope_id"]) for record in _iter_records(source) if record.record_type == "scope"
    )
    if tuple(sorted(scope_ids)) != inspection.scopes:
        raise BundleFormatError("manifest scopes do not match scope records")
    return _ParsedBundle(inspection=inspection)


def _ordered_scope_records(source: Path, /) -> tuple[_Record, ...]:
    pending = {
        str(record.identity["scope_id"]): record for record in _iter_records(source) if record.record_type == "scope"
    }
    ordered: list[_Record] = []
    while pending:
        ready = sorted(
            scope_id
            for scope_id, record in pending.items()
            if record.payload["parent_scope_id"] is None or record.payload["parent_scope_id"] not in pending
        )
        if not ready:
            raise BundleFormatError("scope hierarchy contains a cycle")
        for scope_id in ready:
            ordered.append(pending.pop(scope_id))
    return tuple(ordered)


def _iter_records(source: Path, /) -> Iterator[_Record]:
    """Yield verified records without retaining the archive body in memory."""

    try:
        with zipfile.ZipFile(source) as archive, archive.open(_RECORDS_NAME) as stream:
            count = 0
            while line := stream.readline(_MAX_LINE_BYTES + 1):
                if len(line) > _MAX_LINE_BYTES:
                    raise BundleFormatError("record line exceeds size limit")
                if not line.strip():
                    continue
                count += 1
                if count > _MAX_RECORDS:
                    raise BundleFormatError("record count exceeds limit")
                yield _parse_record(json.loads(line, parse_constant=_reject_json_constant))
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise BundleFormatError("cannot read portable bundle") from error


def _validate_container(archive: zipfile.ZipFile) -> None:
    items = archive.infolist()
    names = [item.filename for item in items]
    if len(items) != 2 or set(names) != {_MANIFEST_NAME, _RECORDS_NAME}:
        raise BundleFormatError("bundle must contain only manifest.json and records.ndjson")
    if any(
        item.is_dir()
        or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        or item.compress_size > _MAX_ARCHIVE_BYTES
        for item in items
    ):
        raise BundleFormatError("bundle exceeds resource limits")
    if archive.getinfo(_MANIFEST_NAME).file_size > _MAX_MANIFEST_BYTES:
        raise BundleFormatError("manifest exceeds resource limit")
    if archive.getinfo(_RECORDS_NAME).file_size > _MAX_RECORDS_BYTES:
        raise BundleFormatError("records exceed resource limit")


def _validate_manifest(manifest: object) -> None:  # noqa: C901 - manifest fields have independent constraints.
    if not isinstance(manifest, dict):
        raise BundleFormatError("manifest must be an object")
    manifest = cast(dict[str, object], manifest)
    required = {
        "format_version",
        "bundle_id",
        "producer",
        "scopes",
        "record_count",
        "records_by_type",
        "excluded_record_classes",
        "total_digest",
    }
    if set(manifest) != required:
        raise BundleFormatError("manifest has unknown or missing fields")
    if manifest["format_version"] != FORMAT_VERSION:
        raise BundleFormatError("unsupported bundle format version")
    if (
        not isinstance(manifest["bundle_id"], str)
        or isinstance(manifest["record_count"], bool)
        or not isinstance(manifest["record_count"], int)
        or manifest["record_count"] < 0
        or manifest["record_count"] > _MAX_RECORDS
    ):
        raise BundleFormatError("manifest has invalid identity or record count")
    try:
        UUID(manifest["bundle_id"])
    except ValueError as error:
        raise BundleFormatError("manifest has invalid bundle identity") from error
    producer = manifest["producer"]
    if not isinstance(producer, dict):
        raise BundleFormatError("manifest has invalid producer")
    typed_producer = cast(dict[str, object], producer)
    producer_version = typed_producer.get("version")
    if (
        set(typed_producer) != {"name", "version"}
        or typed_producer.get("name") != "powercontext"
        or not isinstance(producer_version, str)
        or not producer_version
    ):
        raise BundleFormatError("manifest has invalid producer")
    scopes = manifest["scopes"]
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise BundleFormatError("manifest has invalid scopes")
    typed_scopes = cast(list[str], scopes)
    if _validate_scopes(typed_scopes) != tuple(typed_scopes):
        raise BundleFormatError("manifest has invalid scopes")
    counts = manifest["records_by_type"]
    if (
        not isinstance(counts, dict)
        or any(
            key not in _SPECS or isinstance(value, bool) or not isinstance(value, int) or value < 0
            for key, value in counts.items()
        )
        or sum(cast(dict[str, int], counts).values()) != manifest["record_count"]
        or manifest["excluded_record_classes"] != list(_EXCLUDED_RECORD_CLASSES)
        or not _valid_digest(manifest["total_digest"])
    ):
        raise BundleFormatError("manifest has invalid checksums")


def _parse_record(value: object) -> _Record:
    if not isinstance(value, dict):
        raise BundleFormatError("record must be an object")
    value = cast(dict[str, object], value)
    if set(value) != {"record_type", "schema_version", "identity", "payload", "digest"}:
        raise BundleFormatError("record has unknown or missing fields")
    record_type = value.get("record_type")
    if record_type not in _SPECS or value.get("schema_version") != 1:
        raise BundleFormatError("unsupported record type or schema")
    identity = value.get("identity")
    payload = value.get("payload")
    digest = value.get("digest")
    if not isinstance(identity, dict) or not isinstance(payload, dict) or not isinstance(digest, str):
        raise BundleFormatError("record has invalid fields")
    identity = cast(dict[str, str | int], identity)
    payload = cast(dict[str, Any], payload)
    typed = cast(RecordType, record_type)
    spec = _SPECS[typed]
    if (
        set(identity) != set(spec.identity)
        or set(payload) != set(spec.payload)
        or any(isinstance(item, bool) or not isinstance(item, str | int) for item in identity.values())
        or not _valid_digest(digest)
    ):
        raise BundleFormatError("record has invalid identity or digest")
    canonical = {"record_type": typed, "schema_version": 1, "identity": identity, "payload": payload}
    if _digest(canonical) != digest:
        raise BundleFormatError("record digest does not match content")
    record = _Record(record_type=typed, identity=identity, payload=payload, digest=digest)
    _validate_record_values(record)
    _validate_record_safe(record)
    return record


def _validate_record_values(record: _Record, /) -> None:  # noqa: C901 - one versioned schema contract.
    identity = record.identity
    payload = record.payload
    if any(isinstance(value, str) and (not value or value != value.strip()) for value in identity.values()):
        raise BundleFormatError("record identity contains an invalid string")
    positive_identity_fields = {"revision", "version"}
    for field, value in identity.items():
        if isinstance(value, int) and (value < 0 or (field in positive_identity_fields and value == 0)):
            raise BundleFormatError("record identity contains an invalid integer")

    def strings(*fields: str, nullable: tuple[str, ...] = ()) -> None:
        for field in fields:
            value = payload[field]
            if field in nullable and value is None:
                continue
            if not isinstance(value, str):
                raise BundleFormatError("record payload has an invalid string field")

    def integers(*fields: str, positive: bool = False) -> None:
        for field in fields:
            value = payload[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < int(positive):
                raise BundleFormatError("record payload has an invalid integer field")

    def bytes_values(*fields: str, nullable: tuple[str, ...] = ()) -> None:
        for field in fields:
            value = payload[field]
            if field in nullable and value is None:
                continue
            encoded = cast(dict[str, object], value).get("base64") if isinstance(value, dict) else None
            if not isinstance(value, dict) or set(value) != {"base64"} or not isinstance(encoded, str):
                raise BundleFormatError("record payload has an invalid binary field")
            try:
                base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise BundleFormatError("record payload has an invalid binary field") from error

    kind = record.record_type
    if kind == "scope":
        strings("title", "summary", "parent_scope_id", nullable=("parent_scope_id",))
        integers("version", positive=True)
    elif kind == "scope_context_reference":
        if identity["scope_id"] == identity["referenced_scope_id"]:
            raise BundleFormatError("scope cannot reference itself")
    elif kind == "scope_external_reference":
        strings("kind", "value", "value_digest")
    elif kind == "scope_creation_request":
        strings("request_digest", "scope_id")
    elif kind == "source_journal_head":
        integers("position")
    elif kind == "source":
        bytes_values("payload")
        integers("journal_position", positive=True)
    elif kind == "artifact_revision":
        bytes_values("content", "memory_citations", nullable=("memory_citations",))
    elif kind == "artifact_lineage_source":
        strings("source_type", "source_id")
    elif kind == "artifact_lineage_artifact":
        strings("upstream_family", "upstream_artifact_id")
        integers("upstream_revision", positive=True)
    elif kind == "artifact_publication":
        strings("source_scope_id", "source_family", "source_artifact_id", "content_digest", "idempotency_key")
        integers("source_revision", positive=True)
    elif kind == "artifact_head":
        strings("lifecycle_state", "replacement_artifact_id", nullable=("replacement_artifact_id",))
        integers("revision", positive=True)
        integers("governance_generation")
        if payload["lifecycle_state"] not in {"active", "deprecated", "retired"} or (
            payload["replacement_artifact_id"] is not None and payload["lifecycle_state"] != "deprecated"
        ):
            raise BundleFormatError("artifact head has invalid governance state")
    elif kind == "memory_entry_version":
        strings(
            "family",
            "entry_id",
            "previous_version_id",
            "kind",
            "text",
            "entry_content_hash",
            nullable=("previous_version_id",),
        )
        integers("version", "created_in_revision", positive=True)
        bytes_values("source_refs", "artifact_refs")
    elif kind == "memory_entry_head":
        strings("family", "entry_version_id", "entry_content_hash")
        integers("head_revision", positive=True)
    elif kind == "candidate_version":
        strings(
            "family",
            "target_family",
            "target_artifact_id",
            "reason",
            nullable=("target_family", "target_artifact_id", "reason"),
        )
        bytes_values("proposal", "source_refs", "artifact_refs", "memory_citations", nullable=("memory_citations",))
        target = (payload["target_family"], payload["target_artifact_id"], payload["target_revision"])
        if target != (None, None, None) and (
            target[0] is None
            or target[1] is None
            or isinstance(target[2], bool)
            or not isinstance(target[2], int)
            or target[2] < 1
        ):
            raise BundleFormatError("candidate version has an incomplete target")
    elif kind == "candidate_head":
        strings(
            "family",
            "status",
            "result_family",
            "result_artifact_id",
            "decision_reason",
            nullable=("result_family", "result_artifact_id", "decision_reason"),
        )
        integers("version", positive=True)
        result = (payload["result_family"], payload["result_artifact_id"], payload["result_revision"])
        status = payload["status"]
        valid = (
            (status == "pending" and result == (None, None, None) and payload["decision_reason"] is None)
            or (status == "rejected" and result == (None, None, None) and payload["decision_reason"] is not None)
            or (
                status == "approved"
                and all(value is not None for value in result)
                and isinstance(result[2], int)
                and not isinstance(result[2], bool)
                and result[2] > 0
                and payload["decision_reason"] is None
            )
        )
        if not valid:
            raise BundleFormatError("candidate head has invalid decision state")


async def _validate_selected_scopes(connection: AsyncConnection, scopes: tuple[str, ...], /) -> None:
    selected = set(scopes)
    rows = tuple(
        (
            await connection.execute(
                select(SCOPES_TABLE.c.scope_id, SCOPES_TABLE.c.parent_scope_id).where(
                    SCOPES_TABLE.c.scope_id.in_(scopes)
                )
            )
        ).mappings()
    )
    if {str(row["scope_id"]) for row in rows} != selected:
        raise BundleFormatError("one or more selected scopes do not exist")
    if any(row["parent_scope_id"] is not None and str(row["parent_scope_id"]) not in selected for row in rows):
        raise BundleFormatError("selected scope set omits a required parent scope")
    referenced = (
        await connection.execute(
            select(SCOPE_CONTEXT_REFERENCES_TABLE.c.referenced_scope_id).where(
                SCOPE_CONTEXT_REFERENCES_TABLE.c.scope_id.in_(scopes)
            )
        )
    ).scalars()
    if any(str(scope_id) not in selected for scope_id in referenced):
        raise BundleFormatError("selected scope set omits a referenced context scope")
    publication_sources = (
        await connection.execute(
            select(ARTIFACT_PUBLICATIONS_TABLE.c.source_scope_id).where(
                ARTIFACT_PUBLICATIONS_TABLE.c.target_scope_id.in_(scopes)
            )
        )
    ).scalars()
    if any(str(scope_id) not in selected for scope_id in publication_sources):
        raise BundleFormatError("selected scope set omits an Artifact publication source scope")


def _validate_record_safe(record: _Record, /) -> None:
    """Reject secret-shaped configuration and host paths without echoing content."""

    for field, value in record.payload.items():
        encoded = cast(dict[str, object], value).get("base64") if isinstance(value, dict) else None
        if isinstance(encoded, str):
            try:
                decoded = json.loads(base64.b64decode(encoded, validate=True))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            _validate_safe_value(decoded, field)
        else:
            _validate_safe_value(value, field)


def _validate_safe_value(value: object, key: str | None = None) -> None:
    normalized = "" if key is None else key.casefold().replace("-", "_")
    sensitive_suffixes = ("_api_key", "_credential", "_credentials", "_password", "_secret", "_token")
    if (normalized in _SENSITIVE_KEYS or normalized.endswith(sensitive_suffixes)) and value is not None and value != "":
        raise BundleFormatError("bundle contains a credential-shaped field")
    if normalized == "authorization" and isinstance(value, str) and value.casefold().startswith(("bearer ", "basic ")):
        raise BundleFormatError("bundle contains a credential-shaped field")
    if (
        isinstance(value, str)
        and (
            normalized.endswith(("_path", "_directory", "_home", "_root"))
            or normalized in {"cwd", "directory", "home", "path", "root", "working_directory", "workspace"}
        )
        and _ABSOLUTE_HOST_PATH.match(value)
    ):
        raise BundleFormatError("bundle contains an absolute host path")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _validate_safe_value(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            _validate_safe_value(child, key)


def _validate_dependencies(source: Path, /) -> None:
    """Check references with a temporary on-disk identity index.

    The index deliberately stores only canonical identities, never record
    payloads.  This permits dependency validation for large bundles without
    retaining their content in the importing process.
    """

    with _identity_index(source) as contains:
        for record in _iter_records(source):
            _validate_record_dependencies(record, contains)


def _validate_record_dependencies(  # noqa: C901 - logical record kinds have distinct dependency edges.
    record: _Record,
    contains: Callable[[RecordType, Mapping[str, object]], bool],
    /,
) -> None:
    payload = record.payload
    identity = record.identity
    scope_field = _SPECS[record.record_type].scope_field
    scope = identity.get(scope_field, payload.get(scope_field))
    if not isinstance(scope, str):
        raise BundleFormatError("record has invalid scope identity")
    if record.record_type != "scope" and not contains("scope", {"scope_id": scope}):
        raise BundleFormatError("record references missing scope")
    if record.record_type == "scope":
        parent = payload["parent_scope_id"]
        if parent is not None and not contains("scope", {"scope_id": parent}):
            raise BundleFormatError("scope references missing parent")
    elif record.record_type == "scope_context_reference":
        if not contains("scope", {"scope_id": identity["referenced_scope_id"]}):
            raise BundleFormatError("scope context references missing scope")
    elif record.record_type == "source":
        if not contains("source_journal_head", {"scope_id": scope}):
            raise BundleFormatError("source has no journal head")
    elif record.record_type == "artifact_lineage_source":
        if not contains(
            "artifact_revision",
            {
                "scope_id": scope,
                "family": identity["family"],
                "artifact_id": identity["artifact_id"],
                "revision": identity["revision"],
            },
        ):
            raise BundleFormatError("artifact lineage references missing revision")
        if not contains(
            "source",
            {"scope_id": scope, "source_type": payload["source_type"], "source_id": payload["source_id"]},
        ):
            raise BundleFormatError("artifact lineage references missing source")
    elif record.record_type == "artifact_lineage_artifact":
        if not contains(
            "artifact_revision",
            {
                "scope_id": scope,
                "family": identity["family"],
                "artifact_id": identity["artifact_id"],
                "revision": identity["revision"],
            },
        ):
            raise BundleFormatError("artifact lineage references missing revision")
        if not contains(
            "artifact_revision",
            {
                "scope_id": scope,
                "family": payload["upstream_family"],
                "artifact_id": payload["upstream_artifact_id"],
                "revision": payload["upstream_revision"],
            },
        ):
            raise BundleFormatError("record references missing artifact revision")
    elif record.record_type == "artifact_publication":
        if not contains("scope", {"scope_id": payload["source_scope_id"]}):
            raise BundleFormatError("Artifact publication references missing source scope")
        target_exists = contains(
            "artifact_revision",
            {
                "scope_id": identity["target_scope_id"],
                "family": identity["target_family"],
                "artifact_id": identity["target_artifact_id"],
                "revision": identity["target_revision"],
            },
        )
        source_exists = contains(
            "artifact_revision",
            {
                "scope_id": payload["source_scope_id"],
                "family": payload["source_family"],
                "artifact_id": payload["source_artifact_id"],
                "revision": payload["source_revision"],
            },
        )
        if not target_exists or not source_exists:
            raise BundleFormatError("Artifact publication references missing revision")
    elif record.record_type == "memory_entry_version":
        if not contains(
            "artifact_revision",
            {
                "scope_id": scope,
                "family": payload["family"],
                "artifact_id": identity["memory_artifact_id"],
                "revision": payload["created_in_revision"],
            },
        ):
            raise BundleFormatError("record references missing artifact revision")
        previous = payload["previous_version_id"]
        if previous is not None and not contains(
            "memory_entry_version",
            {
                "scope_id": scope,
                "memory_artifact_id": identity["memory_artifact_id"],
                "entry_version_id": previous,
            },
        ):
            raise BundleFormatError("memory version references missing previous version")
    elif record.record_type == "artifact_head":
        if not contains("artifact_revision", {**identity, "revision": payload["revision"]}):
            raise BundleFormatError("artifact head references missing revision")
    elif record.record_type == "memory_entry_head":
        if not contains(
            "memory_entry_version",
            {
                "scope_id": scope,
                "memory_artifact_id": identity["memory_artifact_id"],
                "entry_version_id": payload["entry_version_id"],
            },
        ):
            raise BundleFormatError("memory head references missing entry version")
        if not contains(
            "artifact_revision",
            {
                "scope_id": scope,
                "family": payload["family"],
                "artifact_id": identity["memory_artifact_id"],
                "revision": payload["head_revision"],
            },
        ):
            raise BundleFormatError("memory head references missing artifact revision")
    elif record.record_type == "candidate_version":
        target_family = payload["target_family"]
        if target_family is not None and not contains(
            "artifact_revision",
            {
                "scope_id": scope,
                "family": target_family,
                "artifact_id": payload["target_artifact_id"],
                "revision": payload["target_revision"],
            },
        ):
            raise BundleFormatError("candidate references missing target revision")
    elif record.record_type == "candidate_head":
        if not contains(
            "candidate_version",
            {"scope_id": scope, "candidate_id": identity["candidate_id"], "version": payload["version"]},
        ):
            raise BundleFormatError("candidate head references missing version")
        result_family = payload["result_family"]
        if result_family is not None and not contains(
            "artifact_revision",
            {
                "scope_id": scope,
                "family": result_family,
                "artifact_id": payload["result_artifact_id"],
                "revision": payload["result_revision"],
            },
        ):
            raise BundleFormatError("candidate head references missing result revision")
    _validate_embedded_references((record,), contains)
    _validate_memory_citations(record, contains)


@contextmanager
def _identity_index(source: Path, /) -> Iterator[Callable[[RecordType, Mapping[str, object]], bool]]:
    index_path = _temporary_path(Path(tempfile.gettempdir()), suffix=".identities.sqlite3")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(index_path)
        connection.execute(
            "CREATE TABLE identities (record_type TEXT NOT NULL, identity TEXT NOT NULL, "
            "PRIMARY KEY (record_type, identity))"
        )
        for record in _iter_records(source):
            try:
                connection.execute(
                    "INSERT INTO identities (record_type, identity) VALUES (?, ?)",
                    (record.record_type, _identity_key(record.identity)),
                )
            except sqlite3.IntegrityError as error:
                raise BundleFormatError("bundle contains duplicate immutable identity") from error
        connection.commit()

        def contains(record_type: RecordType, identity: Mapping[str, object]) -> bool:
            return (
                connection.execute(
                    "SELECT 1 FROM identities WHERE record_type = ? AND identity = ?",
                    (record_type, _identity_key(identity)),
                ).fetchone()
                is not None
            )

        yield contains
    finally:
        if connection is not None:
            connection.close()
        index_path.unlink(missing_ok=True)


def _validate_embedded_references(
    records: Iterable[_Record],
    contains: Callable[[RecordType, Mapping[str, object]], bool],
) -> None:
    for record in records:
        if record.record_type not in {"memory_entry_version", "candidate_version"}:
            continue
        scope_id = record.identity["scope_id"]
        for reference in _reference_items(record.payload["source_refs"], "source"):
            if not contains(
                "source",
                {
                    "scope_id": scope_id,
                    "source_type": reference["source_type"],
                    "source_id": reference["source_id"],
                },
            ):
                raise BundleFormatError("record references missing source")
        for reference in _reference_items(record.payload["artifact_refs"], "artifact"):
            if not contains(
                "artifact_revision",
                {
                    "scope_id": scope_id,
                    "family": reference["family"],
                    "artifact_id": reference["artifact_id"],
                    "revision": reference["revision"],
                },
            ):
                raise BundleFormatError("record references missing artifact revision")


def _reference_items(value: object, kind: Literal["source", "artifact"], /) -> tuple[Mapping[str, object], ...]:
    encoded = cast(dict[str, object], value).get("base64") if isinstance(value, dict) else None
    if not isinstance(value, dict) or set(value) != {"base64"} or not isinstance(encoded, str):
        raise BundleFormatError(f"{kind} references are not encoded bytes")
    try:
        decoded = json.loads(base64.b64decode(encoded, validate=True), parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError) as error:
        raise BundleFormatError(f"{kind} references are not valid JSON") from error
    if not isinstance(decoded, list):
        raise BundleFormatError(f"{kind} references must be an array")
    fields = {"source_type", "source_id"} if kind == "source" else {"family", "artifact_id", "revision"}
    references: list[Mapping[str, object]] = []
    for item in decoded:
        if (
            not isinstance(item, dict)
            or set(item) != fields
            or any(isinstance(field, bool) or not isinstance(field, str | int) for field in item.values())
        ):
            raise BundleFormatError(f"{kind} reference has invalid fields")
        references.append(cast(Mapping[str, object], item))
    return tuple(references)


def _validate_memory_citations(
    record: _Record,
    contains: Callable[[RecordType, Mapping[str, object]], bool],
    /,
) -> None:
    if record.record_type not in {"artifact_revision", "candidate_version"}:
        return
    encoded_value = record.payload["memory_citations"]
    if encoded_value is None:
        return
    encoded = cast(dict[str, object], encoded_value).get("base64") if isinstance(encoded_value, dict) else None
    if not isinstance(encoded, str):
        raise BundleFormatError("memory citations are not encoded bytes")
    try:
        citations = json.loads(base64.b64decode(encoded, validate=True), parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError) as error:
        raise BundleFormatError("memory citations are not valid JSON") from error
    if not isinstance(citations, list):
        raise BundleFormatError("memory citations must be an array")
    scope_id = record.identity["scope_id"]
    for citation in citations:
        if not isinstance(citation, dict) or set(citation) != {"memory_ref", "entry_id", "entry_version_id"}:
            raise BundleFormatError("memory citation has invalid fields")
        memory_ref = citation["memory_ref"]
        if (
            not isinstance(memory_ref, dict)
            or set(memory_ref) != {"family", "artifact_id", "revision"}
            or not isinstance(citation["entry_id"], str)
            or not isinstance(citation["entry_version_id"], str)
        ):
            raise BundleFormatError("memory citation has invalid fields")
        artifact_exists = contains(
            "artifact_revision",
            {
                "scope_id": scope_id,
                "family": memory_ref["family"],
                "artifact_id": memory_ref["artifact_id"],
                "revision": memory_ref["revision"],
            },
        )
        version_exists = contains(
            "memory_entry_version",
            {
                "scope_id": scope_id,
                "memory_artifact_id": memory_ref["artifact_id"],
                "entry_version_id": citation["entry_version_id"],
            },
        )
        if not artifact_exists or not version_exists:
            raise BundleFormatError("memory citation references missing version")


def _required_artifact_families(records: Iterable[_Record]) -> set[str]:
    families: set[str] = set()
    for record in records:
        identity = record.identity
        payload = record.payload
        if record.record_type in {
            "artifact_revision",
            "artifact_lineage_source",
            "artifact_lineage_artifact",
            "artifact_head",
        }:
            families.add(str(identity["family"]))
        if record.record_type == "artifact_publication":
            families.update((str(identity["target_family"]), str(payload["source_family"])))
        if record.record_type in {"artifact_lineage_artifact", "candidate_version", "candidate_head"}:
            for field in ("upstream_family", "target_family", "result_family"):
                value = payload.get(field)
                if value is not None:
                    families.add(str(value))
        if record.record_type in {"memory_entry_version", "memory_entry_head", "candidate_version", "candidate_head"}:
            families.add(str(payload["family"]))
    return families


def _validate_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    if isinstance(scopes, str):
        raise BundleFormatError("scopes must be an iterable of scope IDs")
    try:
        values = tuple(scopes)
    except TypeError as error:
        raise BundleFormatError("scopes must be an iterable of scope IDs") from error
    if not values or any(not isinstance(scope, str) or not scope.strip() or scope != scope.strip() for scope in values):
        raise BundleFormatError("scopes must contain at least one non-empty trimmed scope ID")
    selected = tuple(sorted(set(values)))
    return selected


def _identity_key(identity: Mapping[str, object], /) -> str:
    return _canonical_json(identity).decode("utf-8")


class _DigestAccumulator:
    """Incrementally hash a deterministic ordered record-digest sequence."""

    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self._first = True

    def add(self, digest: str, /) -> None:
        if not self._first:
            self._hash.update(b"\n")
        self._hash.update(digest.encode("ascii"))
        self._first = False

    def value(self) -> str:
        return _SHA256 + self._hash.hexdigest()


def _temporary_path(directory: Path, /, *, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(
        prefix=".powercontext-archive-", suffix=suffix, dir=directory, delete=False
    ) as handle:
        return Path(handle.name)


def _zip_info(name: str, compression: int, /) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.external_attr = 0o600 << 16
    return info


def _digest(value: object) -> str:
    return _SHA256 + hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_value(value: object) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _database_value(value: object) -> object:
    encoded = cast(dict[str, object], value).get("base64") if isinstance(value, dict) else None
    if isinstance(value, dict) and set(value) == {"base64"} and isinstance(encoded, str):
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise BundleFormatError("invalid base64 column value") from error
    return value


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _HEX_DIGEST.fullmatch(value) is not None


def _reject_json_constant(_value: str, /) -> None:
    raise BundleFormatError("bundle contains a non-standard JSON value")


def _report_progress(
    observer: ProgressObserver | None,
    phase: Literal["export", "inspect", "validate", "target", "restore", "projections"],
    processed: int,
    total: int | None,
) -> None:
    if observer is not None and (processed in {0, 1, total} or processed % 1_000 == 0):
        observer(BundleProgress(phase=phase, processed=processed, total=total))


def _producer_version() -> str:
    try:
        return version("powercontext")
    except PackageNotFoundError:
        return "unknown"
