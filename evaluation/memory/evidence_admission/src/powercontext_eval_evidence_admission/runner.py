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

"""Run paired, isolated experiments without publishing Memory or a production cursor."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, TypedDict

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryGenerationPipeline,
    atomic_memory_extraction_instructions,
    project_atomic_memory_evidence,
)
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.inference.errors import InferenceError, InvalidInferenceOutputError
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.memory_windows import MemorySourceWindowRepository
from powercontext.builtin.triggers import SourceHighWatermark, SourceWindowTrigger
from pydantic import ValidationError

from .admission import Admission, admit, fragments, summarize, token_count
from .bounded_v2 import V2Admission, hybrid_admission
from .bounded_v3 import hybrid_admission as v3_admission
from .citation import render_citations
from .manifests import EvaluationError, Suite
from .models import Arm, Case, Sample, digest
from .replay import Replay, checkpoint, replay_case
from .scoring import score_generation, score_preparation

INSTRUCTIONS = atomic_memory_extraction_instructions(MemoryExtractionProfile.CODING)
CITATION_RULES = """
Citation protocol evidence-id-only-v2: each candidate.evidence_ids entry must copy an exact evidence_id
from an object in the top-level evidence array. Range positions, Source IDs, materialization/projection hashes
and content values are data, never citation IDs. Preserve the complete evidence_id including its prefix.
""".strip()


def instructions_for(suite: Suite) -> str:
    if suite.experiment.citation_protocol == "evidence-id-only-v3":
        return INSTRUCTIONS.replace(
            "Each candidate must cite one or more supplied evidence_ids and contain self-contained kind/text.",
            "Cite only full top-level evidence_id values in evidence_ids; use self-contained kind/text.",
        )
    return f"{INSTRUCTIONS}\n{CITATION_RULES}" if suite.experiment.citation_protocol else INSTRUCTIONS


class SampleIdentity(TypedDict):
    case_hash: str
    arm: Arm
    budget: int
    repetition: int


@dataclass
class CapturedGenerator:
    generator: Any
    sample_id: str
    output_sink: Callable[[dict], None] | None = None
    calls: list[tuple[AtomicMemoryExtractionInput, GenerationResult]] = field(default_factory=list)
    attempts: int = 0

    async def generate(self, value: AtomicMemoryExtractionInput, /):
        self.attempts += 1
        result = await self.generator.generate(value)
        self.calls.append((value, result))
        if self.output_sink is not None:
            output = result.output.model_dump(mode="json")
            self.output_sink(
                {
                    "sample_id": self.sample_id,
                    "call_index": len(self.calls) - 1,
                    "input_hash": digest(value.model_dump(mode="json")),
                    "output_hash": digest(output),
                    "output": output,
                    "usage": result.usage.model_dump(mode="json"),
                }
            )
        return result


class NoReconciliation:
    async def generate(self, value, /):
        raise EvaluationError("reconciliation_is_outside_evaluation")


def pipeline_for(generator: Any) -> AtomicMemoryGenerationPipeline:
    return AtomicMemoryGenerationPipeline(
        extractor=generator,
        reconciler=NoReconciliation(),
        estimator=character_token_estimator(),
        extraction_instructions=INSTRUCTIONS,
    )


async def window_admission(
    replay: Replay, budget: int, fragment_bytes: int, instructions: str = INSTRUCTIONS
) -> tuple[Admission, tuple]:
    windows = MemorySourceWindowRepository()
    trigger = SourceWindowTrigger()
    state = trigger.initial_state()
    inventory = await fragments(replay.value, replay.materializations, fragment_bytes)
    admitted = []
    inputs = []
    attempts = []
    while state.sequence < len(replay.value.evidence):
        async with replay.contexts.database.transaction() as connection:
            watermark, limit = await windows.signal(
                connection, replay.scope_id, state.sequence, len(replay.value.evidence)
            )
            transition = trigger.activate(SourceHighWatermark(sequence=watermark, limit=limit), state)
            action = transition.actions[0]
            stored = await replay.sources.list_window(
                connection, replay.scope_id, after=action.after, through=action.through
            )
        if tuple(item.journal_position for item in stored) != tuple(range(action.after + 1, action.through + 1)):
            raise EvaluationError("noncontiguous_source_window")
        value = AtomicMemoryExtractionInput(
            evidence=tuple(
                [
                    await project_atomic_memory_evidence(
                        item, replay.sources, evidence_id=f"source:{item.journal_position}"
                    )
                    for item in stored
                ]
            )
        )
        fits = token_count(value, instructions) <= budget
        attempts.append((action.after, action.through, token_count(value, instructions), fits))
        if not fits:
            if action.through - action.after == 1:
                break
            async with replay.contexts.database.transaction() as connection:
                await windows.reduce(
                    connection,
                    replay.scope_id,
                    source_through=watermark,
                    window_limit=max(1, (action.through - action.after) // 2),
                )
            await checkpoint("window_retry")
            continue
        inputs.append(value)
        admitted.extend(item for item in inventory if action.after < item.source.journal_position <= action.through)
        state = transition.state
        async with replay.contexts.database.transaction() as connection:
            await windows.clear_consumed(connection, replay.scope_id, state.sequence)
    result = summarize(
        tuple(inputs),
        inventory,
        tuple(admitted),
        instructions,
        bounded=False,
        accepted=state.sequence == len(replay.value.evidence),
        reason="source_window",
    )
    return result, tuple(attempts)


async def run_sample(
    suite: Suite,
    case: Case,
    arm: Arm,
    budget: int,
    repetition: int,
    *,
    generator: Any = None,
    output_sink: Callable[[dict], None] | None = None,
    audits: dict | None = None,
) -> Sample:
    instructions = instructions_for(suite)
    selection_instructions = INSTRUCTIONS if suite.experiment.selection_cost_protocol else instructions
    protocol_v2 = suite.experiment.citation_protocol is not None
    schema_version = "evidence-admission.sample.v2" if protocol_v2 else "evidence-admission.sample.v1"
    identity: SampleIdentity = {
        "case_hash": digest(case.model_dump(mode="json")),
        "arm": arm,
        "budget": budget,
        "repetition": repetition,
    }
    sample_id = digest(identity)
    sample_started = time.perf_counter()
    materialization_seconds = None
    admission_seconds = 0.0
    generation_started = None
    cancelled_at = None
    cancel_requested = False

    async def phase(name: str):
        nonlocal cancelled_at, cancel_requested
        if name == case.cancel_phase:
            cancel_requested = True
            cancelled_at = time.perf_counter()
            task = asyncio.current_task()
            if task is not None:
                asyncio.get_running_loop().call_soon(task.cancel)
        await checkpoint(name)

    class WaitingGenerator:
        async def generate(self, value, /):
            await phase("generation")
            await asyncio.Event().wait()
            raise AssertionError("cancelled generation cannot return")

    try:
        async with replay_case(case, on_phase=phase) as replay:
            materialization_seconds = replay.seconds
            started = time.perf_counter()
            attempts = ()
            if arm == "bounded-fragments-v3":
                if suite.experiment.v3 is None:
                    raise EvaluationError("v3_requires_versioned_experiment")
                admission, attempts = await v3_admission(
                    replay, budget, suite.experiment.fragment_bytes, selection_instructions, suite.experiment.v3
                )
            elif arm == "bounded-fragments-v2":
                if suite.experiment.v2 is None:
                    raise EvaluationError("v2_requires_versioned_experiment")
                admission, attempts = await hybrid_admission(
                    replay,
                    budget,
                    suite.experiment.fragment_bytes,
                    selection_instructions,
                    suite.experiment.v2,
                )
            elif arm == "window-reduction-v1":
                admission, attempts = await window_admission(
                    replay, budget, suite.experiment.fragment_bytes, selection_instructions
                )
            else:
                admission = await admit(
                    replay.value,
                    replay.materializations,
                    budget=budget,
                    fragment_bytes=suite.experiment.fragment_bytes,
                    instructions=selection_instructions,
                    bounded=arm == "bounded-fragments-v1",
                )
            if suite.experiment.selection_cost_protocol:
                admission = render_citations(admission, instructions, selection_instructions, budget)
            admission_seconds = time.perf_counter() - started
            admission = replace(
                admission,
                metrics=admission.metrics.model_copy(
                    update={
                        "prepared_requests": len(admission.inputs),
                        "prepared_tokens_total": sum(token_count(value, instructions) for value in admission.inputs),
                    }
                ),
            )
            preparation = score_preparation(case, replay.value, admission, budget, INSTRUCTIONS)
            status = "prepared" if admission.accepted else "budget_rejected"
            generation = None
            generation_seconds = 0.0
            outputs: list[str] = []
            requests = 0
            generation_calls = 0
            input_tokens = output_tokens = None
            usage_complete = True
            failure_phase = "none"
            failure_kind = "none"
            selected_generator = WaitingGenerator() if case.cancel_phase == "generation" else generator
            if selected_generator is not None and admission.inputs:
                captured = CapturedGenerator(selected_generator, sample_id, output_sink)
                pipeline = pipeline_for(captured)
                candidates = []
                started = time.perf_counter()
                generation_started = started
                try:
                    for value in admission.inputs:
                        candidates.extend(await pipeline.extract(value))
                except InferenceError as error:
                    status = "failed"
                    usage_complete = False
                    if protocol_v2:
                        failure_phase, failure_kind = "generation", "inference_failed"
                        if isinstance(error, InvalidInferenceOutputError):
                            failure_phase, failure_kind = "output_validation", "invalid_output"
                            if captured.calls:
                                supplied_input, returned = captured.calls[-1]
                                supplied_ids = {item.evidence_id for item in supplied_input.evidence}
                                if any(not set(c.evidence_ids) <= supplied_ids for c in returned.output.candidates):
                                    failure_kind = "invalid_evidence_reference"
                else:
                    # Partial window progress remains a budget rejection for the fixture.
                    status = "generated" if admission.accepted else "budget_rejected"
                    generation = score_generation(case, replay.value, admission, tuple(candidates), audit=audits)
                generation_seconds = time.perf_counter() - started
                generation_calls = captured.attempts
                requests = sum(result.usage.requests for _, result in captured.calls)
                usages = [result.usage for _, result in captured.calls]
                if usages and all(u.input_tokens is not None for u in usages):
                    input_tokens = sum(u.input_tokens for u in usages if u.input_tokens is not None)
                if usages and all(u.output_tokens is not None for u in usages):
                    output_tokens = sum(u.output_tokens for u in usages if u.output_tokens is not None)
                usage_complete = (
                    (len(captured.calls) == captured.attempts if protocol_v2 else usage_complete)
                    and bool(usages)
                    and all(u.input_tokens is not None and u.output_tokens is not None for u in usages)
                )
                outputs.extend(digest(result.output.model_dump(mode="json")) for _, result in captured.calls)
            memories, cursor = await replay.mutations()
            return Sample(
                schema_version=schema_version,
                sample_id=sample_id,
                **identity,
                completeness=admission.completeness,
                status=status,
                input_hashes=tuple(digest(value.model_dump(mode="json")) for value in admission.inputs),
                output_hashes=tuple(outputs),
                materializations=replay.materializations,
                projections=tuple(digest(item.model_dump(mode="json")) for item in replay.value.evidence),
                source_hashes=tuple(digest(item.source_ref.source_id) for item in replay.value.evidence),
                definitions=replay.definitions,
                projection_versions=replay.projection_versions,
                metrics=admission.metrics,
                omissions=admission.omissions,
                supports=admission.supports,
                preparation=preparation,
                generation=generation,
                window_attempts=attempts,
                materialization_seconds=replay.seconds,
                admission_seconds=admission_seconds,
                generation_seconds=generation_seconds,
                requests=requests,
                generation_calls=generation_calls,
                provider_input_tokens=input_tokens,
                provider_output_tokens=output_tokens,
                usage_complete=usage_complete,
                failure_phase=failure_phase,
                failure_kind=failure_kind,
                memory_mutations=memories,
                published_cursor=cursor,
                reason="generation_failed"
                if status == "failed"
                else "none"
                if admission.accepted
                else (
                    "resource_budget"
                    if isinstance(admission, V2Admission) and admission.limit_exhausted
                    else "input_budget"
                ),
            )
    except asyncio.CancelledError as error:
        if not cancel_requested or cancelled_at is None:
            raise
        # The fixture requests a real task cancellation. Restore the outer suite's cancellation counter.
        task = asyncio.current_task()
        if task is not None:
            if task.cancelling() > 1:
                raise
            task.uncancel()
        return Sample(
            schema_version=schema_version,
            sample_id=sample_id,
            **identity,
            completeness="cancelled",
            status="cancelled",
            reason="cancelled",
            source_hashes=tuple(digest(source.source_id) for source in case.sources),
            materialization_seconds=materialization_seconds
            if materialization_seconds is not None
            else time.perf_counter() - sample_started,
            admission_seconds=admission_seconds,
            generation_seconds=time.perf_counter() - generation_started if generation_started is not None else 0,
            cancellation_seconds=time.perf_counter() - cancelled_at,
            memory_mutations=getattr(error, "memory_mutations", -1),
            published_cursor=getattr(error, "published_cursor", -1),
        )
    except (RepositoryNotFoundError, ValidationError):
        if case.fault == "none":
            raise
        return Sample(
            schema_version=schema_version,
            sample_id=sample_id,
            **identity,
            completeness="unavailable",
            status="unavailable",
            reason="materialization_unavailable",
            source_hashes=tuple(digest(source.source_id) for source in case.sources),
            materialization_seconds=time.perf_counter() - sample_started,
        )
