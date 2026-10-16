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

"""Operational Topic processing state, containing no Source or model content."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from powercontext.errors import PowerContextError


class TopicMemoryWorkBlock(BaseModel):
    block_id: str
    attempt_id: str
    scope_id: str
    binding_name: str
    source_after: int
    source_through: int | None
    budget_source_through: int | None
    legacy_source_through: int | None = None
    target_source_through: int
    attempts: int
    requests: int
    tokens: int
    error_code: str
    last_error_code: str | None
    blocked_at: datetime
    retried_at: datetime | None = None
    retry_request_generation: int | None = None


class TopicMemoryProcessingStatus(BaseModel):
    state: Literal["idle", "pending", "blocked"]
    source_cursor: int
    source_head: int
    target_source_through: int | None
    block: TopicMemoryWorkBlock | None = None


class TopicMemoryRetryResult(BaseModel):
    status: Literal["accepted"] = "accepted"
    block: TopicMemoryWorkBlock


class TopicMemoryBlockNotFoundError(PowerContextError, RuntimeError):
    """The requested block does not belong to this Scope."""


class TopicMemoryBlockConflictError(PowerContextError, RuntimeError):
    """The requested block no longer owns the current frontier."""
