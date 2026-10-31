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

"""v14 live boundary: send the cell-encoded payload, restore cells from the model output."""

from __future__ import annotations

from dataclasses import dataclass

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryGenerationPipeline
from powercontext.builtin.inference import GenerationResult, character_token_estimator

from ..manifests import EvaluationError
from ..runner import NoReconciliation
from .codec import INSTRUCTIONS, TemplateOutput, cell_binding, render
from .wire import Request, prepare


def request_binding(request: Request) -> str:
    return request.binding_hash


@dataclass(frozen=True)
class BoundRequest:
    encoded: Request
    binding_hash: str


def bind(value, supports) -> BoundRequest:
    encoded = prepare(value, supports)
    return BoundRequest(encoded, encoded.binding_hash)


class Extractor:
    """Sends the cell-encoded payload to the model; restores cells and aliases on the way out."""

    def __init__(self, request, generator):
        self.request, self.generator = request, generator

    async def generate(self, value, /):
        request = self.request
        if value != request.encoded.value:
            raise EvaluationError("v14_request_value_mismatch")
        generated = await self.generator.generate(request.encoded.payload)
        output = render(
            TemplateOutput.model_validate(generated.output),
            request.encoded.cells,
            request.encoded.aliases,
            request_hash=cell_binding(request.encoded.cells),
        )
        return GenerationResult(output=output, usage=generated.usage)


def pipeline(request, generator):
    return AtomicMemoryGenerationPipeline(
        extractor=Extractor(request, generator),
        reconciler=NoReconciliation(),
        estimator=character_token_estimator(),
        extraction_instructions=INSTRUCTIONS,
    )
