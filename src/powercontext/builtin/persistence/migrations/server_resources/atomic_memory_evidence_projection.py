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

"""Rebuild disposable exact-revision evidence from retained declarations."""

from sqlalchemy import inspect, select

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_evidence_schema import ATOMIC_MEMORY_EVIDENCE_SCHEMA_TABLE
from powercontext.builtin.persistence.migrations.atomic_memory_evidence import require_atomic_memory_evidence_schema
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.pinned import finish, pinned
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.runtime.atomic_memory_rebuild import (
    rebuild_atomic_memory_evidence,
    verify_atomic_memory_evidence,
)


def _index(connection, configuration):
    profile = (
        EmbeddingProfile.model_validate(configuration["embedding_profile"])
        if configuration.get("embedding_enabled")
        else None
    )
    return (
        SQLiteAtomicMemoryIndex(profile) if connection.dialect.name == "sqlite" else OceanBaseAtomicMemoryIndex(profile)
    )


def preflight(connection, *, configuration):
    # The marker table may precede other retained tables in a committed MySQL
    # DDL prefix. Its final row distinguishes readiness from resumable work.
    marker = ATOMIC_MEMORY_EVIDENCE_SCHEMA_TABLE
    if (
        marker.name in inspect(connection).get_table_names()
        and connection.execute(select(marker.c.schema_version).limit(1)).first() is not None
    ):
        finish(require_atomic_memory_evidence_schema(pinned(connection)))


def apply(connection, *, configuration):
    finish(rebuild_atomic_memory_evidence(pinned(connection), _index(connection, configuration)))
    validate(connection, configuration=configuration)


def validate(connection, *, configuration):
    report = finish(verify_atomic_memory_evidence(pinned(connection), _index(connection, configuration)))
    if not report.ready:
        raise MigrationError(
            "projection_verification_failed", "Atomic Memory evidence projections differ from retained declarations."
        )


verify = validate
