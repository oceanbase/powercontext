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

"""Custom Artifact search through generation, revisions, unload and index recovery."""

import asyncio
import json
import shutil
from pathlib import Path

from powercontext.builtin.persistence.artifact_indexes import ArtifactIndexBuilder
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from tests.e2e.test_artifact_plugins import (
    EXAMPLE,
    REGISTRATION,
    _wait_for_search_ready,
    change,
    create_scope,
    deploy_plugin,
    enable_all,
    plugin_state,
    publish_plugin,
    reload_plugin,
    running_server,
)

_PIPELINE = """

class SearchPipeline:
    def __init__(self, ctx):
        self.ctx = ctx

    async def generate(self, batch):
        drafts = []
        for source in batch.sources:
            value = json.loads(TextEvidence.model_validate(source.value).content)
            artifact_id = value.pop("artifact_id")
            previous = await self.ctx.read_latest("incident-report", artifact_id)
            drafts.append(ArtifactDraft(
                family="incident-report", schema_version=families[0].schema_version,
                artifact_id=artifact_id,
                expected_revision=0 if previous is None else previous.ref.revision,
                content=value, source_refs=(source.ref,),
            ))
        return drafts

async def create_pipeline(ctx, config):
    return SearchPipeline(ctx)
"""


async def _write_report(client, scope, source_id, artifact_id, body, *, title="Report", revision=1, fragments=None):
    response = await client.post(
        "/v1/sources/content",
        json={
            "scope_id": scope,
            "source_id": source_id,
            "content": json.dumps(
                {
                    "artifact_id": artifact_id,
                    "title": title,
                    "evidence_text": body,
                    "private_text": "不可检索 secretword",
                    **({"fragments": fragments} if fragments is not None else {}),
                },
                ensure_ascii=False,
            ),
        },
    )
    assert response.status_code == 202, response.text
    async with asyncio.timeout(45):
        while True:
            page = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report")
            assert page.status_code == 200, page.text
            if any(
                item["artifact_id"] == artifact_id and item["revision"] == revision for item in page.json()["items"]
            ):
                response = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/{artifact_id}")
                assert response.status_code == 200, response.text
                return response.json()
            await asyncio.sleep(0.2)


async def _search(client, scope, query):
    response = await client.post(f"/v1/scopes/{scope}/artifacts/incident-report/search", json={"query": query})
    assert response.status_code == 200, response.text
    return response.json()


def test_custom_search_preserves_full_content_and_rebuilds_retained_revisions(tmp_path: Path) -> None:
    asyncio.run(_search_through_lifecycle(tmp_path))


