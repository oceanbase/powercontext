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

"""Full Server bootstrap and read-only admission for versioned storage."""

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from .bundle import sqlite_inventory
from .models import MigrationError, digest


def check_server_schema(connection, *, _archive_recovery=False, _startup=False):
    bundle = check_server_layout(connection, allow_declared_spaces=_startup)
    if _startup and connection.dialect.name == "sqlite":
        _check_sqlite_startup_history(connection, bundle)
    pending_restore = connection.exec_driver_sql(
        "SELECT 1 FROM pc_portable_restore_receipts WHERE projections_ready = false LIMIT 1"
    ).first()
    if pending_restore is not None:
        if _archive_recovery:
            # Only the explicit archive restore path may open an incomplete
            # projection set. Its registered schema is still checked above.
            return
        from powercontext.builtin.portability import BundleRecoveryRequiredError

        raise BundleRecoveryRequiredError()
    backend = "sqlite" if connection.dialect.name == "sqlite" else "seekdb"
    configuration = (
        {"embedding_profile": bundle.vector_profile.profile.model_dump(mode="json")}
        if bundle.vector_profile is not None
        else {}
    )
    bundle.domains.validate(connection, bundle.head, backend=backend, configuration=configuration)


def check_server_layout(connection, *, allow_declared_spaces=False):
    """Validate the exact managed layout before a registered domain repair."""
    from .deployment import server_bundle
    from .mysql_schema import MySQLSchema, mysql_inventory
    from .vector_profile import load_sqlite_vector_extension, read_embedding_profile

    bundle = server_bundle(embedding_profile=read_embedding_profile(connection))
    if connection.dialect.name == "sqlite" and bundle.vector_profile is not None:
        load_sqlite_vector_extension(connection)
    revisions = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalars().all()
    if revisions != [bundle.head]:
        raise MigrationError("migration_required", "The full Server revision chain is incomplete.")
    if connection.dialect.name == "sqlite":
        inventory = sqlite_inventory(connection)
        for name in bundle.retained_objects:
            objects = [row for row in inventory if row[1] == name or row[2] == name]
            if objects and digest(objects) not in bundle.retained_fingerprints[name]:
                raise MigrationError("incompatible_schema", "A retained migration object has changed.")
        bundle.verify(
            bundle.head,
            digest(sqlite_inventory(connection, exclude=frozenset({"pc_schema_revision", *bundle.retained_objects}))),
        )
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise MigrationError("verification_failed", "A live foreign key is invalid.")
    else:
        inventory = mysql_inventory(connection)
        embedded_code = connection.engine.url.drivername == "mysql+aseekdb" and any(
            name.startswith("pc_code_") for name in inventory["tables"]
        )
        schema = MySQLSchema(bundle, embedded_code=embedded_code, allow_declared_spaces=allow_declared_spaces)
        schema.verify(inventory, bundle.head)
    return bundle


def _check_sqlite_startup_history(connection, bundle):
    """Honor observable local history without requiring an external journal to exist."""
    from .connections import BackendIdentity
    from .deployment import server_bundle
    from .mysql import require_completed_domain_maintenance
    from .sources import MigrationSources
    from .sqlite import read_maintenance_evidence, verify_release_checksums, verify_retained_objects

    filename = next(
        (filename for _, name, filename in connection.exec_driver_sql("PRAGMA database_list") if name == "main"),
        "",
    )
    if not filename:
        return
    target = Path(filename).resolve()
    directory = target.with_name(target.name + ".pc-migration-state")
    identity = BackendIdentity("sqlite", digest({"kind": "sqlite", "path": str(target)}), target.name)
    evidence = read_maintenance_evidence(directory / "maintenance.json", identity.database_id)
    if evidence is not None:
        if evidence["state"] != "complete":
            raise MigrationError("recovery_required", "Complete the recorded full Server maintenance before startup.")
        profile = bundle.vector_profile.profile if bundle.vector_profile is not None else None
        bundle = MigrationSources(
            bundle, (server_bundle(embedding_profile=profile, source_release="1.4.0"),)
        ).from_evidence(evidence)
        verify_release_checksums(evidence["release_checksums"], bundle.checksums)
        verify_retained_objects(sqlite_inventory(connection), bundle.head, bundle=bundle, evidence=evidence)
    require_completed_domain_maintenance(directory, identity)


def prepare_server_storage(settings, *, injected_embedding=False, embedding_profile=None):  # noqa: C901 -- Keep startup admission and bootstrap decisions together.
    from .deployment import configured_embedding_profile, deployment_runner

    database = settings.database
    if database.kind == "sqlite" and database.is_in_memory:
        return
    if database.kind == "sqlite":
        from .locking import assert_no_local_migration

        url = make_url(database.url)
        if url.database and not url.query:
            assert_no_local_migration(Path(url.database).absolute())
    profile = embedding_profile or configured_embedding_profile(settings)
    if injected_embedding and profile is None:
        _check_unmanaged_optional_profile(database)
        return
    if database.kind == "oceanbase":
        from .connections import MaintenanceConnections

        def verify(connection, identity, guard):
            if connection is None or "pc_schema_revision" not in inspect(connection).get_table_names():
                raise MigrationError(
                    "migration_required", "Initialize the remote database with an explicit db-migrate job."
                )
            bundle = check_server_layout(connection, allow_declared_spaces=True)
            stored = bundle.vector_profile.profile if bundle.vector_profile is not None else None
            if stored != profile:
                raise MigrationError("embedding_profile_mismatch", "Server configuration differs from managed storage.")
            check_server_schema(connection, _startup=True)

        MaintenanceConnections(database).run(verify)
        return
    runner = deployment_runner(settings, embedding_profile=profile)
    from .mysql import MySQLMigrationRunner

    plan = runner.plan(_startup=True) if isinstance(runner, MySQLMigrationRunner) else runner.plan()
    if plan.state == "uninitialized" and database.kind in {"sqlite", "seekdb"}:
        runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    elif plan.state != "ready":
        raise MigrationError(plan.state, "Stop writers and run powercontext server db-migrate apply before startup.")


def _check_unmanaged_optional_profile(database):
    """Preserve existing optional-profile bootstrap without adopting managed storage.

    Optional layouts need separate full-bundle profiles. Existing domain startup
    checks still reject old Candidate layouts; this is not an upgrade fallback.
    """
    from powercontext.builtin.persistence.dream_schema import check_dream_schema

    from .connections import MaintenanceConnections

    def check(connection):
        if "pc_schema_revision" in inspect(connection).get_table_names():
            raise MigrationError(
                "unsupported_upgrade_configuration",
                "This managed database has no registered vector/Code configuration transition.",
            )
        check_dream_schema(connection)

    if database.kind != "sqlite":
        MaintenanceConnections(database).run(
            lambda connection, identity, guard: check(connection) if connection is not None else None
        )
        return
    from pathlib import Path
    from urllib.parse import quote

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = make_url(database.url)
    # Unmanaged URI profiles retain their existing domain admission behavior.
    if url.query or not url.database or not Path(url.database).is_file():
        return
    path = quote(str(Path(url.database).absolute()), safe="/")
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    try:
        with engine.connect() as connection:
            check(connection)
    finally:
        engine.dispose()
