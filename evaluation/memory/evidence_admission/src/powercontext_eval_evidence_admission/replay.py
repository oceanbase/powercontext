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

"""Capture, close, reopen and project actual scoped SQLite Sources."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    project_atomic_memory_evidence,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, SOURCES_TABLE
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.builtin.sources.content import CONTENT_SOURCE_ADAPTER, ContentCapture
from powercontext.sources import SourceProjectionValue, SourceRef
from powercontext.sources.observations import manifest_for_definition, project_source_for_transport
from sqlalchemy import func, select

from .manifests import EvaluationError
from .models import Case, digest

Checkpoint = Callable[[str], Awaitable[None]]


class ReplayCancelled(asyncio.CancelledError):
    def __init__(self, memory_mutations: int, published_cursor: int):
        super().__init__()
        self.memory_mutations = memory_mutations
        self.published_cursor = published_cursor


async def checkpoint(phase: str) -> None:
    """A cooperative boundary for cancellation, including CPU-only preparation."""
    await asyncio.sleep(0)


@dataclass
class Replay:
    contexts: RelationalContexts
    scope_id: str
    sources: SourceRepository
    value: AtomicMemoryExtractionInput
    materializations: tuple[str, ...]
    definitions: tuple[str, ...]
    projection_versions: tuple[str, ...]
    seconds: float

    async def mutations(self) -> tuple[int, int]:
        table = ARTIFACT_HEADS_TABLE
        async with self.contexts.database.transaction() as connection:
            memories = await connection.scalar(
                select(func.count())
                .select_from(table)
                .where(table.c.scope_id == self.scope_id, table.c.family == "atomic-memory")
            )
        context = await self.contexts.get(self.scope_id)
        return int(memories or 0), (await context.triggers.cursor()).sequence


async def _capture(contexts: RelationalContexts, scope_id: str, case: Case) -> None:
    context = await contexts.get(scope_id)
    for source in case.sources:
        body, metadata = source.capture()
        if source.route == "native":
            for _ in range(2):
                await contexts.records.capture_source(scope_id, "content", source.source_id, body, metadata)
        else:
            native = await CONTENT_SOURCE_ADAPTER.resolve(
                ContentCapture(source_id=source.source_id, content=body, metadata=metadata)
            )
            observation = project_source_for_transport(BUILTIN_SOURCE_REGISTRY, native)
            if case.fault == "invalid_projection":
                projection = observation.projections[0]
                observation = observation.model_copy(
                    update={
                        "projections": (SourceProjectionValue(key=projection.key, value={"content": None}),),
                    }
                )
            for _ in range(2):
                await context.sources.add(observation)


@asynccontextmanager
async def replay_case(case: Case, *, on_phase: Checkpoint = checkpoint) -> AsyncIterator[Replay]:
    started = time.perf_counter()
    with TemporaryDirectory(prefix="pc-evidence-") as directory:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{Path(directory) / 'source.db'}"))
        async with open_builtin_contexts(config) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Evidence admission", summary="Isolated evaluation", idempotency_key="fixture")
            )
            scope_id = scope.scope_id
            await _capture(contexts, scope_id, case)
        async with open_builtin_contexts(config) as contexts:
            sources = SourceRepository(contexts.source_registry)
            evidence: list[AtomicMemoryEvidence] = []
            materials: list[str] = []
            definitions: list[str] = []
            versions: list[str] = []
            probe = Replay(contexts, scope_id, sources, AtomicMemoryExtractionInput(evidence=()), (), (), (), 0)
            try:
                await on_phase("before_read")
                for source in case.sources:
                    ref = SourceRef(source_type="content", source_id=source.source_id)
                    if case.fault == "missing_source":
                        ref = SourceRef(source_type="content", source_id="unavailable-fixture-source")
                    async with contexts.database.transaction() as connection:
                        stored = await sources.get(connection, scope_id, ref)
                        table = SOURCES_TABLE
                        payload = await connection.scalar(
                            select(table.c.payload).where(
                                table.c.scope_id == scope_id,
                                table.c.source_type == ref.source_type,
                                table.c.source_id == ref.source_id,
                            )
                        )
                    if payload is None:
                        raise EvaluationError("persisted_materialization_unavailable")
                    materials.append(hashlib.sha256(bytes(payload)).hexdigest())
                    definitions.append(
                        digest(
                            {
                                "version": stored.value.definition_version,
                                "fingerprint": getattr(stored.value, "definition_fingerprint", None)
                                or manifest_for_definition(
                                    contexts.source_registry.definition_for_name(ref.source_type)
                                ).fingerprint,
                            }
                        )
                    )
                    versions.append(
                        "powercontext.text-evidence@1" if source.route == "observation" else "content.read@1"
                    )
                    projected = await project_atomic_memory_evidence(
                        stored, sources, evidence_id=f"source:{stored.journal_position}"
                    )
                    await on_phase("projection")
                    evidence.append(projected)
                yield Replay(
                    contexts,
                    scope_id,
                    sources,
                    AtomicMemoryExtractionInput(evidence=tuple(evidence)),
                    tuple(materials),
                    tuple(definitions),
                    tuple(versions),
                    time.perf_counter() - started,
                )
            except asyncio.CancelledError:
                raise ReplayCancelled(*await probe.mutations()) from None
