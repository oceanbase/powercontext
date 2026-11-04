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

"""Validate retained evidence only after its introducing Server revision."""

from sqlalchemy import inspect, select

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_evidence_schema import ATOMIC_MEMORY_EVIDENCE_SCHEMA_TABLE
from powercontext.builtin.persistence.migrations.atomic_memory_evidence import require_atomic_memory_evidence_schema
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.pinned import finish, pinned
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.runtime.atomic_memory_rebuild import verify_atomic_memory_current


def preflight(connection, *, configuration):
    # MySQL may commit the marker table before the remaining DDL. Its row is
    # inserted last; only that completed declaration requires full readiness.
    marker = ATOMIC_MEMORY_EVIDENCE_SCHEMA_TABLE
    if (
        marker.name in inspect(connection).get_table_names()
        and connection.execute(select(marker.c.schema_version).limit(1)).first() is not None
    ):
        finish(require_atomic_memory_evidence_schema(pinned(connection)))


def validate(connection, *, configuration):
    profile = (
        EmbeddingProfile.model_validate(configuration["embedding_profile"])
        if configuration.get("embedding_enabled")
        else None
    )
    index = (
        SQLiteAtomicMemoryIndex(profile) if connection.dialect.name == "sqlite" else OceanBaseAtomicMemoryIndex(profile)
    )
    report = finish(verify_atomic_memory_current(pinned(connection), index, require_evidence=True))
    if not report.ready:
        raise MigrationError(
            "projection_verification_failed", "Atomic Memory projections differ from their authoritative records."
        )


# This additive revision validates existing history; it never rewrites declarations.
apply = verify = validate
