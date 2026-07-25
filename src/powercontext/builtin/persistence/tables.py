"""Shared dialect-neutral relational tables."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import MEDIUMBLOB, MEDIUMTEXT

from powercontext.limits import (
    MAX_ARTIFACT_FAMILY_LENGTH,
    MAX_ARTIFACT_ID_LENGTH,
    MAX_BINDING_NAME_LENGTH,
    MAX_SCOPE_ID_LENGTH,
    MAX_SOURCE_ID_LENGTH,
    MAX_SOURCE_TYPE_LENGTH,
)

SHARED_METADATA = MetaData()


def _canonical_payload_type():
    return LargeBinary().with_variant(MEDIUMBLOB(), "mysql")


SOURCES_TABLE = Table(
    "pc_sources",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("source_type", String(MAX_SOURCE_TYPE_LENGTH), primary_key=True),
    Column("source_id", String(MAX_SOURCE_ID_LENGTH), primary_key=True),
    Column("payload", _canonical_payload_type(), nullable=False),
    Column("journal_position", BigInteger, nullable=False),
    UniqueConstraint("scope_id", "journal_position", name="uq_pc_sources_scope_journal_position"),
)

SOURCE_JOURNAL_HEADS_TABLE = Table(
    "pc_source_journal_heads",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("position", BigInteger, nullable=False),
    CheckConstraint("position >= 0", name="ck_pc_source_journal_heads_position_nonnegative"),
)

ARTIFACTS_TABLE = Table(
    "pc_artifacts",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), primary_key=True),
    Column("artifact_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("content", _canonical_payload_type(), nullable=False),
)

ARTIFACT_HEADS_TABLE = Table(
    "pc_artifact_heads",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), primary_key=True),
    Column("artifact_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("revision", Integer, nullable=False),
    ForeignKeyConstraint(
        ("scope_id", "family", "artifact_id", "revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="RESTRICT",
    ),
    CheckConstraint("revision > 0", name="ck_pc_artifact_heads_revision_positive"),
)

ARTIFACT_LINEAGE_SOURCES_TABLE = Table(
    "pc_artifact_lineage_sources",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), primary_key=True),
    Column("artifact_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("source_type", String(MAX_SOURCE_TYPE_LENGTH), nullable=False),
    Column("source_id", String(MAX_SOURCE_ID_LENGTH), nullable=False),
    ForeignKeyConstraint(
        ("scope_id", "family", "artifact_id", "revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ("scope_id", "source_type", "source_id"),
        ("pc_sources.scope_id", "pc_sources.source_type", "pc_sources.source_id"),
        ondelete="RESTRICT",
    ),
)

ARTIFACT_LINEAGE_ARTIFACTS_TABLE = Table(
    "pc_artifact_lineage_artifacts",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), primary_key=True),
    Column("artifact_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("upstream_family", String(MAX_ARTIFACT_FAMILY_LENGTH), nullable=False),
    Column("upstream_artifact_id", String(MAX_ARTIFACT_ID_LENGTH), nullable=False),
    Column("upstream_revision", Integer, nullable=False),
    ForeignKeyConstraint(
        ("scope_id", "family", "artifact_id", "revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ("scope_id", "upstream_family", "upstream_artifact_id", "upstream_revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="RESTRICT",
    ),
)

SOURCE_CURSORS_TABLE = Table(
    "pc_source_cursors",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("binding_name", String(MAX_BINDING_NAME_LENGTH), primary_key=True),
    Column("cursor", _canonical_payload_type(), nullable=False),
    Column("generation", BigInteger, nullable=False),
    CheckConstraint("generation >= 0", name="ck_pc_source_cursors_generation_nonnegative"),
)

SHARED_TABLES = (
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
    ARTIFACTS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    SOURCE_CURSORS_TABLE,
)


MAX_MEMORY_ENTRY_ID_LENGTH = 128
MAX_MEMORY_ENTRY_KIND_LENGTH = 128
MAX_MEMORY_HASH_LENGTH = 64


def _entry_text_type():
    return Text().with_variant(MEDIUMTEXT(), "mysql")


MEMORY_ENTRY_VERSIONS_TABLE = Table(
    "pc_memory_entry_versions",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), nullable=False),
    Column("memory_artifact_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("entry_id", String(MAX_MEMORY_ENTRY_ID_LENGTH), nullable=False),
    Column("entry_version_id", String(MAX_MEMORY_ENTRY_ID_LENGTH), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("previous_version_id", String(MAX_MEMORY_ENTRY_ID_LENGTH)),
    Column("kind", String(MAX_MEMORY_ENTRY_KIND_LENGTH), nullable=False),
    Column("text", _entry_text_type(), nullable=False),
    Column("source_refs", _canonical_payload_type(), nullable=False),
    Column("artifact_refs", _canonical_payload_type(), nullable=False),
    Column("entry_content_hash", String(MAX_MEMORY_HASH_LENGTH), nullable=False),
    Column("created_in_revision", Integer, nullable=False),
    UniqueConstraint(
        "scope_id",
        "memory_artifact_id",
        "entry_id",
        "version",
        name="uq_pc_memory_entry_versions_logical_version",
    ),
    UniqueConstraint(
        "scope_id",
        "memory_artifact_id",
        "entry_id",
        "entry_version_id",
        name="uq_pc_memory_entry_versions_identity",
    ),
    ForeignKeyConstraint(
        ("scope_id", "family", "memory_artifact_id", "created_in_revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="RESTRICT",
    ),
    CheckConstraint("version > 0", name="ck_pc_memory_entry_versions_version_positive"),
    CheckConstraint(
        "created_in_revision > 0",
        name="ck_pc_memory_entry_versions_revision_positive",
    ),
)

MEMORY_ENTRY_HEADS_TABLE = Table(
    "pc_memory_entry_heads",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), nullable=False),
    Column("memory_artifact_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("head_revision", Integer, nullable=False),
    Column("entry_id", String(MAX_MEMORY_ENTRY_ID_LENGTH), primary_key=True),
    Column("entry_version_id", String(MAX_MEMORY_ENTRY_ID_LENGTH), nullable=False),
    Column("entry_content_hash", String(MAX_MEMORY_HASH_LENGTH), nullable=False),
    Column("searchable_text", _entry_text_type(), nullable=False),
    ForeignKeyConstraint(
        ("scope_id", "family", "memory_artifact_id", "head_revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ("scope_id", "memory_artifact_id", "entry_id", "entry_version_id"),
        (
            "pc_memory_entry_versions.scope_id",
            "pc_memory_entry_versions.memory_artifact_id",
            "pc_memory_entry_versions.entry_id",
            "pc_memory_entry_versions.entry_version_id",
        ),
        ondelete="RESTRICT",
    ),
    CheckConstraint("head_revision > 0", name="ck_pc_memory_entry_heads_revision_positive"),
)


MEMORY_TABLES = (MEMORY_ENTRY_VERSIONS_TABLE, MEMORY_ENTRY_HEADS_TABLE)

BUILTIN_TABLES = SHARED_TABLES + MEMORY_TABLES
