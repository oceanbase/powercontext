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

"""Plugin writes commit with the consumption cursor, or not at all."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from powercontext.builtin.plugins.manifest import load_plugin
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig, SQLiteConfig
from powercontext.builtin.scope import ScopeDraft
from tests.plugin_support import current, invoke, write_plugin

REPORTS = """
from powercontext.plugins import ArtifactWriter, GenerateRequest


class Reports:
    def __init__(self, ctx):
        self.fail = (ctx.plugin_dir / "fail").exists()

    async def generate(self, request: GenerateRequest, writer: ArtifactWriter) -> None:
        async with writer.transaction() as tx:
            for source in request.sources:
                text = source.projection["content"]
                ref = await tx.put(
                    writer.stable_id(source.ref.source_id), content={"text": text}, source_refs=[source.ref]
                )
                summary = await tx.get("summary")
                count = 0 if summary is None else summary.content["count"]
                await tx.put("summary", content={"count": count + 1}, source_refs=[source.ref])
                # A plugin-owned store would be written here, after the real revision is known.
                assert ref.ref.revision >= 1
            if self.fail:
                raise RuntimeError("model output rejected")


async def setup(ctx):
    return Reports(ctx)
"""


@pytest.fixture
def runtime(tmp_path):
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'plugin.db'}"))
    return config


async def _scope(contexts) -> str:
    draft = ScopeDraft(title="Plugins", summary="Plugins", idempotency_key=str(uuid4()))
    return (await contexts.scopes.create(draft)).scope_id


def test_writes_commit_with_cursor_and_failures_leave_both_unchanged(tmp_path, runtime):
    directory = write_plugin(tmp_path / "plugins", "reports", REPORTS)

    async def scenario():
        async with open_builtin_contexts(runtime) as contexts:
            database = contexts.database
            scope = await _scope(contexts)
            first = await contexts.records.create_source(scope, "content", "Deploy to east")
            await contexts.records.create_source(scope, "content", "Rotate keys")

            (directory / "fail").touch()
            with pytest.raises(RuntimeError, match="model output rejected"):
                await invoke(database, directory, scope)
            async with database.transaction() as connection:
                assert await SourceCursorRepository().load(connection, scope, "plugin:reports") is None
            with pytest.raises(RepositoryNotFoundError):
                await current(database, "reports", scope, "summary")

            (directory / "fail").unlink()
            await invoke(database, directory, scope)
            summary = await current(database, "reports", scope, "summary")
            assert summary.content.content == {"count": 2}
            assert summary.revision == 2
            # Each revision records the plugin and code version that wrote it.
            manifest, _ = load_plugin(directory)
            async with database.transaction() as connection:
                metadata = await connection.scalar(
                    select(ARTIFACTS_TABLE.c.metadata).where(
                        ARTIFACTS_TABLE.c.scope_id == scope,
                        ARTIFACTS_TABLE.c.family == "reports",
                        ARTIFACTS_TABLE.c.artifact_id == "summary",
                        ARTIFACTS_TABLE.c.revision == 2,
                    )
                )
            assert metadata is not None
            assert json.loads(metadata) == {"plugin": "reports", "version": manifest.fingerprint}
            async with database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, scope, "plugin:reports")
            assert cursor is not None and cursor.cursor.sequence == 2

            # The committed window is never delivered again.
            await invoke(database, directory, scope)
            assert (await current(database, "reports", scope, "summary")).revision == 2
            assert first.source_id

    asyncio.run(scenario())


# Each Source carries a JSON list of writer operations, so one window drives one transaction.
LIFECYCLE = """
import json

from powercontext.plugins import ArtifactWriter, GenerateRequest


class Lifecycle:
    async def generate(self, request: GenerateRequest, writer: ArtifactWriter) -> None:
        results = {}
        async with writer.transaction() as tx:
            commands = [item for source in request.sources for item in json.loads(source.projection["content"])]
            for command in commands:
                op = command["op"]
                if op == "put":
                    await tx.put(command["id"], content={"text": command["text"]})
                elif op == "merge":
                    await tx.merge(command["inputs"], into=command["into"], content={"text": command["text"]})
                elif op == "undo":
                    await tx.undo_merge(command["id"])
                elif op == "lifecycle":
                    await tx.set_lifecycle(command["id"], command["state"])
                elif op == "revert":
                    await tx.revert(command["id"], to_revision=command["revision"])
                elif op == "tags":
                    await tx.set_tags(command["id"], command["tags"])
                elif op == "handle":
                    snapshot = await tx.get(command["id"])
                    await tx.bind_handle(command["key"], snapshot.ref)
                elif op == "find":
                    # Active Artifacts whose text contains the query, read inside the open transaction.
                    page = await tx.list(limit=200)
                    results[command["query"]] = [
                        item.ref.artifact_id for item in page.items if command["query"] in item.content.get("text", "")
                    ]
            if results:
                await tx.put("search-log", content={"results": results})


async def setup(ctx):
    return Lifecycle()
