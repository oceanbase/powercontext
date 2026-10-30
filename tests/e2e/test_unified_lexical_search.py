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

# ruff: noqa: RUF001 - actual multilingual question forms.
"""One public recall contract on real SQLite, seekdb and OceanBase databases."""

import asyncio

import httpx
import pytest

from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    PrepareContextRequest,
    PreparedContext,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

FACT = "本项目的 PowerContext Server 固定在 127.0.0.1:8000；测试写入。"
QUESTIONS = (
    "PowerContext Server 地址",
    "PowerContext Server 地址是什么？",
    "我之前让你记住的 PowerContext Server 地址是什么？",
)

STOPWORD_PREFIX = (
    "i me my myself we us our ours ourselves you your yours yourself yourselves he him his himself "
    "she her hers herself it its itself they them their theirs themselves what which who whom this "
    "that these those am is are was were be been being have has had having do does did doing will "
    "would shall should can could may might must ought"
)


@pytest.mark.parametrize("evidence", ["deploymentmarker", '"the"', "not"])
def test_prepared_topic_recall_preserves_evidence_after_stopwords(migration_backend, evidence):
    async def scenario():
        config = BuiltinConfig(
            database=migration_backend.config, runtime=RuntimeConfig(artifact_processing_families=())
        )
        async with open_builtin_runtime(config, scheduler_path=migration_backend.directory / "scheduler") as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(
                        title="Bounded recall", summary="Preserve effective query evidence", idempotency_key="bounded"
                    )
                )
            ).scope_id
            original = await runtime.records.for_scope(scope).create_artifact(
                "topic-memory",
                ArtifactWrite(
                    content={"title": "deploymentmarker", "summary": "the not", "detail": "Durable deployment evidence"}
                ),
            )

            result = await runtime.context.for_scope(scope).prepare(
                PrepareContextRequest.model_validate({
                    "query": f"{STOPWORD_PREFIX} {evidence}",
                    "max_bytes": 8192,
                    "assembly": {"sections": [{"family": "topic-memory", "limit": 8}]},
                })
            )
            assert isinstance(result, PreparedContext)

            assert result.status == "ready"
            assert result.content is not None and original.artifact_id in result.content

    asyncio.run(scenario())


def test_public_word_recall_identifiers_and_prepare(migration_backend):
    async def scenario():
        app = create_server_app(
            settings=ServerSettings(
                database=migration_backend.config,
                runtime=RuntimeConfig(artifact_processing_families=()),
                mcp=McpConfig(enabled=False),
            ),
            scheduler_path=migration_backend.directory / "scheduler",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client,
        ):

            async def scope(label):
                response = await client.post(
                    "/v1/scopes",
                    json={
                        "title": label,
                        "summary": "Lexical acceptance",
                        "idempotency_key": label,
                    },
                )
                response.raise_for_status()
                return response.json()["scope_id"]

            async def remember(scope_id, text):
                response = await client.post(
                    "/v1/memory/remember",
                    json={
                        "scope_id": scope_id,
                        "kind": "fact",
                        "text": text,
                    },
                )
                response.raise_for_status()
                return response.json()["records"][0]["artifact"]

            primary = await scope("primary")
            fact_ref = await remember(primary, FACT)
            await remember(primary, "Dinner recipe baking instructions.")
            identifier_ref = await remember(primary, "alpha u_7684")
            long_identifier = "service_" + "a" * 200
            await remember(primary, long_identifier)
            prohibition = await remember(primary, "不要保存密码")
            await remember(primary, "保存密码")
            operators = await remember(primary, "AND and OR operators.")
            negation = await remember(primary, "not store passwords")

            async def search(query):
                response = await client.post(
                    "/v1/memory/search",
                    json={
                        "scope_id": primary,
                        "query": query,
                        "mode": "fts",
                    },
                )
                response.raise_for_status()
                return response.json()["hits"]

            for question in QUESTIONS:
                hits = await search(question)
                assert [hit["memory"]["text"] for hit in hits] == [FACT]
            prepared = await client.post(
                "/v1/context/prepare",
                json={"scope_id": primary, "query": QUESTIONS[-1], "max_bytes": 8192},
            )
            prepared.raise_for_status()
            assert prepared.json()["status"] == "ready"
            assert "127.0.0.1:8000" in prepared.json()["content"]
            assert [hit["memory"]["artifact"] for hit in await search("u_7684")] == [identifier_ref]
            assert [hit["memory"]["artifact"] for hit in await search('"的"')] == [fact_ref]
            assert [hit["memory"]["artifact"] for hit in await search("不要")] == [prohibition]
            assert [hit["memory"]["artifact"] for hit in await search('"AND"')] == [operators]
            assert [hit["memory"]["artifact"] for hit in await search("not")] == [negation]
            assert [hit["memory"]["text"] for hit in await search(long_identifier)] == [long_identifier]
            assert await search("的 是 什么") == []

    asyncio.run(scenario())
