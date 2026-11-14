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

"""Render the effective inference budget matrix for the development docs.

The block is deterministic and language neutral so the zh and en pages can embed
the identical text; ``tests/builtin/runtime/test_budget_matrix_docs.py`` fails if
either page drifts from the values resolved by :meth:`InferenceConfig.generation_budget`.
"""

from __future__ import annotations

from powercontext.builtin.artifacts.topic_memory.generation import narrow_stage_budget, topic_memory_stage_budget
from powercontext.builtin.dream.models import DreamBudget
from powercontext.builtin.inference.budget import GENERATION_OPERATIONS
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig

BEGIN_MARKER = "<!-- budget-matrix:begin -->"
END_MARKER = "<!-- budget-matrix:end -->"

_HEADER = (
    "| operation | binding dimension | complete input ceiling (tokens) | selection ceiling (tokens) | output reserve (tokens) "
    "| safety margin (tokens) | max requests | timeout (s) |"
)
_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- |"


def _effective_requests(config: InferenceConfig, operation: str) -> tuple[int, float]:
    limits = config.generation_limits(operation)
    return limits.max_requests, limits.timeout_seconds


def render_matrix(config: InferenceConfig | None = None) -> str:
    """Return the generated markdown block, markers included."""
    resolved = InferenceConfig() if config is None else config
    lines = [BEGIN_MARKER, _HEADER, _SEPARATOR]
    for operation in sorted(GENERATION_OPERATIONS):
        budget = resolved.generation_budget(operation)
        requested = resolved.generation_model_settings.get("max_tokens")
        selection = "not applicable"
        if operation.startswith("topic_memory."):
            stage = narrow_stage_budget(
                topic_memory_stage_budget(
                    context_window_tokens=resolved.generation_model_context_window_tokens,
                    max_requests=resolved.generation_max_requests,
                    model_settings=resolved.generation_model_settings,
                ),
                budget,
            )
            requested = stage.max_output_tokens_per_request
            selection = str(stage.input_tokens_limit)
        budget = budget.with_output_limit(requested if isinstance(requested, int) else None)
        if operation.startswith("atomic_memory."):
            selection = str(min(RuntimeConfig().atomic_memory_input_tokens_limit, budget.allowed_input_tokens))
        max_requests, timeout = _effective_requests(resolved, operation)
        lines.append(
            f"| `{operation}` | `{budget.binding_dimension}` | {budget.allowed_input_tokens} "
            f"| {selection} | {budget.output_reserve_tokens} | {budget.safety_margin_tokens} "
            f"| {max_requests} | {timeout:g} |"
        )
    embedding = resolved.embedding_budget
    lines.extend((
        "",
        "Embedding (independent limits; unset means unbounded):",
        "",
        f"- items per batch: {_optional(embedding.max_batch_items)}",
        f"- input tokens per text: {_optional(embedding.max_input_tokens_per_text)}",
        f"- input tokens per batch: {_optional(embedding.max_input_tokens_per_batch)}",
        f"- estimator: `{embedding.estimator.estimator_id}@{embedding.estimator.version}`",
    ))
    dream_limits = DreamBudget()
    configured = resolved.generation_model_settings.get("max_tokens")
    dream = resolved.generation_deployment_budget.with_output_limit(
        min(configured if isinstance(configured, int) else 4096, dream_limits.max_output_tokens)
    )
    lines.extend((
        "",
        "Dream (charged per Run against `DreamBudget`, admitted against the deployment window only):",
        "",
        f"- allowed input (tokens): {dream.allowed_input_tokens}",
        f"- context limit (tokens): {dream.context_limit_tokens}",
        f"- output reserve (tokens): {dream.output_reserve_tokens}",
        f"- safety margin (tokens): {dream.safety_margin_tokens}",
        f"- max output (tokens): {dream_limits.max_output_tokens}",
        f"- max model calls per Run: {dream_limits.max_model_calls}",
        f"- timeout per Run (s): {dream_limits.timeout_seconds:g}",
        END_MARKER,
    ))
    return "\n".join(lines)


def _optional(value: int | None) -> str:
    return "not set" if value is None else str(value)


def main() -> None:
    print(render_matrix())


if __name__ == "__main__":
    main()
