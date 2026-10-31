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

"""Shared search build state and exact, index-isolated projections."""

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Table,
    Text,
    UniqueConstraint,
)


def artifact_search_tables(metadata, identity):
    spaces = Table(
        "pc_artifact_vector_spaces",
        metadata,
        Column("space_id", identity(32), primary_key=True),
        Column("profile_fingerprint", identity(64), nullable=False),
        Column("profile_json", JSON, nullable=False),
        Column("dimension", Integer, nullable=False),
        Column("distance", identity(16), nullable=False),
        Column("normalization", identity(16), nullable=False),
        Column("storage_format_version", identity(64), nullable=False),
        Column("backend", identity(32), nullable=False),
        Column("physical_table", identity(128), nullable=False),
        Column("provision_state", identity(32), nullable=False),
        UniqueConstraint(
            "backend", "profile_fingerprint", "storage_format_version", name="uq_pc_artifact_vector_space"
        ),
        CheckConstraint("dimension > 0", name="ck_pc_artifact_vector_dimension"),
    )
    definitions = Table(
        "pc_artifact_vector_indexes",
        metadata,
        Column("index_def_id", identity(64), primary_key=True),
        Column("family", identity(128), nullable=False),
        Column("index_name", identity(128), nullable=False),
        Column("schema_version", identity(128), nullable=False),
        Column("projection_version", identity(128), nullable=False),
        Column("space_id", identity(32)),
        Column("source_kind", identity(16), nullable=False),
        Column("projection_spec", JSON, nullable=False),
        Column("definition_fingerprint", identity(64), nullable=False),
        UniqueConstraint(
            "family",
            "index_name",
            "schema_version",
            "projection_version",
            "space_id",
            "definition_fingerprint",
            name="uq_pc_artifact_index_definition",
        ),
        ForeignKeyConstraint(("space_id",), ("pc_artifact_vector_spaces.space_id",)),
        ForeignKeyConstraint(
            ("family", "schema_version"),
            ("pc_artifact_family_schemas.family", "pc_artifact_family_schemas.schema_version"),
        ),
    )
    states = Table(
        "pc_artifact_search_states",
        metadata,
        Column("scope_id", identity(256), primary_key=True),
        Column("family", identity(128), primary_key=True),
        Column("build_id", identity(32)),
        Column("state", identity(16), nullable=False),
        Column("manifest", JSON, nullable=False),
        Column("cleanup_targets", JSON, nullable=False),
        Column("validation_token", identity(32)),
        Column("start_seq", BigInteger, nullable=False, server_default="0"),
        Column("applied_seq", BigInteger, nullable=False, server_default="0"),
        Column("change_seq", BigInteger, nullable=False, server_default="0"),
    )
    coverage = Table(
        "pc_artifact_index_coverage",
        metadata,
        Column("scope_id", identity(256), primary_key=True),
        Column("family", identity(128), primary_key=True),
        Column("build_id", identity(32), primary_key=True),
        Column("index_def_id", identity(64), primary_key=True),
        Column("artifact_id", identity(128), primary_key=True),
        Column("revision", Integer, nullable=False),
        Column("schema_version", identity(128), nullable=False),
        Column("fragment_count", Integer, nullable=False),
        Column("projection_input_hash", identity(64), nullable=False),
        Column("governance_generation", BigInteger, nullable=False),
        ForeignKeyConstraint(
            ("scope_id", "family"), ("pc_artifact_search_states.scope_id", "pc_artifact_search_states.family")
        ),
        CheckConstraint("fragment_count >= 0", name="ck_pc_artifact_index_fragment_count"),
    )
    changes = Table(
        "pc_artifact_index_changes",
        metadata,
        Column("scope_id", identity(256), primary_key=True),
        Column("family", identity(128), primary_key=True),
        Column("seq", BigInteger, primary_key=True),
        Column("artifact_id", identity(128), nullable=False),
        Column("revision", Integer),
        Column("operation", identity(16), nullable=False),
        Column("schema_version", identity(128)),
        Column("governance_generation", BigInteger, nullable=False),
        ForeignKeyConstraint(
            ("scope_id", "family"), ("pc_artifact_search_states.scope_id", "pc_artifact_search_states.family")
        ),
    )

    def rows(name, vector):
        return Table(
            name,
            metadata,
            Column("vector_id" if vector else "text_id", identity(64), primary_key=True),
            Column("scope_id", identity(256), nullable=False),
            Column("family", identity(128), nullable=False),
            Column("artifact_id", identity(128), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("schema_version", identity(128), nullable=False),
            Column("index_def_id", identity(64), nullable=False),
            Column("build_id", identity(32), nullable=False),
            Column("fragment_id", identity(128), nullable=False),
            Column("fragment_locator", Text, nullable=False),
            Column("input_hash", identity(64), nullable=False),
            Column("governance_generation", BigInteger, nullable=False),
            *(
                (
                    Column("space_id", identity(32), nullable=False),
                    Column("dimension", Integer, nullable=False),
                    Column("embedding", LargeBinary, nullable=False),
                )
                if vector
                else (Column("searchable_text", Text, nullable=False),)
            ),
            UniqueConstraint("build_id", "index_def_id", "artifact_id", "fragment_id", name=f"uq_{name}_fragment"),
            ForeignKeyConstraint(
                ("scope_id", "family", "artifact_id", "revision"),
                ("pc_artifacts.scope_id", "pc_artifacts.family", "pc_artifacts.artifact_id", "pc_artifacts.revision"),
            ),
            ForeignKeyConstraint(
                ("scope_id", "family"), ("pc_artifact_search_states.scope_id", "pc_artifact_search_states.family")
            ),
            Index(
                f"ix_{name}_search",
                "scope_id",
                "family",
                "build_id",
                *(("space_id",) if vector else ()),
                "index_def_id",
                "artifact_id",
            ),
            *(
                (
                    CheckConstraint(
                        "dimension > 0 AND length(embedding) = 4 * dimension", name="ck_pc_artifact_vectors_encoding"
                    ),
                )
                if vector
                else ()
            ),
        )

    return (
        spaces,
        definitions,
        states,
        coverage,
        changes,
        rows("pc_artifact_vectors", True),
        rows("pc_artifact_search_text", False),
    )


def ensure_search_support(connection):  # noqa: C901 - Validate the fixed layout before repairing derived FTS support.
    """Create missing backend full-text support during shared database initialization."""
    from sqlalchemy import inspect, text

    from powercontext.builtin.persistence.tables import ARTIFACT_SEARCH_TABLES

    inspector = inspect(connection)
    for table in ARTIFACT_SEARCH_TABLES:
        if table.name == "pc_artifact_vectors" and connection.dialect.name != "sqlite":
            continue
        name, expected = table.name, set(table.c.keys())
        if {column["name"] for column in inspector.get_columns(name)} != expected:
            raise ValueError("Artifact search schema is incompatible; a database migration is required")  # noqa: TRY003
    if inspector.has_table("pc_artifact_index_generations"):
        raise ValueError("Legacy Artifact search generations require an explicit database migration")  # noqa: TRY003
    if connection.dialect.name == "sqlite" and inspector.has_table("pc_artifact_search_fts"):
        columns = {column["name"] for column in inspector.get_columns("pc_artifact_search_fts")}
        if not {"build_id", "index_def_id"}.issubset(columns):
            raise ValueError("Artifact full-text schema is incompatible; a database migration is required")  # noqa: TRY003
    if connection.dialect.name != "sqlite":
        indexes = {index["name"] for index in inspect(connection).get_indexes("pc_artifact_search_text")}
        if "ix_pc_artifact_search_fts" not in indexes:
            connection.exec_driver_sql(
                "CREATE FULLTEXT INDEX ix_pc_artifact_search_fts "
                "ON pc_artifact_search_text (searchable_text) WITH PARSER SPACE"
            )
        return
    helpers = (
        (
            "pc_artifact_search_fts",
            "CREATE VIRTUAL TABLE pc_artifact_search_fts USING fts5(text_id UNINDEXED, scope_id UNINDEXED, family UNINDEXED, artifact_id UNINDEXED, revision UNINDEXED, build_id UNINDEXED, index_def_id UNINDEXED, searchable_text, tokenize='unicode61')",
        ),
        (
            "pc_artifact_search_fts_insert",
            "CREATE TRIGGER pc_artifact_search_fts_insert AFTER insert ON pc_artifact_search_text BEGIN INSERT INTO pc_artifact_search_fts (text_id, scope_id, family, artifact_id, revision, build_id, index_def_id, searchable_text) VALUES (new.text_id, new.scope_id, new.family, new.artifact_id, new.revision, new.build_id, new.index_def_id, new.searchable_text); END",
        ),
        (
            "pc_artifact_search_fts_update",
            "CREATE TRIGGER pc_artifact_search_fts_update AFTER update ON pc_artifact_search_text BEGIN DELETE FROM pc_artifact_search_fts WHERE text_id = old.text_id;INSERT INTO pc_artifact_search_fts (text_id, scope_id, family, artifact_id, revision, build_id, index_def_id, searchable_text) VALUES (new.text_id, new.scope_id, new.family, new.artifact_id, new.revision, new.build_id, new.index_def_id, new.searchable_text); END",
        ),
        (
            "pc_artifact_search_fts_delete",
            "CREATE TRIGGER pc_artifact_search_fts_delete AFTER delete ON pc_artifact_search_text BEGIN DELETE FROM pc_artifact_search_fts WHERE text_id = old.text_id; END",
        ),
    )
    missing = False
    for name, ddl in helpers:
        if connection.scalar(text("SELECT 1 FROM sqlite_master WHERE name=:name"), {"name": name}) is None:
            connection.exec_driver_sql(ddl)
            missing = True
    if missing:
        # Repair derived FTS rows only when support was missing, never on normal startup.
        connection.exec_driver_sql("DELETE FROM pc_artifact_search_fts")
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_search_fts "
            "(text_id, scope_id, family, artifact_id, revision, build_id, index_def_id, searchable_text) "
            "SELECT text_id, scope_id, family, artifact_id, revision, build_id, index_def_id, searchable_text "
            "FROM pc_artifact_search_text"
        )
