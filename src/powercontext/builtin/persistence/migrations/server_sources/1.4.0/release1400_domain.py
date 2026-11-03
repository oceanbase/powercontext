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

"""Preserve current 1.4 tasks and authority while joining the shared Server chain."""

import hashlib
import runpy
from pathlib import Path

from sqlalchemy import inspect

from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.pinned import finish, pinned

shared = runpy.run_path(str(Path(__file__).parent / "server_domain.py"))


def preflight(connection, *, configuration):
    shared["preflight"](connection, configuration=configuration)
    validate_source_data(connection, configuration=configuration)


def validate_source_data(connection, *, configuration):
    names = set(inspect(connection).get_table_names())
    if not names or names == {"pc_schema_revision"}:
        return
    frozen = shared["dream"]()
    try:
        manifest = frozen["_processing_manifest"](connection, frozen["_schema"](), upgraded=True)
    except frozen["DreamUpgradeError"] as error:
        raise MigrationError(error.reason, "The released Processing configuration is not recognized.") from error
    target = configuration["processing_manifest"]
    if any(manifest[key] != target[key] for key in ("bindings", "mode", "capabilities")):
        raise MigrationError(
            "incompatible_processing_configuration", "Processing ownership differs from the source declaration."
        )
    shared["_validate_intent_bindings"](connection)
    # Current tasks are decoded by their supported repository contract. The
    # frozen 1.3 task recognizer rejects newer operations and is not applicable.
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


def apply(connection, *, configuration):
    shared["apply"](connection, configuration=configuration)


def verify(connection, *, configuration):
    # No legacy Candidate conversion occurred on this source path.
    validate(connection, configuration=configuration)


def validate(connection, *, configuration):
    shared["validate"](connection, configuration=configuration)