async def _search_through_lifecycle(directory: Path) -> None:
    async with running_server(directory) as client:
        scope = await create_scope(client, "search")
        other_scope = await create_scope(client, "other-search")
        plugin = deploy_plugin(EXAMPLE, directory / "search-v1", [scope, other_scope])
        entry = plugin / "plugin.py"
        entry.write_text(
            entry.read_text().replace(
                "class IncidentReport(BaseModel):", "class IncidentReport(BaseModel):\n    private_text: str"
            )
            + _PIPELINE,
            encoding="utf-8",
        )
        await reload_plugin(client, directory, plugin, 0)
        await enable_all(client)
        state = await plugin_state(client, "enabled")
        removed = await client.post(f"/v1/scopes/{scope}/artifacts/incident-report/query", json={"query": "数据库"})
        assert removed.status_code == 405  # The remaining GET /{artifact_id} route has no POST operation.
        for invalid in (
            {"query": "数据库", "limit": 201},
            {"query": "数据库", "indexes": {"text": []}},
            {"query": "数据库", "mode": "fts"},
        ):
            response = await client.post(f"/v1/scopes/{scope}/artifacts/incident-report/search", json=invalid)
            assert response.status_code == 422, response.text

        original = "数据库连接超时 TIMEOUT，重试后恢复。"  # noqa: RUF001
        first = await _write_report(client, scope, "first", "report", original, title="故障记录")
        # The first write creates the durable build request; empty scopes no longer
        # receive prebuilt releases when the plugin is loaded.
        await _wait_for_search_ready(client, scope, "数据库")
        for query in ("数据库", "连接超时", "timeout", "数据库 timeout", "故障记录"):
            hits = (await _search(client, scope, query))["results"]
            assert [{key: hit[key] for key in ("family", "artifact_id", "revision")} for hit in hits] == [
                {"family": "incident-report", "artifact_id": "report", "revision": 1}
            ]
            assert hits[0]["content"] == first["content"]
            assert set(hits[0]) == {"family", "artifact_id", "revision", "content", "lineage"}
        for query in ("不存在的英文token", "secretword", "不可检索", "!!!"):
            assert (await _search(client, scope, query))["results"] == []
        assert (await _search(client, other_scope, "数据库"))["results"] == []

        # Normalization can change code point counts; excerpts must still use the exact original.
        for index, (needle, query) in enumerate((
            ("磁盘告警", "磁盘告警"),
            ("Straße", "strasse"),
            ("Cafe\u0301", "café"),
            ("customer_id", "customer_id"),
            ("pcu123", "pcu123"),
        )):
            body = "prefix ß e\u0301. " * 80 + needle + " suffix." * 80
            await _write_report(client, scope, f"long-{index}", f"long-{index}", body)
            hits = (await _search(client, scope, query))["results"]
            assert len(hits) == 1
            assert hits[0]["content"]["evidence_text"] == body
        for query in ("cafe", "customer", "id"):
            assert (await _search(client, scope, query))["results"] == []

        # Replacing a head must remove its old words while retaining the exact historical revision.
        await _write_report(client, scope, "updated", "report", "网络恢复正常 recovered", revision=2)
        assert (await _search(client, scope, "数据库 timeout"))["results"] == []
        assert (await _search(client, scope, "网络恢复"))["results"][0]["revision"] == 2
        historical = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/report/revisions/1")
        assert historical.status_code == 200
        assert historical.json()["content"] == first["content"]

        # A schema bump must not bypass the fixed search declaration.
        replacement = deploy_plugin(plugin, directory / "search-v2", [scope, other_scope], version="2")
        entry = replacement / "plugin.py"
        entry.write_text(
            entry
            .read_text()
            .replace('schema_version="1"', 'schema_version="2"')
            .replace('fields=("title", "evidence_text")', 'fields=("evidence_text",)'),
            encoding="utf-8",
        )
        publish_plugin(directory, replacement)
        rejected = await client.post(
            f"{REGISTRATION}/reload", json={"expected_generation": state["binding_generation"]}
        )
        assert rejected.status_code == 409, rejected.text
        assert (await _search(client, scope, "网络恢复"))["results"][0]["revision"] == 2
        # Code-only reload remains supported with exactly the same search declaration.
        entry.write_text((plugin / "plugin.py").read_text())
        state = await reload_plugin(client, directory, replacement, state["binding_generation"], "enabled")
        await _write_report(client, scope, "after-reload", "after-reload", "缓存刷新 complete")
        await _wait_for_search_ready(client, scope, "缓存刷新")
        disabled = await change(client, "disable", state["binding_generation"], "disabled")
        assert (await _search(client, scope, "缓存刷新"))["results"][0]["content"][
            "evidence_text"
        ] == "缓存刷新 complete"
        await change(client, "unload", disabled["binding_generation"], "unloaded")
        before = {
            query: await _search(client, scope, query)
            for query in ("网络恢复", "磁盘告警", "strasse", "café", "customer_id", "pcu123", "缓存刷新")
        }
        saved = (await client.get(f"/v1/scopes/{scope}/artifacts/incident-report")).json()

    # Persist a rebuild request, then recover it without either plugin's code.
    async with SQLiteProfile.open(
        SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'server.db'}"), tables=BUILTIN_TABLES
    ) as storage:
        await ArtifactIndexBuilder(storage.database).refresh(scope, "incident-report", rebuild=True)
    shutil.rmtree(plugin)
    shutil.rmtree(replacement)
    async with running_server(directory) as client:
        assert (await client.get(REGISTRATION)).json()["phase"] == "unloaded"
        assert (await client.get(f"/v1/scopes/{scope}/artifacts/incident-report")).json() == saved
        # Server startup no longer waits for rebuilding retained data. Only the
        # affected search can be unavailable until its complete manifest is published.
        await _wait_for_search_ready(client, scope, "网络恢复")
        for query, expected in before.items():
            assert await _search(client, scope, query) == expected
        assert (await _search(client, scope, "数据库 timeout hiddenfield secretword"))["results"] == []
        assert (await client.get(f"/v1/scopes/{scope}/artifacts/incident-report")).json() == saved


