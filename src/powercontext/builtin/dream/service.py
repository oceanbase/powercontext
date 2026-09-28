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

"""Admission and transactional Dream execution, independent of scheduling."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.dream.bindings import DREAM_BINDINGS
from powercontext.builtin.dream.generation import DreamGenerationInput, DreamGenerator
from powercontext.builtin.dream.models import (
    DREAM_PROMPT_VERSION,
    CreateDreamRunRequest,
    DreamBudget,
    DreamCandidateRef,
    DreamError,
    DreamOperation,
    DreamPlan,
    DreamRecord,
    DreamRun,
    DreamRunPage,
    DreamUsage,
    ListDreamRunsRequest,
)
from powercontext.builtin.evidence.models import EvidenceResolutionError, ResolvedEvidence
from powercontext.builtin.evidence.resolver import EvidenceAuthorizer, EvidenceReference, EvidenceResolver, evidence_id
from powercontext.builtin.evidence.selection import select_evidence
from powercontext.builtin.inference.errors import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.dream import DreamRepository, database_now
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import SCOPES_TABLE
from powercontext.builtin.review.errors import ArtifactTargetConflictError
from powercontext.builtin.review.generation import SkillGenerationOrigin, validate_skill_lineage
from powercontext.builtin.review.service import ReviewService
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation

DreamPermission = Literal["read", "contribute", "write"]
DreamAuthorizer = Callable[[str, str, DreamPermission, EvidenceReference | None], Awaitable[None]]
EvidenceFactory = Callable[[str, EvidenceAuthorizer], EvidenceResolver]
ReviewFactory = Callable[[str, AsyncConnection | None], ReviewService]
CandidateAttester = Callable[[AsyncConnection, DreamRecord, str, str], Awaitable[None]]


class DreamService:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        evidence: EvidenceFactory,
        review: ReviewFactory,
        generator: DreamGenerator | None,
        budget: DreamBudget | None = None,
        max_pending_per_scope: int = 32,
        authorize: DreamAuthorizer | None = None,
        authorization_context: Callable[[], AbstractAsyncContextManager[None]] = nullcontext,
        attest_candidate: CandidateAttester | None = None,
        operations: tuple[DreamOperation, ...] = (),
        processing: ScopeInvocation | None = None,
    ) -> None:
        self.database = database
        self.repository = DreamRepository()
        self.artifacts = artifacts
        self.evidence = evidence
        self.review = review
        self.generator = generator
        self.budget = DreamBudget() if budget is None else budget
        self.max_pending_per_scope = max_pending_per_scope
        self.authorize = authorize
        self.authorization_context = authorization_context
        self.attest_candidate = attest_candidate
        self.operations = operations
        self.processing = processing
        self.intents = ArtifactProcessingIntentRepository()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection]:
        async with self.authorization_context(), self.database.transaction() as connection:
            if self.processing is not None:
                await self.processing.guard(connection)
            yield connection

    async def _authorize(
        self,
        scope_id: str,
        principal_id: str,
        permission: DreamPermission,
        ref: EvidenceReference | None = None,
    ) -> None:
        if self.authorize is not None:
            await self.authorize(scope_id, principal_id, permission, ref)
        elif principal_id != "runtime":
            raise DreamError("access_revoked")

    def _resolver(self, scope_id: str, principal_id: str) -> EvidenceResolver:
        async def authorize(ref: EvidenceReference) -> None:
            await self._authorize(scope_id, principal_id, "read", ref)

        resolver = self.evidence(scope_id, authorize)
        resolver.limits = self.budget
        return resolver

    async def create(self, scope_id: str, principal_id: str, request: CreateDreamRunRequest) -> DreamRun:
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            existing = await self.repository.find_request(connection, scope_id, principal_id, request)
            if existing is not None:
                return existing.run
        await self._authorize(scope_id, principal_id, "contribute")
        if request.operation not in self.operations:
            raise DreamError("capability_unavailable")
        resolver = self._resolver(scope_id, principal_id)
        async with self._transaction() as connection:
            # Lock the intent before business rows, matching Worker commit order.
            binding = DREAM_BINDINGS[request.operation]
            await self.intents.ensure(connection, scope_id, binding)
            await self.intents.load(connection, scope_id, binding, for_update=True)
            # Scope-local admission capacity is durable and serialized across replicas.
            result = await connection.execute(
                update(SCOPES_TABLE)
                .where(
                    SCOPES_TABLE.c.scope_id == scope_id,
                )
                .values(version=SCOPES_TABLE.c.version)
            )
            if result.rowcount != 1:
                raise DreamError("scope_not_found")
            existing = await self.repository.find_request(connection, scope_id, principal_id, request, current=True)
            if existing is not None:
                return existing.run
            await self._check_target(connection, scope_id, principal_id, request.target)
            await resolver.resolve(
                connection,
                sources=request.sources,
                artifacts=request.artifacts,
                memory_citations=request.memory_citations,
                project=False,
                lock_memory=True,
            )
            if await self.repository.pending_count(connection, scope_id) >= self.max_pending_per_scope:
                raise DreamError("capacity_exceeded")
            intent = await self.intents.request(connection, scope_id, DREAM_BINDINGS[request.operation])
            record = DreamRecord(
                run=DreamRun(
                    scope_id=scope_id,
                    run_id="dr_" + uuid4().hex,
                    operation=request.operation,
                    target=request.target,
                    accepted_at=await database_now(connection),
                    budget=self.budget,
                ),
                request=request,
                principal_id=principal_id,
                request_generation=intent.requested_generation,
            )
            return (await self.repository.create(connection, record)).run

    async def get(self, scope_id: str, principal_id: str, run_id: str) -> DreamRun:
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            return (await self.repository.get(connection, scope_id, run_id)).run

    async def list(self, scope_id: str, principal_id: str, request: ListDreamRunsRequest) -> DreamRunPage:
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            return await self.repository.list(connection, scope_id, request)

    async def execute(self) -> bool:
        """Execute one accepted Run attempt within a Supervisor Scope invocation.

        Returning false leaves a non-Dream invocation to the Family processor.
        A completed attempt acknowledges this invocation and durably schedules
        any remaining Runs, even when automatic processing is disabled.
        """

        if self.processing is None:
            raise RuntimeError("Dream execution requires a Supervisor invocation")  # noqa: TRY003
        work = self.processing.assignment
        operation: DreamOperation = "derive_skill" if work.artifact_family == "skill" else "refine_experience"
        if DREAM_BINDINGS[operation] != work.binding_name:
            raise ValueError("Dream operation and processing binding do not match")  # noqa: TRY003
        async with self._transaction() as connection:
            await self.processing.start(connection)
            record = await self.repository.next_pending(
                connection, work.scope_id, operation, through_generation=work.claimed_request_generation
            )
            if record is None:
                return False
            record = await self.repository.claim(
                connection, record, model_config_id=None if self.generator is None else self.generator.config_id
            )
            if record.run.terminal:
                await self._complete_invocation(connection, operation)
                return True
        try:
            async with self._transaction() as connection:
                remaining = _remaining_seconds(record, await database_now(connection))
            async with asyncio.timeout(max(remaining, 0)):
                await self._execute(record)
        except (ArtifactProcessingLeadershipLostError, InvocationAlreadyHandled):
            raise
        except (InferenceTimeoutError, InferenceUnavailableError) as error:
            await self._fail_or_retry(record, _error_code(error), retry=True)
        except TimeoutError:
            await self._fail_or_retry(record, "budget_exceeded", retry=False)
        except Exception as error:
            await self._fail_or_retry(record, _error_code(error), retry=False)
        return True

    async def _complete_invocation(self, connection: AsyncConnection, operation: DreamOperation) -> None:
        if self.processing is None:
            raise RuntimeError("Dream execution requires a Supervisor invocation")  # noqa: TRY003
        work = self.processing.assignment
        current = await self.processing.guard(connection)
        remaining = await self.repository.next_pending(connection, work.scope_id, operation)
        if remaining is not None and current.requested_generation == work.claimed_request_generation:
            await self.intents.request(connection, work.scope_id, work.binding_name)
        # Dream never consumes or clears the Family's ordinary Source progress.
        await self.processing.complete(connection, remaining_work=True)

    async def _execute(self, record: DreamRecord) -> None:
        run = record.run
        if (
            self.generator is None
            or self.generator.config_id != run.model_config_id
            or run.prompt_version != DREAM_PROMPT_VERSION
        ):
            raise DreamError("capability_unavailable")
        await self._authorize(run.scope_id, record.principal_id, "contribute")
        resolved = await self._resolve(record)
        usage = run.usage.model_copy(update={"model_calls": run.usage.model_calls + 1})
        if usage.model_calls > run.budget.max_model_calls:
            raise DreamError("budget_exceeded")
        record = record.model_copy(
            update={
                "run": run.model_copy(update={"input_manifest": resolved.manifest, "usage": usage}),
            }
        )
        async with self._transaction() as connection:
            await self.repository.checkpoint(connection, record)
        generated = await self.generator.generate(
            DreamGenerationInput(
                operation=run.operation,
                target_evidence_id=None if run.target is None else evidence_id(run.target),
                evidence=resolved.projection,
            )
        )
        record = record.model_copy(
            update={
                "run": record.run.model_copy(
                    update={
                        "usage": _usage(record.run.usage, generated.usage),
                    }
                )
            }
        )
        async with self._transaction() as connection:
            await self.repository.checkpoint(connection, record)
        if generated.usage.requests > 1 or (
            generated.usage.output_tokens is not None and generated.usage.output_tokens > run.budget.max_output_tokens
        ):
            raise DreamError("budget_exceeded")
        generated.output.validate_operation(record.request)
        plan = _supported_plan(record, generated.output, resolved)
        if isinstance(plan.proposal, SkillContent):
            prepared = await self.review(run.scope_id, None).prepare_skill(plan.proposal)
            plan = plan.model_copy(update={"proposal": prepared})
        await self._commit(record, plan)

    async def _resolve(self, record: DreamRecord, connection: AsyncConnection | None = None) -> ResolvedEvidence:
        request = record.request
        async with self.database.connection(connection) as bound:
            await self._check_target(bound, record.run.scope_id, record.principal_id, request.target)
            resolver = self._resolver(record.run.scope_id, record.principal_id)
            resolver.limits = record.run.budget
            return await resolver.resolve(
                bound,
                sources=request.sources,
                artifacts=request.artifacts,
                memory_citations=request.memory_citations,
                include_memory_text=request.operation != "derive_skill",
                pinned=record.run.input_manifest,
                lock_memory=connection is not None,
            )

    async def _commit(self, record: DreamRecord, plan: DreamPlan) -> None:
        run = record.run
        async with self._transaction() as connection:
            await self.repository.lock_owned(connection, record)
            await self._authorize(run.scope_id, record.principal_id, "contribute")
            resolved = await self._resolve(record, connection)
            candidate = None
            if plan.outcome == "proposed":
                if self.attest_candidate is not None:
                    family = "skill" if run.operation == "derive_skill" else "experience"
                    await self.attest_candidate(connection, record, _candidate_id(record), family)
                candidate = await self._propose(connection, record, plan, resolved)
            completed = run.model_copy(
                update={
                    "status": "succeeded",
                    "outcome": plan.outcome,
                    "reason": plan.reason,
                    "candidate": candidate,
                    "completed_at": await database_now(connection),
                }
            )
            await self.repository.finish(connection, record, completed)
            await self._complete_invocation(connection, run.operation)

    async def _propose(
        self,
        connection: AsyncConnection,
        record: DreamRecord,
        plan: DreamPlan,
        resolved: ResolvedEvidence,
    ) -> DreamCandidateRef:
        selected = select_evidence(
            resolved.manifest,
            plan.evidence_ids,
            skill=record.run.operation == "derive_skill",
            target=record.run.target,
        )
        review = self.review(record.run.scope_id, connection)
        if isinstance(plan.proposal, ExperienceContent):
            candidate = await review.propose_experience(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                memory_citations=selected.memory_citations,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, SkillContent):
            validate_skill_lineage(SkillGenerationOrigin.EXPERIENCE, selected.sources, selected.artifacts, None)
            candidate = await review.propose_skill(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=None,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        else:
            raise DreamError("invalid_generation_output")
        return DreamCandidateRef(candidate_id=candidate.candidate_id, version=candidate.version)

    async def _check_target(
        self,
        connection: AsyncConnection,
        scope_id: str,
        principal_id: str,
        target: ArtifactRef | None,
    ) -> None:
        if target is None:
            return
        await self._authorize(scope_id, principal_id, "write", target)
        current = await self.artifacts.latest(
            connection,
            scope_id,
            target.family,
            target.artifact_id,
            for_update=True,
        )
        if current.as_ref() != target:
            raise DreamError("artifact_conflict")

    async def _fail_or_retry(self, record: DreamRecord, code: str, *, retry: bool) -> None:
        if code == "attempt_conflict":
            return
        try:
            async with self._transaction() as connection:
                await self.repository.lock_owned(connection, record)
                current = await self.repository.get(connection, record.run.scope_id, record.run.run_id, current=True)
                now = await database_now(connection)
                can_retry = (
                    retry
                    and current.run.attempt_count < current.run.budget.max_model_calls
                    and current.deadline_at is not None
                    and now < current.deadline_at
                )
                if can_retry:
                    queued = current.model_copy(
                        update={
                            "run": current.run.model_copy(update={"status": "queued"}),
                        }
                    )
                    await self.repository.checkpoint(connection, queued)
                else:
                    failed = current.run.model_copy(update={"status": "failed", "error": code, "completed_at": now})
                    await self.repository.finish(connection, current, failed)
                await self._complete_invocation(connection, current.run.operation)
        except DreamError as error:
            if error.code != "attempt_conflict":
                raise


def _candidate_id(record: DreamRecord) -> str:
    return "cand_dream_" + record.run.run_id


def _supported_plan(record: DreamRecord, plan: DreamPlan, resolved: ResolvedEvidence) -> DreamPlan:
    if plan.outcome != "proposed":
        return plan
    try:
        select_evidence(
            resolved.manifest,
            plan.evidence_ids,
            skill=record.run.operation == "derive_skill",
            target=record.run.target,
        )
    except EvidenceResolutionError as error:
        if error.code != "needs_evidence":
            raise
        return DreamPlan(
            outcome="needs_evidence",
            reason="The selected Memory-derived evidence has no usable root Source supporting task actions and results.",
        )
    return plan


def _remaining_seconds(record: DreamRecord, now: datetime) -> float:
    if record.deadline_at is None:
        raise DreamError("budget_exceeded")
    return (record.deadline_at - now).total_seconds()


def _usage(previous: DreamUsage, current: InferenceUsage) -> DreamUsage:
    first = previous.model_calls == 1
    return DreamUsage(
        model_calls=previous.model_calls + max(0, current.requests - 1),
        input_tokens=_add_tokens(previous.input_tokens, current.input_tokens, first=first),
        output_tokens=_add_tokens(previous.output_tokens, current.output_tokens, first=first),
    )


def _add_tokens(previous: int | None, current: int | None, *, first: bool) -> int | None:
    if current is None or (not first and previous is None):
        return None
    return (previous or 0) + current


def _error_code(error: Exception) -> str:
    if isinstance(error, EvidenceResolutionError) and error.code in {"invalid_memory_citation", "reference_not_found"}:
        return "evidence_unavailable"
    if isinstance(error, (DreamError, EvidenceResolutionError)):
        return error.code
    if isinstance(error, ArtifactTargetConflictError):
        return "artifact_conflict"
    if isinstance(error, InvalidInferenceOutputError):
        return "invalid_generation_output"
    return "generation_failed"
