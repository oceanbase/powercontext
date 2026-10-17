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

"""Read-only exact replay over realized Artifact-evolution attempts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from powercontext_eval_artifact_evolution.models import ArtifactEvolutionBundle, DecisionRequest, ProposalAttempt


@dataclass(frozen=True)
class ReplayResult:
    """One exact replay lookup result."""

    status: str
    attempt: ProposalAttempt | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ReplayCoverage:
    """Exact replay support counts, retaining unsupported requests in denominators."""

    requested_decisions: int
    supported_decisions: int
    unsupported_decision_ids: tuple[str, ...]
    requested_trajectories: int
    complete_trajectories: int
    saved_model_calls: int
    saved_retries: int
    saved_input_tokens: int | None
    saved_output_tokens: int | None
    input_tokens_complete: bool
    output_tokens_complete: bool

    @property
    def decision_ratio(self) -> float:
        return self.supported_decisions / self.requested_decisions

    @property
    def trajectory_ratio(self) -> float:
        return self.complete_trajectories / self.requested_trajectories


class ReplayEngine:
    """Replay only what the bundle recorded; never call a model or Runtime."""

    def __init__(self, bundle: ArtifactEvolutionBundle) -> None:
        self._bundle = bundle
        self._attempts = {attempt.decision.key: attempt for attempt in bundle.attempts}

    def replay(self, request: DecisionRequest) -> ReplayResult:
        """Return an exact realized attempt or deterministic ``out_of_support``."""

        attempt = self._attempts.get(request.key)
        if attempt is None:
            return ReplayResult(
                status="out_of_support",
                reason="no realized attempt has the requested trajectory, observations, and action signature",
            )
        return ReplayResult(status="replayed", attempt=attempt)

    def coverage(self) -> ReplayCoverage:
        decisions = self._bundle.requested_decisions
        supported = sum(1 for decision in decisions if decision.key in self._attempts)
        unsupported = tuple(decision.decision_id for decision in decisions if decision.key not in self._attempts)
        complete_trajectories = 0
        for trajectory in self._bundle.requested_trajectories:
            trajectory_decisions = {
                decision.key
                for decision in decisions
                if decision.trajectory_id == trajectory.trajectory_id
                and decision.decision_id in trajectory.decision_ids
            }
            if trajectory_decisions and trajectory_decisions <= self._attempts.keys():
                complete_trajectories += 1
        costs = tuple(attempt.cost for attempt in self._bundle.attempts)
        input_tokens = tuple(cost.input_tokens for cost in costs)
        output_tokens = tuple(cost.output_tokens for cost in costs)
        return ReplayCoverage(
            requested_decisions=len(decisions),
            supported_decisions=supported,
            unsupported_decision_ids=unsupported,
            requested_trajectories=len(self._bundle.requested_trajectories),
            complete_trajectories=complete_trajectories,
            saved_model_calls=sum(cost.model_calls for cost in costs),
            saved_retries=sum(cost.retries for cost in costs),
            saved_input_tokens=_sum_optional(input_tokens),
            saved_output_tokens=_sum_optional(output_tokens),
            input_tokens_complete=all(value is not None for value in input_tokens),
            output_tokens_complete=all(value is not None for value in output_tokens),
        )


def _sum_optional(values: Iterable[int | None]) -> int | None:
    entries = tuple(values)
    numbers = tuple(value for value in entries if isinstance(value, int))
    return sum(numbers) if numbers else None
