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
from pathlib import Path

import httpx
import opendalfs
import pytest
from fastapi import FastAPI
from powercontext.builtin.artifacts.memory import (
    MemoryCandidateRequest,
    MemoryEntryInput,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import (
    PowerContextClient,
    RemoteConnectorWorker,
    ServerResponseError,
)
from powercontext.http import (
    CommitConnectorCheckpointRequest,
    FlushMemoryRequest,
    ListMemoryEntriesRequest,
    ListMemoryEntriesResponse,
    RegisterSourceDefinitionRequest,
    SubmitSourceObservationRequest,
)
from powercontext.http import (
    ConnectorBinding as HttpConnectorBinding,
)
from powercontext.http import (
    SourceDefinitionManifest as HttpSourceDefinitionManifest,
)
from powercontext.http import (
    SourceObservation as HttpSourceObservation,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    ConnectorBinding,
    ConnectorRunResult,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
    SourceDefinitionRegistry,
    SourceObservation,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)

from powercontext_connector_opendal import (
    OPENDAL_TEXT_FILE_CONNECTOR_NAME,
    TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,
    OpenDALTextFileConnector,
    TextFileSnapshotCapture,
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


class TextEvidenceCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        entries: list[MemoryEntryInput] = []
        for source in request.sources:
            if not isinstance(source, SourceObservation):
                continue
            evidence = TextEvidence.model_validate(source.projection(TEXT_EVIDENCE_PROJECTION_KEY))
            entries.append(MemoryEntryInput(kind="document", text=evidence.content, sources=(source,)))
        return tuple(entries)


def _binding() -> ConnectorBinding:
    return ConnectorBinding(
        scope_id="project-a",
        binding_id="documents-a",
        connector_name=OPENDAL_TEXT_FILE_CONNECTOR_NAME,
        connector_version="1",
    )


def _app(database: Path, *, memory: bool = False) -> FastAPI:
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=TextEvidenceCandidatePipeline() if memory else None,
    )


async def _run(
    app: FastAPI,
    connector: OpenDALTextFileConnector,
    *,
    flush_memory: bool = False,
) -> tuple[ConnectorRunResult, ListMemoryEntriesResponse | None]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as transport,
    ):
        client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
        worker = RemoteConnectorWorker(
            client=client,
            registry=SourceDefinitionRegistry((TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,)),
        )
        result = await worker.run(connector, _binding())
        memory = None
        if flush_memory:
            await client.flush_memory(FlushMemoryRequest(scope_id="project-a"))
            memory = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id="project-a"))
        return result, memory


def test_remote_opendal_worker_persists_incremental_checkpoint_across_server_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("docs/readme.md", b"First value")
        filesystem.pipe_file("docs/nested/note.txt", b"Nested value")
        filesystem.pipe_file("docs/image.bin", b"\x00\x01")
        connector = OpenDALTextFileConnector(filesystem, source_namespace="workspace-a", root="docs")
        database = tmp_path / "powercontext.db"

        first, _ = await _run(_app(database), connector)
        unchanged, _ = await _run(_app(database), connector)
        filesystem.pipe_file("docs/readme.md", b"Second value")
        changed, _ = await _run(_app(database), connector)

        assert first.status is ConnectorRunStatus.COMPLETE
        assert [item.item_id for item in first.items] == ["nested/note.txt", "readme.md"]
        assert all(item.status is ConnectorSubmissionStatus.ACCEPTED for item in first.items)
        assert unchanged.previous_checkpoint == first.committed_checkpoint
        assert unchanged.items == ()
        assert [item.item_id for item in changed.items] == ["readme.md"]
        assert changed.previous_checkpoint == first.committed_checkpoint
        assert changed.committed_checkpoint != first.committed_checkpoint

    asyncio.run(scenario())


