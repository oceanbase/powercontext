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

"""Controlled model boundaries under the real Supervisor for Dream regressions."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from copy import copy
from weakref import WeakKeyDictionary

from sqlalchemy import func, select

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.dream.bindings import DREAM_BINDINGS
from powercontext.builtin.inference import GenerationResult, StructuredGenerator, character_token_estimator
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE
from powercontext.builtin.runtime import BuiltinRuntime
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding
from powercontext.builtin.runtime.composition import open_builtin_runtime as open_runtime
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation

_controllers: WeakKeyDictionary[BuiltinRuntime, Controller] = WeakKeyDictionary()


class _IndependentMemoryReconciler:
    async def generate(self, request: AtomicMemoryReconciliationInput, /):
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create",
                compared_ids=tuple(item.item_id for item in request.related),
                content=AtomicMemoryReconciliationContent(kind=request.proposal.kind, text=request.proposal.text),
                evidence_ids=request.proposal.evidence_ids,
                reason="Preserve each independent fixture fact and its exact Source evidence.",
            )
        )


def atomic_memory_pipeline(
    extractor: StructuredGenerator[AtomicMemoryExtractionInput, AtomicMemoryExtractionOutput],
) -> AtomicMemoryGenerationPipeline:
    return AtomicMemoryGenerationPipeline(
        extractor=extractor,
        reconciler=_IndependentMemoryReconciler(),
        estimator=character_token_estimator(),
    )


def memory_source_text(evidence: AtomicMemoryEvidence) -> str | None:
    content = evidence.content
    if isinstance(content, dict):
        text = content.get("content", content.get("text"))
        return text if isinstance(text, str) else None
    return content if isinstance(content, str) else None


class Handle:
    def __init__(self, controller, assignment):
        self.controller = controller
        self.assignment = assignment
        self.ready = asyncio.Event()
        self.task = asyncio.create_task(self.execute())

    async def execute(self):
        await self.ready.wait()
        assert self.controller.runtime is not None
        service = copy(self.controller.runtime._dream_service)
        assert service is not None
        service.processing = ScopeInvocation(self.assignment)
        try:
            await service.execute()
        except InvocationAlreadyHandled:
            pass
        except ArtifactProcessingLeadershipLostError:
            return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST)
        return ArtifactProcessingWorkerCompletion()

    async def wait(self):
        return await asyncio.shield(self.task)

    async def terminate(self):
        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task


class Controller:
    def __init__(self):
        self.runtime: BuiltinRuntime | None = None
        self.handles: list[Handle] = []

    async def start(self, assignment):
        handle = Handle(self, assignment)
        self.handles.append(handle)
        return handle


@asynccontextmanager
async def open_dream_runtime(config, *, controller: Controller | None = None, **kwargs):
    controller = Controller() if controller is None else controller
    bindings = tuple(
        ArtifactProcessingBinding(
            binding_name=binding,
            artifact_family="skill" if operation == "derive_skill" else "experience",
            launcher=controller,
            max_workers=4,
            worker_timeout_seconds=180,
        )
        for operation, binding in DREAM_BINDINGS.items()
    )
    async with open_runtime(config, artifact_processing_bindings=bindings, **kwargs) as runtime:
        controller.runtime = runtime
        _controllers[runtime] = controller
        try:
            yield runtime
        finally:
            _controllers.pop(runtime, None)


async def process_pending(runtime: BuiltinRuntime) -> None:
    """Release one dispatched batch, retaining control over model race boundaries."""

    controller = _controllers[runtime]
    assert runtime._dream_service is not None
    async with runtime._dream_service.database.transaction() as connection:
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        pending = await connection.scalar(
            select(func.count()).select_from(table).where(table.c.requested_generation > table.c.handled_generation)
        )
    if not pending:
        return
    async with asyncio.timeout(15):
        while True:
            handles = [handle for handle in controller.handles if not handle.task.done()]
            if handles:
                break
            await asyncio.sleep(0.01)
    for handle in handles:
        handle.ready.set()
    await asyncio.gather(*(handle.task for handle in handles))
