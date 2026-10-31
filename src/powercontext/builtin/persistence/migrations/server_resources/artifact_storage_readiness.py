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

"""Server readiness with durable Artifact plugin bindings at v1.5.0.001.

The live validation retains the previous Server checks. Its only new admission
rule recognizes persisted plugin bindings, including unloaded registrations.
No business data or projection transformation accompanies this revision.
"""

import hashlib
import json
import runpy
from pathlib import Path

from sqlalchemy import text

from powercontext.builtin.persistence.experience_index import APPROVED_ARTIFACT_PROJECTION_VERSION
from powercontext.builtin.persistence.lexical_projection import LEXICAL_ANALYZER_VERSION
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.pinned import finish, pinned

core = runpy.run_path(str(Path(__file__).parent / "server_domain.py"))
vectors = runpy.run_path(str(Path(__file__).parent / "server_vector_domain.py"))


def preflight(connection, *, configuration):
    """The prior revision owns source admission before fixed schema expansion."""


def apply(connection, *, configuration):
    """Plugin storage requires no rewrite of existing records or projections."""


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
        if binding in manifest["bindings"]:
            continue
        registration = connection.execute(
            text(
                "SELECT b.plugin_id,b.plugin_family,b.plugin_spec FROM pc_artifact_processing_binding_states AS b "
                "WHERE b.binding_name=:binding AND b.plugin_id IS NOT NULL AND b.plugin_family IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM pc_artifact_family_schemas AS s WHERE s.family=b.plugin_family)"
            ),
            {"binding": binding},
        ).first()
        if registration is None:
            raise MigrationError("unsupported_task_format", "A pending Processing intent has no registered binding.")
        plugin_id, family, specification = registration
        # Unload retains the durable binding identity and family schemas while
        # removing deployable code configuration. Its pending evidence stays valid.
        if specification is None:
            continue
        specification = json.loads(specification) if isinstance(specification, str) else specification
        if (
            specification["manifest"]["binding_name"] != binding
            or specification["manifest"]["id"] != plugin_id
            or specification["declaration"]["plugin_id"] != plugin_id
            or specification["declaration"]["family"] != family
        ):
            raise MigrationError("unsupported_task_format", "A plugin binding disagrees with its durable declaration.")
