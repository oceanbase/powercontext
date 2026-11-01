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

"""Atomic Memory input contract over the shared bounded JSON codec."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Literal

from pydantic import ValidationError

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput
from powercontext.builtin.artifacts.atomic_memory.reconciliation import AtomicMemoryReconciliationInput
from powercontext.builtin.inference.input_encoding import InputEncoding as AtomicMemoryInputEncoding
from powercontext.builtin.inference.input_encoding import InputEncodingError as AtomicMemoryInputEncodingError
from powercontext.builtin.inference.input_encoding import InputEncodingMode as AtomicMemoryEncodingMode
from powercontext.builtin.inference.input_encoding import decode_input, encode_input
from powercontext.builtin.inference.models import InputEncodingReport as AtomicMemoryInputEncodingReport
from powercontext.builtin.inference.tokens import TokenEstimator
from powercontext.builtin.inference.usage import (
    collect_input_encodings as collect_atomic_memory_input_encodings,
)
from powercontext.builtin.inference.usage import (
    record_input_encoding as record_atomic_memory_input_encoding,
)

AtomicMemoryEncodingStage = Literal["atomic_memory.extract", "atomic_memory.reconcile", "unsupported"]
_Input = AtomicMemoryExtractionInput | AtomicMemoryReconciliationInput
_SCHEMA = "powercontext.atomic-memory-input.v1"
_STAGES = {
    AtomicMemoryExtractionInput: "atomic_memory.extract",
    AtomicMemoryReconciliationInput: "atomic_memory.reconcile",
}


def encode_atomic_memory_input(
    value: _Input,
    *,
    mode: AtomicMemoryEncodingMode = "off",
    estimator: TokenEstimator | None = None,
    allowed: bool = True,
) -> AtomicMemoryInputEncoding:
    """Preserve the Atomic Memory schema while sharing the general codec.

    Only the two built-in input DTOs support this contract. Unsupported DTOs
    retain their exact serialized input and never enter candidate construction.
    """

    if mode not in {"off", "shadow", "active"}:
        raise ValueError("unsupported Atomic Memory encoding mode")  # noqa: TRY003
    stage = _STAGES.get(type(value))
    original = value.model_dump_json()
    result = encode_input(
        original,
        stage=stage or "unsupported",
        mode=mode if stage is not None else "off",
        estimator=estimator,
        allowed=allowed,
        schema=_SCHEMA,
    )
    if stage is None and mode != "off":
        return replace(result, report=replace(result.report, mode=mode, reason="unsupported_schema"))
    if result.report.reason in {"applied", "shadow_candidate"}:
        try:
            valid = type(value).model_validate_json(original).model_dump_json() == original
        except ValidationError:
            valid = False
        if not valid:
            return replace(
                result,
                prompt=original,
                report=replace(
                    result.report,
                    reason="roundtrip_failed",
                    selected_bytes=result.report.original_bytes,
                    selected_estimated_tokens=result.report.original_estimated_tokens,
                ),
            )
    return result


def decode_atomic_memory_input(prompt: str) -> str:
    """Restore and validate the complete, canonical Atomic Memory stage input."""

    restored = decode_input(prompt, schema=_SCHEMA)
    # The shared decoder has already validated the envelope and bounded parsing.
    stage = json.loads(prompt)["stage"]
    input_types = {name: model for model, name in _STAGES.items()}
    input_type = input_types.get(stage)
    if input_type is None:
        raise AtomicMemoryInputEncodingError("unsupported_envelope")
    try:
        validated = input_type.model_validate_json(restored)
    except ValidationError:
        raise AtomicMemoryInputEncodingError("invalid_stage_input") from None
    if validated.model_dump_json() != restored:
        raise AtomicMemoryInputEncodingError("noncanonical_stage_input")
    return restored


__all__ = [
    "AtomicMemoryEncodingMode",
    "AtomicMemoryEncodingStage",
    "AtomicMemoryInputEncoding",
    "AtomicMemoryInputEncodingError",
    "AtomicMemoryInputEncodingReport",
    "collect_atomic_memory_input_encodings",
    "decode_atomic_memory_input",
    "encode_atomic_memory_input",
    "record_atomic_memory_input_encoding",
]