"""


def test_writer_covers_shared_lifecycle_merge_and_reads(tmp_path, runtime):
    directory = write_plugin(tmp_path / "plugins", "facts", LIFECYCLE)

    async def scenario():
        async with open_builtin_contexts(runtime) as contexts:
            database = contexts.database
            scope = await _scope(contexts)

            async def run(database, scope, *commands):
                await contexts.records.create_source(scope, "content", json.dumps(commands))
                await invoke(database, directory, scope)

            await run(
                database,
                scope,
                {"op": "put", "id": "a", "text": "region east"},
                {"op": "put", "id": "b", "text": "region north"},
                {"op": "put", "id": "a", "text": "region east"},
                {"op": "find", "query": "region"},
            )
            log = await current(database, "facts", scope, "search-log")
            # Reads inside the transaction see writes not yet committed.
            assert sorted(log.content.content["results"]["region"]) == ["a", "b"]
            assert (await current(database, "facts", scope, "a")).revision == 1

            await run(
                database, scope, {"op": "merge", "inputs": ["a", "b"], "into": "c", "text": "region east and north"}
            )
            await run(
                database, scope, {"op": "merge", "inputs": ["a", "b"], "into": "c", "text": "region east and north"}
            )
            await run(database, scope, {"op": "find", "query": "region"})
            log = await current(database, "facts", scope, "search-log")
            assert log.content.content["results"]["region"] == ["c"]

            await run(database, scope, {"op": "undo", "id": "c"})
            await run(database, scope, {"op": "find", "query": "region"})
            log = await current(database, "facts", scope, "search-log")
            assert sorted(log.content.content["results"]["region"]) == ["a", "b"]
            assert (await current(database, "facts", scope, "a")).revision == 2

            await run(
                database,
                scope,
                {"op": "put", "id": "a", "text": "region west"},
                {"op": "revert", "id": "a", "revision": 1},
                {"op": "lifecycle", "id": "b", "state": "deprecated"},
                {"op": "tags", "id": "a", "tags": ["Ops", "region"]},
                {"op": "handle", "id": "a", "key": "primary-region"},
                {"op": "find", "query": "region"},
            )
            log = await current(database, "facts", scope, "search-log")
            assert log.content.content["results"]["region"] == ["a"]
            reverted = await current(database, "facts", scope, "a")
            assert reverted.revision == 4 and reverted.content.content == {"text": "region east"}

            await run(database, scope, {"op": "lifecycle", "id": "b", "state": "active"})
            await run(database, scope, {"op": "lifecycle", "id": "b", "state": "retired"})
            with pytest.raises(Exception, match="retired"):
                await run(database, scope, {"op": "lifecycle", "id": "b", "state": "active"})

    asyncio.run(scenario())


def _writer(database, scope: str, **options):
    from powercontext.builtin.persistence.sources import SourceRepository
    from powercontext.builtin.plugins.families import register_plugin_family
    from powercontext.builtin.plugins.writer import PluginArtifactWriter
    from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY

    register_plugin_family("notes")
    return PluginArtifactWriter(
        database=database, scope_id=scope, family="notes", sources=SourceRepository(BUILTIN_SOURCE_REGISTRY), **options
    )


def test_a_write_decided_on_a_stale_read_conflicts_instead_of_overwriting(runtime):
    from powercontext.builtin.plugins.writer import UserChange
    from powercontext.errors import RevisionConflictError

    async def scenario():
        async with open_builtin_contexts(runtime) as contexts:
            database = contexts.database
            scope = await _scope(contexts)
            async with _writer(database, scope).transaction() as tx:
                await tx.put("a", content={"text": "first"})

            stale = _writer(database, scope)
            assert (await stale.get("a")).ref.revision == 1
            async with _writer(database, scope, change=UserChange("update", "a")).transaction() as tx:
                await tx.put("a", content={"text": "user edit"}, expected_revision=1)

            with pytest.raises(RevisionConflictError):
                async with stale.transaction() as tx:
                    await tx.put("a", content={"text": "derived from first"})
            assert (await current(database, "notes", scope, "a")).content.content == {"text": "user edit"}

    asyncio.run(scenario())


def test_user_changes_complete_exactly_their_write_and_always_record_a_revision(runtime):
    from powercontext.builtin.plugins.writer import PluginContractError, UserChange

    async def scenario():
        async with open_builtin_contexts(runtime) as contexts:
            database = contexts.database
            scope = await _scope(contexts)
            async with _writer(database, scope).transaction() as tx:
                await tx.put("a", content={"text": "same"})
                await tx.put("b", content={"text": "other"})

            with pytest.raises(PluginContractError):
                async with _writer(database, scope, change=UserChange("update", "a")).transaction():
                    pass
            with pytest.raises(PluginContractError):
                async with _writer(database, scope, change=UserChange("update", "a")).transaction() as tx:
                    await tx.put("b", content={"text": "not the target"})
            assert (await current(database, "notes", scope, "b")).revision == 1

            # Unchanged content from a user still appends a revision bound to its lineage-only Source.
            async with _writer(database, scope, change=UserChange("update", "a")).transaction() as tx:
                edited = await tx.put("a", content={"text": "same"}, expected_revision=1)
            assert edited.ref.revision == 2
            assert [ref.source_type for ref in edited.lineage.sources] == ["content"]

            # Background writes with unchanged content add nothing, as in Atomic Memory.
            async with _writer(database, scope).transaction() as tx:
                kept = await tx.put("a", content={"text": "same"})
            assert kept.ref.revision == 2

    asyncio.run(scenario())


def test_lifecycle_changes_report_the_target_and_its_group(runtime):
    async def scenario():
        async with open_builtin_contexts(runtime) as contexts:
            database = contexts.database
            scope = await _scope(contexts)
            async with _writer(database, scope).transaction() as tx:
                await tx.put("a", content={"text": "a"})
                await tx.put("b", content={"text": "b"})
                await tx.merge(("a", "b"), into="ab", content={"text": "a and b"})
            async with _writer(database, scope).transaction() as tx:
                restored = await tx.set_lifecycle("a", "active")
            assert restored.artifact.lifecycle_state == "active"
            assert {ref.artifact_id for ref in restored.restored} == {"b"}
            assert {ref.artifact_id for ref in restored.retired} == {"ab"}

    asyncio.run(scenario())
