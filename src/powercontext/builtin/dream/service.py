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
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.memory.models import MemoryDreamCandidateProposal, MemoryDreamWrite
from powercontext.builtin.artifacts.profile.models import ProfileCandidateProposal, ProfileWriteContent
from powercontext.builtin.artifacts.prompt.models import PromptContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent
from powercontext.builtin.catalog_changes.models import CatalogChangeCandidate, CatalogChangeProposal, TagChangeProposal
from powercontext.builtin.catalog_changes.service import CatalogChangeService
from powercontext.builtin.dream.bindings import (
    DREAM_BINDINGS,
    binding_for_request,
    operation_spec,
    operations_for_binding,
)
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
from powercontext.builtin.dream.provenance import candidate_audit, proposal_fingerprint
from powercontext.builtin.evidence.models import (
    EvidenceResolutionError,
    ResolvedEvidence,
    reference_key,
    unique_references,
)
from powercontext.builtin.evidence.resolver import EvidenceAuthorizer, EvidenceReference, EvidenceResolver, evidence_id
from powercontext.builtin.evidence.selection import select_evidence
from powercontext.builtin.inference.errors import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import attach_candidate_audit, proposal_digest
from powercontext.builtin.persistence.catalog_changes import CATALOG_CANDIDATE_HEADS
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.dream import DreamRepository, database_now
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.persistence.tables import ARTIFACT_CANDIDATE_HEADS_TABLE, SCOPES_TABLE
from powercontext.builtin.review.errors import ArtifactTargetConflictError, InvalidCandidateError
from powercontext.builtin.review.generation import SkillGenerationOrigin, validate_skill_lineage
from powercontext.builtin.review.models import ArtifactCandidate, CandidateStatus
from powercontext.builtin.review.service import ReviewService
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.tags import TagPreconditionError

