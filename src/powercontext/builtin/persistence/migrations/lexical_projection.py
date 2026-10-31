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

"""Upgrade derived lexical tokens without changing authority or embedding spaces."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import Connection, select, tuple_
from typing_extensions import override

from powercontext.builtin.artifacts import search
from powercontext.builtin.persistence import artifact_indexes, experience_index, lexical_projection
from powercontext.builtin.persistence.artifact_indexes import (
    COVERAGE,
    DEFINITIONS,
    SCHEMAS,
    STATES,
    TEXTS,
    custom_artifact_text_analyzers_current,
    upgrade_custom_artifact_text_analyzers,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import atomic_memory_current_table
from powercontext.builtin.persistence.experience_index import APPROVED_ARTIFACT_PROJECTION_VERSION
from powercontext.builtin.persistence.lexical_projection import LEXICAL_ANALYZER_VERSION, refresh_lexical_projections
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    LEXICAL_PROJECTION_VERSIONS_TABLE,
    SKILL_PACKAGES_TABLE,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
)

from .bundle import sqlite_inventory
from .connections import BackendIdentity
from .maintenance import ManagedMaintenanceRunner
from .models import MigrationError, digest
from .mysql_schema import mysql_inventory
from .pinned import finish, pinned
from .server_readiness import check_server_layout

_PREVIOUS_ANALYZER = "jieba-0.42.1-words-v1"
_EXPECTED = {
    "pc_atomic_memory_current": LEXICAL_ANALYZER_VERSION,
    "pc_topic_memory_active_topics": LEXICAL_ANALYZER_VERSION,
    "pc_topic_memory_active_chunks": LEXICAL_ANALYZER_VERSION,
    "pc_artifact_heads": APPROVED_ARTIFACT_PROJECTION_VERSION,
}


class LexicalProjectionMigrationRunner(ManagedMaintenanceRunner):
    """Explicitly replace known old token projections in one maintenance transaction."""

    operation = "lexical_projection"
    supports_vectors = True
    allow_declared_spaces = True
    affected_objects: tuple[str, ...] = (
        "pc_atomic_memory_current",
        "pc_atomic_memory_current_fts",
        "pc_topic_memory_active_topics",
        "pc_topic_memory_active_chunks",
        "pc_topic_memory_topic_fts",
        "pc_topic_memory_chunk_fts",
        "pc_topic_memory_fts_index",
        "pc_artifact_heads",
        "pc_artifact_fts",
        "pc_lexical_projection_versions",
        "pc_artifact_vector_indexes",
        "pc_artifact_search_states",
        "pc_artifact_search_text",
        "pc_artifact_search_fts",
        "pc_artifact_index_coverage",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            implementation_checksum=digest({
                "owner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "analyzer": hashlib.sha256(Path(search.__file__).read_bytes()).hexdigest(),
                "lexical": hashlib.sha256(Path(lexical_projection.__file__).read_bytes()).hexdigest(),
                "approved": hashlib.sha256(Path(experience_index.__file__).read_bytes()).hexdigest(),
                "custom": hashlib.sha256(Path(artifact_indexes.__file__).read_bytes()).hexdigest(),
            }),
            **kwargs,
        )
        if not isinstance(self.connections.config, SQLiteConfig):
            helpers = {
                "pc_atomic_memory_current_fts",
                "pc_topic_memory_topic_fts",
                "pc_topic_memory_chunk_fts",
                "pc_topic_memory_fts_index",
                "pc_artifact_fts",
                "pc_artifact_search_fts",
            }
            self.affected_objects = tuple(name for name in self.affected_objects if name not in helpers)

    @override
    def _state(self, connection: Connection, identity: BackendIdentity) -> tuple[str, bool, bool]:
        check_server_layout(connection, allow_declared_spaces=True)
        versions = dict(connection.execute(select(LEXICAL_PROJECTION_VERSIONS_TABLE)).tuples().all())
        if set(versions) != set(_EXPECTED) or any(
            version not in {expected, _PREVIOUS_ANALYZER + (":approved-v1" if name == "pc_artifact_heads" else "")}
            for name, expected in _EXPECTED.items()
            for version in (versions[name],)
        ):
            raise MigrationError(
                "unsupported_analyzer_version", "Lexical maintenance requires a complete, recognized analyzer version."
            )
        try:
            custom_current = finish(custom_artifact_text_analyzers_current(pinned(connection)))
        except ValueError as error:
            raise MigrationError(
                "unsupported_analyzer_version", "A custom Artifact text analyzer has no registered upgrade path."
            ) from error
        required = versions != _EXPECTED or not custom_current
        try:
            self.bundle.domains.validate(
                connection, self.bundle.head, backend=identity.product, configuration=self.configuration
            )
        except MigrationError as error:
            # Only the declared analyzer transition is admissible. Full domain
            # validation runs again before committing any rebuilt projection.
            if error.code != "projection_migration_required" or not required:
                raise
        fingerprint = digest({
            "schema": sqlite_inventory(connection) if identity.product == "sqlite" else mysql_inventory(connection),
            "rows": self._rows_digest(connection),
        })
        return fingerprint, required, not required

    def _rows_digest(self, connection):
        snapshot = hashlib.sha256()
        for table in (
            ARTIFACTS_TABLE,
            ARTIFACT_HEADS_TABLE,
            SKILL_PACKAGES_TABLE,
            atomic_memory_current_table(),
            TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
            TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
            LEXICAL_PROJECTION_VERSIONS_TABLE,
            SCHEMAS,
            DEFINITIONS,
            STATES,
            TEXTS,
            COVERAGE,
        ):
            snapshot.update(table.name.encode())
            keys = tuple(table.primary_key.columns)
            # Lexical maintenance neither reads nor rewrites native vector bytes.
            statement = select(*(column for column in table.c if column.name != "embedding"))
            statement = statement.order_by(*keys).limit(self.batch_size)
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
        return snapshot.hexdigest()

    @override
    def _apply_operation(self, connection: Connection, guard) -> None:
        async_connection = pinned(connection)
        finish(refresh_lexical_projections(async_connection, atomic_memory_current_table()))
        guard()
        if connection.dialect.name == "sqlite":
            from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
            from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
            from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex

            finish(SQLiteAtomicMemoryIndex().rebuild_fts(async_connection))
            # Layout admission established both FTS tables already exist; this
            # initializer only restores their rows and the invalidated marker.
            finish(SQLiteTopicMemoryFTSIndex().initialize(async_connection))
            finish(SQLiteExperienceFTSIndex().rebuild(async_connection))
        else:
            from powercontext.builtin.persistence.oceanbase.experience_index import OceanBaseExperienceFTSIndex

            finish(OceanBaseExperienceFTSIndex().rebuild(async_connection))
        finish(upgrade_custom_artifact_text_analyzers(async_connection))
        guard()
