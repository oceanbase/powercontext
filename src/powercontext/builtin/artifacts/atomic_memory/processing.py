# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Content-free Atomic Memory input-budget blocks and recovery receipts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from powercontext.builtin.inference import InvalidInferenceOutputError
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef


class AtomicMemoryInputBudgetError(InvalidInferenceOutputError):
    """A local budget rejection with an extraction-only input snapshot.

    ``input_fingerprint`` hashes the exact extraction instructions and rendered
    input consumed by the estimator. Reconcile rejections have no fingerprint
    and retain their existing recovery behavior.
    """

    def __init__(self, input_fingerprint: str | None = None) -> None:
        super().__init__("atomic-memory-input-budget", "A complete input exceeds its token budget")
        self.input_fingerprint = input_fingerprint


class AtomicMemoryWorkBlock(BaseModel):
    block_id: str
    scope_id: str
    binding_name: str
    source_after: int
    source_through: int
    input_fingerprint: str
    estimated_input_tokens: int
    effective_budget: int
    token_estimator_id: str
    failure_location: Literal["atomic_memory.extract"] = "atomic_memory.extract"
    error_code: Literal["input_budget_exceeded"] = "input_budget_exceeded"
    blocked_at: datetime
    retried_at: datetime | None = None
    retry_request_generation: int | None = None


class AtomicMemoryProcessingStatus(BaseModel):
    state: Literal["idle", "pending", "blocked"]
    source_cursor: int
    source_head: int
    block: AtomicMemoryWorkBlock | None = None


class AtomicMemoryRetryResult(BaseModel):
    status: Literal["accepted"] = "accepted"
    block: AtomicMemoryWorkBlock


class AtomicMemoryBlockNotFoundError(PowerContextError, RuntimeError):
    """The exact block does not belong to the selected Scope and binding."""


class AtomicMemoryBlockConflictError(PowerContextError, RuntimeError):
    """An unretried block no longer owns the current Source frontier."""


class AtomicMemoryBlockSourceIneligibleError(PowerContextError, RuntimeError):
    """The blocked Source no longer qualifies as generation evidence."""

    def __init__(self, block_id: str, source_ref: SourceRef) -> None:
        super().__init__("The blocked Source is no longer eligible for Memory processing")
        self.block_id = block_id
        self.source_ref = source_ref


class AtomicMemoryRecoveryUnavailableError(PowerContextError, RuntimeError):
    """Recovery needs a configured generation pipeline."""
