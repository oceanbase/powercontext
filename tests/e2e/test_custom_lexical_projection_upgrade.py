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

"""Real backends upgrade reconstructed v1 custom projections, not an old installed package."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy

import httpx
from pydantic import SecretStr
from sqlalchemy import delete, insert, select, update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence import artifact_indexes as indexes
from powercontext.builtin.persistence.custom_artifacts import custom_artifact_repository
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    MetricsConfig,
    ServerSettings,
)
from tests.builtin.persistence.test_artifact_indexes import PROFILE, body
from tests.e2e.atomic_memory_migration_backend import migration_profile
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_artifact_plugin_search import _PIPELINE, _write_report
from tests.e2e.test_artifact_plugins import (
    ADMIN_AUTHORIZATION_VALUE,
    EXAMPLE,
    _wait_for_search_ready,
    create_scope,
    deploy_plugin,
    enable_all,
    reload_plugin,
)
from tests.e2e.test_artifact_vector_http import _DECLARATION


@asynccontextmanager
async def _server(settings, directory):
    app = create_server_app(settings=settings, scheduler_path=directory / "scheduler.db")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"},
            timeout=30,
        ) as client,
    ):
        yield client


async def _snapshot(backend):
    async with migration_profile(backend.config) as profile, profile.database.transaction() as connection:
        artifacts = tuple(
            (
                await connection.execute(select(ARTIFACTS_TABLE).order_by(*ARTIFACTS_TABLE.primary_key.columns))
            ).mappings()
        )
        definitions = tuple(
            (
                await connection.execute(select(indexes.DEFINITIONS).order_by(indexes.DEFINITIONS.c.index_def_id))
            ).mappings()
        )
        vectors = []
        vector_ids = []
        for definition in definitions:
            if definition["space_id"] is None:
                continue
            vector_ids.append(definition["index_def_id"])
            table = await indexes.projection_table(connection, definition)
            rows = (
                await connection.execute(
                    select(table).where(table.c.index_def_id == definition["index_def_id"]).order_by(table.c.vector_id)
                )
            ).mappings()
            for value in rows:
                row = dict(value)
                if not isinstance(row["embedding"], bytes):
                    row["embedding"] = tuple(float(item) for item in row["embedding"])
                vectors.append(row)
        coverage = tuple(
            (
                await connection.execute(
                    select(indexes.COVERAGE)
                    .where(indexes.COVERAGE.c.index_def_id.in_(vector_ids))
                    .order_by(*indexes.COVERAGE.primary_key.columns)
                )
            ).mappings()
        )
        return {"artifacts": artifacts, "definitions": definitions, "vectors": vectors, "vector_coverage": coverage}


async def _reconstruct_v1(backend):
    # Only rebuildable rows/metadata are changed to the published v1 format.
    # The current package created the authoritative records and vector space.
    async with migration_profile(backend.config) as profile, profile.database.transaction() as connection:
        states = (await connection.execute(select(indexes.STATES))).mappings().all()
        for state in states:
            manifest = deepcopy(state["manifest"])
            for schema, entry in indexes.entries(manifest):
                current = dict(
                    (
                        await connection.execute(
                            select(indexes.DEFINITIONS).where(
                                indexes.DEFINITIONS.c.index_def_id == entry["index_def_id"]
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                if current["space_id"] is not None:
                    continue
                previous = deepcopy(current)
                previous["projection_spec"]["analyzer_version"] = "jieba-0.42.1-words-v1"
                previous["definition_fingerprint"] = indexes.digest(previous["projection_spec"])
                identity = {
                    key: previous[key]
                    for key in ("family", "schema_version", "index_name", "projection_version", "space_id")
                }
                previous["index_def_id"] = indexes.digest({**identity, "spec": previous["projection_spec"]})
                await connection.execute(insert(indexes.DEFINITIONS).values(**previous))
                repository = custom_artifact_repository(state["family"])
                for table in (indexes.TEXTS, indexes.COVERAGE):
                    rows = (
                        (
                            await connection.execute(
                                select(table).where(
                                    indexes.projection_key(
                                        table,
                                        state["scope_id"],
                                        state["family"],
                                        state["build_id"],
                                        current["index_def_id"],
                                    )
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    for row in rows:
                        artifact = await repository.get(
                            connection,
                            state["scope_id"],
                            ArtifactRef(
                                family=state["family"], artifact_id=row["artifact_id"], revision=row["revision"]
                            ),
                        )
                        content = artifact.content.content
                        values = {"index_def_id": previous["index_def_id"]}
                        if table is indexes.TEXTS:
                            # Every fixture field contains ordinary ASCII words >= 3 characters,
                            # whose v1 document representation was the literal lowercase word.
                            values.update(
                                text_id=indexes.digest((
                                    state["scope_id"],
                                    state["family"],
                                    row["artifact_id"],
                                    row["revision"],
                                    schema,
                                    previous["index_def_id"],
                                    state["build_id"],
                                    row["fragment_id"],
                                )),
                                searchable_text=" ".join(
                                    str(content[field]).casefold() for field in previous["projection_spec"]["fields"]
                                ),
                                input_hash=indexes.digest({
                                    "projection": previous["definition_fingerprint"],
                                    "part": content,
                                }),
                            )
                        else:
                            values["projection_input_hash"] = indexes.digest({
                                "projection": previous["definition_fingerprint"],
                                "content": content,
                            })
                        await connection.execute(
                            update(table)
                            .where(*(column == row[column.name] for column in table.primary_key.columns))
                            .values(**values)
                        )
                entry["index_def_id"] = previous["index_def_id"]
                await connection.execute(
                    delete(indexes.DEFINITIONS).where(indexes.DEFINITIONS.c.index_def_id == current["index_def_id"])
                )
            await connection.execute(
                update(indexes.STATES)
                .where(indexes.key(indexes.STATES, state["scope_id"], state["family"]))
                .values(manifest=manifest)
            )


def test_managed_custom_lexical_upgrade_preserves_public_search_and_vectors(migration_backend):
    directory = migration_backend.directory
    (directory / "plugins").mkdir()
    settings = ServerSettings(
        database=migration_backend.config,
        workspace=directory,
        artifact_plugins_dir=directory / "plugins",
        runtime=RuntimeConfig(artifact_processing_families=()),
        auth=BearerAuthConfig(token=SecretStr(ADMIN_AUTHORIZATION_VALUE)),
        access=AccessControlConfig(mode="enforced"),
        dashboard=DashboardConfig(enabled=False),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
    )
    prepare_server_database(settings, evidence_dir=directory / "initialization")
    vector_request = {
        "mode": "vector",
        "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1.0, 0.0]},
        "include_scores": True,
    }

    async def seed():
        async with _server(settings, directory) as client:
            scope = await create_scope(client, "custom-lexical-maintenance")
            plugin = deploy_plugin(EXAMPLE, directory / "report-plugin", [scope])
            entry = plugin / "plugin.py"
            entry.write_text(entry.read_text() + _DECLARATION + _PIPELINE, encoding="utf-8")
            await reload_plugin(client, directory, plugin, 0)
            await enable_all(client)
            first = await _write_report(
                client, scope, "first", "report", "the www retiredmarker", fragments=body("", [0.0, 1.0])["fragments"]
            )
            await _wait_for_search_ready(client, scope, "retiredmarker")
            current = await _write_report(
                client,
                scope,
                "second",
                "report",
                "the www currentmarker",
                revision=2,
                fragments=body("", [1.0, 0.0])["fragments"],
            )
            response = await client.post(f"/v1/scopes/{scope}/artifacts/incident-report/search", json=vector_request)
            assert response.status_code == 200, response.text
            assert [(item["artifact_id"], item["revision"]) for item in response.json()["results"]] == [("report", 2)]
            return scope, first, current, response.json()

    scope, first, current, vector_result = asyncio.run(seed())
    original = asyncio.run(_snapshot(migration_backend))
    asyncio.run(_reconstruct_v1(migration_backend))
    previous = asyncio.run(_snapshot(migration_backend))
    assert all(previous[key] == original[key] for key in ("artifacts", "vectors", "vector_coverage"))
    endpoint = f"/v1/scopes/{scope}/artifacts/incident-report/search"

    async def verify_unready_text():
        async with _server(settings, directory) as client:
            for query in ('"the"', '"www"'):
                response = await client.post(endpoint, json={"query": query, "mode": "text"})
                assert response.status_code == 503, response.text
                assert response.json()["error"]["code"] == "artifact_search_unavailable"
            response = await client.post(endpoint, json=vector_request)
            assert response.status_code == 200, response.text
            assert response.json() == vector_result

    asyncio.run(verify_unready_text())
    maintenance = deployment_runner(
        settings, bundle="lexical-projection", evidence_dir=directory / "maintenance", lock_coordination="single-host"
    )
    plan = maintenance.plan(backup_policy="skip")
    assert plan.state == "migration_required"
    result = maintenance.apply(
        plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy="skip", accept_no_backup=True
    )
    assert result.changed and maintenance.verify().state == "ready"
    assert not maintenance.apply(backup_policy="skip").changed
    upgraded = asyncio.run(_snapshot(migration_backend))
    assert all(upgraded[key] == previous[key] for key in ("artifacts", "vectors", "vector_coverage"))
    assert all(definition in upgraded["definitions"] for definition in previous["definitions"])
    assert len(upgraded["definitions"]) == len(previous["definitions"]) + 1

    async def verify_recovered_search():
        async with _server(settings, directory) as client:
            for query in ('"the"', '"www"', "www", "currentmarker"):
                response = await client.post(endpoint, json={"query": query, "mode": "text"})
                assert response.status_code == 200, response.text
                assert [
                    (item["artifact_id"], item["revision"], item["content"]) for item in response.json()["results"]
                ] == [("report", 2, current["content"])]
            response = await client.post(endpoint, json={"query": "retiredmarker", "mode": "text"})
            assert response.status_code == 200 and response.json()["results"] == []
            response = await client.post(endpoint, json=vector_request)
            assert response.status_code == 200 and response.json() == vector_result
            for revision, expected in ((1, first), (2, current)):
                response = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/report/revisions/{revision}")
                assert response.status_code == 200, response.text
                assert response.json()["content"] == expected["content"]
                assert response.json()["revision"] == revision

    asyncio.run(verify_recovered_search())
