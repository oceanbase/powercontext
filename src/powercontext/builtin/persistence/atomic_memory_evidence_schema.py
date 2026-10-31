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

"""Retained exact-version evidence, independent of disposable search projections."""

from sqlalchemy import BigInteger, CheckConstraint, Column, LargeBinary, Table, Text
from sqlalchemy.dialects.mysql import LONGBLOB, MEDIUMTEXT

from powercontext.builtin.persistence.tables import SHARED_METADATA, identity_string
from powercontext.limits import (
    MAX_ARTIFACT_ID_LENGTH,
    MAX_SCOPE_ID_LENGTH,
    MAX_SOURCE_ID_LENGTH,
    MAX_SOURCE_TYPE_LENGTH,
)

ATOMIC_MEMORY_EVIDENCE_SCHEMA_VERSION = "p0005"
ATOMIC_MEMORY_EVIDENCE_SCHEMA_TABLE = Table(
    "pc_atomic_memory_evidence_schema",
    SHARED_METADATA,
    Column("schema_version", identity_string(32), primary_key=True),
)
ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE = Table(
    "pc_atomic_memory_evidence_history",
    SHARED_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("revision", BigInteger, primary_key=True),
    Column("sources", LargeBinary().with_variant(LONGBLOB(), "mysql"), nullable=False),
    CheckConstraint("revision > 0", name="ck_pc_atomic_memory_evidence_history_revision"),
)
SOURCE_EVIDENCE_BINDINGS_TABLE = Table(
    "pc_source_evidence_bindings",
    SHARED_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("source_type", identity_string(MAX_SOURCE_TYPE_LENGTH), primary_key=True),
    Column("source_id", identity_string(MAX_SOURCE_ID_LENGTH), primary_key=True),
    Column("material_digest", identity_string(71), nullable=False),
    Column("definition_name", Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
    Column("definition_version", Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
    Column("manifest_fingerprint", identity_string(71)),
    Column("declaration", LargeBinary().with_variant(LONGBLOB(), "mysql"), nullable=False),
)
ATOMIC_MEMORY_EVIDENCE_TABLES = (
    ATOMIC_MEMORY_EVIDENCE_SCHEMA_TABLE,
    ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE,
    SOURCE_EVIDENCE_BINDINGS_TABLE,
)
