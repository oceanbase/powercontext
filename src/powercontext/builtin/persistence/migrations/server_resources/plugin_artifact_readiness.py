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

"""Server readiness for directory-loaded plugins at v1.5.0.005.

This revision removes the storage of the previous Artifact plugin framework and
adds revision metadata. Plugin bindings are named "plugin:<name>" and are never
registered in the database; their intents stay valid while the plugin is absent.
Plugin families keep no rows in the built-in full-text helper, so the frozen
helper check still applies unchanged.
"""

import hashlib
import json
import runpy
from pathlib import Path

from powercontext.builtin.persistence.experience_index import APPROVED_ARTIFACT_PROJECTION_VERSION
from powercontext.builtin.persistence.lexical_projection import LEXICAL_ANALYZER_VERSION
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.pinned import finish, pinned

core = runpy.run_path(str(Path(__file__).parent / "server_domain.py"))
vectors = runpy.run_path(str(Path(__file__).parent / "server_vector_domain.py"))

# Tables of the removed framework that hold plugin data when they have any row.
_RETIRED_DATA = (
    "pc_artifact_family_schemas",
    "pc_artifact_vector_spaces",
    "pc_artifact_vector_indexes",
    "pc_artifact_search_states",
    "pc_artifact_search_text",
)


def preflight(connection, *, configuration):
    """Refuse to drop the previous plugin framework while it still holds data."""
    from sqlalchemy import inspect

    names = set(inspect(connection).get_table_names())
    retained = [name for name in _RETIRED_DATA if name in names and _has_rows(connection, name)]
    states = "pc_artifact_processing_binding_states"
    columns = {column["name"] for column in inspect(connection).get_columns(states)} if states in names else set()
    if (
        "plugin_id" in columns
        and connection.exec_driver_sql(
            "SELECT 1 FROM pc_artifact_processing_binding_states WHERE plugin_id IS NOT NULL LIMIT 1"
        ).first()
    ):
        retained.append("pc_artifact_processing_binding_states")
    if retained:
        raise MigrationError(
            "unsupported_plugin_data",
            "Plugins of the removed Artifact plugin framework still hold data in "
            + ", ".join(retained)
            + "; export it and remove those plugins on the previous version before upgrading.",
        )


def apply(connection, *, configuration):
    """The schema transition drops only empty tables; no records are rewritten."""


def verify(connection, *, configuration):
    validate(connection, configuration=configuration)


def validate(connection, *, configuration):  # noqa: C901 -- Preserve the complete versioned Server readiness contract.
    vector = configuration.get("embedding_profile") is not None
    if vector:
        vectors["_validate_vector_configuration"](connection, configuration)
        vectors["_require_declaration"](connection, configuration)
    finish(core["assert_atomic_memory_migration_ready"](pinned(connection)))
    expected = {
        "pc_atomic_memory_current": LEXICAL_ANALYZER_VERSION,
        "pc_topic_memory_active_topics": LEXICAL_ANALYZER_VERSION,
        "pc_topic_memory_active_chunks": LEXICAL_ANALYZER_VERSION,
        "pc_artifact_heads": APPROVED_ARTIFACT_PROJECTION_VERSION,
    }
    actual = dict(
        connection
        .exec_driver_sql("SELECT projection, analyzer_version FROM pc_lexical_projection_versions")
        .tuples()
        .all()
    )
    if actual != expected:
        raise MigrationError("projection_migration_required", "The registered search projections are not current.")
    frozen = core["dream"]()
    manifest = frozen["_processing_manifest"](connection, frozen["_schema"](), upgraded=True)
    _validate_intent_bindings(connection)
    target = configuration.get("processing_manifest")
    if target and any(manifest[key] != target[key] for key in ("bindings", "mode", "capabilities")):
        raise MigrationError(
            "incompatible_processing_configuration", "Processing ownership differs from the migrated declaration."
        )
    from powercontext.builtin.persistence.dream import DreamRepository

    for row in connection.exec_driver_sql(
        "SELECT scope_id,run_id,status,operation,generation,request_generation,principal_key,idempotency_key FROM pc_dream_runs"
    ).mappings():
        record = finish(DreamRepository().get(pinned(connection), row["scope_id"], row["run_id"]))
        if (
            any(getattr(record.run, key) != row[key] for key in ("scope_id", "run_id", "status", "operation"))
            or record.generation != row["generation"]
            or record.request_generation != row["request_generation"]
            or hashlib.sha256(record.principal_id.encode()).hexdigest() != row["principal_key"]
            or (record.request is not None and record.request.idempotency_key != row["idempotency_key"])
        ):
            raise MigrationError("unsupported_task_format", "A Dream payload disagrees with its durable task identity.")
    from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository

    for scope, binding in connection.exec_driver_sql(
        "SELECT scope_id,binding_name FROM pc_artifact_processing_intents"
    ):
        finish(ArtifactProcessingIntentRepository().load(pinned(connection), scope, binding))
    from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
    from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
    from powercontext.builtin.runtime.atomic_memory_rebuild import verify_atomic_memory_current

    profile = vectors["_profile"](configuration) if vector else None
    atomic_index = (
        SQLiteAtomicMemoryIndex(profile) if connection.dialect.name == "sqlite" else OceanBaseAtomicMemoryIndex(profile)
    )
    report = finish(verify_atomic_memory_current(pinned(connection), atomic_index))
    if not report.ready:
        raise MigrationError(
            "projection_verification_failed", "Atomic Memory projections differ from their authoritative records."
        )

    from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository

    if connection.dialect.name == "sqlite":
        from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex

        index = SQLiteTopicMemoryFTSIndex()
        core["_validate_sqlite_fts"](connection)
        if vector:
            vectors["_validate_atomic_helper"](connection, configuration)
    else:
        from powercontext.builtin.persistence.oceanbase.topic_memory_index import OceanBaseTopicMemoryFTSIndex

        index = OceanBaseTopicMemoryFTSIndex()
    if vector:
        index = vectors["_topic_index"](connection, configuration)
    finish(TopicMemoryRepository(index=index).initialize(pinned(connection), validate_only=True))


def _validate_intent_bindings(connection):
    manifest = json.loads(
        connection.exec_driver_sql(
            "SELECT config_manifest FROM pc_artifact_processing_schema WHERE singleton=1"
        ).scalar_one()
    )
    for (binding,) in connection.exec_driver_sql("SELECT DISTINCT binding_name FROM pc_artifact_processing_intents"):
        if binding not in manifest["bindings"] and not binding.startswith("plugin:"):
            raise MigrationError("unsupported_task_format", "A pending Processing intent has no registered binding.")


def _has_rows(connection, table):
    # Only the fixed retired table names above are interpolated.
    return connection.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first() is not None  # noqa: S608
