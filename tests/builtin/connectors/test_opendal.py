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

import pytest

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.connectors import (
    OPENDAL_TEXT_FILE_CONNECTOR_NAME,
    OpenDALTextFileConnector,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    TextFileSnapshotSource,
)
from powercontext.sources import (
    ConnectorBinding,
    ConnectorCapability,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)


def _binding() -> ConnectorBinding:
    return ConnectorBinding(
        scope_id="project-a",
        binding_id="documents-a",
        connector_name=OPENDAL_TEXT_FILE_CONNECTOR_NAME,
        connector_version="1",
    )


class MemoryFileSystem:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def pipe_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def find(self, path: str, *, detail: bool) -> dict[str, dict[str, object]]:
        assert detail
        prefix = f"{path.rstrip('/')}/" if path else ""
        return {
            name: {"name": name, "size": len(content), "type": "file"}
            for name, content in self.files.items()
            if not prefix or name.startswith(prefix)
        }

    def cat_file(self, path: str) -> bytes:
        return self.files[path]


def _filesystem() -> MemoryFileSystem:
    return MemoryFileSystem()


class TextFileCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="document",
                text=source.content,
                sources=(source,),
            )
            for source in request.sources
            if isinstance(source, TextFileSnapshotSource)
        )


def test_opendal_connector_persists_incremental_snapshots_across_runtime_restart(tmp_path) -> None:
    async def scenario() -> None:
        filesystem = _filesystem()
        filesystem.pipe_file("docs/readme.md", b"First value")
        filesystem.pipe_file("docs/nested/note.txt", b"Nested value")
        filesystem.pipe_file("docs/image.bin", b"\x00\x01")
        connector = OpenDALTextFileConnector(
            filesystem,
            source_namespace="workspace-a",
            root="docs",
        )
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'powercontext.db'}"))

        async with open_builtin_contexts(config) as contexts:
            first = await contexts.run_connector(connector, _binding())
            context = await contexts.get("project-a")
            first_sources = await context.sources.list()

            assert first.status is ConnectorRunStatus.COMPLETE
            assert [item.item_id for item in first.items] == ["nested/note.txt", "readme.md"]
            assert all(item.status is ConnectorSubmissionStatus.ACCEPTED for item in first.items)
            assert len(first_sources) == 2
            readme = next(
                source
                for source in first_sources
                if isinstance(source, TextFileSnapshotSource) and source.path == "readme.md"
            )
            assert context.sources.catalog.project(readme, TEXT_EVIDENCE_PROJECTION_KEY) == {
                "source_type": "text-file-snapshot",
                "source_id": readme.name,
                "content": "First value",
                "metadata": {
                    "namespace": "workspace-a",
                    "path": "readme.md",
                    "media_type": "text/markdown",
                    "encoding": "utf-8",
                    "content_digest": readme.content_digest,
                    "size": 11,
                },
            }

        async with open_builtin_contexts(config) as contexts:
            unchanged = await contexts.run_connector(connector, _binding())
            assert unchanged.previous_checkpoint == first.committed_checkpoint
            assert unchanged.items == ()

            filesystem.pipe_file("docs/readme.md", b"Second value")
            changed = await contexts.run_connector(connector, _binding())
            context = await contexts.get("project-a")
            sources = await context.sources.list()

            assert [item.item_id for item in changed.items] == ["readme.md"]
            assert len(sources) == 3
            readme_snapshots = [
                source
                for source in sources
                if isinstance(source, TextFileSnapshotSource) and source.path == "readme.md"
            ]
            assert {source.content for source in readme_snapshots} == {"First value", "Second value"}
            assert len({source.name for source in readme_snapshots}) == 2

    asyncio.run(scenario())


def test_opendal_connector_keeps_checkpoint_before_a_rejected_item() -> None:
    async def scenario() -> None:
        filesystem = _filesystem()
        filesystem.pipe_file("good.md", b"Good value")
        filesystem.pipe_file("invalid.txt", b"\xff")
        connector = OpenDALTextFileConnector(filesystem, source_namespace="workspace-a")

        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            rejected = await contexts.run_connector(connector, _binding())
            context = await contexts.get("project-a")

            assert rejected.status is ConnectorRunStatus.INCOMPLETE
            assert rejected.committed_checkpoint is None
            assert [(item.item_id, item.status) for item in rejected.items] == [
                ("good.md", ConnectorSubmissionStatus.ACCEPTED),
                ("invalid.txt", ConnectorSubmissionStatus.REJECTED),
            ]
            assert len(await context.sources.list()) == 1

            filesystem.pipe_file("invalid.txt", b"Recovered value")
            recovered = await contexts.run_connector(connector, _binding())

            assert recovered.status is ConnectorRunStatus.COMPLETE
            assert recovered.committed_checkpoint is not None
            assert len(await context.sources.list()) == 2

    asyncio.run(scenario())


def test_opendal_connector_sources_complete_the_memory_ingestion_loop() -> None:
    async def scenario() -> None:
        filesystem = _filesystem()
        filesystem.pipe_file("decision.md", b"Use exact snapshot references.")
        connector = OpenDALTextFileConnector(filesystem, source_namespace="workspace-a")

        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=TextFileCandidatePipeline(),
        ) as contexts:
            connector_result = await contexts.run_connector(connector, _binding())
            context = await contexts.get("project-a")
            flush_result = await context.triggers.flush(limit=10)
            memory = await context.artifacts.memory.head("memory")
            entries = await context.artifacts.memory.entries(memory)

            assert flush_result.source_count == 1
            assert len(entries) == 1
            assert entries[0].text == "Use exact snapshot references."
            assert entries[0].sources == (connector_result.items[0].source_ref,)

    asyncio.run(scenario())


def test_opendal_connector_does_not_claim_authoritative_deletion() -> None:
    connector = OpenDALTextFileConnector(_filesystem(), source_namespace="workspace-a")

    assert ConnectorCapability.CHECKPOINT_RESUME in connector.capabilities
    assert ConnectorCapability.AUTHORITATIVE_DELETION not in connector.capabilities
    assert ConnectorCapability.CHANGE_FEED not in connector.capabilities


def test_opendal_connector_reads_the_real_opendalfs_memory_backend() -> None:
    opendalfs = pytest.importorskip("opendalfs")

    async def scenario() -> None:
        filesystem = opendalfs.OpendalFileSystem(
            scheme="memory",
            asynchronous=False,
            skip_instance_cache=True,
        )
        filesystem.pipe_file("docs/readme.md", b"OpenDAL value")
        connector = OpenDALTextFileConnector(
            filesystem,
            source_namespace="opendal-memory",
            root="docs",
        )

        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            result = await contexts.run_connector(connector, _binding())

            assert result.status is ConnectorRunStatus.COMPLETE
            assert result.items[0].item_id == "readme.md"
            assert result.items[0].status is ConnectorSubmissionStatus.ACCEPTED

    asyncio.run(scenario())
