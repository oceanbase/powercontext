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

"""Generic writes remain visible through family reads, tags and publication."""

import asyncio
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUnavailableError, InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex
from powercontext.builtin.persistence.tag_schema import ensure_topic_memory_tag_schema
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.statistics import RelationalScopedStatistics
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


class Embeddings:
    profile = EmbeddingProfile(profile_id="topic-api", model="test", dimension=3)
    unavailable = False

    async def embed(self, texts, /):
        if self.unavailable:
            raise InferenceUnavailableError("embed")
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


class UsageEmbeddings(Embeddings):
    async def embed(self, texts, /):
        result = await super().embed(texts)
        return result.model_copy(update={"usage": InferenceUsage(requests=1, input_tokens=len(texts), output_tokens=0)})


def _app(tmp_path, embedding=None, *, embedding_timeout=30.0):
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topics.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            inference=InferenceConfig(embedding_timeout_seconds=embedding_timeout),
            auth=BearerAuthConfig(enabled=False),
            access=AccessControlConfig(mode="disabled"),
            mcp=McpConfig(enabled=False),
        ),
        embedding_model=embedding,
        scheduler_path=tmp_path / "scheduler.db",
    )


def _scope(client, name):
    response = client.post("/v1/scopes", json={"title": name, "summary": name, "idempotency_key": name})
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


def _content(word):
    return {"title": word, "summary": f"{word} recovery", "detail": f"# {word}\nDurable {word} recovery."}


def _create(client, scope, word):
    response = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "topic-memory", "content": _content(word)})
    assert response.status_code == 201, response.text
    return response


