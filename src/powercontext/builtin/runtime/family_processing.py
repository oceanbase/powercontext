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

"""Scope child processors for Memory, Experience, and Profile."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.dream.bindings import (
    HANDOFF_DREAM_BINDING,
    PROMPT_DREAM_BINDING,
    SKILL_DREAM_BINDING,
    operations_for_binding,
)
from powercontext.builtin.dream.generation import DreamGenerator
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError, GenerationConflictError
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.errors import RevisionConflictError

if TYPE_CHECKING:
    from powercontext.builtin.runtime.relational import RelationalContexts
    from powercontext.server.processing_security import WorkerSecurity


FAMILY_BINDINGS = {
    "memory": SOURCE_WINDOW_TRIGGER_NAME,
    "experience": EXPERIENCE_INCUBATION_CURSOR_NAME,
    "profile": PROFILE_SOURCE_WINDOW_BINDING,
    "skill": SKILL_DREAM_BINDING,
    "handoff": HANDOFF_DREAM_BINDING,
    "prompt": PROMPT_DREAM_BINDING,
}
logger = logging.getLogger(__name__)


class FamilyWorkerSpec(BaseModel):
    """Serializable child configuration; never expose credentials through repr."""

    config: BuiltinConfig = Field(repr=False)
    worker_security: dict[str, Any] | None = Field(default=None, repr=False)


def run_family_worker(
    spec: FamilyWorkerSpec, assignment: ArtifactProcessingWorkAssignment, /
) -> ArtifactProcessingWorkerCompletion:
    return asyncio.run(_run_family_worker(spec, assignment))


async def _run_family_worker(
    spec: FamilyWorkerSpec, assignment: ArtifactProcessingWorkAssignment
) -> ArtifactProcessingWorkerCompletion:
    from powercontext.builtin.runtime.composition import (
        _configured_memory_write_gate,
        _dream_generator,
        _embedding_models,
        _fail_open_decision_model,
        _generation_pipelines,
        _prompt_registry,
        _usage_reporting_embedding_model,
        open_builtin_contexts,
    )

    config = spec.config
    async with AsyncExitStack() as resources:
        pipelines = await _generation_pipelines(
            config.inference, config.runtime, resources, None, BUILTIN_SOURCE_REGISTRY
        )
        decision_model = _fail_open_decision_model(
            None,
            pipelines[7],
            None,
            timeout_seconds=config.inference.decision_timeout_seconds or config.inference.generation_timeout_seconds,
        )
        memory_write_gate = _configured_memory_write_gate(None, decision_model, config.runtime)
        embedding, _ = await _embedding_models(config.inference, resources, None)
        contexts = await resources.enter_async_context(
            open_builtin_contexts(
                config,
                candidate_pipeline=pipelines[1],
                experience_pipeline=pipelines[2],
                embedding_model=_usage_reporting_embedding_model(embedding),
                decision_model=decision_model,
                memory_write_gate=memory_write_gate,
                prompt_registry=_prompt_registry(
                    config.runtime,
                    (
                        ("profile.generate", None, pipelines[0]),
                        ("memory.extract", None, pipelines[1]),
                        ("experience.incubate", None, pipelines[2]),
                        ("experience.generate", None, pipelines[3]),
                        ("skill.generate", None, pipelines[4]),
                        ("handoff.generate", None, pipelines[5]),
                        ("memory.rerank", None, pipelines[6]),
                        *(
                            (f"topic_memory.{stage}", None, object())
                            for stage in ("probe", "global", "planner", "evolve", "temporary", "reduce", "reconcile")
                            if config.inference.generation_model is not None
                        ),
                    ),
                ),
                _topic_memory_worker=True,
            )
        )
        contexts.profiles.generator = pipelines[0]
        contexts.profiles.max_sources = config.runtime.profile_max_sources_per_window
        security = None
        if spec.worker_security is not None:
            # The optional Server adapter stays outside Runtime-only SDK composition.
            from powercontext.server.processing_security import open_worker_security

            security = await resources.enter_async_context(
                open_worker_security(spec.worker_security, contexts.database)
            )
        generator = None
        operations = operations_for_binding(assignment.binding_name)
        if operations and config.runtime.dream_enabled:
            async with contexts.database.transaction() as connection:
                record = await DreamRepository().next_pending(
                    connection,
                    assignment.scope_id,
                    operations,
                    through_generation=assignment.claimed_request_generation,
                )
            if record is not None:
                generator = await _dream_generator(config.inference, record.run.budget, resources, None)
        return await process_family_invocation(
            contexts, assignment, config=config, security=security, dream_generator=generator
        )


async def process_family_invocation(
    contexts: RelationalContexts,
    assignment: ArtifactProcessingWorkAssignment,
    *,
    config: BuiltinConfig,
    security: WorkerSecurity | None = None,
    dream_generator: DreamGenerator | None = None,
) -> ArtifactProcessingWorkerCompletion:
    """Run one bounded domain window, preserving its own Review/Cursor rules."""

    generation_purpose = {
        "memory": ModelUsagePurpose.MEMORY_EXTRACTION,
        "experience": ModelUsagePurpose.EXPERIENCE_GENERATION,
        "skill": ModelUsagePurpose.SKILL_GENERATION,
    }.get(assignment.artifact_family)
    with bind_usage_reporter(
        # The runtime-owned recorder writes this window's usage, so the worker's
        # own deadline never covers a statistics transaction.
        contexts.model_usage_reporter(assignment.scope_id),
        generation_purpose=generation_purpose,
        embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING if assignment.artifact_family == "memory" else None,
    ):
        try:
            return await _process_family_invocation(
                contexts, assignment, config=config, security=security, dream_generator=dream_generator
            )
        finally:
            # A completed window leaves its own usage readable.
            await contexts.flush_model_usage()


async def _process_family_invocation(  # noqa: C901 - one guarded dispatch per registered Family
    contexts: RelationalContexts,
    assignment: ArtifactProcessingWorkAssignment,
    *,
    config: BuiltinConfig,
    security: WorkerSecurity | None,
    dream_generator: DreamGenerator | None,
) -> ArtifactProcessingWorkerCompletion:
    if FAMILY_BINDINGS.get(assignment.artifact_family) != assignment.binding_name:
        raise ValueError("processor Family and binding do not match")  # noqa: TRY003
    scope = assignment.scope_id
    invocation = ScopeInvocation(
        assignment,
        authorize_transaction=None if security is None else partial(security.authorize_transaction, scope_id=scope),
    )
    try:
        if operations_for_binding(assignment.binding_name):
            from powercontext.builtin.runtime.dream_processing import process_dream_invocation

            if await process_dream_invocation(
                contexts, assignment, config=config, generator=dream_generator, security=security
            ):
                return ArtifactProcessingWorkerCompletion()
            if assignment.artifact_family in {"skill", "handoff", "prompt"}:
                async with contexts.database.transaction() as connection:
                    await invocation.start(connection)
                    await invocation.complete(connection, remaining_work=False)
                return ArtifactProcessingWorkerCompletion()
        if assignment.artifact_family == "memory":
            result = await contexts.process_memory(
                scope,
                config.runtime.source_window_limit,
                processing=invocation,
                authorize_snapshot=None if security is None else partial(security.authorize_memory, scope),
                on_commit=None if security is None else partial(security.memory_commit, scope_id=scope),
            )
            if result.held_count:
                return ArtifactProcessingWorkerCompletion(held_count=result.held_count, hold_codes=result.hold_codes)
        elif assignment.artifact_family == "experience":
            await contexts.incubate_experience(
                scope,
                config.runtime.source_window_limit,
                processing=invocation,
                on_commit=None if security is None else partial(security.experience_commit, scope_id=scope),
            )
        else:
            result = await contexts.profiles.flush(
                scope,
                processing=invocation,
                authorize_snapshot=None if security is None else partial(security.authorize_profile, scope),
                authorize_commit=None
                if security is None
                else partial(security.authorize_profile_commit, scope_id=scope),
                on_commit=None if security is None else partial(security.profile_commit, scope_id=scope),
            )
            if result.status == "conflict":
                return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.HEAD_CONFLICT)
    except InvocationAlreadyHandled:
        return ArtifactProcessingWorkerCompletion()
    except GenerationConflictError:
        return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
    except RevisionConflictError:
        return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.HEAD_CONFLICT)
    except ArtifactProcessingLeadershipLostError:
        return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST)
    return ArtifactProcessingWorkerCompletion()
