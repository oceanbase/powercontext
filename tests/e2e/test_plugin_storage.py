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

"""A plugin's dedicated storage follows the Server's database backend, in the host process and in Workers."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import make_url, text

from tests.e2e.plugin_server import capture, create_scope, running_server, wait_for
from tests.plugin_support import write_plugin

LEDGER = """
from sqlalchemy import text

from powercontext.plugins import ApiRequest, PluginApi, Rendering, Route


class Ledger:
    def __init__(self, ctx):
        self.ctx = ctx

    async def record(self, entry, origin):
        async with self.ctx.storage.engine().begin() as connection:
            await connection.execute(
                text("INSERT INTO entries (id, origin) VALUES (:id, :origin)"), {"id": entry, "origin": origin}
            )

    async def render_artifact(self, request):
        return Rendering(text=request.artifact.content["text"])

    async def generate(self, request, writer):
        # Worker processes open the same storage as the host process.
        for source in request.sources:
            await self.record(source.ref.source_id, "worker")
        async with writer.transaction() as tx:
            for source in request.sources:
                await tx.put(writer.stable_id(source.ref.source_id), content={"text": "x"}, source_refs=[source.ref])

    async def add(self, request: ApiRequest):
        await self.record(request.path_params["entry"], "host")

    async def entries(self, request: ApiRequest):
        async with self.ctx.storage.engine().connect() as connection:
            rows = (await connection.execute(text("SELECT id, origin FROM entries ORDER BY id"))).all()
        return {"backend": self.ctx.storage.backend, "entries": [[row.id, row.origin] for row in rows]}


async def setup(ctx):
    async with ctx.storage.engine().begin() as connection:
        await connection.execute(
            text("CREATE TABLE IF NOT EXISTS entries (id VARCHAR(64) PRIMARY KEY, origin VARCHAR(16) NOT NULL)")
        )
    return Ledger(ctx)
"""

API = """PluginApi(routes=(
    Route(method="POST", path="/entries/{entry}", handler="add", access="authenticated"),
    Route(method="GET", path="/entries", handler="entries", access="authenticated"),
))"""


async def _create_database(url: str, name: str) -> None:
    from powercontext.builtin.persistence.bootstrap import ensure_database
    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
    from powercontext.builtin.persistence.oceanbase.profile import sibling_database_engines

    engine, tenant_engine = sibling_database_engines(OceanBaseConfig(url=SecretStr(url)), name)
    try:
        await ensure_database(engine, tenant_engine, name=name)
    finally:
        await engine.dispose()


def _migrate(url: str, evidence) -> None:
    from powercontext.builtin.persistence.migrations.deployment import deployment_runner
    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
    from powercontext.server.settings import ServerSettings

    runner = deployment_runner(
        ServerSettings(database=OceanBaseConfig(url=SecretStr(url))),
        evidence_dir=evidence,
        lock_coordination="single-host",
    )
    plan = runner.plan(backup_policy="skip")
    applied = runner.apply(
        plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy="skip", accept_no_backup=True
    )
    assert applied.state == "ready"


async def _drop_databases(url: str, *names: str) -> None:
    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
    from powercontext.builtin.persistence.oceanbase.profile import sibling_database_engines

    engine, tenant_engine = sibling_database_engines(OceanBaseConfig(url=SecretStr(url)), names[0])
    await engine.dispose()
    tenant = tenant_engine()
    try:
        async with tenant.begin() as connection:
            for name in names:
                await connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
    finally:
        await tenant.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "seekdb", "oceanbase"])
def test_plugin_storage_is_shared_by_host_and_workers_and_survives_a_restart(tmp_path, backend):
    environment = {}
    # An embedded seekdb instance takes over a minute to start for the first time.
    startup_seconds = 60 if backend == "sqlite" else 300
    name, url, databases = "ledger", "", ()
    if backend == "seekdb":
        if os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1":
            pytest.skip("real seekdb acceptance not enabled")
        pytest.importorskip("pylibseekdb")
        environment["POWERCONTEXT_SERVER_DATABASE"] = json.dumps({"kind": "seekdb", "path": str(tmp_path / "seekdb")})
    if backend == "oceanbase":
        tenant = os.environ.get("POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL")
        if not tenant:
            pytest.skip("real OceanBase acceptance not enabled")
        # The tenant outlives the test, so the core database and the plugin's database get names of their own.
        run = uuid4().hex[:8]
        name = f"ledger-{run}"
        databases = (f"pc_plugin_storage_{run}", f"powercontext_plugin_ledger_{run}")
        url = make_url(tenant).set(database=databases[0]).render_as_string(hide_password=False)
        environment["POWERCONTEXT_SERVER_DATABASE"] = json.dumps({"kind": "oceanbase", "url": url})
    plugins = tmp_path / "plugins"
    directory = write_plugin(plugins, name, LEDGER)
    entry = directory / "plugin.py"
    # The helper's declaration takes literal fields only; the routes are appended to it here.
    entry.write_text(entry.read_text(encoding="utf-8").replace("    setup=setup,", f"    setup=setup,\n    api={API},"))
    log = tmp_path / "server.log"

    async def entries(client):
        response = await client.get(f"/v1/plugins/{name}/api/entries")
        assert response.status_code == 200, response.text
        return response.json()

    async def scenario():
        async with running_server(tmp_path, plugins, extra_env=environment, startup_seconds=startup_seconds) as client:
            added = await client.post(f"/v1/plugins/{name}/api/entries/from-route")
            assert added.status_code in {200, 204}, added.text
            scope = await create_scope(client, "plugin-storage")
            await capture(client, scope, "from-source", "anything")
            stored = await wait_for(lambda: entries(client), lambda value: len(value["entries"]) == 2, log=log)
            assert stored == {"backend": backend, "entries": [["from-route", "host"], ["from-source", "worker"]]}

        async with running_server(tmp_path, plugins, extra_env=environment, startup_seconds=startup_seconds) as client:
            assert (await entries(client))["entries"] == [["from-route", "host"], ["from-source", "worker"]]

    try:
        if databases:
            # A remote database is created and migrated explicitly; the Server creates only the plugin's.
            asyncio.run(_create_database(url, databases[0]))
            _migrate(url, tmp_path / "migration-evidence")
        asyncio.run(scenario())
    finally:
        if databases:
            asyncio.run(_drop_databases(url, *databases))
