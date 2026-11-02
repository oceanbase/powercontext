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

"""Registered offline Processing configuration changes on current Server storage."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import Connection, text
from typing_extensions import override

from powercontext.builtin.persistence import processing_migration
from powercontext.builtin.persistence.processing_migration import ProcessingSchemaNotReadyError

from .bundle import sqlite_inventory
from .connections import BackendIdentity
from .maintenance import ManagedMaintenanceRunner
from .models import MigrationError, digest
from .mysql_schema import mysql_inventory
from .pinned import finish, pinned
from .server_readiness import check_server_schema


class ProcessingConfigurationMigrationRunner(ManagedMaintenanceRunner):
    """Publish mode changes and lease fencing in the same shared transaction."""

    operation = "processing_configuration"
    affected_objects = (
        "pc_artifact_processing_schema",
        "pc_artifact_processing_leases",
        "pc_artifact_processing_auto_wave_targets",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            implementation_checksum=digest({
                "owner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "processing": hashlib.sha256(Path(processing_migration.__file__).read_bytes()).hexdigest(),
            }),
            **kwargs,
        )

    @override
    def _state(self, connection: Connection, identity: BackendIdentity) -> tuple[str, bool, bool]:
        check_server_schema(connection)
        marker = dict(
            connection
            .execute(
                text(
                    "SELECT schema_version,source_version,phase,migration_id,config_manifest "
                    "FROM pc_artifact_processing_schema WHERE singleton=1"
                )
            )
            .mappings()
            .one()
        )
        required = finish(
            processing_migration.plan_processing_migration(
                pinned(connection), config_manifest=self.configuration["processing_manifest"]
            )
        ).required
        if required and marker["migration_id"] == self.migration_id:
            raise MigrationError(
                "migration_id_reused", "A Processing configuration switch requires a new migration ID."
            )
        fingerprint = digest({
            "schema": sqlite_inventory(connection) if identity.product == "sqlite" else mysql_inventory(connection),
            "processing": marker,
        })
        return fingerprint, required, marker["migration_id"] == self.migration_id

    @override
    def _apply_operation(self, connection: Connection, guard) -> None:
        try:
            while not finish(
                processing_migration.apply_processing_migration(
                    pinned(connection),
                    config_manifest=self.configuration["processing_manifest"],
                    migration_id=self.migration_id,
                    batch_size=self.batch_size,
                )
            ).complete:
                guard()
        except ProcessingSchemaNotReadyError as error:
            raise MigrationError("incompatible_processing_configuration", str(error)) from error
