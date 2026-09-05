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

"""Opt-in real .env model/database acceptance with disposable storage.

Run: uv run pytest tests/e2e/test_real_artifact_tags.py --run-real-e2e -q
OceanBase requires permission to create and drop an isolated test database.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.http._generated.operations import CAPTURE_CONTENT_SOURCE
from powercontext.server.configuration import server_settings_context
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.test_artifact_tags import exercise_tag_http


async def _generated_journey(settings: ServerSettings) -> None:
    app = create_server_app(settings=settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=120) as client,
    ):
        created = await client.post(
            "/v1/scopes",
            json={
                "title": "Real tag generation",
                "summary": "Isolated provider acceptance",
                "idempotency_key": uuid4().hex,
            },
        )
        assert created.status_code == 201, "scope creation failed"
        scope = created.json()["scope_id"]
        source = await client.post(
            CAPTURE_CONTENT_SOURCE.path,
            json={
                "scope_id": scope,
                "source_id": "engineering-rule",
                "content": "For this project, always run the compatibility test suite before every release. This is a permanent engineering rule, not a temporary task.",
            },
        )
        assert source.status_code == CAPTURE_CONTENT_SOURCE.success_status, (
            f"source capture status {source.status_code}"
        )
        flushed = await client.post("/v1/memory/flush", json={"scope_id": scope})
        assert flushed.status_code == 200, f"real generation status {flushed.status_code}"
        listed = await client.post("/v1/memory/entries/list", json={"scope_id": scope})
        assert listed.status_code == 200
        entries = listed.json()["entries"]
        assert entries, "real model produced no durable entries"
        entry = entries[0]
        citation = entry["citation"]
        path = f"/v1/scopes/{scope}/artifacts/memory/{citation['memory_ref']['artifact_id']}/entries/{citation['entry_id']}/tags"
        current = await client.get(path)
        assigned = await client.put(
            path, json={"tags": ["real-generated", "客户验收"]}, headers={"If-Match": current.headers["ETag"]}
        )
        assert assigned.status_code == 200
        for mode in ("fts", "vector", "hybrid"):
            result = await client.post(
                "/v1/memory/search",
                json={
                    "scope_id": scope,
                    "query": entry["text"],
                    "mode": mode,
                    "limit": 1,
                    "tag_filter": {"tags": ["REAL-GENERATED"]},
                },
            )
            assert result.status_code == 200, f"{mode} search status {result.status_code}"
            assert [item["citation"]["entry_id"] for item in result.json()["hits"]] == [citation["entry_id"]], (
                f"{mode} lost eligible generated entry"
            )
        print(
            json.dumps({
                "database": settings.database.kind,
                "real_generation": "passed",
                "generated_entries": len(entries),
                "real_embedding": "passed",
                "tagged_search_modes": ["fts", "vector", "hybrid"],
            }),
            flush=True,
        )


@pytest.mark.parametrize("backend", ["oceanbase", "sqlite"])
def test_real_models_and_tags(backend: str, tmp_path: Path, pytestconfig: pytest.Config) -> None:
    if not pytestconfig.getoption("run_real_e2e"):
        pytest.skip("pass --run-real-e2e to use .env models and disposable databases")
    env_file = pytestconfig.getoption("real_e2e_env_file")
    with server_settings_context(env_file=env_file, data_dir=tmp_path / "state") as configured:
        assert configured.inference.generation_model and configured.inference.embedding_model

        async def exercise(database: OceanBaseConfig | SQLiteConfig) -> None:
            settings = configured.model_copy(
                update={
                    "database": database,
                    "mcp": McpConfig(enabled=False),
                    "auth": configured.auth.model_copy(update={"enabled": False}),
                }
            )
            await exercise_tag_http(create_server_app(settings=settings))
            await _generated_journey(settings)

        async def scenario() -> None:
            if backend == "sqlite":
                await exercise(SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'real-tags.db'}"))
                return
            assert isinstance(configured.database, OceanBaseConfig)
            database_name = "pc_tag_accept_" + uuid4().hex[:16]
            async with OceanBaseProfile.open(configured.database, tables=()) as admin:
                async with admin.database.transaction() as connection:
                    await connection.exec_driver_sql(
                        f"CREATE DATABASE `{database_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                    )
                try:
                    url = make_url(configured.database.url.get_secret_value()).set(database=database_name)
                    await exercise(OceanBaseConfig(url=SecretStr(url.render_as_string(hide_password=False))))
                finally:
                    async with admin.database.transaction() as connection:
                        await connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")

        asyncio.run(scenario())