@pytest.mark.parametrize("vector", [False, True])
def test_topic_lifecycle_tags_filtered_pages_and_publication_survive_restart(tmp_path, vector):
    embedding = Embeddings() if vector else None
    with TestClient(_app(tmp_path, embedding)) as client:
        scope, target = _scope(client, "source"), _scope(client, "target")
        created = _create(client, scope, "amber")
        path = created.headers["Location"]
        ref = {key: created.json()[key] for key in ("family", "artifact_id", "revision")}
        tags = client.get(path + "/tags")
        tagged = client.put(
            path + "/tags", headers={"If-Match": tags.headers["ETag"]}, json={"tags": ["Chosen", "运维"]}
        )
        assert tagged.status_code == 200, tagged.text
        assert (
            client.put(path + "/tags", headers={"If-Match": tags.headers["ETag"]}, json={"tags": []}).status_code == 412
        )
        assert client.put(path, json={"content": _content("cobalt")}).status_code == 428
        revised = client.put(path, headers={"If-Match": created.headers["ETag"]}, json={"content": _content("cobalt")})
        assert revised.status_code == 200, revised.text
        assert revised.json()["revision"] == 2
        assert (
            client.put(
                path, headers={"If-Match": created.headers["ETag"]}, json={"content": _content("stale")}
            ).status_code
            == 412
        )
        assert client.get(path + "/revisions/1").json()["content"] == _content("amber")
        assert [item["revision"] for item in client.get(path + "/revisions").json()["items"]] == [2, 1]
        assert client.get(path + "/tags").headers["ETag"] == tagged.headers["ETag"]
        second = _create(client, scope, "silver")
        second_path = second.headers["Location"]
        empty = client.get(second_path + "/tags")
        assert (
            client.put(
                second_path + "/tags", headers={"If-Match": empty.headers["ETag"]}, json={"tags": ["chosen"]}
            ).status_code
            == 200
        )
        _create(client, scope, "unmatched")
        listing = f"/v1/scopes/{scope}/artifacts/topic-memory"
        first = client.get(listing, params={"tag": "CHOSEN", "limit": 1}).json()
        assert first["next_cursor"] and len(first["items"]) == 1
        assert first["items"][0]["artifact_id"] == second.json()["artifact_id"]
        after = {"tag": "chosen", "limit": 1, "cursor": first["next_cursor"]}
        final = client.get(listing, params=after).json()
        assert final["next_cursor"] is None and final["items"][0]["artifact_id"] == ref["artifact_id"]
        assert final["items"][0]["source_count"] == 1
        assert client.get(listing, params=after | {"tag": "other"}).status_code == 400
        assert client.get(listing, params=after | {"tag_match": "any"}).status_code == 400
        assert client.get(f"/v1/scopes/{target}/artifacts/topic-memory", params=after).status_code == 400
        all_tags = client.get(listing, params=[("tag", "chosen"), ("tag", "运维")]).json()["items"]
        assert [item["artifact_id"] for item in all_tags] == [ref["artifact_id"]]
        assert (
            len(client.get(listing, params=[("tag", "chosen"), ("tag", "运维"), ("tag_match", "any")]).json()["items"])
            == 2
        )
        query = client.post(f"/v1/scopes/{scope}/artifact-tags/query", json={"tags": ["chosen"]})
        assert query.status_code == 200 and len(query.json()["items"]) == 2, query.text
        request = {
            "source": {"scope_id": scope, "artifact": ref},
            "target_scope_id": target,
            "idempotency_key": "publish",
        }
        published = client.post("/v1/artifact-publications", json=request)
        assert published.status_code == 201, published.text
        target_ref = published.json()["target"]["artifact"]
        if embedding:
            embedding.unavailable = True
        assert client.post("/v1/artifact-publications", json=request).json() == published.json()
        conflict = request | {"source": {"scope_id": scope, "artifact": ref | {"revision": 2}}}
        assert client.post("/v1/artifact-publications", json=conflict).status_code == 409
        if embedding:
            failed = client.post(
                f"/v1/scopes/{target}/artifacts", json={"family": "topic-memory", "content": _content("failure")}
            )
            assert failed.status_code == 503, failed.text
            embedding.unavailable = False
        published_list = client.get(f"/v1/scopes/{target}/artifacts/topic-memory").json()["items"]
        assert len(published_list) == 1 and published_list[0]["source_count"] == 0
        exact = client.post("/v1/topic-memory/get", json={"scope_id": target, "artifact": target_ref})
        assert exact.status_code == 200 and exact.json()["title"] == "amber", exact.text
        search = client.post("/v1/topic-memory/search", json={"scope_id": target, "query": "amber"})
        assert search.status_code == 200 and search.json()["hits"], search.text
        assert (
            client.get(f"/v1/scopes/{target}/artifacts/topic-memory/{target_ref['artifact_id']}/tags").json()["tags"]
            == []
        )

    with TestClient(_app(tmp_path, embedding)) as client:
        assert client.get(path).json()["content"] == _content("cobalt")
        search = client.post("/v1/topic-memory/search", json={"scope_id": target, "query": "amber"})
        assert search.status_code == 200 and search.json()["hits"], search.text


@pytest.mark.parametrize("content", [{"title": "x"}, _content("x") | {"extra": 1}, _content("x") | {"title": " "}])
def test_manual_topic_rejects_incomplete_blank_and_extra_content(tmp_path, content):
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client, "invalid")
        response = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "topic-memory", "content": content})
        assert response.status_code == 422, response.text
        assert client.get(f"/v1/scopes/{scope}/artifacts/topic-memory").json()["items"] == []


def test_valid_emoji_and_punctuation_content_has_empty_lexical_projection(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        source, target = _scope(client, "lexical-source"), _scope(client, "lexical-target")
        content = {
            "title": "😀",
            "summary": "!!!",
            "detail": "Durable recovery procedures remain available.",
        }
        created = client.post(
            f"/v1/scopes/{source}/artifacts",
            json={"family": "topic-memory", "content": content},
        )
        assert created.status_code == 201, created.text
        path = created.headers["Location"]
        assert client.get(path).json()["content"] == content
        search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": source, "query": "Durable"},
        )
        assert search.status_code == 200 and search.json()["hits"], search.text
        replaced = client.put(
            path,
            headers={"If-Match": created.headers["ETag"]},
            json={
                "content": {
                    "title": ";",
                    "summary": "🤖",
                    "detail": "Changed recovery procedures remain available.",
                }
            },
        )
        assert replaced.status_code == 200, replaced.text
        assert client.get(path).json()["content"]["title"] == ";"
        old_search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": source, "query": "Durable"},
        )
        assert old_search.status_code == 200 and old_search.json()["hits"] == []
        ref = {key: created.json()[key] for key in ("family", "artifact_id", "revision")}
        published = client.post(
            "/v1/artifact-publications",
            json={
                "source": {"scope_id": source, "artifact": ref},
                "target_scope_id": target,
                "idempotency_key": "emoji-publish",
            },
        )
        assert published.status_code == 201, published.text
        target_ref = published.json()["target"]["artifact"]
        exact = client.post(
            "/v1/topic-memory/get",
            json={"scope_id": target, "artifact": target_ref},
        )
        assert exact.status_code == 200 and exact.json()["title"] == "😀"


