# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Private, content-free preparation history and explicitly reported feedback."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from powercontext.artifacts import ArtifactRef


class ContextUsageItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(min_length=1, max_length=256)
    artifact: ArtifactRef


class ContextUsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    usage_id: str = Field(pattern="^[0-9a-f]{32}$")
    scope_id: str = Field(min_length=1, max_length=256)
    created_at: datetime
    status: Literal["ready", "empty"]
    content_bytes: int = Field(ge=0)
    reason: str | None = None
    truncated: bool = False
    items: tuple[ContextUsageItem, ...] = ()


class ContextFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feedback_id: str = Field(pattern="^[0-9a-f]{32}$")
    record_kind: Literal["preparation", "bootstrap"]
    record_id: str = Field(min_length=1, max_length=64)
    adoption: Literal["yes", "no", "unknown"]
    helpful: Literal["yes", "no", "unknown"]
    reason: str = Field(default="", max_length=2000)


class ContextUsageFeedback(ContextFeedbackRequest):
    scope_id: str
    created_at: datetime
    reported_by: str
