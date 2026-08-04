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
    MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH,
    MAX_EXTERNAL_SKILL_HOST_ID_LENGTH,
    MAX_EXTERNAL_SKILL_LOCATOR_LENGTH,
    MAX_EXTERNAL_SKILL_NAME_LENGTH,
    MAX_SCOPE_ID_LENGTH,
    MAX_SOURCE_ID_LENGTH,
    MAX_SOURCE_TYPE_LENGTH,
)

SHARED_METADATA = MetaData()


def _canonical_payload_type():
    return LargeBinary().with_variant(MEDIUMBLOB(), "mysql")


def _entry_text_type():
    return Text().with_variant(MEDIUMTEXT(), "mysql")


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
    Column("searchable_text", _entry_text_type()),
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

ARTIFACT_CANDIDATE_VERSIONS_TABLE = Table(
    "pc_artifact_candidate_versions",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("candidate_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), nullable=False),
    Column("proposal", _canonical_payload_type(), nullable=False),
    Column("source_refs", _canonical_payload_type(), nullable=False),
    Column("artifact_refs", _canonical_payload_type(), nullable=False),
    Column("target_family", String(MAX_ARTIFACT_FAMILY_LENGTH)),
    Column("target_artifact_id", String(MAX_ARTIFACT_ID_LENGTH)),
    Column("target_revision", Integer),
    Column("reason", _entry_text_type()),
    ForeignKeyConstraint(
        ("scope_id", "target_family", "target_artifact_id", "target_revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="RESTRICT",
    ),
    CheckConstraint("version > 0", name="ck_pc_artifact_candidate_versions_version_positive"),
    CheckConstraint(
        "(target_family IS NULL AND target_artifact_id IS NULL AND target_revision IS NULL) OR "
        "(target_family IS NOT NULL AND target_artifact_id IS NOT NULL AND target_revision > 0)",
        name="ck_pc_artifact_candidate_versions_target_complete",
    ),
)

ARTIFACT_CANDIDATE_HEADS_TABLE = Table(
    "pc_artifact_candidate_heads",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("candidate_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("family", String(MAX_ARTIFACT_FAMILY_LENGTH), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("result_family", String(MAX_ARTIFACT_FAMILY_LENGTH)),
    Column("result_artifact_id", String(MAX_ARTIFACT_ID_LENGTH)),
    Column("result_revision", Integer),
    Column("decision_reason", _entry_text_type()),
    ForeignKeyConstraint(
        ("scope_id", "candidate_id", "version"),
        (
            "pc_artifact_candidate_versions.scope_id",
            "pc_artifact_candidate_versions.candidate_id",
            "pc_artifact_candidate_versions.version",
        ),
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ("scope_id", "result_family", "result_artifact_id", "result_revision"),
        (
            "pc_artifacts.scope_id",
            "pc_artifacts.family",
            "pc_artifacts.artifact_id",
            "pc_artifacts.revision",
        ),
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected')",
        name="ck_pc_artifact_candidate_heads_status",
    ),
    CheckConstraint(
        "(status = 'approved' AND result_family IS NOT NULL AND result_artifact_id IS NOT NULL "
        "AND result_revision > 0 AND decision_reason IS NULL) OR "
        "(status = 'rejected' AND result_family IS NULL AND result_artifact_id IS NULL "
        "AND result_revision IS NULL AND decision_reason IS NOT NULL) OR "
        "(status = 'pending' AND result_family IS NULL AND result_artifact_id IS NULL "
        "AND result_revision IS NULL AND decision_reason IS NULL)",
        name="ck_pc_artifact_candidate_heads_terminal_result",
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

EXTERNAL_SKILL_REGISTRATIONS_TABLE = Table(
    "pc_external_skill_registrations",
    SHARED_METADATA,
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("external_skill_id", String(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("provider", String(MAX_SOURCE_TYPE_LENGTH), nullable=False),
    Column("agent_kind", String(MAX_SOURCE_TYPE_LENGTH), nullable=False),
    Column("host_id", String(MAX_EXTERNAL_SKILL_HOST_ID_LENGTH), nullable=False),
    Column("installation_scope", String(16), nullable=False),
    Column("locator", String(MAX_EXTERNAL_SKILL_LOCATOR_LENGTH), nullable=False),
    Column("locator_hash", String(64), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("name", String(MAX_EXTERNAL_SKILL_NAME_LENGTH), nullable=False),
    Column("description", String(MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH), nullable=False),
    UniqueConstraint(
        "scope_id",
        "provider",
        "host_id",
        "installation_scope",
        "locator_hash",
        name="uq_pc_external_skill_registrations_binding",
    ),
    CheckConstraint(
        "installation_scope IN ('user', 'project', 'plugin')",
        name="ck_pc_external_skill_registrations_scope",
    ),
)

SHARED_TABLES = (
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
    ARTIFACTS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    SOURCE_CURSORS_TABLE,
    EXTERNAL_SKILL_REGISTRATIONS_TABLE,
)


MAX_MEMORY_ENTRY_ID_LENGTH = 128
MAX_MEMORY_ENTRY_KIND_LENGTH = 128
MAX_MEMORY_HASH_LENGTH = 64


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
