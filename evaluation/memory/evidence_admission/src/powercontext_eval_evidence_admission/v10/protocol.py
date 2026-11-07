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

"""Adapt compact requests at the extractor boundary of the real generation Pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryGenerationPipeline
from powercontext.builtin.inference import GenerationResult, character_token_estimator

from ..manifests import EvaluationError, file_hash, read_json
from ..models import canonical, digest
from ..runner import NoReconciliation
from ..v7.workflow import budget_status
from ..v8.admission import range_units, unit_input
from ..v9.codec import INSTRUCTIONS as V9_INSTRUCTIONS
from ..v9.codec import Request, TemplateOutput, prepare, render, restore
from ..v9.contract import identity as v9_identity

ATOMICITY_RULES = (
    "Split independently changeable assertions into separate self-contained candidates, even when they share a subject."
)
INSTRUCTIONS = V9_INSTRUCTIONS.replace("Split facts that can change independently.", ATOMICITY_RULES)
INSTRUCTIONS += "\nUse copy parts for exact literals and raw Unicode."
ROOT = Path(__file__).resolve().parent


def identity():
    return {
        "contract": read_json(ROOT / "contract.json"),
        "v9_dependency": v9_identity(),
        "files": {p.name: file_hash(p) for p in sorted(ROOT.iterdir()) if p.suffix in {".py", ".json"}},
        "instructions_hash": digest(INSTRUCTIONS),
        "schema_hash": digest(TemplateOutput.model_json_schema()),
    }


@dataclass(frozen=True)
class BoundRequest:
    encoded: Request
    binding_hash: str
    cost: dict


def request_binding(encoded):
    return digest(
        {"v9_request": encoded.binding_hash, "instructions": INSTRUCTIONS, "schema": TemplateOutput.model_json_schema()}
    )


def bind(value, supports):
    encoded = prepare(value, supports)
    total = character_token_estimator().estimate(
        canonical(
            {
                "instructions": INSTRUCTIONS,
                "input": encoded.payload,
                "output_schema": TemplateOutput.model_json_schema(),
            }
        ).decode()
    )
    cost = {
        **encoded.cost,
        "single_estimated_input_tokens": total,
        "instructions_hash": digest(INSTRUCTIONS),
        "provider_wire_overhead_unknown": True,
    }
    # v9 component totals belong to the v9 instruction envelope, not to this request.
    cost.pop("components")
    cost["serialized_request_bytes"] = len(
        canonical(
            {
                "instructions": INSTRUCTIONS,
                "input": encoded.payload,
                "output_schema": TemplateOutput.model_json_schema(),
            }
        )
    )
    return BoundRequest(encoded, request_binding(encoded), cost)


@dataclass(frozen=True)
class Plan:
    requests: tuple[BoundRequest, ...]
    accepted: bool
    overflow: dict[str, bool]


def pack(inputs, supports, budget, policy, *, allow_repacking):
    """Preserve original control requests; only fragment arms may repack whole admitted ranges."""
    requests = []
    for value in inputs:
        request = bind(value, supports)
        if request.cost["single_estimated_input_tokens"] <= budget or not allow_repacking:
            requests.append(request)
            continue
        pending = []
        for evidence in value.evidence:
            for unit in range_units(evidence, supports):
                candidate = bind(*unit_input([*pending, unit]))
                if candidate.cost["single_estimated_input_tokens"] <= budget:
                    pending.append(unit)
                else:
                    if pending:
                        requests.append(bind(*unit_input(pending)))
                    pending = [unit]
        if pending:
            requests.append(bind(*unit_input(pending)))
    overflow = budget_status([r.cost for r in requests], budget, policy)
    return Plan(tuple(requests), not any(overflow.values()), overflow)


class Extractor:
    """The wire generator is injected; this module has no credentials or network constructor."""

    def __init__(self, request, generator):
        self.request, self.generator = request, generator

    async def generate(self, value, /):
        request = self.request
        if value != request.encoded.value or request.binding_hash != request_binding(request.encoded):
            raise EvaluationError("v10_request_binding_mismatch")
        restored = restore(request.encoded, request.encoded.payload, request_hash=request.encoded.binding_hash)
        if restored != value:
            raise EvaluationError("v10_wire_input_changed")
        generated = await self.generator.generate(request.encoded.payload)
        output = render(
            TemplateOutput.model_validate(generated.output), request.encoded, request_hash=request.encoded.binding_hash
        )
        return GenerationResult(output=output, usage=generated.usage)


def pipeline(request, generator):
    return AtomicMemoryGenerationPipeline(
        extractor=Extractor(request, generator),
        reconciler=NoReconciliation(),
        estimator=character_token_estimator(),
        extraction_instructions=INSTRUCTIONS,
    )
