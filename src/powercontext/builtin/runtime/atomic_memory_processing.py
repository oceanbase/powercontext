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

"""One Source window: prepare all Atomic Memory decisions, then publish once."""

from __future__ import annotations

import logging
import math
from base64 import urlsafe_b64encode
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, replace
from functools import partial
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal, NoReturn, cast

from sqlalchemy import func, literal, select, true, tuple_, update
from sqlalchemy.exc import OperationalError

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryGenerationPipeline,
    log_prefixed_extraction,
    project_atomic_memory_evidence,
    require_atomic_memory_pipeline,
)
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.one_pass import (
    ONE_PASS_MAX_MEMORIES,
    ONE_PASS_MEMORIES_PER_QUERY,
    apply_one_pass_operations,
    apply_one_pass_recheck,
    one_pass_instructions,
    parse_one_pass_entries,
    render_existing_memories,
    render_recheck,
)
from powercontext.builtin.artifacts.atomic_memory.prefixed_extraction import (
    PrefixedExtractionUnavailable,
    prefix_handle,
    prefix_start,
    source_records,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS,
    AtomicMemoryArtifactEvidence,
    AtomicMemoryReconciliationOutput,
    AtomicMemoryRevisionIdentityError,
    AtomicMemoryWindowWorkset,
    AtomicMemoryWorkingItem,
    RedundantAtomicMemoryRevisionError,
)
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.memory.protocols import (
    MemoryWriteGate,
    MemoryWriteGateRequest,
    MemoryWriteVerdict,
)
from powercontext.builtin.artifacts.prompt.errors import PromptError
from powercontext.builtin.artifacts.prompt.service import PromptService, current_prompt
from powercontext.builtin.artifacts.search import analyze_text, fts_query_requirements
from powercontext.builtin.decision_observations import DecisionObservation
from powercontext.builtin.inference import (
    InferencePrivacyError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
    embed_query,
)
from powercontext.builtin.inference.models import ConversationReply, ConversationTurn
from powercontext.builtin.inference.protocols import StructuredGenerator
from powercontext.builtin.persistence.atomic_memory_index import (
    DEFAULT_ATOMIC_MEMORY_RELATED_PAGE_SIZE,
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryRelatedCursor,
    AtomicMemoryRelatedRequest,
    atomic_memory_embedding_input,
    atomic_memory_related_requirement,
)
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.decision_observations import DecisionObservationRepository
from powercontext.builtin.persistence.errors import (
    GenerationConflictError,
    RepositoryNotFoundError,
)
from powercontext.builtin.persistence.memory_windows import MemorySourceWindowRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.processing_diagnostics import (
    ProcessingDiagnostics,
    ProcessingFailureReason,
    ProcessingStage,
    attach_diagnostics,
    classify_processing_storage_failure,
)
from powercontext.builtin.runtime.atomic_memory import AtomicMemoryApplication, deferred_decision_audit
from powercontext.builtin.runtime.decision_policy import emit_decision_observation
from powercontext.builtin.runtime.models import MemoryFlushResult
from powercontext.builtin.runtime.processing_execution import ScopeInvocation
from powercontext.builtin.runtime.protocols import RuntimeTracing
from powercontext.builtin.source_eligibility import is_generation_eligible
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME, SourceHighWatermark, SourceWindowTrigger
from powercontext.errors import RevisionConflictError

if TYPE_CHECKING:
    from powercontext.builtin.runtime.config import RuntimeConfig


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AtomicMemoryProcessingConfig:
    related_mode: Literal["auto", "fts", "vector", "hybrid"] = "auto"
    related_max_distance: float = 1.0
    related_page_size: int = DEFAULT_ATOMIC_MEMORY_RELATED_PAGE_SIZE
    related_fts_fallback: bool = False
    alias_recall: bool = True
    comparison_batch_size: int = 20
    input_tokens_limit: int = 64_000
    processing_mode: Literal["per_candidate", "one_pass"] = "per_candidate"

    def __post_init__(self) -> None:
        if (
            self.related_mode not in {"auto", "fts", "vector", "hybrid"}
            or self.processing_mode not in {"per_candidate", "one_pass"}
            or not math.isfinite(self.related_max_distance)
            or self.related_max_distance < 0
            or (
                isinstance(self.related_page_size, bool)
                or not isinstance(self.related_page_size, int)
                or not 1 <= self.related_page_size <= 1000
            )
            or self.comparison_batch_size < 1
            or self.input_tokens_limit < 1
        ):
            raise ValueError("Atomic Memory processing requires a valid mode, threshold, page size and budgets")  # noqa: TRY003

    @classmethod
    def from_runtime(cls, runtime: RuntimeConfig) -> AtomicMemoryProcessingConfig:
        return cls(
            related_mode=runtime.atomic_memory_related_mode,
            related_max_distance=runtime.atomic_memory_related_max_distance,
            related_page_size=runtime.atomic_memory_related_page_size,
            related_fts_fallback=runtime.atomic_memory_related_fts_fallback,
            alias_recall=runtime.atomic_memory_alias_recall,
            comparison_batch_size=runtime.atomic_memory_comparison_batch_size,
            input_tokens_limit=runtime.atomic_memory_input_tokens_limit,
            processing_mode=runtime.atomic_memory_processing_mode,
        )


