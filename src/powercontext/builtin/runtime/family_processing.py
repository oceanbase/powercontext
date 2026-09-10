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
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from powercontext._logging import log_safely
from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError, GenerationConflictError
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.errors import RevisionConflictError

if TYPE_CHECKING:
    from powercontext.builtin.runtime.relational import RelationalContexts
    from powercontext.server.processing_security import WorkerSecurity


FAMILY_BINDINGS = {
    "memory": SOURCE_WINDOW_TRIGGER_NAME,
    "experience": EXPERIENCE_INCUBATION_CURSOR_NAME,
    "profile": PROFILE_SOURCE_WINDOW_BINDING,
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
        _embedding_models,
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
        embedding, _ = await _embedding_models(config.inference, resources, None)
        contexts = await resources.enter_async_context(
            open_builtin_contexts(
                config,
                candidate_pipeline=pipelines[1],
                experience_pipeline=pipelines[2],
                embedding_model=_usage_reporting_embedding_model(embedding),
                prompt_registry=_prompt_registry(
                    config.runtime,
                    (
                        ("memory.extract", None, pipelines[1]),
                        ("experience.incubate", None, pipelines[2]),
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
        return await process_family_invocation(contexts, assignment, config=config, security=security)


async def process_family_invocation(
    contexts: RelationalContexts,
    assignment: ArtifactProcessingWorkAssignment,
    *,
    config: BuiltinConfig,
    security: WorkerSecurity | None = None,
) -> ArtifactProcessingWorkerCompletion:
    """Run one bounded domain window, preserving its own Review/Cursor rules."""

    async def report(purpose: ModelUsagePurpose, operation: ModelUsageOperation, usage: InferenceUsage) -> None:
        try:
            await contexts.statistics(assignment.scope_id).record(purpose, operation, usage, datetime.now(UTC).date())
        except Exception as error:
            # Usage attribution keeps the same best-effort behavior as the SDK facade.
            log_safely(
                logger,
                logging.WARNING,
                "Artifact processing usage recording failed",
                extra={"event": "artifact_processing.usage_failed", "exception_type": type(error).__name__},
            )

    generation_purpose = {
        "memory": ModelUsagePurpose.MEMORY_EXTRACTION,
        "experience": ModelUsagePurpose.EXPERIENCE_GENERATION,
    }.get(assignment.artifact_family)
    with bind_usage_reporter(
        report,
        generation_purpose=generation_purpose,
        embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING if assignment.artifact_family == "memory" else None,
    ):
        return await _process_family_invocation(contexts, assignment, config=config, security=security)


async def _process_family_invocation(
    contexts: RelationalContexts,
    assignment: ArtifactProcessingWorkAssignment,
    *,
    config: BuiltinConfig,
    security: WorkerSecurity | None,
) -> ArtifactProcessingWorkerCompletion:
    if FAMILY_BINDINGS.get(assignment.artifact_family) != assignment.binding_name:
        raise ValueError("processor Family and binding do not match")  # noqa: TRY003
    scope = assignment.scope_id
    invocation = ScopeInvocation(
        assignment,
        authorize_transaction=None if security is None else partial(security.authorize_transaction, scope_id=scope),
    )
    try:
        if assignment.artifact_family == "memory":
            await contexts.process_memory(
                scope,
                config.runtime.source_window_limit,
                processing=invocation,
                authorize_snapshot=None if security is None else partial(security.authorize_memory, scope),
                on_commit=None if security is None else partial(security.memory_commit, scope_id=scope),
            )
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
