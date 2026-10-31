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
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.engine import make_url

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest

from .atomic_projection import AtomicProjectionMigrationRunner
from .bundle import MigrationBundle
from .connections import MaintenanceConnections
from .lexical_projection import LexicalProjectionMigrationRunner
from .models import MigrationError
from .mysql import MySQLMigrationRunner
from .processing_configuration import ProcessingConfigurationMigrationRunner
from .sqlite import SQLiteMigrationRunner
from .topic_memory_recovery import TopicMemoryRecoveryMigrationRunner

if TYPE_CHECKING:
    from powercontext.server.settings import ServerSettings


def server_bundle(
    *, embedding_profile: EmbeddingProfile | None = None, source_release: Literal["1.4.0"] | None = None
) -> MigrationBundle:
    """The shared Server chain, optionally reached through a frozen source bridge."""
    root = Path(__file__).parent
    return MigrationBundle(
        root / "server_resources",
        embedding_profile=embedding_profile,
        source_directory=root / "server_sources" / source_release if source_release else None,
    )


def production_bundle() -> MigrationBundle:
    """Load installed resources; readiness is scoped to this acceptance bundle."""
    return MigrationBundle(Path(__file__).parent / "resources")


MigrationRunner = (
    SQLiteMigrationRunner
    | MySQLMigrationRunner
    | TopicMemoryRecoveryMigrationRunner
    | ProcessingConfigurationMigrationRunner
    | AtomicProjectionMigrationRunner
    | LexicalProjectionMigrationRunner
)


def _migration_configuration(settings: ServerSettings) -> dict[str, Any]:
    config = BuiltinConfig(
        code=settings.code,
        runtime=settings.runtime,
        database=settings.database,
        handoff_report=settings.handoff_report,
        inference=settings.inference,
        external_skills=settings.external_skills,
    )
    return {
        "database": settings.database.model_dump(mode="json", exclude={"url", "echo"}),
        "processing_manifest": canonical_processing_manifest(config),
    }


def configured_embedding_profile(settings: ServerSettings) -> EmbeddingProfile | None:
    inference = settings.inference
    if inference.embedding_model is None:
        return None
    if inference.embedding_profile_id is None or inference.embedding_dimension is None:
        raise MigrationError(
            "embedding_profile_mismatch", "Embedding configuration needs its exact profile and dimension."
        )
    return EmbeddingProfile(
        profile_id=inference.embedding_profile_id,
        model=inference.embedding_model,
        dimension=inference.embedding_dimension,
        normalization=inference.embedding_normalization,
    )


def deployment_runner(  # noqa: C901 -- Ordered backend admission checks.
    settings: ServerSettings,
    *,
    evidence_dir: Path | None = None,
    lock_coordination: Literal["single-host"] | None = None,
    acknowledge_previous_run: str | None = None,
    bundle: Literal[
        "server", "artifacts", "topic-memory-recovery", "processing", "atomic-projection", "lexical-projection"
    ] = "server",
    migration_id: str = "rfc1515",
    batch_size: int = 100,
    embedding_profile: EmbeddingProfile | None = None,
) -> MigrationRunner:
    """Use the real configured identity without opening a business profile."""
    database = settings.database
    configuration = _migration_configuration(settings)
    profile = embedding_profile or configured_embedding_profile(settings)
    if bundle in {"server", "processing", "atomic-projection", "lexical-projection"}:
        if profile is not None and bundle not in {"server", "lexical-projection"}:
            raise MigrationError(
                "unsupported_upgrade_configuration",
                "This partial maintenance bundle has no registered vector configuration path.",
            )
        configuration["embedding_enabled"] = profile is not None
        if profile is not None:
            configuration["embedding_profile"] = profile.model_dump(mode="json")
        if settings.code.enabled and isinstance(database, SeekDBConfig):
            configuration["embedded_code_enabled"] = True
    selected = (
        server_bundle(embedding_profile=profile)
        if bundle in {"server", "processing", "atomic-projection", "lexical-projection"}
        else production_bundle()
    )
    sources = (
        (server_bundle(embedding_profile=profile, source_release="1.4.0"),)
        if bundle in {"server", "processing", "atomic-projection", "lexical-projection"}
        else ()
    )
    maintenance_runner = {
        "atomic-projection": AtomicProjectionMigrationRunner,
        "lexical-projection": LexicalProjectionMigrationRunner,
    }.get(bundle, ProcessingConfigurationMigrationRunner)
    if bundle == "topic-memory-recovery":
        if not isinstance(database, (SQLiteConfig, SeekDBConfig, OceanBaseConfig)):
            raise MigrationError("unsupported_backend", "The database has no Topic recovery migration adapter.")
        return TopicMemoryRecoveryMigrationRunner(
            database,
            evidence_directory=evidence_dir,
            lock_coordination=lock_coordination,
            acknowledge_previous_run=acknowledge_previous_run,
        )
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
        connections = MaintenanceConnections(
            database,
            evidence_directory=evidence_dir,
            lock_coordination=lock_coordination,
            acknowledge_previous_run=acknowledge_previous_run,
        )
        if bundle in {"processing", "atomic-projection", "lexical-projection"}:
            return maintenance_runner(
                connections,
                selected,
                source_bundles=sources,
                configuration=configuration,
                evidence_directory=evidence_dir,
                migration_id=migration_id,
                batch_size=batch_size,
            )
        return MySQLMigrationRunner(
            connections,
            selected,
            source_bundles=sources,
            evidence_directory=evidence_dir,
            configuration=configuration,
        )
    if not isinstance(database, SQLiteConfig):
        raise MigrationError("unsupported_backend", "The database has no registered migration adapter.")
    url = make_url(database.url)
    if url.query or not url.database or url.username or url.password or url.host or url.port or database.is_in_memory:
        raise MigrationError(
            "unsupported_target", "Use a regular SQLite file URL without credentials, host, port or URI options."
        )
    # Match SQLite's URL normalization before the runner expands standalone Path inputs.
    target = Path(os.path.abspath(url.database))
    if bundle in {"processing", "atomic-projection", "lexical-projection"}:
        directory = evidence_dir or target.with_name(target.name + ".pc-migration-state")
        return maintenance_runner(
            MaintenanceConnections(database),
            selected,
            source_bundles=sources,
            configuration=configuration,
            evidence_directory=directory,
            migration_id=migration_id,
            batch_size=batch_size,
        )
    return SQLiteMigrationRunner(
        target,
        selected,
        source_bundles=sources,
        configuration=configuration,
        evidence_directory=evidence_dir,
    )
