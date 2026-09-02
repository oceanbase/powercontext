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

"""Canonical Handoff Report values."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.artifacts.handoff import HandoffContent
from powercontext.builtin.scope.models import ScopeDescriptor, ScopeSelection

HandoffReportStatus = Literal["continuable", "blocked", "complete", "no_handoff"]


class _ReportValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScopeHandoffReport(_ReportValue):
    """The exact latest Handoff projected for one selected Scope."""

    scope: ScopeDescriptor
    status: HandoffReportStatus
    handoff: ArtifactAddress | None = None
    content: HandoffContent | None = None

    @model_validator(mode="after")
    def validate_state(self) -> ScopeHandoffReport:
        if self.status == "no_handoff":
            if self.handoff is not None or self.content is not None:
                raise ValueError("no_handoff cannot contain Handoff data")  # noqa: TRY003
            return self
        if self.handoff is None or self.content is None:
            raise ValueError("reported Scope must contain an exact Handoff")  # noqa: TRY003
        if self.handoff.scope_id != self.scope.scope_id:
            raise ValueError("Handoff address must belong to the reported Scope")  # noqa: TRY003
        if self.status != self.content.disposition:
            raise ValueError("report status must match Handoff disposition")  # noqa: TRY003
        return self


class HandoffReportSummary(_ReportValue):
    continuable_count: StrictInt = Field(ge=0)
    blocked_count: StrictInt = Field(ge=0)
    complete_count: StrictInt = Field(ge=0)
    no_handoff_count: StrictInt = Field(ge=0)


class HandoffReport(_ReportValue):
    """A read-only projection over all, exact, or subtree Scope selection."""

    schema_version: Literal["powercontext.handoff-report.v2"] = Field(
        default="powercontext.handoff-report.v2",
        alias="schema",
    )
    selection: ScopeSelection
    scope_ids: tuple[str, ...]
    generated_at: datetime
    summary: HandoffReportSummary
    scopes: tuple[ScopeHandoffReport, ...]
    selection_digest: str | None = None
    report_digest: str | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> HandoffReport:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")  # noqa: TRY003
        if self.scope_ids != tuple(entry.scope.scope_id for entry in self.scopes):
            raise ValueError("scope_ids must match report entries")  # noqa: TRY003
        expected = {
            "continuable_count": sum(entry.status == "continuable" for entry in self.scopes),
            "blocked_count": sum(entry.status == "blocked" for entry in self.scopes),
            "complete_count": sum(entry.status == "complete" for entry in self.scopes),
            "no_handoff_count": sum(entry.status == "no_handoff" for entry in self.scopes),
        }
        if self.summary.model_dump() != expected:
            raise ValueError("summary must match report entries")  # noqa: TRY003
        return self


__all__ = ["HandoffReport", "HandoffReportStatus", "HandoffReportSummary", "ScopeHandoffReport"]
