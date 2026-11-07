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

"""Registered current-row repair without changing managed schema or authority."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import Connection, select, tuple_
from typing_extensions import override

from powercontext.builtin.persistence.atomic_memory_evidence_schema import (
    ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE,
    SOURCE_EVIDENCE_BINDINGS_TABLE,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_TAGS_TABLE,
    ARTIFACTS_TABLE,
)
from powercontext.builtin.runtime import atomic_memory_rebuild

from .bundle import sqlite_inventory
from .connections import BackendIdentity
from .maintenance import ManagedMaintenanceRunner
from .models import MigrationError, digest
from .mysql_schema import mysql_inventory
from .pinned import finish, pinned
from .server_readiness import check_server_layout


def _sqlite_helper_digest(connection, batch_size):
    snapshot = hashlib.sha256()
    after = None
    while True:
        query = "SELECT rowid,* FROM pc_atomic_memory_current_fts"
        parameters = {"batch": batch_size}
        if after is not None:
            query += " WHERE rowid > :after"
            parameters["after"] = after
        rows = connection.exec_driver_sql(query + " ORDER BY rowid LIMIT :batch", parameters).all()
        if not rows:
            return snapshot.hexdigest()
        for row in rows:
            snapshot.update(digest(tuple(row)).encode())
        after = rows[-1][0]


class AtomicProjectionMigrationRunner(ManagedMaintenanceRunner):
    operation = "atomic_projection"
    affected_objects: tuple[str, ...] = (
        "pc_atomic_memory_current",
        "pc_atomic_memory_current_fts",
        "pc_atomic_memory_evidence",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            implementation_checksum=digest({
                "owner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "rebuild": hashlib.sha256(Path(atomic_memory_rebuild.__file__).read_bytes()).hexdigest(),
            }),
            **kwargs,
        )
        self.affected_objects = (
            ("pc_atomic_memory_current", "pc_atomic_memory_current_fts", "pc_atomic_memory_evidence")
            if isinstance(self.connections.config, SQLiteConfig)
            else ("pc_atomic_memory_current", "pc_atomic_memory_evidence")
        )

    def _index(self, connection):
        return SQLiteAtomicMemoryIndex() if connection.dialect.name == "sqlite" else OceanBaseAtomicMemoryIndex()

    @override
    def _state(self, connection: Connection, identity: BackendIdentity) -> tuple[str, bool, bool]:
        check_server_layout(connection)
        index = self._index(connection)
        # Built from the migration bundle, so no host SourceDefinitionRegistry exists:
        # admission uses BUILTIN_SOURCE_REGISTRY while host reads use the configured one.
        try:
            report = finish(
                atomic_memory_rebuild.verify_atomic_memory_current(
                    pinned(connection),
                    index,
                    batch_size=self.batch_size,
                    require_evidence=True,
                    require_evidence_projection=True,
                )
            )
        except Exception as error:
            raise MigrationError("invalid_authority", "Repair requires valid Atomic Memory authority.") from error
        required = not report.ready
        if isinstance(index, SQLiteAtomicMemoryIndex):
            required |= not finish(index.fts_matches_current(pinned(connection)))
        try:
            self.bundle.domains.validate(
                connection, self.bundle.head, backend=identity.product, configuration=self.configuration
            )
        except MigrationError as error:
            # Only the current projection/head or helper mismatch can be
            # admitted here. Full validation must pass before repair commits.
            if error.code != "projection_verification_failed" or not required:
                raise
        fingerprint = digest({
            "schema": sqlite_inventory(connection) if identity.product == "sqlite" else mysql_inventory(connection),
            "rows": self._rows_digest(connection, index.table),
        })
        return fingerprint, required, not required

    def _rows_digest(self, connection, current):
        snapshot = hashlib.sha256()
        for table in (
            ARTIFACTS_TABLE,
            ARTIFACT_HEADS_TABLE,
            ARTIFACT_TAGS_TABLE,
            ARTIFACT_LINEAGE_SOURCES_TABLE,
            ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
            current,
            ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE,
            SOURCE_EVIDENCE_BINDINGS_TABLE,
            ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE,
        ):
            snapshot.update(table.name.encode())
            keys = tuple(table.primary_key.columns)
            statement = select(table).order_by(*keys).limit(self.batch_size)
            if "family" in table.c:
                statement = statement.where(table.c.family == "atomic-memory")
            after = None
            while True:
                query = statement if after is None else statement.where(tuple_(*keys) > after)
                rows = connection.execute(query).mappings().all()
                if not rows:
                    break
                for row in rows:
                    snapshot.update(
                        digest([
                            {"bytes": value.hex()}
                            if isinstance(value, bytes)
                            else value.isoformat()
                            if isinstance(value, datetime)
                            else value
                            for value in row.values()
                        ]).encode()
                    )
                after = tuple(rows[-1][key.name] for key in keys)
        if connection.dialect.name == "sqlite":
            snapshot.update(_sqlite_helper_digest(connection, self.batch_size).encode())
        return snapshot.hexdigest()

    @override
    def _apply_operation(self, connection: Connection, guard) -> None:
        index = self._index(connection)
        # No host registry on this bundle path; see the note in _state.
        report = finish(
            atomic_memory_rebuild.rebuild_atomic_memory_current(
                pinned(connection), index, batch_size=self.batch_size, guard=guard
            )
        )
        if not report.ready:
            raise MigrationError("projection_verification_failed", "Atomic current repair failed validation.")
        if isinstance(index, SQLiteAtomicMemoryIndex):
            finish(index.rebuild_fts(pinned(connection)))
