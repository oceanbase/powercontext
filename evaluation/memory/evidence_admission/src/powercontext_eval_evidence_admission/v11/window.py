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

"""Reduce persisted Source windows using both native and final protocol input costs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    project_atomic_memory_evidence,
)
from powercontext.builtin.persistence.memory_windows import MemorySourceWindowRepository
from powercontext.builtin.triggers import SourceHighWatermark, SourceWindowTrigger

from ..admission import Admission, fragments, summarize, token_count
from ..manifests import EvaluationError, file_hash, read_json
from ..replay import checkpoint
from ..runner import INSTRUCTIONS as SELECTION_INSTRUCTIONS
from ..v10.protocol import BoundRequest, bind
from ..v10.protocol import identity as v10_identity
from .lineage import content_identity

ROOT = Path(__file__).resolve().parent


def identity():
    return {
        "contract": read_json(ROOT / "contract.json"),
        "v10_dependency": content_identity(v10_identity()),
        "files": {p.name: file_hash(p) for p in sorted(ROOT.iterdir()) if p.suffix in {".py", ".json"}},
    }


@dataclass(frozen=True)
class WindowPlan:
    admission: Admission
    requests: tuple[BoundRequest, ...]
    attempts: tuple[dict, ...]
    status: str
    reason: str
    consumed_through: int


async def prepare(replay, budget, fragment_bytes, policy, *, on_phase=checkpoint):
    """Read exact journal windows; never split a Source to satisfy either budget."""
    windows = MemorySourceWindowRepository()
    trigger = SourceWindowTrigger()
    state = trigger.initial_state()
    inventory = await fragments(replay.value, replay.materializations, fragment_bytes)
    full = summarize((replay.value,), inventory, inventory, SELECTION_INSTRUCTIONS, bounded=False, accepted=True)
    requests, admitted, attempts = [], [], []
    reason = "none"
    total_cost = 0
    while state.sequence < len(replay.value.evidence):
        await on_phase("window_read")
        async with replay.contexts.database.transaction() as connection:
            watermark, limit = await windows.signal(
                connection, replay.scope_id, state.sequence, len(replay.value.evidence)
            )
            transition = trigger.activate(SourceHighWatermark(sequence=watermark, limit=limit), state)
            action = transition.actions[0]
            stored = await replay.sources.list_window(
                connection, replay.scope_id, after=action.after, through=action.through
            )
        if tuple(s.journal_position for s in stored) != tuple(range(action.after + 1, action.through + 1)):
            raise EvaluationError("v11_noncontiguous_source_window")
        value = AtomicMemoryExtractionInput(
            evidence=tuple(
                [
                    await project_atomic_memory_evidence(s, replay.sources, evidence_id=f"source:{s.journal_position}")
                    for s in stored
                ]
            )
        )
        expected = tuple(e for e in replay.value.evidence if action.after < e.journal_position <= action.through)
        if value.evidence != expected:
            raise EvaluationError("v11_materialization_or_projection_changed")
        request = bind(value, full.supports)
        native_cost = token_count(value, SELECTION_INSTRUCTIONS)
        wire_cost = request.cost["single_estimated_input_tokens"]
        native_fits, protocol_fits = native_cost <= budget, wire_cost <= budget
        attempt = {
            "after": action.after,
            "through": action.through,
            "recovery_limit": limit,
            "native_cost": native_cost,
            "protocol_cost": wire_cost,
            "native_fits": native_fits,
            "protocol_fits": protocol_fits,
            "accepted": False,
            "reason": "input_budget",
        }
        attempts.append(attempt)
        await on_phase("window_cost")
        if not native_fits or not protocol_fits:
            if action.through - action.after == 1:
                reason = "one_source_floor"
                break
            async with replay.contexts.database.transaction() as connection:
                await windows.reduce(
                    connection,
                    replay.scope_id,
                    source_through=watermark,
                    window_limit=max(1, (action.through - action.after) // 2),
                )
            await on_phase("window_retry")
            continue
        if (
            len(requests) + 1 > policy.max_calls_per_sample
            or total_cost + wire_cost > budget * policy.total_input_budget_multiplier
        ):
            reason = "resource_budget"
            attempt["reason"] = reason
            break
        requests.append(request)
        total_cost += wire_cost
        attempt.update(accepted=True, reason="none")
        admitted.extend(f for f in inventory if action.after < f.source.journal_position <= action.through)
        state = transition.state
        async with replay.contexts.database.transaction() as connection:
            await windows.clear_consumed(connection, replay.scope_id, state.sequence)
        await on_phase("window_admitted")
    complete = state.sequence == len(replay.value.evidence)
    admission = summarize(
        tuple(r.encoded.value for r in requests),
        inventory,
        tuple(admitted),
        SELECTION_INSTRUCTIONS,
        bounded=False,
        accepted=complete,
        reason="source_window",
    )
    return WindowPlan(
        admission,
        tuple(requests),
        tuple(attempts),
        "prepared" if complete else "resource_budget_rejected" if reason == "resource_budget" else "budget_rejected",
        reason,
        state.sequence,
    )
