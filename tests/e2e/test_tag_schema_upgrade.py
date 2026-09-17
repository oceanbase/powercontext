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

"""Real SeekDB/OceanBase legacy-database startup with disposable storage.

Embedded SeekDB requires powercontext[seekdb]. For a server backend, set
POWERCONTEXT_TEST_OCEANBASE_URL to a server allowing isolated database creation.
The server URL can point to OceanBase or standalone SeekDB.
"""

import asyncio
import importlib.util
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


@asynccontextmanager
async def _isolated_database(backend, tmp_path):
    if backend == "seekdb":
        if importlib.util.find_spec("pylibseekdb") is None:
            pytest.skip("install powercontext[seekdb] for the real embedded backend")
        yield SeekDBConfig(path=tmp_path / "seekdb"), SeekDBProfile.open
        return
    live_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not live_url:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL for the real server backend")
    name = "pc_tag_upgrade_" + uuid4().hex[:16]
    async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(live_url)), tables=()) as admin:
        async with admin.database.transaction() as connection:
            await connection.exec_driver_sql(f"CREATE DATABASE `{name}`")
        try:
            url = make_url(live_url).set(database=name).render_as_string(hide_password=False)
            yield OceanBaseConfig(url=SecretStr(url)), OceanBaseProfile.open
        finally:
            async with admin.database.transaction() as connection:
                await connection.exec_driver_sql(f"DROP DATABASE `{name}`")


@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
@pytest.mark.parametrize("topic_family", [False, True])
def test_real_legacy_tags_survive_repeated_server_startup(backend, topic_family, tmp_path):
    async def scenario():
        async with _isolated_database(backend, tmp_path) as (database, open_profile):
            settings = ServerSettings(
                database=database,
                runtime=RuntimeConfig(artifact_processing_families=()),
                auth=BearerAuthConfig(enabled=False),
                access=AccessControlConfig(mode="disabled"),
                mcp=McpConfig(enabled=False),
            )

            @asynccontextmanager
            async def client():
                app = create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
                ):
                    yield http

            # Seed ordinary usage without creating any Topic Memory artifacts.
            async with client() as http:
                scope = await http.post(
                    "/v1/scopes", json={"title": "Legacy", "summary": "Tags", "idempotency_key": "legacy"}
                )
                assert scope.status_code == 201, scope.text
                scope_id = scope.json()["scope_id"]
                created = await http.post(
                    f"/v1/scopes/{scope_id}/artifacts",
                    json={
                        "family": "experience",
                        "content": {"situation": "Legacy", "action": "Upgrade", "outcome": "Kept", "lesson": "Tags"},
                    },
                )
                assert created.status_code == 201, created.text
                path = created.headers["Location"] + "/tags"
                empty = await http.get(path)
                tagged = await http.put(
                    path, headers={"If-Match": empty.headers["ETag"]}, json={"tags": ["客户A", "Release"]}
                )
                assert tagged.status_code == 200, tagged.text

            # Reproduce either the old release or a previously upgraded whitelist.
            async with (
                open_profile(database, tables=()) as profile,
                profile.database.transaction() as connection,
            ):
                ddl = (await connection.exec_driver_sql("SHOW CREATE TABLE pc_artifact_tags")).one()[1]
                assert "ck_pc_artifact_tags_family" not in ddl
                families = "'memory', 'experience', 'skill', 'handoff'" + (", 'topic-memory'" if topic_family else "")
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_artifact_tags ADD CONSTRAINT "
                    f"ck_pc_artifact_tags_family CHECK (family IN ({families}))"
                )

            # First startup still never uses Topic Memory; the second tests retry.
            for restart in range(2):
                async with client() as http:
                    current = await http.get(path)
                    assert current.status_code == 200, current.text
                    assert current.json() == tagged.json()
                    assert current.headers["ETag"] == tagged.headers["ETag"]
                    if restart:
                        topic = await http.post(
                            f"/v1/scopes/{scope_id}/artifacts",
                            json={
                                "family": "topic-memory",
                                "content": {"title": "New", "summary": "New", "detail": "New"},
                            },
                        )
                        assert topic.status_code == 201, topic.text
                        topic_path = topic.headers["Location"] + "/tags"
                        empty = await http.get(topic_path)
                        assigned = await http.put(
                            topic_path, headers={"If-Match": empty.headers["ETag"]}, json={"tags": ["Release"]}
                        )
                        assert assigned.status_code == 200, assigned.text
            async with (
                open_profile(database, tables=()) as profile,
                profile.database.transaction() as connection,
            ):
                ddl = (await connection.exec_driver_sql("SHOW CREATE TABLE pc_artifact_tags")).one()[1]
                assert "ck_pc_artifact_tags_family" not in ddl
                assert "ck_pc_artifact_tags_target" in ddl
                assert "PRIMARY KEY" in ddl and "FOREIGN KEY" in ddl
                assert "ix_pc_artifact_tags_family_key" in ddl and "ix_pc_artifact_tags_key" in ddl

    asyncio.run(scenario())