DreamPermission = Literal["read", "contribute", "write", "write_tags"]
DreamAuthorizer = Callable[[str, str, DreamPermission, EvidenceReference | None], Awaitable[None]]
EvidenceFactory = Callable[[str, EvidenceAuthorizer], EvidenceResolver]
ReviewFactory = Callable[[str, AsyncConnection | None], ReviewService]
CandidateAttester = Callable[[AsyncConnection, DreamRecord, str, str], Awaitable[None]]
CatalogFactory = Callable[[str, AsyncConnection | None], CatalogChangeService]


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
        authorize_candidate: CandidateAttester | None = None,
        operations: tuple[DreamOperation, ...] = (),
        processing: ScopeInvocation | None = None,
        catalog_changes: CatalogFactory | None = None,
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
        self.authorize_candidate = authorize_candidate
        self.operations = operations
        self.processing = processing
        self.intents = ArtifactProcessingIntentRepository()
        self.catalog_changes = catalog_changes

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
        if request.operation == "revise_prompt" and request.target is not None:
            await self.review(scope_id, None).require_prompt_target(request.target)
        resolver = self._resolver(scope_id, principal_id)
        async with self._transaction() as connection:
            # Lock the intent before business rows, matching Worker commit order.
            binding = binding_for_request(request)
            if not any(DREAM_BINDINGS.get(operation) == binding for operation in self.operations):
                raise DreamError("capability_unavailable")
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
            profile_policy = (
                await ProfilePolicyRepository().get(connection, scope_id, for_update=True)
                if request.operation == "revise_profile"
                else None
            )
            await self._check_target(connection, scope_id, principal_id, request.target)
            await self._authorize_memory_changes(scope_id, principal_id, request)
            if request.tag_target is not None:
                await self._check_tag_target(connection, scope_id, principal_id, request)
            artifacts, citations, target = _resolve_references(request)
            await resolver.resolve(
                connection,
                sources=request.sources,
                artifacts=artifacts,
                memory_citations=citations,
                target=target,
                project=False,
                lock_memory=True,
                expose_catalog_target=request.operation == "revise_tags",
            )
            if await self.repository.pending_count(connection, scope_id) >= self.max_pending_per_scope:
                raise DreamError("capacity_exceeded")
            intent = await self.intents.request(connection, scope_id, binding)
            record = DreamRecord(
                run=DreamRun(
                    scope_id=scope_id,
                    run_id="dr_" + uuid4().hex,
                    operation=request.operation,
                    target=request.target,
                    tag_target=request.tag_target,
                    accepted_at=await database_now(connection),
                    budget=self.budget,
                ),
                request=request,
                principal_id=principal_id,
                request_generation=intent.requested_generation,
                profile_policy=profile_policy,
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

    async def execute(self) -> bool:  # noqa: C901 - fenced admission, fair dispatch, and bounded recovery
        """Execute one accepted Run attempt within a Supervisor Scope invocation.

        Returning false leaves a non-Dream invocation to the Family processor.
        A completed attempt acknowledges this invocation and durably schedules
        any remaining Runs, even when automatic processing is disabled.
        """

        if self.processing is None:
            raise RuntimeError("Dream execution requires a Supervisor invocation")  # noqa: TRY003
        work = self.processing.assignment
        operations = operations_for_binding(work.binding_name)
        if not operations:
            raise ValueError("Dream operation and processing binding do not match")  # noqa: TRY003
        async with self._transaction() as connection:
            await self.processing.start(connection)
            record = await self.repository.next_pending(
                connection,
                work.scope_id,
                operations,
                through_generation=work.claimed_request_generation,
                binding=work.binding_name,
            )
            if record is None:
                return False
            intent = await self.processing.guard(connection)
            if (
                intent.consecutive_dream_attempts >= 4
                and intent.dirty_generation > intent.clean_generation
                and work.artifact_family not in {"skill", "handoff", "prompt"}
            ):
                # The Source pass acknowledges this invocation. Keep the queued
                # Dream runnable even if that pass consumes all ordinary input.
                if intent.requested_generation == work.claimed_request_generation:
                    await self.intents.request(connection, work.scope_id, work.binding_name)
                return False
            record = await self.repository.claim(
                connection, record, model_config_id=None if self.generator is None else self.generator.config_id
            )
            if record.run.terminal:
                await self._complete_invocation(connection)
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

    async def _complete_invocation(self, connection: AsyncConnection) -> None:
        if self.processing is None:
            raise RuntimeError("Dream execution requires a Supervisor invocation")  # noqa: TRY003
        work = self.processing.assignment
        current = await self.processing.guard(connection)
        remaining = await self.repository.next_pending(
            connection,
            work.scope_id,
            operations_for_binding(work.binding_name),
            binding=work.binding_name,
        )
        if remaining is not None and current.requested_generation == work.claimed_request_generation:
            await self.intents.request(connection, work.scope_id, work.binding_name)
        # Dream never consumes or clears the Family's ordinary Source progress.
        await self.processing.complete(connection, remaining_work=True, dream=True)

    async def _execute(self, record: DreamRecord) -> None:
        run = record.run
        if (
            self.generator is None
            or self.generator.config_id != run.model_config_id
            or run.prompt_version != DREAM_PROMPT_VERSION
        ):
            raise DreamError("capability_unavailable")
        await self._authorize(run.scope_id, record.principal_id, "contribute")
        prompt_definition = None
        if run.operation == "revise_prompt" and run.target is not None:
            prompt_definition = await self.review(run.scope_id, None).require_prompt_target(run.target)
        before_tags = ()
        if record.request.tag_target is not None:
            before_tags = (await self._catalog(run.scope_id, None).inspect_target(record.request.tag_target)).tags
        await self._preflight_target(record)
        resolved = await self._resolve(record)
        record = record.model_copy(
            update={
                "proposal_fingerprint": proposal_fingerprint(record, resolved),
                "run": run.model_copy(update={"input_manifest": resolved.manifest}),
            }
        )
        if await self._reuse_before_generation(record):
            return
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
                target_evidence_id=_target_evidence_id(record.request),
                evidence=resolved.projection,
                profile_policy=record.profile_policy,
                prompt_definition=prompt_definition,
                tag_target=record.request.tag_target,
                before_tags=before_tags,
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
        operation_spec(run.operation).validator(generated.output, record.request)
        plan = _supported_plan(record, generated.output, resolved)
        if isinstance(plan.proposal, TagChangeProposal) and plan.proposal.after_tags == before_tags:
            plan = DreamPlan(outcome="no_change", reason="The proposed labels already match the current tag set.")
        if isinstance(plan.proposal, SkillContent):
            if run.operation == "revise_skill" and run.target is not None:
                prepared = await self.review(run.scope_id, None).prepare_skill_revision(run.target, plan.proposal)
            else:
                prepared = await self.review(run.scope_id, None).prepare_skill(plan.proposal)
            plan = plan.model_copy(update={"proposal": prepared})
        await self._commit(record, plan)

    async def _preflight_target(self, record: DreamRecord) -> None:
        run = record.run
        if run.operation == "revise_skill" and run.target is not None:
            try:
                await self.review(run.scope_id, None).require_instruction_skill_target(run.target)
            except InvalidCandidateError as error:
                if error.detail == "unsupported_target":
                    raise DreamError("unsupported_target") from error
                raise
        if run.operation == "revise_profile":
            async with self._transaction() as connection:
                await self._check_profile_policy(connection, record)

    async def _resolve(self, record: DreamRecord, connection: AsyncConnection | None = None) -> ResolvedEvidence:
        request = record.request
        # Tag basis inspection acquires a write lock even during generation.
        # Flush access audit writes after this transaction, including standalone resolution.
        async with self.authorization_context(), self.database.connection(connection) as bound:
            await self._check_target(bound, record.run.scope_id, record.principal_id, request.target)
            await self._authorize_memory_changes(record.run.scope_id, record.principal_id, request)
            if request.tag_target is not None:
                await self._check_tag_target(bound, record.run.scope_id, record.principal_id, request)
            resolver = self._resolver(record.run.scope_id, record.principal_id)
            resolver.limits = record.run.budget
            artifacts, citations, target = _resolve_references(request)
            return await resolver.resolve(
                bound,
                sources=request.sources,
                artifacts=artifacts,
                memory_citations=citations,
                target=target,
                include_memory_text=request.operation != "derive_skill",
                pinned=record.run.input_manifest,
                lock_memory=connection is not None,
                expose_prompt_target=request.operation == "revise_prompt",
                expose_catalog_target=request.operation == "revise_tags",
            )

    async def _commit(self, record: DreamRecord, plan: DreamPlan) -> None:
        run = record.run
        async with self._transaction() as connection:
            await self.repository.lock_owned(connection, record)
            await self._authorize(run.scope_id, record.principal_id, "contribute")
            if run.operation == "revise_profile":
                await self._check_profile_policy(connection, record)
            resolved = await self._resolve(record, connection)
            candidate = None
            if plan.outcome == "proposed":
                if self.attest_candidate is not None and run.operation != "revise_tags":
                    family = {
                        "derive_skill": "skill",
                        "revise_skill": "skill",
                        "refine_experience": "experience",
                        "revise_profile": "profile",
                        "revise_memory": "memory",
                        "revise_topic_memory": "topic-memory",
                        "refresh_handoff": "handoff",
                        "revise_prompt": "prompt",
                    }[run.operation]
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
            await self._complete_invocation(connection)

    async def _check_profile_policy(self, connection: AsyncConnection, record: DreamRecord) -> None:
        policy = await ProfilePolicyRepository().get(connection, record.run.scope_id, for_update=True)
        if record.profile_policy is None or policy is None:
            raise DreamError("capability_unavailable")
        if policy.version != record.profile_policy.version:
            raise DreamError("policy_changed")

    async def _propose(  # noqa: C901 - operation-specific Candidate adapters share one atomic Run commit
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
            target=_resolve_references(record.request)[2],
            catalog=record.run.operation == "revise_tags",
        )
        if isinstance(plan.proposal, TagChangeProposal) and record.request.tag_target is not None:
            service = self._catalog(record.run.scope_id, connection)
            current = await service.inspect_target(record.request.tag_target)
            proposal = CatalogChangeProposal(
                **record.request.tag_target.model_dump(mode="python"),
                before_tags=current.tags,
                after_tags=plan.proposal.after_tags,
            )
            candidate = await service.propose(
                proposal,
                reason=plan.reason,
                sources=selected.sources,
                artifacts=selected.artifacts,
                memory_citations=selected.memory_citations,
                dream_run_id=record.run.run_id,
                candidate_id=_candidate_id(record),
                audit=candidate_audit(record, proposal),
            )
            return DreamCandidateRef(
                candidate_id=candidate.candidate_id, version=candidate.version, kind="catalog_change"
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
            if record.run.operation == "derive_skill":
                validate_skill_lineage(SkillGenerationOrigin.EXPERIENCE, selected.sources, selected.artifacts, None)
            candidate = await review.propose_skill(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                memory_citations=selected.memory_citations,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, ProfileWriteContent) and record.run.operation == "revise_profile":
            await self._check_profile_policy(connection, record)
            policy = record.profile_policy
            if policy is None or record.run.target is None or self.generator is None:
                raise DreamError("capability_unavailable")
            proposal = ProfileCandidateProposal(
                content=plan.proposal.content,
                dream_run_id=record.run.run_id,
                policy_version=policy.version,
                generator_id=self.generator.config_id,
                generator_version=DREAM_PROMPT_VERSION,
                created_at=await database_now(connection),
            )
            candidate = await review.propose_profile_dream(
                proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
                memory_citations=selected.memory_citations,
            )
        elif isinstance(plan.proposal, MemoryDreamWrite) and record.run.operation == "revise_memory":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            proposal = MemoryDreamCandidateProposal(
                base=record.run.target,
                dream_run_id=record.run.run_id,
                changes=plan.proposal.changes,
            )
            candidate = await review.propose_memory_dream(
                proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                memory_citations=selected.memory_citations,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, TopicMemoryContent) and record.run.operation == "revise_topic_memory":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            candidate = await review.propose_topic_memory_dream(
                plan.proposal,
                memory_citations=selected.memory_citations,
                sources=selected.sources,
                artifacts=selected.artifacts,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, HandoffContent) and record.run.operation == "refresh_handoff":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            candidate = await review.propose_handoff_dream(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                memory_citations=selected.memory_citations,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        elif isinstance(plan.proposal, PromptContent) and record.run.operation == "revise_prompt":
            if record.run.target is None:
                raise DreamError("invalid_generation_output")
            candidate = await review.propose_prompt_dream(
                plan.proposal,
                sources=selected.sources,
                artifacts=selected.artifacts,
                memory_citations=selected.memory_citations,
                target=record.run.target,
                reason=plan.reason,
                candidate_id=_candidate_id(record),
            )
        else:
            raise DreamError("invalid_generation_output")
        await attach_candidate_audit(
            connection, record.run.scope_id, candidate, candidate_audit(record, candidate.proposal)
        )
        return DreamCandidateRef(candidate_id=candidate.candidate_id, version=candidate.version)

    async def _reuse_before_generation(self, record: DreamRecord) -> bool:
        async with self._transaction() as connection:
            await self.repository.lock_owned(connection, record)
            if record.run.operation == "revise_profile":
                await self._check_profile_policy(connection, record)
            resolved = await self._resolve(record, connection)
            for previous in await self.repository.matching_proposals(connection, record):
                ref = previous.run.candidate
                if ref is None:
                    continue
                table = CATALOG_CANDIDATE_HEADS if ref.kind == "catalog_change" else ARTIFACT_CANDIDATE_HEADS_TABLE
                await connection.execute(
                    update(table)
                    .where(
                        table.c.scope_id == record.run.scope_id,
                        table.c.candidate_id == ref.candidate_id,
                    )
                    .values(version=table.c.version)
                )
                if ref.kind == "catalog_change":
                    candidate = await self._catalog(record.run.scope_id, connection).get(ref.candidate_id, current=True)
                else:
                    candidate = await self.review(record.run.scope_id, connection).get_candidate(
                        ref.candidate_id, current=True
                    )
                audit = candidate.audit
                if (
                    audit is None
                    or audit.proposal_fingerprint != record.proposal_fingerprint
                    or audit.proposal_digest != proposal_digest(candidate.proposal)
                    or candidate.status == CandidateStatus.APPROVED
                    or not _candidate_matches_evidence(candidate, record, resolved)
                ):
                    continue
                if self.authorize_candidate is not None:
                    family = (
                        candidate.proposal.target.family
                        if isinstance(candidate, CatalogChangeCandidate)
                        else candidate.family
                    )
                    await self.authorize_candidate(connection, record, candidate.candidate_id, family)
                completed = record.run.model_copy(
                    update={
                        "status": "succeeded",
                        "outcome": "proposed" if candidate.status == CandidateStatus.PENDING else "no_change",
                        "reason": "Reused pending proposal for the same target, evidence and policy."
                        if candidate.status == CandidateStatus.PENDING
                        else "An unchanged proposal was already rejected; new evidence or a changed target is required.",
                        "candidate": DreamCandidateRef(
                            candidate_id=candidate.candidate_id, version=candidate.version, kind=ref.kind
                        ),
                        "reused": True,
                        "completed_at": await database_now(connection),
                    }
                )
                await self.repository.finish(connection, record, completed)
                await self._complete_invocation(connection)
                return True
        return False

    def _catalog(self, scope_id: str, connection: AsyncConnection | None) -> CatalogChangeService:
        if self.catalog_changes is None:
            raise DreamError("capability_unavailable")
        return self.catalog_changes(scope_id, connection)

    async def _check_tag_target(
        self, connection: AsyncConnection, scope_id: str, principal_id: str, request: CreateDreamRunRequest
    ) -> None:
        target = request.tag_target
        if target is None:
            raise DreamError("invalid_target")
        await self._authorize(scope_id, principal_id, "write_tags", target.basis_ref or target.basis_citation)
        await self._catalog(scope_id, connection).inspect_target(target)

    async def _authorize_memory_changes(self, scope_id: str, principal_id: str, request: CreateDreamRunRequest) -> None:
        if request.operation == "revise_memory":
            for citation in request.memory_citations:
                await self._authorize(scope_id, principal_id, "write", citation)

    async def _check_target(
        self,
        connection: AsyncConnection,
        scope_id: str,
        principal_id: str,
        target: ArtifactRef | None,
    ) -> None:
        if target is None:
            return
        await self._authorize(scope_id, principal_id, "read" if target.family == "memory" else "write", target)
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
                await self._complete_invocation(connection)
        except DreamError as error:
            if error.code != "attempt_conflict":
                raise


def _candidate_id(record: DreamRecord) -> str:
    return "cand_dream_" + record.run.run_id


def _candidate_matches_evidence(
    candidate: ArtifactCandidate[Any] | CatalogChangeCandidate, record: DreamRecord, resolved: ResolvedEvidence
) -> bool:
    """A shared root alone cannot authorize stale or differently selected evidence."""
    if isinstance(candidate, CatalogChangeCandidate):
        requested = record.request.tag_target
        if requested is None or any(
            getattr(candidate.proposal, field) != getattr(requested, field)
            for field in ("target", "expected_etag", "basis_ref", "basis_citation")
        ):
            return False
    elif candidate.target != record.run.target:
        return False
    available = set()
    for node in resolved.manifest.nodes:
        if node.source is not None:
            available.add(reference_key(node.source))
        if node.artifact is not None:
            available.add(reference_key(node.artifact))
        available.update(reference_key(citation) for citation in node.memory_citations)
    return all(
        reference_key(reference) in available
        for reference in (*candidate.sources, *candidate.artifacts, *candidate.memory_citations)
    )


def _resolve_references(request: CreateDreamRunRequest):
    target = request.tag_target
    if target is None:
        return request.artifacts, request.memory_citations, request.target
    return (
        unique_references((*request.artifacts, *((target.basis_ref,) if target.basis_ref is not None else ()))),
        unique_references((
            *request.memory_citations,
            *((target.basis_citation,) if target.basis_citation is not None else ()),
        )),
        target.basis_ref,
    )


def _target_evidence_id(request: CreateDreamRunRequest) -> str | None:
    target = request.target
    if request.tag_target is not None:
        target = request.tag_target.basis_ref or request.tag_target.basis_citation
    return None if target is None else evidence_id(target)


def _supported_plan(record: DreamRecord, plan: DreamPlan, resolved: ResolvedEvidence) -> DreamPlan:
    if plan.outcome != "proposed":
        return plan
    try:
        select_evidence(
            resolved.manifest,
            plan.evidence_ids,
            skill=record.run.operation == "derive_skill",
            target=_resolve_references(record.request)[2],
            catalog=record.run.operation == "revise_tags",
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
    if isinstance(error, TagPreconditionError):
        return "tag_precondition_failed"
    if isinstance(error, (InvalidInferenceOutputError, InvalidCandidateError)):
        return "invalid_generation_output"
    return "generation_failed"