def test_write_embeddings_are_attributed_to_the_operation_scope(tmp_path):
    embedding = UsageEmbeddings()
    with TestClient(_app(tmp_path, embedding)) as client:
        source, target = _scope(client, "usage-source"), _scope(client, "usage-target")
        created = _create(client, source, "create")
        path = created.headers["Location"]
        replaced = client.put(
            path,
            headers={"If-Match": created.headers["ETag"]},
            json={"content": _content("replace")},
        )
        assert replaced.status_code == 200, replaced.text
        ref = {key: created.json()[key] for key in ("family", "artifact_id", "revision")}
        published = client.post(
            "/v1/artifact-publications",
            json={
                "source": {"scope_id": source, "artifact": ref},
                "target_scope_id": target,
                "idempotency_key": "usage-publish",
            },
        )
        assert published.status_code == 201, published.text

    with sqlite3.connect(tmp_path / "topics.db") as connection:
        rows = connection.execute(
            "SELECT scope_id, purpose, operation, requests, input_tokens, output_tokens "
            "FROM pc_model_usage_daily WHERE purpose = 'topic_memory_indexing' ORDER BY scope_id"
        ).fetchall()
    assert {(scope_id, purpose, operation, requests) for scope_id, purpose, operation, requests, *_ in rows} == {
        (source, "topic_memory_indexing", "embedding", 2),
        (target, "topic_memory_indexing", "embedding", 1),
    }
    assert all(input_tokens > 0 and output_tokens == 0 for _, _, _, _, input_tokens, output_tokens in rows)


def test_statistics_outage_does_not_block_topic_memory_writes(tmp_path, monkeypatch):
    async def fail_record(*args, **kwargs):
        raise RuntimeError("injected statistics storage failure")  # noqa: TRY003

    monkeypatch.setattr(RelationalScopedStatistics, "record", fail_record)
    embedding = UsageEmbeddings()
    with TestClient(_app(tmp_path, embedding)) as client:
        source, target = _scope(client, "statistics-source"), _scope(client, "statistics-target")
        created = _create(client, source, "create")
        path = created.headers["Location"]
        replaced = client.put(
            path,
            headers={"If-Match": created.headers["ETag"]},
            json={"content": _content("replace")},
        )
        assert replaced.status_code == 200, replaced.text
        ref = {key: created.json()[key] for key in ("family", "artifact_id", "revision")}
        published = client.post(
            "/v1/artifact-publications",
            json={
                "source": {"scope_id": source, "artifact": ref},
                "target_scope_id": target,
                "idempotency_key": "statistics-outage",
            },
        )
        assert published.status_code == 201, published.text
        assert client.get(path).json()["content"] == _content("replace")
        target_ref = published.json()["target"]["artifact"]
        assert client.get(f"/v1/scopes/{target}/artifacts/topic-memory/{target_ref['artifact_id']}").status_code == 200


