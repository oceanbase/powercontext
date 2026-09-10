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

"""Dream's exact requests execute inside their output Family's Scope Worker."""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING

from powercontext.builtin.dream.generation import DreamGenerator
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
from powercontext.builtin.runtime.processing_execution import ScopeInvocation

if TYPE_CHECKING:
    from powercontext.builtin.runtime.relational import RelationalContexts
    from powercontext.server.processing_security import WorkerSecurity


async def process_dream_invocation(
    contexts: RelationalContexts,
    assignment: ArtifactProcessingWorkAssignment,
    *,
    config: BuiltinConfig,
    generator: DreamGenerator | None,
    security: WorkerSecurity | None = None,
) -> bool:
    access = None
    if security is not None:
        from powercontext.server.dream_access import DreamAccess

        access = DreamAccess(security.access)
    service = contexts.dream(
        generator,
        budget=config.runtime.dream_budget,
        max_pending_per_scope=config.runtime.dream_max_pending_per_scope,
        authorize=None if access is None else access.authorize,
        authorization_context=nullcontext if access is None else access.access.defer_decision_audit,
        attest_candidate=None if access is None else access.attest_candidate,
        processing=ScopeInvocation(assignment),
    )
    return await service.execute()
