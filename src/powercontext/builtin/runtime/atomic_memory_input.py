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

"""Bind the two Atomic Memory stages to the shared input preparation contract."""

from __future__ import annotations

from collections.abc import Mapping

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput
from powercontext.builtin.artifacts.atomic_memory.reconciliation import AtomicMemoryReconciliationInput
from powercontext.builtin.inference.input_encoding import InputEncodingMode
from powercontext.builtin.inference.privacy import PrivacyPolicy
from powercontext.builtin.inference.tokens import TokenEstimator
from powercontext.builtin.runtime.input_preparation import render_input


def render_atomic_memory_input(
    value: AtomicMemoryExtractionInput | AtomicMemoryReconciliationInput,
    *,
    modes: Mapping[str, InputEncodingMode],
    estimator: TokenEstimator,
    policy: PrivacyPolicy,
    observe: bool = False,
) -> str:
    stage = "atomic_memory.extract" if isinstance(value, AtomicMemoryExtractionInput) else "atomic_memory.reconcile"
    mode = modes[stage]
    if mode == "off":
        return value.model_dump_json()
    return render_input(
        value,
        stage=stage,
        mode=mode,
        estimator=estimator,
        policy=policy,
        observe=observe,
        schema="powercontext.atomic-memory-input.v1",
    )
