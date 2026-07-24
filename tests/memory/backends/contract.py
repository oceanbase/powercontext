from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from powercontext.artifacts import ArtifactRef
from powercontext.memory import (
    EmbeddingProfile,
    Memory,
    MemoryBackend,
    MemoryCapabilities,
    MemoryCitation,
    MemoryCommit,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryHit,
    MemoryProjection,
    MemoryRevisionChanges,
    MemorySearchChannels,
    MemorySearchRequest,
    MemoryService,
    MemoryUnitOfWork,
)


class ContractIds:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def __call__(self, kind: str) -> str:
        count = self._counts.get(kind, 0) + 1
        self._counts[kind] = count
        return f"contract-{kind}-{count}"


class CaptureIds(ContractIds):
    def __init__(self, namespace: str) -> None:
        super().__init__()
        self._namespace = namespace

    def __call__(self, kind: str) -> str:
        return f"captured-{self._namespace}-{super().__call__(kind)}"


class CommitCapturingBackend(MemoryBackend, MemoryUnitOfWork, AbstractAsyncContextManager[MemoryUnitOfWork]):
    """Delegate reads while capturing one service-prepared write without storing it."""

    def __init__(self, backend: MemoryBackend) -> None:
        self._backend = backend
        self.commit_value: MemoryCommit | None = None

    async def __aenter__(self) -> MemoryUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def capabilities(self) -> MemoryCapabilities:
        return await self._backend.capabilities()

    async def get(self, memory: ArtifactRef, /) -> Memory:
        return await self._backend.get(memory)

    async def latest(self, artifact_id: str, /) -> Memory:
        return await self._backend.latest(artifact_id)

    async def entries(self, memory: ArtifactRef, /) -> tuple[MemoryEntryVersion, ...]:
        return await self._backend.entries(memory)

    async def projections(self, memory: ArtifactRef, /) -> tuple[MemoryProjection, ...]:
        return await self._backend.projections(memory)

    def begin(self) -> AbstractAsyncContextManager[MemoryUnitOfWork]:
        return self

    async def commit(self, value: MemoryCommit, /) -> Memory:
        self.commit_value = value
        return value.memory

    async def rollback(self) -> None:
        return None

    async def changes(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
        /,
    ) -> tuple[MemoryRevisionChanges, ...]:
        return await self._backend.changes(memory, since_revision)

    async def vector_complete(
        self,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        return await self._backend.vector_complete(memories, profile)

    async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
        return await self._backend.search(request)

    async def expand(self, hits: tuple[MemoryHit, ...], /) -> tuple[MemoryEntryVersion, ...]:
        return await self._backend.expand(hits)


async def prepare_memory_commit(
    backend: MemoryBackend,
    memory: Memory,
    entry: MemoryEntryInput,
    *,
    namespace: str = "default",
) -> MemoryCommit:
    capture = CommitCapturingBackend(backend)
    service = MemoryService(backend=capture, id_factory=CaptureIds(namespace))
    prepared = await service.remember(memory=memory, entries=(entry,), mode="append")
    assert prepared is not None
    assert capture.commit_value is not None
    return capture.commit_value


async def exercise_memory_backend(backend: MemoryBackend) -> Memory:
    service = MemoryService(backend=backend, id_factory=ContractIds())
    first = await service.remember(
        memory=None,
        entries=(
            MemoryEntryInput(kind="constraint", text="修改文档前运行 make docs-test。"),
            MemoryEntryInput(kind="fact", text="PowerContext uses SQLite FTS for lexical retrieval."),
        ),
        mode="append",
    )
    assert first is not None
    first_manifest = first.content.manifest.entries
    first_entries = await service.entries(first)
    revised = await service.remember(
        memory=first,
        entries=(
            MemoryEntryInput(
                entry=first_entries[0],
                kind="constraint",
                text="修改文档前运行 make docs-test, 并在评审前运行 make check。",
                reason="user_correction",
            ),
        ),
        mode="append",
    )
    assert revised is not None
    revised_entries = await service.entries(revised)
    forgotten = await service.forget(
        revised,
        entries=(revised_entries[1],),
        reason="temporarily_hidden",
    )
    restored = await service.reactivate(
        forgotten,
        entries=((await service.entries(forgotten))[1],),
        reason="user_restored",
    )

    assert await backend.get(first.ref) == first
    assert await backend.latest(first.artifact_id) == restored
    assert tuple(entry.entry_version_id for entry in await backend.entries(first.ref)) == tuple(
        item.entry_version_id for item in first_manifest
    )
    assert len(await backend.entries(restored.ref)) == 2

    deltas = await service.changes(restored, since_revision=1)
    assert tuple(delta.memory_ref.revision for delta in deltas) == (2, 3, 4)
    assert tuple(delta.changes[0].op for delta in deltas) == ("revise", "deactivate", "reactivate")

    chinese = await service.search("文档 检查", memories=(restored,), mode="fts")
    english = await service.search("SQLite lexical", memories=(restored,), mode="fts")
    operator_like = await service.search('" OR 中文*', memories=(restored,), mode="fts")
    assert chinese.mode == english.mode == "fts"
    assert chinese.hits[0].entry_id == first_manifest[0].entry_id
    assert english.hits[0].entry_id == first_manifest[1].entry_id
    assert operator_like.hits[0].entry_id == first_manifest[0].entry_id

    cited = await service.validate_citation(
        MemoryCitation(
            memory_ref=first.ref,
            entry_id=first_manifest[0].entry_id,
            entry_version_id=first_manifest[0].entry_version_id,
        )
    )
    assert cited.text == "修改文档前运行 make docs-test。"

    unchanged = await service.remember(
        memory=restored,
        entries=(
            MemoryEntryInput(
                entry=(await service.entries(restored))[0],
                kind="constraint",
                text="修改文档前运行 make docs-test, 并在评审前运行 make check。",
            ),
        ),
        mode="append",
    )
    assert unchanged == restored
    return restored
