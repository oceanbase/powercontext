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

"""Extraction candidates contain facts and evidence, without server-owned merge metadata."""

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput


@pytest.mark.parametrize("creation", [None, {"input_artifact_ids": ["art_existing_a", "art_existing_b"]}])
def test_extraction_rejects_model_supplied_creation(creation):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AtomicMemoryExtractionOutput.model_validate({
            "candidates": [
                {
                    "kind": "fact",
                    "text": "Use a reviewed rollout policy.",
                    "description": "Rollout policy",
                    "evidence_ids": ["source:1"],
                    "creation": creation,
                }
            ],
        })