def test_remote_opendal_worker_keeps_checkpoint_before_a_rejected_item(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("good.md", b"Good value")
        filesystem.pipe_file("invalid.txt", b"\xff")
        connector = OpenDALTextFileConnector(filesystem, source_namespace="workspace-a")
        database = tmp_path / "powercontext.db"

        rejected, _ = await _run(_app(database), connector)
        filesystem.pipe_file("invalid.txt", b"Recovered value")
        recovered, _ = await _run(_app(database), connector)

        assert rejected.status is ConnectorRunStatus.INCOMPLETE
        assert rejected.committed_checkpoint is None
        assert [(item.item_id, item.status) for item in rejected.items] == [
            ("good.md", ConnectorSubmissionStatus.ACCEPTED),
            ("invalid.txt", ConnectorSubmissionStatus.REJECTED),
        ]
        assert recovered.status is ConnectorRunStatus.COMPLETE
        assert recovered.previous_checkpoint is None
        assert recovered.committed_checkpoint is not None

    asyncio.run(scenario())


def test_remote_opendal_worker_rejects_an_oversized_observation_without_aborting(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("large.txt", b"x" * (2 * 1024 * 1024))
        connector = OpenDALTextFileConnector(
            filesystem,
            source_namespace="workspace-a",
            max_file_size=2 * 1024 * 1024,
        )

        result, _ = await _run(_app(tmp_path / "powercontext.db"), connector)

        assert result.status is ConnectorRunStatus.INCOMPLETE
        assert result.committed_checkpoint is None
        assert [(item.item_id, item.status) for item in result.items] == [
            ("large.txt", ConnectorSubmissionStatus.REJECTED)
        ]

    asyncio.run(scenario())


def test_text_file_definition_declares_its_executable_contract() -> None:
    definition = TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION

    assert definition.name == "text-file-snapshot"
    assert definition.version == "1"
    assert definition.input_class is TextFileSnapshotCapture
    assert len(definition.projections) == 1


def test_remote_opendal_worker_completes_the_source_to_memory_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = MemoryFileSystem()
        filesystem.pipe_file("decision.md", b"Use exact snapshot references.")
        connector = OpenDALTextFileConnector(filesystem, source_namespace="workspace-a")

        result, memory = await _run(_app(tmp_path / "powercontext.db", memory=True), connector, flush_memory=True)

        assert result.status is ConnectorRunStatus.COMPLETE
        assert memory is not None
        assert [entry.text for entry in memory.entries] == ["Use exact snapshot references."]
        assert memory.entries[0].source_refs[0].name == "text-file-snapshot"

    asyncio.run(scenario())


def test_opendal_connector_reads_the_real_opendalfs_memory_backend(tmp_path: Path) -> None:
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

        result, _ = await _run(_app(tmp_path / "powercontext.db"), connector)

        assert result.status is ConnectorRunStatus.COMPLETE
        assert result.items[0].item_id == "readme.md"
        assert result.items[0].status is ConnectorSubmissionStatus.ACCEPTED

    asyncio.run(scenario())


def test_remote_ingestion_rejects_invalid_projection_and_stale_checkpoint(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path / "powercontext.db")
        registry = SourceDefinitionRegistry((TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,))
        manifest = manifest_for_definition(TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION)
        source = await registry.resolve(
            TextFileSnapshotCapture(namespace="workspace-a", path="decision.md", content="Keep worker authority.")
        )
        observation = project_source_for_transport(registry, source)
        malformed = observation.model_copy(update={"projections": ()})
        binding = HttpConnectorBinding.model_validate(_binding().model_dump(mode="json"))

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            with pytest.raises(ServerResponseError) as missing:
                await client.submit_source_observation(
                    SubmitSourceObservationRequest(
                        scope_id=binding.scope_id,
                        observation=HttpSourceObservation.model_validate(observation.model_dump(mode="json")),
                    )
                )
            await client.register_source_definition(
                RegisterSourceDefinitionRequest(
                    manifest=HttpSourceDefinitionManifest.model_validate(
                        manifest.model_dump(mode="json", by_alias=True)
                    )
                )
            )
            with pytest.raises(ServerResponseError) as invalid:
                await client.submit_source_observation(
                    SubmitSourceObservationRequest(
                        scope_id=binding.scope_id,
                        observation=HttpSourceObservation.model_validate(malformed.model_dump(mode="json")),
                    )
                )
            await client.commit_connector_checkpoint(
                CommitConnectorCheckpointRequest(binding=binding, expected=None, checkpoint={"cursor": 1})
            )
            with pytest.raises(ServerResponseError) as stale:
                await client.commit_connector_checkpoint(
                    CommitConnectorCheckpointRequest(binding=binding, expected=None, checkpoint={"cursor": 2})
                )

        assert (missing.value.status_code, missing.value.code) == (404, "source_definition_not_found")
        assert (invalid.value.status_code, invalid.value.code) == (422, "invalid_source_ingestion")
        assert (stale.value.status_code, stale.value.code) == (409, "connector_checkpoint_conflict")

    asyncio.run(scenario())
