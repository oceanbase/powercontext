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

"""The single rebuildable current Atomic Memory projection."""

from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, LargeBinary, MetaData, Table, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.types import TypeEngine

from powercontext.builtin.persistence.tables import identity_string
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

ATOMIC_MEMORY_CURRENT_TABLE_NAME = "pc_atomic_memory_current"
ATOMIC_MEMORY_PROJECTION_FORMAT = "powercontext.atomic-memory.current.v1"


def atomic_memory_current_table(embedding_type: TypeEngine[Any] | None = None, /) -> Table:
    """Build one backend-owned table, with its deployment's native vector type.

    This is the same logical table on both backends. Independent metadata lets
    OceanBase choose VECTOR(dimension) without mutating SQLite's BLOB column.
    """

    body_type = Text().with_variant(MEDIUMTEXT(), "mysql")
    return Table(
        ATOMIC_MEMORY_CURRENT_TABLE_NAME,
        MetaData(),
        Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
        Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
        Column("revision", BigInteger, nullable=False),
        Column("state_version", BigInteger, nullable=False),
        Column("content_hash", identity_string(64), nullable=False),
        Column("projection_format", identity_string(64), nullable=False),
        Column("kind", body_type, nullable=False),
        Column("text", body_type, nullable=False),
        Column("searchable_text", body_type, nullable=False),
        Column("tag_keys", body_type, nullable=False),
        Column("owner_type", identity_string(16), nullable=False),
        Column("owner_id", identity_string(255), nullable=False),
        Column("read_grants", body_type, nullable=False),
        Column("embedding", LargeBinary() if embedding_type is None else embedding_type),
        Column("profile_fingerprint", identity_string(64)),
        Column("embedding_input_hash", identity_string(64)),
        CheckConstraint("revision > 0", name="ck_pc_atomic_memory_current_revision"),
        CheckConstraint("state_version >= 0", name="ck_pc_atomic_memory_current_state_version"),
        CheckConstraint(
            "(embedding IS NULL AND profile_fingerprint IS NULL AND embedding_input_hash IS NULL) "
            "OR (embedding IS NOT NULL AND profile_fingerprint IS NOT NULL AND embedding_input_hash IS NOT NULL)",
            name="ck_pc_atomic_memory_current_embedding_metadata",
        ),
        Index("ix_pc_atomic_memory_current_owner", "scope_id", "owner_type", "owner_id"),
    )


__all__ = ["ATOMIC_MEMORY_CURRENT_TABLE_NAME", "ATOMIC_MEMORY_PROJECTION_FORMAT", "atomic_memory_current_table"]
