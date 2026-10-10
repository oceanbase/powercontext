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

"""Select maintenance adapters without initializing the business runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig

from .bundle import MigrationBundle
from .connections import MaintenanceConnections
from .models import MigrationError
from .mysql import MySQLMigrationRunner
from .sqlite import SQLiteMigrationRunner

if TYPE_CHECKING:
    from powercontext.server.settings import ServerSettings


def production_bundle() -> MigrationBundle:
    """Load installed resources; readiness is scoped to this acceptance bundle."""
    return MigrationBundle(Path(__file__).parent / "resources")


MigrationRunner = SQLiteMigrationRunner | MySQLMigrationRunner


def deployment_runner(
    settings: ServerSettings,
    *,
    evidence_dir: Path | None = None,
    lock_coordination: Literal["single-host"] | None = None,
    acknowledge_previous_run: str | None = None,
) -> MigrationRunner:
    """Use the real configured identity without opening a business profile."""
    database = settings.database
    if acknowledge_previous_run is not None and not isinstance(database, OceanBaseConfig):
        raise MigrationError("unsupported_option", "--acknowledge-previous-run is supported only for OceanBase.")
    if isinstance(database, (SeekDBConfig, OceanBaseConfig)):
        if evidence_dir is None:
            if isinstance(database, OceanBaseConfig):
                raise MigrationError(
                    "evidence_required",
                    "OceanBase maintenance requires --evidence-dir on one fixed host, shared by all migration Jobs.",
                )
            target = database.path.expanduser().resolve()
            evidence_dir = target.with_name(target.name + ".pc-migration-state")
        if isinstance(database, OceanBaseConfig) and lock_coordination != "single-host":
            raise MigrationError(
                "coordination_required",
                "OceanBase requires --lock-coordination single-host; run all migrators on one fixed host and evidence directory.",
            )
        return MySQLMigrationRunner(
            MaintenanceConnections(
                database,
                evidence_directory=evidence_dir,
                lock_coordination=lock_coordination,
                acknowledge_previous_run=acknowledge_previous_run,
            ),
            production_bundle(),
            evidence_directory=evidence_dir,
            configuration=database.model_dump(mode="json", exclude={"url", "echo"}),
        )
    if not isinstance(database, SQLiteConfig):
        raise MigrationError("unsupported_backend", "The database has no registered migration adapter.")
    url = make_url(database.url)
    if database.is_in_memory or url.query or not url.database or url.username or url.password or url.host or url.port:
        raise MigrationError(
            "unsupported_target", "Use a regular SQLite file URL without credentials, host, port or URI options."
        )
    # Match SQLite's URL normalization before the runner expands standalone Path inputs.
    target = Path(os.path.abspath(url.database))
    return SQLiteMigrationRunner(
        target,
        production_bundle(),
        configuration=database.model_dump(exclude={"url", "echo"}),
        evidence_directory=evidence_dir,
    )
