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

"""Fixed revisions preserve Artifact declarations and online native spaces."""

import asyncio
import json
import shutil

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.artifact_indexes import (
    SCHEMAS,
    STATES,
    ArtifactIndexBuilder,
    ArtifactProjectionPublisher,
    register_definition,
)
from powercontext.builtin.persistence.artifact_vector_space import (
    native_vector_table,
    prepare_family_spaces,
    verify_native_space,
)
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner
from powercontext.builtin.persistence.migrations.mysql_schema import _parse_table, mysql_inventory
from powercontext.builtin.persistence.migrations.server_readiness import check_server_schema
from powercontext.builtin.persistence.tables import ARTIFACT_VECTOR_INDEXES_TABLE as DEFINITIONS
from powercontext.builtin.persistence.tables import ARTIFACT_VECTOR_SPACES_TABLE as SPACES
from tests.builtin.persistence.test_artifact_indexes import PROFILE, body, declaration, service, write
from tests.builtin.persistence.test_mysql_migration_runner import (
    _apply,
    _full_server_runner,
    _interrupt_after_ddl,
    backend,  # noqa: F401 - Real disposable backend fixture.
)
from tests.e2e.atomic_memory_migration_backend import migration_profile


def test_fixed_artifact_storage_resumes_after_registration_columns(backend):  # noqa: F811
    runner = _full_server_runner(backend)
    _interrupt_after_ddl(
        runner,
        "ALTER TABLE pc_artifact_processing_binding_states ADD COLUMN plugin_id",
        policy="auto",
    )
    (journal,) = backend.evidence_directory.rglob("maintenance.json")
    interrupted = json.loads(journal.read_text())
    assert interrupted["last_revision"] == "v1_4_1_006"
    assert interrupted["pending_revision"] == "v1_5_0_001"
    recovery = _full_server_runner(backend).plan()
    assert recovery.state == "recovery_required" and recovery.source_revision == "v1_4_1_006"
    # The DDL returned and its owned connection closed before the injected failure.
    resumed = _full_server_runner(backend, acknowledge_previous_run=(recovery.coordination or {}).get("pending_run_id"))
    result = _apply(resumed)
    assert result.state == "ready" and result.revision == resumed.bundle.head
    assert resumed.verify().state == "ready"
    completed = json.loads(journal.read_text())
    assert completed["state"] == "complete"
    assert completed["maintenance_window_id"] == interrupted["maintenance_window_id"]
    assert completed["source_fingerprint"] == interrupted["source_fingerprint"]


def _next_fixed_revision(bundle, directory):
    """A subsequent fixed-table addition leaves the space format untouched."""
    shutil.copytree(bundle.directory, directory)
    revision = "v1_5_0_002"
    sql = "CREATE TABLE pc_lifecycle_probe (id INTEGER NOT NULL, PRIMARY KEY (id))"
    name, layout = _parse_table(sql)
    resource = "schemas/mysql-lifecycle-probe.json"
    (directory / resource).write_text(
        json.dumps({
            "format": "powercontext.mysql-state-layouts.v1",
            "revisions": {revision: {"statements": [sql], "changes": [{name: layout}]}},
        })
    )
    (directory / "versions/v1_5_0_002_probe.py").write_text(
        f"revision = '{revision}'\ndown_revision = '{bundle.head}'\ndef upgrade():\n    pass\n"
    )
    (directory / "mysql/versions/v1_5_0_002.py").write_text(
        "from alembic import context\n"
        f"revision = '{revision}'\ndown_revision = '{bundle.head}'\n"
        "def upgrade():\n    context.config.attributes['execute_revision'](revision)\n"
    )
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["revision_resources"][revision] = [resource]
    manifest["mysql_state_layouts"].append(resource)
    manifest["sqlite_fingerprints"][revision] = manifest["sqlite_fingerprints"][bundle.head]
    manifest["domain_readiness"][revision] = manifest["domain_readiness"][bundle.head]
    manifest["affected_objects"].append(name)
    path.write_text(json.dumps(manifest))
    return MigrationBundle(directory)