def test_pending_vector_index_suspends_all_search_but_not_generation(tmp_path):
    async def run():
        async with running_server(tmp_path) as client:
            scope = await create_scope(client, "pending-search")
            plugin = deploy_plugin(EXAMPLE, tmp_path / "initial", [scope])
            entry = plugin / "plugin.py"
            entry.write_text(
                entry.read_text().replace(
                    "class IncidentReport(BaseModel):", "class IncidentReport(BaseModel):\n    private_text: str"
                )
                + _PIPELINE
            )
            entry.write_text(
                entry.read_text()
                + """
from dataclasses import replace
from powercontext.artifact_plugins import VectorIndex
from powercontext.builtin.artifacts.memory import EmbeddingProfile
families = (replace(families[0], search=ArtifactSearchDeclaration(
    version="2", indexes=(*families[0].search.indexes, VectorIndex(
        name="semantic", projection_version="1", source="text", fields=("evidence_text",),
        profile=EmbeddingProfile(profile_id="unavailable", model="unconfigured", dimension=2),
    )), modes=("text", "vector"), default_mode="text",
)),)
"""
            )
            await reload_plugin(client, tmp_path, plugin, 0)
            await enable_all(client)
            await plugin_state(client, "enabled")
            await _write_report(client, scope, "first", "report", "outage")
            response = await client.post(
                f"/v1/scopes/{scope}/artifacts/incident-report/search", json={"mode": "vector", "query": "outage"}
            )
            assert response.status_code == 503
            response = await client.post(
                f"/v1/scopes/{scope}/artifacts/incident-report/search", json={"query": "outage"}
            )
            assert response.status_code == 503
            latest = await _write_report(client, scope, "second", "report", "recovered", revision=2)
            assert latest["revision"] == 2
            historical = await client.get(f"/v1/scopes/{scope}/artifacts/incident-report/report/revisions/1")
            assert historical.status_code == 200, historical.text
            assert historical.json()["revision"] == 1
            assert historical.json()["content"]["evidence_text"] == "outage"
            response = await client.post(
                f"/v1/scopes/{scope}/artifacts/incident-report/search", json={"query": "recovered"}
            )
            assert response.status_code == 503

    asyncio.run(run())