@pytest.mark.parametrize("topic_family", [False, True])
def test_legacy_tags_survive_transactional_upgrade_and_repeated_startup(tmp_path, topic_family):
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client, "legacy")
        created = client.post(
            f"/v1/scopes/{scope}/artifacts",
            json={
                "family": "experience",
                "content": {"situation": "Legacy", "action": "Upgrade", "outcome": "Preserved", "lesson": "Keep tags"},
            },
        )
        assert created.status_code == 201, created.text
        path = created.headers["Location"] + "/tags"
        empty = client.get(path)
        tagged = client.put(path, headers={"If-Match": empty.headers["ETag"]}, json={"tags": ["客户A", "Release"]})
        assert tagged.status_code == 200, tagged.text
    database = tmp_path / "topics.db"
    # Reproduce the previous release's constraint, retaining its persisted tag rows.
    with sqlite3.connect(database) as connection:
        ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'pc_artifact_tags'").fetchone()[0]
        rows = connection.execute("SELECT * FROM pc_artifact_tags").fetchall()
        connection.execute("DROP TABLE pc_artifact_tags")
        families = "'memory', 'experience', 'skill', 'handoff'" + (", 'topic-memory'" if topic_family else "")
        connection.execute(
            ddl.replace(
                "CONSTRAINT ck_pc_artifact_tags_target",
                f"CONSTRAINT ck_pc_artifact_tags_family CHECK (family IN ({families})), "
                "CONSTRAINT ck_pc_artifact_tags_target",
            )
        )
        connection.executemany("INSERT INTO pc_artifact_tags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    class InterruptedUpgrade(Exception):
        pass

    async def interrupted_upgrade():
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        try:
            with pytest.raises(InterruptedUpgrade):
                async with engine.begin() as connection:
                    await ensure_topic_memory_tag_schema(connection)
                    raise InterruptedUpgrade
        finally:
            await engine.dispose()

    asyncio.run(interrupted_upgrade())
    with sqlite3.connect(database) as connection:
        assert (
            "ck_pc_artifact_tags_family"
            in connection.execute("SELECT sql FROM sqlite_master WHERE name = 'pc_artifact_tags'").fetchone()[0]
        )
        assert connection.execute("SELECT * FROM pc_artifact_tags").fetchall() == rows
    for _ in range(2):
        with TestClient(_app(tmp_path)) as client:
            current = client.get(path)
            assert current.json() == tagged.json()
            assert current.headers["ETag"] == tagged.headers["ETag"]
            topic = _create(client, scope, "upgraded")
            topic_tags = topic.headers["Location"] + "/tags"
            empty = client.get(topic_tags)
            assert (
                client.put(
                    topic_tags, headers={"If-Match": empty.headers["ETag"]}, json={"tags": ["Release"]}
                ).status_code
                == 200
            )
    with sqlite3.connect(database) as connection:
        ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'pc_artifact_tags'").fetchone()[0]
        assert "ck_pc_artifact_tags_family" not in ddl
        assert "ck_pc_artifact_tags_target" in ddl
        assert connection.execute("PRAGMA foreign_key_list('pc_artifact_tags')").fetchall()
        assert any(row[5] for row in connection.execute("PRAGMA table_info('pc_artifact_tags')"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('pc_artifact_tags')")}
        assert {"ix_pc_artifact_tags_family_key", "ix_pc_artifact_tags_key"} <= indexes


def test_prepared_replace_rechecks_head_and_publication_races_are_idempotent(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class DelayedEmbeddings(Embeddings):
            async def embed(self, texts, /):
                if texts[0].startswith("slow\n"):
                    entered.set()
                    await release.wait()
                return await super().embed(texts)

        app = _app(tmp_path, DelayedEmbeddings())
        competing_app = _app(tmp_path, Embeddings())
        async with (
            app.router.lifespan_context(app),
            competing_app.router.lifespan_context(competing_app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=competing_app), base_url="http://test"
            ) as competing_client,
        ):
            scopes = []
            for name in ("race-source", "race-target"):
                result = await client.post("/v1/scopes", json={"title": name, "summary": name, "idempotency_key": name})
                scopes.append(result.json()["scope_id"])
            source, target = scopes
            created = await client.post(
                f"/v1/scopes/{source}/artifacts", json={"family": "topic-memory", "content": _content("initial")}
            )
            assert created.status_code == 201, created.text
            path = created.headers["Location"]
            headers = {"If-Match": created.headers["ETag"]}
            slow = asyncio.create_task(client.put(path, headers=headers, json={"content": _content("slow")}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                winner = await asyncio.wait_for(
                    competing_client.put(path, headers=headers, json={"content": _content("winner")}), 5
                )
                assert winner.status_code == 200, winner.text
            finally:
                release.set()
            assert (await slow).status_code == 412
            assert (await client.get(path)).json()["content"] == _content("winner")
            assert len((await client.get(path + "/revisions")).json()["items"]) == 2
            ref = {key: winner.json()[key] for key in ("family", "artifact_id", "revision")}
            request = {
                "source": {"scope_id": source, "artifact": ref},
                "target_scope_id": target,
                "idempotency_key": "race",
            }
            results = await asyncio.gather(
                *(worker.post("/v1/artifact-publications", json=request) for worker in (client, competing_client))
            )
            assert [result.status_code for result in results] == [201, 201]
            assert results[0].json() == results[1].json()
            assert len((await client.get(f"/v1/scopes/{target}/artifacts/topic-memory")).json()["items"]) == 1

    asyncio.run(scenario())


def test_index_failure_rolls_back_create_replace_and_publication(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path), raise_server_exceptions=False) as client:
        source, target = _scope(client, "atomic-source"), _scope(client, "atomic-target")
        created = _create(client, source, "original")
        path = created.headers["Location"]
        ref = {key: created.json()[key] for key in ("family", "artifact_id", "revision")}
        request = {
            "source": {"scope_id": source, "artifact": ref},
            "target_scope_id": target,
            "idempotency_key": "atomic",
        }
        original = SQLiteTopicMemoryFTSIndex.replace

        async def fail_after_index_write(self, *args, **kwargs):
            await original(self, *args, **kwargs)
            raise RuntimeError("injected index storage failure")  # noqa: TRY003

        with sqlite3.connect(tmp_path / "topics.db") as connection:
            source_count = connection.execute("SELECT count(*) FROM pc_sources").fetchone()[0]
        with monkeypatch.context() as patch:
            patch.setattr(SQLiteTopicMemoryFTSIndex, "replace", fail_after_index_write)
            failed_create = client.post(
                f"/v1/scopes/{source}/artifacts", json={"family": "topic-memory", "content": _content("failed")}
            )
            assert failed_create.status_code == 500
            assert (
                client.put(
                    path, headers={"If-Match": created.headers["ETag"]}, json={"content": _content("failed")}
                ).status_code
                == 500
            )
            assert client.post("/v1/artifact-publications", json=request).status_code == 500
        assert client.get(path).json()["content"] == _content("original")
        assert len(client.get(path + "/revisions").json()["items"]) == 1
        assert len(client.get(f"/v1/scopes/{source}/artifacts/topic-memory").json()["items"]) == 1
        assert client.get(f"/v1/scopes/{target}/artifacts/topic-memory").json()["items"] == []
        search = client.post("/v1/topic-memory/search", json={"scope_id": source, "query": "original"})
        assert search.status_code == 200 and search.json()["hits"]
        with sqlite3.connect(tmp_path / "topics.db") as connection:
            assert connection.execute("SELECT count(*) FROM pc_sources").fetchone()[0] == source_count
            assert connection.execute("SELECT count(*) FROM pc_artifact_publications").fetchone()[0] == 0
        assert client.post("/v1/artifact-publications", json=request).status_code == 201


def test_manual_embedding_obeys_configured_timeout_without_writing(tmp_path):
    class SlowEmbeddings(Embeddings):
        async def embed(self, texts, /):
            if texts[0].startswith("blocked\n"):
                await asyncio.Event().wait()
            return await super().embed(texts)

    with TestClient(_app(tmp_path, SlowEmbeddings(), embedding_timeout=0.02)) as client:
        scope = _scope(client, "timeout")
        failed = client.post(
            f"/v1/scopes/{scope}/artifacts", json={"family": "topic-memory", "content": _content("blocked")}
        )
        assert failed.status_code == 503, failed.text
        assert client.get(f"/v1/scopes/{scope}/artifacts/topic-memory").json()["items"] == []
        _create(client, scope, "recovered")
