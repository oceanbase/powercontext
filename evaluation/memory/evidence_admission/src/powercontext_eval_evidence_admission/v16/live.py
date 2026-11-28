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

"""Run the real Atomic Memory Pipeline without rewriting or splitting generated candidates."""

from __future__ import annotations

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryGenerationPipeline
from powercontext.builtin.inference import GenerationResult, character_token_estimator

from ..manifests import EvaluationError
from ..runner import NoReconciliation
from .codec import INSTRUCTIONS, TemplateOutput, cell_binding, render
from .wire import restore


class Extractor:
    def __init__(self, request, generator):
        self.request = request
        self.generator = generator
        self.expected_hash = request.binding_hash
        self.expected_cells_hash = cell_binding(request.cells)
        self.raw_template = None
        self.raw_usage = None

    async def generate(self, value, /):
        request = self.request
        if value != restore(request, request.payload, request_hash=self.expected_hash):
            raise EvaluationError("v16_request_value_mismatch")
        generated = await self.generator.generate(request.payload)
        self.raw_template = TemplateOutput.model_validate(generated.output)
        self.raw_usage = generated.usage
        restore(request, request.payload, request_hash=self.expected_hash)
        output = render(self.raw_template, request.cells, request.aliases, request_hash=self.expected_cells_hash)
        return GenerationResult(output=output, usage=generated.usage)


def pipeline(extractor):
    return AtomicMemoryGenerationPipeline(
        extractor=extractor,
        reconciler=NoReconciliation(),
        estimator=character_token_estimator(),
        extraction_instructions=INSTRUCTIONS,
    )