def test_index_definitions_stay_fixed_while_query_policy_survives_reload_and_restart(tmp_path):
    from tests.e2e.test_artifact_plugins import artifacts, capture

    def deploy(version, profile_id, index_name="visual"):
        plugin = deploy_plugin(EXAMPLE, tmp_path / version, None, version=version)
        entry = plugin / "plugin.py"
        entry.write_text(
            entry
            .read_text()
            .replace(
                "class IncidentReport(BaseModel):",
                "from powercontext.builtin.artifacts.memory import EmbeddingProfile\n"
                f'VECTOR_PROFILE = EmbeddingProfile(profile_id="{profile_id}", model="fixture", dimension=2)\n'
                "class IncidentReport(BaseModel):\n    vector: list[float]\n    profile: EmbeddingProfile",
            )
            .replace('title=f"', 'vector=[1.0, 0.0], profile=VECTOR_PROFILE, title=f"')
            + f'''
from dataclasses import replace
from powercontext.artifact_plugins import VectorIndex
families = (replace(families[0], search=ArtifactSearchDeclaration(
    version="1", indexes=(*families[0].search.indexes, VectorIndex(
        name="{index_name}", projection_version="1", source="content",
        vector_field="vector", profile_field="profile", profile=VECTOR_PROFILE,
    )), modes=("text", "vector", "hybrid"), default_mode="text",
)),)
'''
        )
        return plugin

    async def run():
        async with running_server(tmp_path) as client:
            scope = await create_scope(client, "profile-admission")
            first = deploy("1", "features-v1")
            state = await reload_plugin(client, tmp_path, first, 0)
            # No content exists yet; the declaration is nevertheless already fixed.
            second = deploy("2", "features-v2")
            publish_plugin(tmp_path, second)
            rejected = await client.post(
                f"{REGISTRATION}/reload", json={"expected_generation": state["binding_generation"]}
            )
            assert rejected.status_code == 409, rejected.text
            assert (await client.get(REGISTRATION)).json() == state
            publish_plugin(tmp_path, first)
            await enable_all(client)
            state = await plugin_state(client, "enabled")
            await capture(client, scope, "original")
            items = await artifacts(client, scope, 1)
            await _wait_for_search_ready(client, scope, "Outage")
            state = await change(client, "disable", state["binding_generation"], "disabled")
            before = await _search(client, scope, "Outage")
            vector_request = {
                "mode": "vector",
                "query_vector": {
                    "profile": before["results"][0]["content"]["profile"],
                    "vector": [1.0, 0.0],
                },
            }
            url = f"/v1/scopes/{scope}/artifacts/incident-report/search"
            vector_before = await client.post(url, json=vector_request)
            assert vector_before.status_code == 200, vector_before.text
            assert vector_before.json()["results"]
            # Neither profile, name, projection version, schema identity nor index membership may change.
            edits = (
                ("features-v1", "features-v2"),
                ('name="visual"', 'name="renamed"'),
                ('projection_version="1"', 'projection_version="2"'),
                ('schema_version="1"', 'schema_version="2"'),
                ('version="1", indexes=', 'version="2", indexes='),
                ("indexes=(*families[0].search.indexes, VectorIndex(", "indexes=(VectorIndex("),
            )
            for number, (old, new) in enumerate(edits, start=3):
                replacement = deploy(str(number), "features-v1")
                entry = replacement / "plugin.py"
                assert old in entry.read_text()
                code = entry.read_text().replace(old, new)
                if old.startswith("indexes="):
                    code = code.replace(")), modes=", "),), modes=")
                    code = code.replace(
                        'modes=("text", "vector", "hybrid"), default_mode="text"',
                        'modes=("vector",), default_mode="vector"',
                    )
                entry.write_text(code)
                publish_plugin(tmp_path, replacement)
                rejected = await client.post(
                    f"{REGISTRATION}/reload", json={"expected_generation": state["binding_generation"]}
                )
                assert rejected.status_code == 409, rejected.text
                assert (await client.get(REGISTRATION)).json() == state
                assert await _search(client, scope, "Outage") == before
                unchanged = await client.post(url, json=vector_request)
                assert unchanged.status_code == 200, unchanged.text
                assert unchanged.json() == vector_before.json()
                assert await artifacts(client, scope, 1) == items
            policy = deploy("query-policy", "features-v1")
            entry = policy / "plugin.py"
            entry.write_text(
                entry.read_text().replace(
                    'modes=("text", "vector", "hybrid"), default_mode="text",',
                    'modes=("text", "vector"), default_mode="vector", '
                    'defaults={"text": {"text": ("lexical",)}, "vector": {"vector": ("visual",)}}, '
                    "candidate_limit=17, rrf_k=11, text_weight=2, vector_weight=3,",
                )
            )
            state = await reload_plugin(client, tmp_path, policy, state["binding_generation"])
            default_vector = {"query_vector": vector_request["query_vector"]}
            result = await client.post(url, json=default_vector)
            assert result.status_code == 200, result.text
            assert result.json() == vector_before.json()
            assert (await client.post(url, json={"mode": "text", "query": "Outage"})).json() == before
            assert (await client.post(url, json={"mode": "hybrid", "query": "Outage"})).status_code == 422
            assert await artifacts(client, scope, 1) == items
            # Invalid policy must not replace the last accepted configuration.
            entry.write_text(entry.read_text().replace('("visual",)}', '("missing",)}'))
            publish_plugin(tmp_path, policy)
            invalid = await client.post(
                f"{REGISTRATION}/reload", json={"expected_generation": state["binding_generation"]}
            )
            assert invalid.status_code == 422, invalid.text
            assert (await client.get(REGISTRATION)).json() == state
            assert (await client.post(url, json=default_vector)).json() == vector_before.json()
            state = await change(client, "unload", state["binding_generation"], "unloaded")
        async with running_server(tmp_path) as client:
            # Unload clears the runtime definition, but the persisted contract still rejects changes.
            publish_plugin(tmp_path, second)
            rejected = await client.post(
                f"{REGISTRATION}/reload", json={"expected_generation": state["binding_generation"]}
            )
            assert rejected.status_code == 409, rejected.text
            assert (await client.get(REGISTRATION)).json() == state
            assert (await client.post(url, json={"mode": "text", "query": "Outage"})).json() == before
            assert (await client.post(url, json=default_vector)).json() == vector_before.json()
            assert (await client.post(url, json={"mode": "hybrid", "query": "Outage"})).status_code == 422
            compatible = deploy("code-only", "features-v1")
            state = await reload_plugin(client, tmp_path, compatible, state["binding_generation"])
            assert await _search(client, scope, "Outage") == before
            assert (await client.get(REGISTRATION)).json()["binding_generation"] == state["binding_generation"]

    asyncio.run(run())