class AtomicMemorySourceWindowProcessor:
    """Keep the existing cursor/binding while replacing collection Memory extraction."""

    def __init__(
        self,
        application: AtomicMemoryApplication,
        sources: SourceRepository,
        cursors: SourceCursorRepository,
        *,
        pipeline: object | None,
        prompts: PromptService | None = None,
        config: AtomicMemoryProcessingConfig | None = None,
        memory_write_gate: MemoryWriteGate | None = None,
        decision_observations: DecisionObservationRepository | None = None,
        decision_observation_retention_days: int | None = None,
        tracing: RuntimeTracing | None = None,
    ) -> None:
        self.application = application
        self.sources = sources
        self.cursors = cursors
        self.pipeline = None if pipeline is None else require_atomic_memory_pipeline(pipeline)
        self.prompts = prompts
        self.config = config or AtomicMemoryProcessingConfig()
        self.memory_write_gate = memory_write_gate
        self.decision_observations = decision_observations
        self.decision_observation_retention_days = decision_observation_retention_days
        self.windows = MemorySourceWindowRepository()
        self.tracing = tracing

    async def flush(
        self,
        scope_id: str,
        limit: int,
        *,
        processing: ScopeInvocation | None = None,
        context: ArtifactSearchExecutionContext | None = None,
    ) -> MemoryFlushResult:
        selected_context = context
        # Scope checks may retain a separate configured audit connection. Flush its
        # decision events after every business transaction in this operation closes.
        async with deferred_decision_audit(selected_context):
            with self._stage("memory.flush", {"powercontext.memory.flush.source_count": 0}) as span:
                if self.prompts is not None:
                    keys = ("memory.extract", "atomic_memory.extract", "atomic_memory.reconcile")
                    async with self.prompts.snapshot(scope_id, keys):
                        legacy = await self.prompts.read_configuration(scope_id, "memory.extract")
                        if legacy.mode == "custom":
                            raise PromptError("legacy_memory_prompt_unsupported", during_inference=True)
                        async with (
                            self.prompts.bind(scope_id, "atomic_memory.extract"),
                            self.prompts.bind(scope_id, "atomic_memory.reconcile"),
                        ):
                            return await self._retry_window(scope_id, limit, processing, selected_context, span)
                return await self._retry_window(scope_id, limit, processing, selected_context, span)

    async def _retry_window(self, scope_id, limit, processing, context, span) -> MemoryFlushResult:
        for attempt in range(3):
            try:
                result = await self._flush_once(scope_id, limit, processing, context, span)
            except (AtomicMemoryConflictError, GenerationConflictError, RevisionConflictError):
                if attempt == 2:
                    raise
            else:
                if span is not None:
                    span.set_attributes({"powercontext.memory.flush.source_count": result.source_count})
                    span.set_outcome("success" if result.processed else "noop")
                return result
        raise AssertionError("unreachable")

    async def _flush_once(self, scope_id, limit, processing, context, span) -> MemoryFlushResult:
        application = self.application
        trigger = SourceWindowTrigger()
        async with application.database.transaction() as connection:
            if processing is not None:
                await processing.start(connection)
            await application.security.authorize(connection, scope_id, context, "read")
            await application.security.authorize(connection, scope_id, context, "create")
            cursor = await self.cursors.load(connection, scope_id, SOURCE_WINDOW_TRIGGER_NAME)
            state = trigger.initial_state() if cursor is None else cursor.cursor
            high_watermark, limit = await self.windows.signal(connection, scope_id, state.sequence, limit)
            signal = SourceHighWatermark(sequence=high_watermark, limit=limit)
            transition = trigger.activate(signal, state)
            if not transition.actions:
                if processing is not None:
                    await processing.complete(connection, remaining_work=False)
                return self._result(state.sequence, state.sequence, high_watermark, 0)
            action = transition.actions[0]
            window = await self.sources.list_window(connection, scope_id, after=action.after, through=action.through)
            self._require_window(window, action.after, action.through)
            eligible = tuple(item for item in window if is_generation_eligible(item.value))
            if span is not None:
                span.set_attributes({"powercontext.memory.flush.source_count": len(eligible)})
            await application.security.authorize_sources(
                connection, scope_id, context, tuple(item.ref for item in eligible)
            )
        if eligible and self.pipeline is None:
            raise InferenceUnavailableError(
                "atomic-memory-generation", "Source extraction and reconciliation are not configured"
            )
        pipeline = self.pipeline if eligible else None
        try:
            policy_batch = (
                nullcontext()
                if context is None or context.access is None
                else context.access.point_decision_batch(context.principal, context=context.audit)
            )
            async with policy_batch:
                workset, consulted_artifacts = await self._prepare(scope_id, eligible, pipeline, context)
            prepared, decision, observation = await self._inspect_decisions(scope_id, workset, context)
        except (InferenceTimeoutError, InvalidInferenceOutputError) as error:
            reducible = (isinstance(error, InferenceTimeoutError) and error.operation == "generate") or (
                isinstance(error, InvalidInferenceOutputError) and error.operation == "atomic-memory-input-budget"
            )
            if reducible and action.through - action.after > 1:
                await self._reduce_window(scope_id, context, processing, cursor, state, action, high_watermark)
            raise
        try:
            finalized = await self._publish(
                scope_id,
                context,
                processing,
                cursor,
                transition,
                action,
                window,
                eligible,
                consulted_artifacts,
                workset,
                prepared,
                decision,
                observation,
                high_watermark,
            )
        except Exception:
            self._emit_failed_observation(observation)
            raise
        if finalized is not None:
            emit_decision_observation(finalized)
        return self._result(
            action.after,
            action.through,
            high_watermark,
            len(eligible),
            held=decision is not None and decision.verdict is MemoryWriteVerdict.HOLD,
            hold_codes=() if decision is None or decision.code is None else (decision.code.value,),
        )

    async def _reduce_window(self, scope_id, context, processing, cursor, state, action, high_watermark):
        async with self.application.database.transaction() as connection:
            if processing is not None:
                await processing.guard(connection)
            await self.cursors.save(
                connection,
                scope_id,
                SOURCE_WINDOW_TRIGGER_NAME,
                state,
                expected_generation=None if cursor is None else cursor.generation,
            )
            await self.windows.reduce(
                connection,
                scope_id,
                source_through=high_watermark,
                window_limit=max(1, (action.through - action.after) // 2),
            )

    async def _inspect_decisions(self, scope_id, workset, context):
        application = self.application
        plans = []
        async with self._read_transaction(context, write_policy_first=True) as connection:
            changes = workset.changes()
            source_read_set = await self.sources.preload(
                connection, scope_id, tuple(source for item in changes for source in item.sources)
            )
            async with self._prefetch_artifact_policy(
                connection,
                scope_id,
                context,
                tuple(ref for item in changes for ref in (*item.artifacts, *(read.ref for read in item.origins))),
                include_scope=True,
            ):
                for item in changes:
                    lineage = ArtifactLineage(sources=item.sources, artifacts=item.artifacts)
                    if len(item.origins) >= 2:
                        plan = await application.service.inspect_merge(
                            connection,
                            scope_id,
                            application.id_factory("atomic-memory"),
                            item.origins,
                            item.content,
                            context,
                            lineage=lineage,
                            source_read_set=source_read_set,
                        )
                    else:
                        original = item.origins[0] if item.origins else None
                        plan = await application.service.inspect_change(
                            connection,
                            scope_id,
                            application.id_factory("atomic-memory") if original is None else original.ref.artifact_id,
                            item.content,
                            context,
                            expected_revision=None if original is None else original.ref.revision,
                            expected_state_version=None if original is None else original.state_version,
                            lineage=lineage,
                            source_read_set=source_read_set,
                        )
                    plans.append(plan)
        prepared = tuple([await application.service.prepare_change(plan) for plan in plans])
        decision, observation = await self._assess_write_gate(scope_id, workset, prepared)
        return prepared, decision, observation

    async def _publish(
        self,
        scope_id,
        context,
        processing,
        cursor,
        transition,
        action,
        window,
        eligible,
        consulted_artifacts,
        workset,
        prepared,
        decision,
        observation,
        high_watermark,
    ):
        application = self.application
        writes = tuple(write for item in prepared for write in item.plan.writes)
        finalized = None
        with self._stage(
            "memory.commit",
            {
                "powercontext.memory.commit.memory_changed": bool(writes),
                "powercontext.memory.commit.artifact_revision_count": sum(write.draft is not None for write in writes),
            },
        ):
            async with (
                classify_processing_storage_failure("atomic-memory-publish-storage"),
                application.database.transaction() as connection,
            ):
                # Every Atomic write uses journal -> heads. Source capture's journal
                # reservation prevents the exact window changing under validation.
                await connection.execute(
                    update(SOURCE_JOURNAL_HEADS_TABLE)
                    .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
                    .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
                )
                if processing is not None:
                    await processing.guard(connection)
                actual = await self.cursors.load(connection, scope_id, SOURCE_WINDOW_TRIGGER_NAME, for_update=True)
                actual_generation = None if actual is None else actual.generation
                expected_generation = None if cursor is None else cursor.generation
                if (
                    0 if actual is None else actual.cursor.sequence
                ) != action.after or actual_generation != expected_generation:
                    raise GenerationConflictError(SOURCE_WINDOW_TRIGGER_NAME, expected_generation, actual_generation)
                current_window = await self.sources.list_window(
                    connection, scope_id, after=action.after, through=action.through
                )
                self._require_window(current_window, action.after, action.through)
                if current_window != window:
                    _conflict("Source window changed during preparation")
                if tuple(item for item in current_window if is_generation_eligible(item.value)) != eligible:
                    _conflict("Source generation eligibility changed during preparation")
                local_policy = False
                if context is not None and context.access is not None:
                    from powercontext.builtin.authz import BuiltinAuthorizationProvider
                    from powercontext.builtin.authz.casbin import CasbinAuthorizationProvider

                    local_policy = (
                        type(context.access.provider) in {BuiltinAuthorizationProvider, CasbinAuthorizationProvider}
                        and context.access.supports_read_snapshot(connection.engine)
                        and context.access.supports_write_transaction(connection.engine)
                    )
                async with self._prefetch_artifact_policy(
                    connection,
                    scope_id,
                    context,
                    consulted_artifacts if local_policy else (),
                    include_scope=True,
                ):
                    await application.security.authorize(connection, scope_id, context, "read")
                    await application.security.authorize(connection, scope_id, context, "create")
                    await application.security.authorize_sources(
                        connection, scope_id, context, tuple(item.ref for item in eligible)
                    )
                    for ref in consulted_artifacts:
                        await self._authorize_artifact(connection, scope_id, context, ref)
                    results = ()
                    if decision is None or decision.verdict is not MemoryWriteVerdict.HOLD:
                        source_read_set = await self.sources.preload(
                            connection,
                            scope_id,
                            tuple(
                                source for write in writes if write.draft is not None for source in write.draft.sources
                            ),
                            known=current_window,
                        )
                        results = await application.service.commit_window(
                            connection,
                            scope_id,
                            prepared,
                            context,
                            read_set=tuple(workset.reads.values()),
                            source_read_set=source_read_set,
                        )
                finalized = await self._finalize_observation(
                    connection,
                    observation,
                    final_action=(
                        "memory_write_hold"
                        if decision is not None and decision.verdict is MemoryWriteVerdict.HOLD
                        else "memory_write_applied"
                    ),
                    subject_refs=tuple(_artifact_reference(result.primary.ref) for result in results),
                )
                await self.cursors.save(
                    connection,
                    scope_id,
                    SOURCE_WINDOW_TRIGGER_NAME,
                    transition.state,
                    expected_generation=expected_generation,
                )
                await self.windows.clear_consumed(connection, scope_id, action.through)
                if processing is not None:
                    await processing.complete(connection, remaining_work=action.through < high_watermark)
        return finalized

    async def _prepare(self, scope_id, eligible, pipeline, context):  # noqa: C901 - keep recovery in the authorized window
        workset = AtomicMemoryWindowWorkset()
        artifacts: dict[tuple[str, str, int], ArtifactRef] = {}
        compared_keys: set[str] = set()
        if pipeline is None:
            return workset, ()
        evidence = tuple([
            await project_atomic_memory_evidence(item, self.sources, evidence_id=f"source:{item.journal_position}")
            for item in eligible
        ])
        extraction_input = AtomicMemoryExtractionInput(evidence=evidence, scope_id=scope_id)
        self._require_budget(pipeline, extraction_input, "atomic_memory.extract")
        extraction_prompt = current_prompt("atomic_memory.extract")
        prompt_refs = (
            () if extraction_prompt is None or extraction_prompt.artifact is None else (extraction_prompt.artifact,)
        )
        local_policy = (
            context is not None
            and context.access is not None
            and context.access.supports_read_snapshot(self.application.database.engine)
        )
        if self.config.processing_mode == "one_pass":
            prepared = await self._prepare_one_pass(
                scope_id, eligible, pipeline, context, extraction_input, local_policy=local_policy
            )
            if prepared is not None:
                return prepared
        await self._authorize_model_input(
            scope_id,
            context,
            eligible,
            prompt_refs,
        )
        candidates = await pipeline.extract(extraction_input)
        await self._authorize_model_input(scope_id, context, eligible, prompt_refs)
        admitted_prompts = {(ref.family, ref.artifact_id, ref.revision) for ref in prompt_refs}
        for ref in prompt_refs:
            artifacts[(ref.family, ref.artifact_id, ref.revision)] = ref
        by_id = {item.evidence_id: item for item in evidence}
        # This order groups candidates by their supporting Sources; the model still
        # receives real Source context, never candidate order as a fact timestamp.
        candidates = sorted(
            candidates,
            key=lambda candidate: tuple(
                sorted(by_id[identifier].journal_position for identifier in candidate.evidence_ids)
            ),
        )
        # Built-in extraction decides which candidates are worth keeping, with the whole window in view. With nothing to
        # compare, built-in reconciliation could only ask that again from the candidate's own evidence, so the candidate
        # is kept as extracted. Its unworthy noop is a second opinion only where it compares related items.
        # Custom Prompts and injected stages may apply their own policy.
        skip_unrelated = all(
            prompt is not None and prompt.selection == "built_in"
            for prompt in (extraction_prompt, current_prompt("atomic_memory.reconcile"))
        )
        window_vectors: dict[str, tuple[float, ...]] = {}
        for ordinal, candidate in enumerate(candidates):
            selected = tuple(by_id[identifier] for identifier in dict.fromkeys(candidate.evidence_ids))
            key = workset.add(
                AtomicMemoryWorkingItem(
                    key=f"candidate:{ordinal}",
                    content=AtomicMemoryContent(
                        kind=candidate.kind,
                        text=candidate.text,
                        description=candidate.description,
                    ),
                    evidence=selected,
                    sources=tuple(item.source_ref for item in selected),
                    changed=True,
                )
            )
            related_request = await self._related_request_for(
                scope_id, candidate.text, context, local_policy=local_policy
            )
            prior = await self._window_comparisons(
                workset, key, related_request, window_vectors, related_only=skip_unrelated
            )

            compared_keys.clear()

            def pending_comparisons(pending: list[str], current: str) -> list[str]:
                current = workset.resolve(current)
                # Only this proposal and newly admitted targets can acquire aliases.
                # Previously compared non-proposal roots therefore remain stable.
                return [
                    value
                    for value in dict.fromkeys(pending)
                    if (resolved := workset.resolve(value)) != current and resolved not in compared_keys
                ]

            async def compare_pending(pending: list[str], *, required: bool) -> bool:
                nonlocal key, prompt_refs
                reconciled = False
                while True:
                    pending = pending_comparisons(pending, key)
                    if not pending and (reconciled or not required):
                        return reconciled
                    batch = pending[: self.config.comparison_batch_size]
                    related = workset.related(key, batch)
                    selected_count = len(batch)
                    value = workset.request(key, related)
                    estimated = self._estimate(pipeline, value, "atomic_memory.reconcile")
                    while related and estimated > self.config.input_tokens_limit and selected_count > 1:
                        selected_count = max(1, selected_count // 2)
                        related = workset.related(key, pending[:selected_count])
                        value = workset.request(key, related)
                        estimated = self._estimate(pipeline, value, "atomic_memory.reconcile")
                    self._require_budget(pipeline, value, "atomic_memory.reconcile", estimated=estimated)
                    looked = pending[:selected_count]
                    reconciliation_prompt = current_prompt("atomic_memory.reconcile")
                    if reconciliation_prompt is not None and reconciliation_prompt.artifact is not None:
                        ref = reconciliation_prompt.artifact
                        identity = (ref.family, ref.artifact_id, ref.revision)
                        if identity not in admitted_prompts:
                            await self._authorize_model_input(scope_id, context, (), (ref,))
                            admitted_prompts.add(identity)
                        prompt_refs = tuple(
                            {
                                (value.family, value.artifact_id, value.revision): value
                                for value in (*prompt_refs, ref)
                            }.values()
                        )
                        artifacts[(ref.family, ref.artifact_id, ref.revision)] = ref
                    await self._authorize_comparison_basis(
                        scope_id, context, (workset.items[workset.resolve(key)], *related)
                    )
                    try:
                        result = await pipeline.reconciler.generate(value)
                        key = workset.apply(
                            key, related, AtomicMemoryReconciliationOutput.model_validate(result.output)
                        )
                    except RedundantAtomicMemoryRevisionError as error:
                        key = workset.recover_redundant_revision(key, related, error)
                    except AtomicMemoryRevisionIdentityError:
                        # Correction could not label the combination. Keeping the proposal's current content,
                        # identities and evidence equals create; the batch's items stay unchanged and compared.
                        pass
                    del pending[:selected_count]
                    compared_keys.update(workset.resolve(item) for item in looked)
                    reconciled = True

            compared = await compare_pending(list(prior), required=False) or skip_unrelated

            async def compare_page(page_keys: tuple[str, ...]) -> str:
                nonlocal compared
                compared = await compare_pending(list(page_keys), required=False) or compared
                return key  # noqa: B023 - the callback mutates the surrounding proposal key

            await self._recall_related_items(
                scope_id, context, workset, compare_page, request=related_request, local_policy=local_policy
            )
            if not compared:
                await compare_pending([], required=True)
        return self._with_prompt_refs(workset, artifacts, prompt_refs)

    @staticmethod
    def _with_prompt_refs(workset, artifacts, prompt_refs):
        for item in workset.changes():
            refs = {(ref.family, ref.artifact_id, ref.revision): ref for ref in (*item.artifacts, *prompt_refs)}
            workset.items[item.key] = replace(item, artifacts=tuple(refs.values()))
            artifacts.update(refs)
        return workset, tuple(artifacts.values())

    async def _prepare_one_pass(self, scope_id, eligible, pipeline, context, extraction_input, *, local_policy):
        """Prepare the window in one conversation, or return ``None`` when its input exceeds the budget.

        The caller then prepares the window per candidate, which compares within the same budget in smaller batches.
        """
        if pipeline.conversation is None:
            raise InferenceUnavailableError(
                "atomic-memory-generation", "One-pass processing requires a conversation generator"
            )
        extraction_prompt = current_prompt("atomic_memory.extract")
        reconciliation_prompt = current_prompt("atomic_memory.reconcile")
        prompt_refs = tuple(
            prompt.artifact
            for prompt in (extraction_prompt, reconciliation_prompt)
            if prompt is not None and prompt.artifact is not None
        )
        extraction_rules = (
            pipeline.extraction_instructions if extraction_prompt is None else extraction_prompt.compiled_instructions
        )
        # The compiled reconciliation Prompt describes the per-candidate reply; only its Scope-owned text applies here.
        guidance = (
            reconciliation_prompt.instructions
            if reconciliation_prompt is not None and reconciliation_prompt.selection == "artifact"
            else None
        )
        metadata = None if extraction_prompt is None else extraction_prompt.trace_attributes()
        conversation = _OnePassConversation(
            pipeline,
            pipeline.conversation,
            one_pass_instructions(extraction_rules, guidance),
            metadata,
            self.config.input_tokens_limit,
        )
        evidence = (
            extraction_input.model_dump_json()
            if pipeline.input_renderer is None
            else pipeline.input_renderer(extraction_input)
        )
        if not conversation.fits(evidence):
            _log_one_pass("fallback", conversation, "entries_input_over_budget", conversation.estimate(evidence))
            return None

        async def converse(conversation: _OnePassConversation, first: str):
            return await self._converse_one_pass(
                scope_id, eligible, context, conversation, first, extraction_input, prompt_refs, local_policy
            )

        if pipeline.prefixed_conversation is not None and prefix_handle(extraction_input) is not None:
            # The agent's conversation already holds the evidence, so the first message only lists the Sources.
            try:
                prefixed = _OnePassConversation(
                    pipeline,
                    pipeline.prefixed_conversation,
                    one_pass_instructions(extraction_rules, guidance, evidence_in_prefix=True),
                    metadata,
                    self.config.input_tokens_limit,
                    (prefix_start(extraction_input, conversation.estimate(evidence)),),
                )
                prepared = await converse(prefixed, source_records(extraction_input.evidence))
            except PrefixedExtractionUnavailable as error:
                reason = error.reason
            except InvalidInferenceOutputError as error:
                # Sending the evidence the normal way only adds to an input that is already over its budget.
                if error.operation == "atomic-memory-input-budget":
                    raise
                reason = "invalid_output"
            else:
                # An over-budget second step is reported as the one-pass fallback and leaves nothing prepared here.
                if prepared is not None:
                    log_prefixed_extraction("success", operation="atomic_memory.one_pass")
                return prepared
            # Nothing from the prefixed conversation was kept; the window starts again with its evidence sent by Core.
            log_prefixed_extraction("fallback", reason, operation="atomic_memory.one_pass")
        return await converse(conversation, evidence)

    async def _converse_one_pass(
        self, scope_id, eligible, context, conversation, first, extraction_input, prompt_refs, local_policy
    ):
        """Run the steps of one conversation whose first message is ``first``; ``None`` when step 2 is over budget."""
        workset = AtomicMemoryWindowWorkset()
        await self._authorize_model_input(scope_id, context, eligible, prompt_refs)
        entries = await conversation.turn(first, parse_one_pass_entries)
        await self._authorize_model_input(scope_id, context, eligible, prompt_refs)

        async def recall(query: str) -> tuple[str, ...]:
            recalled: list[str] = []

            async def keep_page(keys: tuple[str, ...]) -> None:
                recalled.extend(keys)

            await self._recall_related_items(
                scope_id,
                context,
                workset,
                keep_page,
                request=await self._related_request_for(scope_id, query, context, local_policy=local_policy),
                local_policy=local_policy,
                limit=ONE_PASS_MEMORIES_PER_QUERY,
            )
            return tuple(recalled)

        shown: dict[str, str] = {}
        for entry in entries.output:
            if len(shown) >= ONE_PASS_MAX_MEMORIES:
                break
            for key in await recall(entry.query):
                if len(shown) < ONE_PASS_MAX_MEMORIES and key not in shown.values():
                    shown[f"M{len(shown) + 1}"] = key
        message = render_existing_memories({label: workset.items[key] for label, key in shown.items()})
        if not conversation.fits(message, entries):
            _log_one_pass(
                "fallback", conversation, "operations_input_over_budget", conversation.estimate(message, entries)
            )
            return None
        basis = tuple(workset.items[key] for key in shown.values())
        await self._authorize_comparison_basis(scope_id, context, basis)
        operations = await conversation.turn(
            message,
            lambda reply: apply_one_pass_operations(workset, reply, shown, extraction_input, entries.output),
            entries,
        )
        workset, created = operations.output

        workset = await self._recheck_one_pass(
            scope_id, context, conversation, workset, created, basis, recall, operations, extraction_input
        )
        _log_one_pass("success", conversation)
        return self._with_prompt_refs(workset, {}, prompt_refs)

    async def _recheck_one_pass(
        self, scope_id, context, conversation, workset, created, basis, recall, operations, extraction_input
    ):
        """Decide created memories again beside the memories that only their own text recalls.

        Step 2 compared a created memory with what its entry's query recalled; ``basis`` holds those memories. Every
        recheck resends them in the conversation, so their read and write authority is checked again with the new ones.
        """
        pending: dict[str, str] = {}
        unseen: dict[str, dict[str, str]] = {}
        compared = {read.ref.artifact_id for item in basis for read in item.origins}
        # A memory that several created memories recall keeps one label, so the model sees it is the same memory.
        labels: dict[str, str] = {}
        for key in created:
            found = tuple(
                item
                for item in dict.fromkeys(await recall(workset.items[key].content.text))
                if not any(read.ref.artifact_id in compared for read in workset.items[item].origins)
            )
            if found:
                label = f"C{len(pending) + 1}"
                pending[label] = key
                for item in found:
                    labels.setdefault(item, f"M{len(basis) + len(labels) + 1}")
                unseen[label] = {labels[item]: item for item in found}
        while pending:
            # Each created memory is decided on its own, so the budget only spreads them over several turns.
            batch = dict(pending)
            while True:
                message = render_recheck(
                    {label: workset.items[workset.resolve(key)] for label, key in batch.items()},
                    {
                        label: {memory: workset.items[workset.resolve(item)] for memory, item in unseen[label].items()}
                        for label in batch
                    },
                )
                if conversation.fits(message, operations) or len(batch) == 1:
                    break
                batch = dict(list(batch.items())[: len(batch) // 2])
            self._require_budget(
                conversation.pipeline,
                extraction_input,
                "atomic_memory.reconcile",
                estimated=conversation.estimate(message, operations),
            )
            await self._authorize_comparison_basis(
                scope_id,
                context,
                (
                    *basis,
                    *(workset.items[workset.resolve(item)] for label in batch for item in unseen[label].values()),
                ),
            )
            try:
                rechecked = await conversation.turn(
                    message,
                    partial(
                        apply_one_pass_recheck,
                        workset,
                        created=batch,
                        unseen={label: unseen[label] for label in batch},
                    ),
                    operations,
                )
            except AtomicMemoryRevisionIdentityError:
                # A memory merged by an earlier batch holds several identities. Once correction is exhausted, keep
                # this batch's created memories unchanged, as a create for each; the rejected reply is not applied.
                pass
            else:
                workset = rechecked.output
            for label in batch:
                del pending[label]
        return workset

    async def _load_related_items(self, connection, scope_id, hits, context, workset, *, joined_heads):
        from powercontext.builtin.authz import AccessDeniedError

        allowed = []
        for hit in hits:
            try:
                await self.application.security.authorize(
                    connection,
                    scope_id,
                    context,
                    "read",
                    hit.artifact_ref,
                )
            except AccessDeniedError:
                continue
            allowed.append(hit)
        if not allowed:
            return []
        by_id = joined_heads
        for hit in allowed:
            if hit.artifact_ref.artifact_id not in by_id:
                raise RepositoryNotFoundError("artifact", (scope_id, "atomic-memory", hit.artifact_ref.artifact_id))
        recalled = []
        for hit in allowed:
            try:
                read = await self.application.service.authorize_comparison_hit(
                    connection, scope_id, by_id[hit.artifact_ref.artifact_id], hit, context
                )
            except AccessDeniedError:
                continue
            content = cast(
                AtomicMemoryContent,
                self.application.artifacts.decode_content(by_id[hit.artifact_ref.artifact_id]),
            ).without_creation()
            supported = AtomicMemoryArtifactEvidence(
                evidence_id=f"artifact:{read.ref.artifact_id}@{read.ref.revision}",
                artifact_ref=read.ref,
                content=content,
            )
            recalled.append(
                workset.add(
                    AtomicMemoryWorkingItem(
                        key=f"memory:{hit.artifact_ref.artifact_id}",
                        content=content,
                        origins=(read,),
                        evidence=(supported,),
                        artifacts=(read.ref,),
                    )
                )
            )
        return recalled

    async def _related_request_for(self, scope_id, query, context, *, local_policy):
        application = self.application
        if local_policy or context is None or (context.access is None and context.trusted_local):
            # Trusted local work needs no policy read. Local policy already checked
            # Scope and rechecks its revision before admitting retrieved bodies.
            filters = AtomicMemoryIndexFilter()
        else:
            async with application.database.transaction() as connection:
                filters = await application.security.filters(connection, scope_id, context)
        return await self._related_request(query, filters)

    async def _recall_related_items(
        self, scope_id, context, workset, compare_page, *, request, local_policy=False, limit=None
    ):
        """Page through the related memories in rank order.

        Compared pages are released from the workset. With ``limit``, only the closest memories are read and they
        stay in the workset for the caller.
        """
        if self.config.alias_recall:
            request, ranks = await self._aliased_rank_snapshot(scope_id, request)
        else:
            request, ranks = await self._related_rank_snapshot(scope_id, request)
        ranks = ranks[:limit]
        if not ranks:
            return
        cursor = AtomicMemoryRelatedCursor(ranks)
        while True:
            page, loaded = await self._read_related_page(
                scope_id, context, workset, request, cursor, local_policy=local_policy
            )
            if page is None:
                break
            cursor = page.cursor
            if loaded:
                key = await compare_page(tuple(loaded))
                workset.release(() if limit is not None else loaded, keep=key)
            if cursor is None:
                break
            del page, loaded

    async def _aliased_rank_snapshot(self, scope_id, request):
        application = self.application
        while True:
            async with application.database.transaction(consistent_snapshot=True) as connection:
                try:
                    direct = await application.index.related_ranks(connection, scope_id, request)
                except AtomicMemoryIndexError as error:
                    if error.code == "stale-recall":
                        _conflict("Related channels returned different memory versions")
                    if not self.config.related_fts_fallback or error.code not in {
                        "incomplete-vector",
                        "embedding-profile",
                    }:
                        raise
                    request = replace(request, mode="fts")
                    continue
                try:
                    # Roll back alias-only SQL failures without invalidating direct
                    # results or the surrounding read/authorization snapshot.
                    async with connection.begin_nested():
                        aliases = await application.index.aliased_related_ranks(connection, scope_id, request)
                except (AtomicMemoryIndexError, OperationalError) as error:
                    _require_alias_snapshot(connection, error)
                    aliases = ()
                ranks = _combine_related_ranks(direct, aliases)
            return request, ranks

    async def _read_related_page(self, scope_id, context, workset, request, cursor, *, local_policy):
        application = self.application
        async with (
            deferred_decision_audit(context),
            application.database.transaction(consistent_snapshot=True) as connection,
        ):
            try:
                page = await application.index.related_page(connection, scope_id, request, cursor)
            except AtomicMemoryIndexError as error:
                if error.code == "stale-recall":
                    _conflict("Related memory changed before page admission")
                raise
            if page is None:
                return None, []
            refs = tuple(hit.artifact_ref for hit in page.hits)
            async with self._prefetch_artifact_policy(connection, scope_id, context, refs, include_scope=local_policy):
                if local_policy:
                    await application.security.authorize(connection, scope_id, context, "read")
                loaded = await self._load_related_items(
                    connection, scope_id, page.hits, context, workset, joined_heads=page.heads
                )
        return page, loaded

    async def _related_rank_snapshot(self, scope_id, request):
        application = self.application
        while True:
            # One statement owns the rank snapshot. The transaction boundary also
            # guards the shared in-memory SQLite connection.
            async with application.database.transaction() as connection:
                try:
                    ranks = await application.index.related_ranks(connection, scope_id, request)
                except AtomicMemoryIndexError as error:
                    if error.code == "stale-recall":
                        _conflict("Related channels returned different memory versions")
                    if not self.config.related_fts_fallback or error.code not in {
                        "incomplete-vector",
                        "embedding-profile",
                    }:
                        raise
                    request = replace(request, mode="fts")
                    continue
            break
        return request, ranks

    async def _window_comparisons(self, workset, key, request, vectors, *, related_only):
        items = tuple(item for item in workset.items.values() if item.key != key and item.changed and item.retain)
        if not related_only or not items:
            return tuple(item.key for item in items)
        # Earlier window items are not indexed until publication. The threshold that selects published memories
        # selects them too, so an unrelated sibling no longer forces a comparison of its own.
        if items and request.mode in {"vector", "hybrid"}:
            try:
                await self._embed_window_items(items, vectors)
            except (AtomicMemoryIndexError, InferenceUnavailableError, InferenceTimeoutError):
                if not self.config.related_fts_fallback:
                    raise
                request = replace(request, mode="fts")
        terms = fts_query_requirements(request.query, floor=request.admission)[0] if request.mode != "vector" else ()
        return tuple(
            item.key
            for item in items
            if _related_window_match(
                request,
                terms,
                item.lexical_terms_and_count if terms else None,
                vectors.get(atomic_memory_embedding_input(item.content.kind, item.content.text)),
            )
        )

    async def _embed_window_items(self, items, vectors) -> None:
        profile = self.application.index.capabilities.embedding_profile
        model = self.application.embedding_model
        if model is None or profile is None or model.profile != profile:
            raise AtomicMemoryIndexError("embedding-profile", "Related-memory vector profile is unavailable")
        inputs = tuple(
            dict.fromkeys(
                value
                for item in items
                if (value := atomic_memory_embedding_input(item.content.kind, item.content.text)) not in vectors
            )
        )
        if not inputs:
            return
        result = await model.embed(inputs)
        if len(result.vectors) != len(inputs):
            raise AtomicMemoryIndexError("embedding-result", "Related window items require one vector each")
        for value, vector in zip(inputs, result.vectors, strict=True):
            vectors[value] = canonical_embedding(
                vector, dimension=profile.dimension, normalization=profile.normalization
            )

    async def _related_request(self, query, filters):
        application = self.application
        mode = self.config.related_mode
        profile = application.index.capabilities.embedding_profile
        if mode == "auto":
            mode = "fts" if profile is None else "hybrid"
        vector = None
        if mode in {"vector", "hybrid"}:
            try:
                vector = await self._query_vector(query)
            except (AtomicMemoryIndexError, InferenceUnavailableError, InferenceTimeoutError):
                if not self.config.related_fts_fallback:
                    raise
                mode = "fts"
        return AtomicMemoryRelatedRequest(
            query,
            filters,
            mode=mode,
            query_vector=vector,
            embedding_profile=profile,
            max_distance=self.config.related_max_distance,
            page_size=self.config.related_page_size,
        )

    async def _query_vector(self, query):
        profile = self.application.index.capabilities.embedding_profile
        model = self.application.embedding_model
        if model is None or profile is None or model.profile != profile:
            raise AtomicMemoryIndexError("embedding-profile", "Related-memory vector profile is unavailable")
        result = await embed_query(model, (query,))
        if len(result.vectors) != 1:
            raise AtomicMemoryIndexError("embedding-result", "Related query requires one vector")
        return canonical_embedding(result.vectors[0], dimension=profile.dimension, normalization=profile.normalization)

    async def _authorize_comparison_basis(self, scope_id, context, items):
        """Recheck one model batch's exact published basis and write authority."""
        if context is None or (context.access is None and context.trusted_local):
            return
        reads = tuple({read.ref.artifact_id: read for item in items for read in item.origins}.values())
        if not reads:
            return
        async with (
            self._read_transaction(context) as connection,
            self._prefetch_artifact_policy(connection, scope_id, context, tuple(read.ref for read in reads)),
        ):
            await self.application.service.authorize_comparison_reads(connection, scope_id, reads, context)

    async def _authorize_model_input(self, scope_id, context, generation_sources, artifact_refs=()):
        """Admit Source and Prompt content against current data and authority."""
        access = None if context is None else context.access
        local_policy = (
            access is not None
            and access.supports_read_snapshot(self.application.database.engine)
            and access.supports_write_transaction(self.application.database.engine)
        )
        refs = tuple(artifact_refs)
        if local_policy or context is None or (access is None and context.trusted_local):
            # A single SELECT needs no pinned snapshot. Larger inputs must keep
            # the same snapshot across bounded observation chunks.
            connection_manager = (
                self.application.database.transaction()
                if len(generation_sources) <= 100
                else self.application.database.transaction(consistent_snapshot=True)
            )
            async with deferred_decision_audit(context), connection_manager as connection:
                revision = await self._observe_model_input(
                    connection, scope_id, generation_sources, include_policy=local_policy
                )
                async with self._prefetch_artifact_policy(
                    connection, scope_id, context, refs, include_scope=True, observed_revision=revision
                ) as ready:
                    if ready:
                        await self._require_model_authority(
                            connection, scope_id, context, generation_sources, artifact_refs
                        )
                        return
        async with (
            self._read_transaction(context) as connection,
            self._prefetch_artifact_policy(connection, scope_id, context, refs, include_scope=True),
        ):
            await self._require_model_authority(connection, scope_id, context, generation_sources, artifact_refs)
            await self._observe_model_input(connection, scope_id, generation_sources)

    async def _observe_model_input(self, connection, scope_id, generation_sources, *, include_policy=False):
        # Decode stored Sources through their supported formats; re-encoding an
        # older payload could mistake a serialization difference for a mutation.
        sources = tuple({(item.ref.source_type, item.ref.source_id): item for item in generation_sources}.values())
        policy = literal(None)
        if include_policy:
            from powercontext.builtin.authz.repository import ACCESS_POLICY_HEADS_TABLE

            policy = func.coalesce(
                select(ACCESS_POLICY_HEADS_TABLE.c.revision)
                .where(ACCESS_POLICY_HEADS_TABLE.c.name == "authorization")
                .scalar_subquery(),
                0,
            )
        revision = None
        for offset in range(0, max(len(sources), 1), 100):
            source_rows = sources[offset : offset + 100]
            selected_sources = (
                select(SOURCES_TABLE)
                .where(
                    SOURCES_TABLE.c.scope_id == scope_id,
                    tuple_(SOURCES_TABLE.c.source_type, SOURCES_TABLE.c.source_id).in_(
                        tuple((item.ref.source_type, item.ref.source_id) for item in source_rows)
                    ),
                )
                .subquery()
            )
            anchor = select(literal(1).label("one")).subquery()
            rows = (
                (
                    await connection.execute(
                        select(policy.label("policy_revision"), selected_sources).select_from(
                            anchor.outerjoin(selected_sources, true())
                        )
                    )
                )
                .mappings()
                .all()
            )
            current = {
                (row["source_type"], row["source_id"]): self.sources.decode_source(row)
                for row in rows
                if row["source_id"] is not None
            }
            if any(current.get((item.ref.source_type, item.ref.source_id)) != item for item in source_rows):
                _conflict("Model input Source basis changed")
            observed = rows[0]["policy_revision"]
            revision = None if observed is None else str(observed)
        return revision

    async def _require_model_authority(self, connection, scope_id, context, generation_sources, artifact_refs):
        await self.application.security.authorize(connection, scope_id, context, "read")
        await self.application.security.authorize(connection, scope_id, context, "create")
        await self.application.security.authorize_sources(
            connection, scope_id, context, tuple(item.ref for item in generation_sources)
        )
        for ref in artifact_refs:
            await self._authorize_artifact(connection, scope_id, context, ref)

    @asynccontextmanager
    async def _prefetch_artifact_policy(
        self, connection, scope_id, context, refs, *, include_scope=False, observed_revision=None
    ):
        if context is None or context.access is None or (not refs and not include_scope):
            yield True
            return
        from powercontext.builtin.authz import ResourceRef

        access = context.access.for_read(connection)
        resources = tuple(
            ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id) for ref in refs
        )
        if include_scope:
            resources = (ResourceRef.scope(scope_id), *resources)
        async with access.prefetch_point_decisions(
            connection, context.principal, resources, context=context.audit, observed_revision=observed_revision
        ) as ready:
            yield ready

    async def _authorize_artifact(self, connection, scope_id, context, ref):
        await self.application.security.authorize(connection, scope_id, context, "read", ref)

    @asynccontextmanager
    async def _read_transaction(self, context, *, write_policy_first=False, consistent_snapshot=True):
        # Local policy can pin this snapshot through its own reads; external
        # providers retain an explicit business-data pin. Audit flushes afterward.
        async with (
            deferred_decision_audit(context),
            self.application.database.transaction(consistent_snapshot=consistent_snapshot) as connection,
        ):
            access = None if context is None else context.access
            pins_with_policy = access is not None and access.supports_read_snapshot(connection.engine)
            if pins_with_policy and write_policy_first:
                from powercontext.builtin.authz import BuiltinAuthorizationProvider

                # Read-side Casbin binds to the business transaction; its write
                # path does not. Builtin policy binds both when relationships
                # and audit share this engine.
                pins_with_policy = type(
                    access.provider
                ) is BuiltinAuthorizationProvider and access.supports_write_transaction(connection.engine)
            if connection.dialect.name == "sqlite" and access is not None and not pins_with_policy:
                # Keep business data pinned before a separate policy decision.
                await connection.execute(select(ARTIFACT_HEADS_TABLE.c.scope_id).limit(1))
            yield connection

    def _stage(self, name, attributes):
        return nullcontext(None) if self.tracing is None else self.tracing.stage(name, attributes=attributes)

    def _estimate(self, pipeline, value, key):
        prompt = current_prompt(key)
        instructions = (
            (
                pipeline.extraction_instructions
                if key == "atomic_memory.extract"
                else ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS
            )
            if prompt is None
            else prompt.compiled_instructions
        )
        payload = value.model_dump_json() if pipeline.input_renderer is None else pipeline.input_renderer(value)
        return pipeline.estimator.estimate(f"{instructions}\n{payload}")

    def _require_budget(self, pipeline, value, key, *, estimated=None):
        if estimated is None:
            estimated = self._estimate(pipeline, value, key)
        if estimated <= self.config.input_tokens_limit:
            return
        error = InvalidInferenceOutputError(
            "atomic-memory-input-budget", "A complete comparison input exceeds its token budget"
        )
        raise attach_diagnostics(
            error,
            ProcessingDiagnostics(
                stage=ProcessingStage.BUSINESS_VALIDATION,
                reason=ProcessingFailureReason.CONTEXT_OVERFLOW,
                input_item_count=_input_item_count(value),
                estimated_input_tokens=estimated,
                token_estimator_id=pipeline.estimator.profile.estimator_id,
                effective_budget=self.config.input_tokens_limit,
                failure_location=key,
            ),
        )

    @staticmethod
    def _require_window(window, after, through):
        if tuple(item.journal_position for item in window) != tuple(range(after + 1, through + 1)):
            _conflict("Source journal window is not an exact contiguous interval")

    async def _assess_write_gate(self, scope_id, workset, prepared):
        gate = self.memory_write_gate
        if gate is None or not prepared:
            return None, None
        items = workset.changes()
        collector = _PendingObservationSink()
        request = MemoryWriteGateRequest(
            candidates=tuple(item.content.text for item in items),
            evidence=tuple(_gate_evidence_text(item) for item in items for item in item.evidence),
            scope_id=scope_id,
            operation_id=_window_operation_id(prepared),
            subject_refs=tuple(f"candidate:{index}" for index in range(1, len(items) + 1)),
            evidence_refs=tuple(dict.fromkeys(_evidence_reference(value) for item in items for value in item.evidence)),
            observation_sink=collector,
            defer_observation_emission=True,
        )
        try:
            decision = await gate.assess(request)
        except InferencePrivacyError:
            raise
        except Exception:
            return None, None
        return decision, collector.observation

    async def _finalize_observation(self, connection, observation, *, final_action, subject_refs):
        if observation is None or self.decision_observations is None:
            return None
        finalized = observation.model_copy(
            update={
                "final_action": final_action,
                "subject_refs": observation.subject_refs if final_action == "memory_write_hold" else subject_refs,
            }
        )
        try:
            async with connection.begin_nested():
                await self.decision_observations.append(connection, finalized)
                if self.decision_observation_retention_days is not None:
                    from datetime import UTC, datetime, timedelta

                    await self.decision_observations.prune_before(
                        connection,
                        finalized.scope_id,
                        datetime.now(UTC) - timedelta(days=self.decision_observation_retention_days),
                    )
        except Exception as error:
            # SQLite RAISE(ROLLBACK, ...) invalidates the owner transaction and
            # destroys this savepoint. Continuing would consume the source window
            # in a new transaction after the Memory write was lost.
            if (
                connection.dialect.name == "sqlite"
                and isinstance(error, OperationalError)
                and "no such savepoint" in str(error)
            ):
                raise
            log_safely(
                logger,
                logging.WARNING,
                "Decision observation persistence failed",
                extra={"event": "decision.observation_sink_failed", "exception_type": type(error).__name__},
            )
            return None
        return finalized

    @staticmethod
    def _emit_failed_observation(observation):
        if observation is not None:
            emit_decision_observation(
                observation.model_copy(update={"final_action": "memory_write_failed", "subject_refs": ()})
            )

    @staticmethod
    def _result(after, through, high_watermark, count, *, held=False, hold_codes=()):
        return MemoryFlushResult(
            previous_cursor=after,
            current_cursor=through,
            high_watermark=high_watermark,
            source_count=count,
            memory_ref=None,
            remaining_work=through < high_watermark,
            held_count=1 if held else 0,
            hold_codes=hold_codes,
        )


@dataclass
class _OnePassConversation:
    """One window's conversation: its fixed instructions and the input budget of each turn.

    A turn's input is the instructions, the transcript of the reply it continues and its new message. Turns that
    continue the same reply are separate requests, so each is estimated on its own.
    """

    pipeline: AtomicMemoryGenerationPipeline
    generator: StructuredGenerator[ConversationTurn[Any], ConversationReply[Any]]
    instructions: str
    metadata: dict[str, str | int] | None
    input_tokens_limit: int
    # The history the first turn continues; a prefixed conversation starts from the agent's prefix.
    start: tuple[object, ...] = ()

    def estimate(self, message: str, previous: ConversationReply[Any] | None = None) -> int:
        transcript = () if previous is None else previous.transcript
        return self.pipeline.estimator.estimate("\n".join((self.instructions, *transcript, message)))

    def fits(self, message: str, previous: ConversationReply[Any] | None = None) -> bool:
        return self.estimate(message, previous) <= self.input_tokens_limit

    async def turn(self, message, check, previous: ConversationReply[Any] | None = None):
        result = await self.generator.generate(
            ConversationTurn(
                instructions=self.instructions,
                message=message,
                check=check,
                history=self.start if previous is None else previous.history,
                metadata=self.metadata,
            )
        )
        return result.output


@dataclass(slots=True)
class _PendingObservationSink:
    observation: DecisionObservation | None = None

    async def record(self, observation: DecisionObservation, /) -> None:
        self.observation = observation


def related_window_match(
    request: AtomicMemoryRelatedRequest, kind: str, body: str, vector: tuple[float, ...] | None, /
) -> bool:
    """Apply the related-enumeration threshold to one unindexed item, as the backends do for indexed rows.

    Lexical coverage counts whole analyzed terms of the projection's searchable text against the related threshold,
    which also measures the item's own indexed words; the vector channel compares the item's document embedding by
    exact L2 distance. Hybrid admits an item that meets either channel.
    """

    terms = fts_query_requirements(request.query, floor=request.admission)[0] if request.mode != "vector" else ()
    words = analyze_text(atomic_memory_embedding_input(kind, body)).split() if terms else []
    return _related_window_match(request, terms, (frozenset(words), len(words)), vector)


def _related_window_match(request, terms, lexical, vector) -> bool:
    if terms and lexical is not None:
        searchable, word_count = lexical
        required = atomic_memory_related_requirement(len(terms), word_count, floor=request.admission)
        if sum(term in searchable for term in terms) >= required:
            return True
    if request.mode in {"vector", "hybrid"}:
        if request.query_vector is None or vector is None or request.max_distance is None:
            raise AtomicMemoryIndexError("embedding-profile", "Related vector matching requires both vectors")
        return math.dist(request.query_vector, vector) <= request.max_distance
    return False


def _gate_evidence_text(value) -> str:
    if isinstance(value, AtomicMemoryEvidence):
        import json

        return json.dumps(value.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value.content.text


def _reference_component(value: str) -> str:
    return urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _artifact_reference(reference: ArtifactRef) -> str:
    return f"artifact:{_reference_component(reference.family)}:{_reference_component(reference.artifact_id)}@{reference.revision}"


def _evidence_reference(value) -> str:
    if isinstance(value, AtomicMemoryEvidence):
        reference = value.source_ref
        return f"source:{_reference_component(reference.source_type)}:{_reference_component(reference.source_id)}"
    return _artifact_reference(value.artifact_ref)


def _window_operation_id(prepared) -> str:
    identities = ":".join(item.plan.primary_artifact_id for item in prepared)
    return f"atomic-memory-window:{sha256(identities.encode('utf-8')).hexdigest()}"


def _conflict(detail: str) -> NoReturn:
    raise AtomicMemoryConflictError(detail)


def _input_item_count(value: object) -> int | None:
    evidence = getattr(value, "evidence", None)
    if isinstance(evidence, (tuple, list)):
        return len(evidence)
    return None


def _combine_related_ranks(direct, aliases):
    """Preserve direct order, then append alias-only successors with their observed versions."""
    ranks = {rank.artifact_ref.artifact_id: rank for rank in direct}
    for rank in aliases:
        identity = rank.artifact_ref.artifact_id
        previous = ranks.get(identity)
        if previous is None:
            ranks[identity] = rank
        elif previous.artifact_ref != rank.artifact_ref or previous.state_version != rank.state_version:
            _conflict("Related channels returned different memory versions")
    return tuple(ranks.values())


def _require_alias_snapshot(connection, error: AtomicMemoryIndexError | OperationalError):
    """Only operational failures with a valid owner snapshot can fall back."""
    if isinstance(error, AtomicMemoryIndexError):
        if error.code == "stale-recall":
            _conflict("Related channels returned different memory versions")
        raise error
    if connection.invalidated or getattr(error, "connection_invalidated", False):
        raise error
    message = str(error).lower()
    if "savepoint" in message and ("no such" in message or "does not exist" in message):
        raise error


def _log_one_pass(
    outcome: str, conversation: _OnePassConversation, reason: str | None = None, estimated: int | None = None
) -> None:
    log_safely(
        logger,
        logging.INFO,
        "One-pass Atomic Memory window prepared"
        if reason is None
        else "One-pass Atomic Memory window fell back to per-candidate processing",
        extra={
            "event": "atomic_memory.one_pass",
            "operation": "atomic_memory.one_pass",
            "unit": "inference",
            "outcome": outcome,
            **(
                {}
                if reason is None
                else {
                    "fallback_reason": reason,
                    "estimated_input_tokens": estimated,
                    "token_estimator_id": conversation.pipeline.estimator.profile.estimator_id,
                    "effective_budget": conversation.input_tokens_limit,
                }
            ),
        },
    )
