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

"""Installed Server and spawned generation Worker share text/vector/hybrid search."""

import asyncio

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from tests.builtin.persistence.test_artifact_indexes import PROFILE, body
from tests.e2e.test_artifact_plugin_search import _PIPELINE, _write_report
from tests.e2e.test_artifact_plugins import (
    EXAMPLE,
    create_scope,
    deploy_plugin,
    enable_all,
    reload_plugin,
    running_server,
)

_DECLARATION = """
from powercontext.artifact_plugins import ArtifactSearchDeclaration, TextIndex, VectorIndex, FragmentProjection
from powercontext.builtin.artifacts.memory import EmbeddingProfile

class Feature(BaseModel):
    id: str
    vector: list[float]
    profile: EmbeddingProfile

class SearchReport(IncidentReport):
    private_text: str
    fragments: list[Feature]

families = (ArtifactFamily(
    family="incident-report", schema_version="1", content_model=SearchReport,
    search=ArtifactSearchDeclaration(
        version="1", modes=("text", "vector", "hybrid"), default_mode="text",
        indexes=(
            TextIndex(name="lexical", fields=("title", "evidence_text"), projection_version="1"),
            VectorIndex(name="visual", projection_version="1", source="content",
                profile=EmbeddingProfile(profile_id="features-v1", model="fixture", dimension=2, normalization="none"),
                vector_field="vector", profile_field="profile",
                fragments=FragmentProjection(array_field="fragments", id_field="id")),
        ),
    ),
),)
inputs = InputContract(projections=inputs.projections, artifact_families=("experience", "skill", "profile"))
"""


def test_vector_search_through_http_and_spawned_worker(migration_backend):
    config, directory = migration_backend.config, migration_backend.directory
    database = config.model_dump(mode="json")
    if isinstance(config, OceanBaseConfig):
        database["url"] = config.url.get_secret_value()

    async def exercise():
        async with running_server(directory, database_settings=database) as client:
            scope = await create_scope(client, "vector-search")
            plugin = deploy_plugin(EXAMPLE, directory / "vector-plugin", [scope])
            entry = plugin / "plugin.py"
            pipeline = _PIPELINE.replace(
                "        drafts = []",
                """        # These use the built-in Searchers, not the custom-family declaration parser.
        assert not (await self.ctx.search_artifacts("experience", "Outage")).matches
        assert not (await self.ctx.search_artifacts("skill", "Outage")).matches
        found = await self.ctx.search_artifacts("incident-report", "Report")
        assert all("evidence_text" in artifact.content.model_dump() for artifact in found.artifacts)
        for artifact in found.artifacts:
            await self.ctx.read_revision(artifact.as_ref())
        drafts = []""",
            ).replace(
                "content=value, source_refs=(source.ref,),",
                "content=value, source_refs=(source.ref,), artifact_refs=tuple(artifact.as_ref() for artifact in found.artifacts),",
            )
            entry.write_text(entry.read_text() + _DECLARATION + pipeline, encoding="utf-8")
            await reload_plugin(client, directory, plugin, 0)
            await enable_all(client)
            first = await _write_report(
                client, scope, "first", "a", "数据库 timeout", fragments=body("", *([[0, 0]] * 8))["fragments"]
            )
            second = await _write_report(
                client, scope, "second", "b", "数据库 normal", fragments=body("", [1, 0])["fragments"]
            )
            assert second["artifacts"] == [{"family": "incident-report", "artifact_id": "a", "revision": 1}]
            endpoint = f"/v1/scopes/{scope}/artifacts/incident-report/search"
            vector = {"profile": PROFILE.model_dump(mode="json"), "vector": [0, 0]}
            request = {"mode": "vector", "query_vector": vector, "limit": 2, "include_scores": True}
            for mode in ("vector", "hybrid"):
                response = await client.post(
                    endpoint, json={**request, "mode": mode, **({"query": "数据库"} if mode == "hybrid" else {})}
                )
                assert response.status_code == 200, response.text
                results = response.json()["results"]
                assert [result["artifact_id"] for result in results] == ["a", "b"]
                assert results[0]["content"] == first["content"]
                assert set(results[0]) == {"family", "artifact_id", "revision", "content", "lineage", "scores"}
            for invalid in (
                {"query": "Report", "indexes": {"vector": ["visual"]}},
                {"query": "Report", "indexes": {"text": []}},
                {**request, "query_vector": {**vector, "vector": [1]}},
                {
                    **request,
                    "mode": "hybrid",
                    "query": "Report",
                    "fusion": {"method": "rrf", "params": {"weights": {"text": 0, "vector": 0}}},
                },
            ):
                response = await client.post(endpoint, json=invalid)
                assert response.status_code == 422, response.text
            unavailable = await client.post(endpoint, json={"mode": "vector", "query": "Report"})
            assert unavailable.status_code == 503
            denied = await client.post(endpoint, json=request, headers={"Authorization": ""})
            assert denied.status_code == 401
            await _write_report(client, scope, "update", "a", "recovered", revision=2, fragments=[])
            response = await client.post(endpoint, json=request)
            assert [item["artifact_id"] for item in response.json()["results"]] == ["b"]
            historical = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/a/revisions/1")
            assert historical.json()["content"] == first["content"]

    asyncio.run(exercise())
