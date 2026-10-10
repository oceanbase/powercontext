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

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryStateValue
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


class _Embedding:
    profile = EmbeddingProfile(profile_id="snapshot", model="test", dimension=3, distance="l2", normalization="unit")

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))


@pytest.fixture(params=("sqlite", "oceanbase"))
def database(request, tmp_path: Path) -> Iterator[SQLiteConfig | OceanBaseConfig]:
    if request.param == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-snapshot.db'}")
        return
    configured_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not configured_url:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL with test database creation and deletion privileges")
    configured = OceanBaseConfig(url=SecretStr(configured_url))
    name = f"pc_snapshot_{uuid4().hex}"

    async def execute(statement: str) -> None:
        async with (
            OceanBaseProfile.open(configured, tables=()) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(statement)

    asyncio.run(execute(f"CREATE DATABASE `{name}`"))
    try:
        url = make_url(configured_url).set(database=name).render_as_string(hide_password=False)
        yield OceanBaseConfig(url=SecretStr(url))
    finally:
        asyncio.run(execute(f"DROP DATABASE `{name}`"))


@pytest.mark.parametrize("mode", ["text", "vector", "hybrid"])
def test_atomic_search_keeps_candidates_and_authority_in_one_snapshot(database, mode: str, monkeypatch) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=database), embedding_model=_Embedding()) as contexts:
            await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            (created,) = await contexts.records.create_atomic_memories(
                "project", ({"kind": "fact", "text": "Alpha beta original body."},)
            )
            index = contexts.atomic_memory.index
            original_search = index.search
            changed = False

            async def interleaved_search(connection, scope_id, request):
                nonlocal changed
                channels = await original_search(connection, scope_id, request)
                if not changed:
                    changed = True
                    revised = await asyncio.create_task(
                        contexts.records.replace_artifact(
                            "project",
                            "atomic-memory",
                            created.artifact_id,
                            '"revision:1"',
                            ArtifactWrite(content={"kind": "fact", "text": "Alpha beta revised body."}),
                        )
                    )
                    assert revised.revision == 2
                return channels

            with monkeypatch.context() as patch:
                patch.setattr(index, "search", interleaved_search)
                first = await asyncio.wait_for(memory.search("alpha", mode=mode), timeout=20)
            assert changed
            assert len(first.hits) == 1
            assert first.hits[0].hit.artifact_ref.revision == 1
            assert first.hits[0].text == "Alpha beta original body."
            current = await memory.search("alpha", mode=mode)
            assert len(current.hits) == 1
            assert current.hits[0].hit.artifact_ref.revision == 2
            assert current.hits[0].text == "Alpha beta revised body."

    asyncio.run(scenario())


def test_atomic_reads_do_not_commit_the_in_memory_writer() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            with pytest.raises(RuntimeError, match="abort outer write"):
                async with contexts.database.transaction():
                    (created,) = await contexts.records.create_atomic_memories(
                        "project", ({"kind": "fact", "text": "Alpha pending write."},)
                    )
                    record = await memory.get(created.artifact_id)
                    assert record.artifact.revision == 1
                    page = await memory.list()
                    assert [item.ref for item in page.items] == [record.ref]
                    search = await memory.search("alpha", mode="text")
                    assert [item.hit.artifact_ref for item in search.hits] == [record.ref]
                    raise RuntimeError("abort outer write")  # noqa: TRY003
            assert (await memory.list()).items == ()
            assert (await memory.search("alpha", mode="text")).hits == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["get", "list"])
def test_atomic_read_keeps_content_and_state_in_one_snapshot(database, operation: str, monkeypatch) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=database)) as contexts:
            await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            (created,) = await contexts.records.create_atomic_memories(
                "project", ({"kind": "fact", "text": "Original body."},)
            )
            repository = contexts.atomic_memory.service.artifacts
            latest = repository.latest
            changed = False

            async def interleaved_latest(connection, scope_id, family, artifact_id, **kwargs):
                nonlocal changed
                value = await latest(connection, scope_id, family, artifact_id, **kwargs)
                if not changed and artifact_id == created.artifact_id:
                    changed = True

                    async def publish() -> None:
                        await contexts.records.replace_artifact(
                            "project",
                            "atomic-memory",
                            created.artifact_id,
                            '"revision:1"',
                            ArtifactWrite(content={"kind": "fact", "text": "Revised body."}),
                        )
                        await memory.forget(created.artifact_id, expected_revision=2, expected_state_version=0)

                    await asyncio.create_task(publish())
                return value

            with monkeypatch.context() as patch:
                patch.setattr(repository, "latest", interleaved_latest)
                if operation == "get":
                    first = await asyncio.wait_for(memory.get(created.artifact_id), timeout=20)
                else:
                    page = await asyncio.wait_for(memory.list(), timeout=20)
                    assert len(page.items) == 1
                    first = page.items[0]
            assert changed
            assert first.ref.revision == 1
            assert first.artifact.content.text == "Original body."
            assert first.state.state is AtomicMemoryStateValue.ACTIVE
            assert first.state.state_version == 0
            current = await memory.get(created.artifact_id)
            assert current.ref.revision == 2
            assert current.artifact.content.text == "Revised body."
            assert current.state.state is AtomicMemoryStateValue.FORGOTTEN
            assert current.state.state_version == 1
            assert (await memory.list()).items == ()

    asyncio.run(scenario())