def test_native_space_online_recovery_and_fixed_upgrade(backend, tmp_path):  # noqa: F811
    runner = _full_server_runner(backend)
    _apply(runner)
    search = declaration()
    vector = search.indexes[1]

    async def exercise():
        async with migration_profile(backend.config) as profile:
            db = profile.database
            async with db.transaction() as connection:
                await connection.execute(
                    insert(SCHEMAS).values(
                        family="lifecycle",
                        schema_version="1",
                        declaration={
                            "family": "lifecycle",
                            "schema_version": "1",
                            "search": search.model_dump(mode="json"),
                        },
                    )
                )
                await register_definition(connection, "lifecycle", "1", vector)
            async with db.transaction() as connection:
                space = dict(
                    (
                        await connection.execute(
                            select(SPACES).where(
                                SPACES.c.space_id.in_(
                                    select(DEFINITIONS.c.space_id).where(DEFINITIONS.c.family == "lifecycle")
                                )
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert space["provision_state"] == "declared"
                await connection.run_sync(lambda c: check_server_schema(c, _startup=True))
            with pytest.raises(MigrationError):
                await asyncio.to_thread(runner.plan, backup_policy="manual")
            # Interrupt after CREATE TABLE, before the standalone index or ready.
            async with db.transaction() as connection:
                await connection.run_sync(lambda c: c.execute(CreateTable(native_vector_table(space))))
            await asyncio.gather(prepare_family_spaces(db, "lifecycle"), prepare_family_spaces(db, "lifecycle"))
            async with db.transaction() as connection:
                ready = dict(
                    (
                        await connection.execute(
                            select(SPACES).where(
                                SPACES.c.space_id.in_(
                                    select(DEFINITIONS.c.space_id).where(DEFINITIONS.c.family == "lifecycle")
                                )
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert ready["space_id"] == space["space_id"] and ready["provision_state"] == "ready"
                assert (await connection.execute(select(STATES))).first() is None
                await connection.run_sync(lambda c: verify_native_space(c, ready))
                await register_definition(connection, "lifecycle", "1", vector)
            # A table-created-before-ready interruption also resumes without DDL.
            async with db.transaction() as connection:
                await connection.execute(
                    update(SPACES).where(SPACES.c.space_id == ready["space_id"]).values(provision_state="declared")
                )
            await prepare_family_spaces(db, "lifecycle")
            publisher = ArtifactProjectionPublisher(db)
            await write(db, publisher, "scope", "lifecycle", "preserved", body("retained", [1, 0]))
            builder = ArtifactIndexBuilder(db)
            await builder.refresh("scope", "lifecycle")
            assert await builder.build("scope", "lifecycle")
            found = await service(db, "lifecycle").search(
                "scope",
                "lifecycle",
                {
                    "mode": "vector",
                    "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]},
                },
            )
            assert [item.artifact_id for item in found.artifacts] == ["preserved"]
            return ready

    async def server_registration():
        import httpx
        from pydantic import SecretStr

        from powercontext.builtin.runtime.config import RuntimeConfig
        from powercontext.server.factory import create_server_app
        from powercontext.server.settings import (
            BearerAuthConfig,
            DashboardConfig,
            McpConfig,
            MetricsConfig,
            ServerSettings,
        )
        from tests.e2e.test_artifact_plugins import ADMIN_AUTHORIZATION_VALUE, EXAMPLE, deploy_plugin, reload_plugin
        from tests.e2e.test_artifact_vector_http import _DECLARATION

        settings = ServerSettings(
            database=backend.config,
            workspace=tmp_path,
            artifact_plugins_dir=tmp_path / "plugins",
            runtime=RuntimeConfig(artifact_processing_families=()),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(ADMIN_AUTHORIZATION_VALUE)),
            dashboard=DashboardConfig(enabled=False),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
        app = create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"},
            ) as client,
            migration_profile(backend.config) as profile,
        ):
            plugin = deploy_plugin(EXAMPLE, tmp_path / "http-plugin", None)
            entry = plugin / "plugin.py"
            entry.write_text(entry.read_text() + _DECLARATION.replace("features-v1", "online-http-profile"))
            await reload_plugin(client, tmp_path, plugin, 0)
            async with asyncio.timeout(30):
                while True:
                    async with profile.database.transaction() as connection:
                        spaces = (await connection.execute(select(SPACES))).mappings().all()
                        if any(
                            row["profile_json"]["profile_id"] == "online-http-profile"
                            and row["provision_state"] == "ready"
                            for row in spaces
                        ):
                            assert (
                                await connection.execute(select(STATES).where(STATES.c.family == "incident-report"))
                            ).first() is None
                            break
                    await asyncio.sleep(0.1)

    asyncio.run(server_registration())
    ready = asyncio.run(exercise())
    next_bundle = _next_fixed_revision(runner.bundle, tmp_path / "next")
    upgrade = MySQLMigrationRunner(
        backend.connections(),
        next_bundle,
        evidence_directory=backend.evidence_directory,
        configuration=runner.configuration,
    )
    plan = upgrade.plan(backup_policy="manual")
    assert ready["physical_table"] in plan.affected_objects

    async def concurrent_registration():
        async with migration_profile(backend.config) as profile:
            db = profile.database
            other_search = declaration(profile=PROFILE.model_copy(update={"profile_id": "later-profile"}))

            async def register(family):
                async with db.transaction() as connection:
                    await connection.execute(
                        insert(SCHEMAS).values(
                            family=family,
                            schema_version="1",
                            declaration={
                                "family": family,
                                "schema_version": "1",
                                "search": other_search.model_dump(mode="json"),
                            },
                        )
                    )
                    await register_definition(connection, family, "1", other_search.indexes[1])

            await asyncio.gather(register("other-a"), register("other-b"))
            await asyncio.gather(prepare_family_spaces(db, "other-a"), prepare_family_spaces(db, "other-b"))
            async with db.transaction() as connection:
                assert len((await connection.execute(select(SPACES))).all()) == 3

    asyncio.run(concurrent_registration())
    with pytest.raises(MigrationError, match="plan_changed"):
        upgrade.apply(
            plan_id=plan.plan_id,
            accepted=True,
            maintenance_confirmed=True,
            backup_policy="manual",
            backup_confirmed=True,
        )
    before = backend.connections().run(lambda c, _i, _g: mysql_inventory(c))
    result = _apply(upgrade, policy="manual")
    assert result.changed and result.backup_state == "user_confirmed"
    after = backend.connections().run(lambda c, _i, _g: mysql_inventory(c))
    assert before["artifact_spaces"] == after["artifact_spaces"]
    assert before["tables"][ready["physical_table"]] == after["tables"][ready["physical_table"]]

    # Ready damage is rejected, never recreated by online preparation.
    async def damage():
        async with migration_profile(backend.config) as profile:
            found = await service(profile.database, "lifecycle").search(
                "scope",
                "lifecycle",
                {
                    "mode": "vector",
                    "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]},
                },
            )
            assert [item.artifact_id for item in found.artifacts] == ["preserved"]
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql(f"DROP TABLE `{ready['physical_table']}`")
            with pytest.raises(ValueError, match="absent"):
                await prepare_family_spaces(profile.database, "lifecycle")

    asyncio.run(damage())
    with pytest.raises(MigrationError):
        upgrade.plan(backup_policy="manual")


def test_managed_readiness_requires_durable_plugin_binding(tmp_path):
    from sqlalchemy import create_engine, text

    from tests.test_managed_maintenance_evidence import _managed

    path, _settings, _runner = _managed(tmp_path, None)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(SCHEMAS).values(
                    family="lifecycle",
                    schema_version="1",
                    declaration={"family": "lifecycle", "schema_version": "1"},
                )
            )
            connection.exec_driver_sql(
                "INSERT INTO pc_artifact_processing_binding_states "
                "(binding_name,plugin_id,plugin_family,desired_state,phase) "
                "VALUES ('custom-handler','plugin','lifecycle','unloaded','unloaded')"
            )
            connection.exec_driver_sql(
                "INSERT INTO pc_artifact_processing_intents "
                "(binding_name,scope_id,pending_sequence,dirty_generation,clean_generation,requested_generation,"
                "handled_generation,last_auto_scan_generation) VALUES ('custom-handler','scope',1,0,0,1,0,0)"
            )
        with engine.connect() as connection:
            check_server_schema(connection, _startup=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE pc_artifact_processing_binding_states SET plugin_family='undeclared' "
                    "WHERE binding_name='custom-handler'"
                )
            )
        with engine.connect() as connection, pytest.raises(MigrationError, match="unsupported_task_format"):
            check_server_schema(connection, _startup=True)
    finally:
        engine.dispose()
