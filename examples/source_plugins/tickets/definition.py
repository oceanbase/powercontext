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

"""Capture immutable ticket snapshots with text and structured projections."""

from __future__ import annotations

import hashlib
from typing import cast

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    Source,
    SourceMaterialization,
    SourceProjection,
    TextEvidence,
)


class TicketCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1)
    project: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    def snapshot_id(self) -> str:
        return "ticket-" + hashlib.sha256(rfc8785.dumps(self.model_dump(mode="json"))).hexdigest()


class TicketSource(Source):
    capture: TicketCapture


class TicketDetails:
    name = "example.ticket-details"
    version = "1"
    source_class = TicketSource
    output_class = TicketCapture

    def project(self, source: TicketSource, /) -> TicketCapture:
        return source.capture


class TicketText:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = TicketSource
    output_class = TextEvidence

    def project(self, source: TicketSource, /) -> TextEvidence:
        ticket = source.capture
        return TextEvidence(
            source_type="example-ticket",
            source_id=source.name,
            content=f"{ticket.title}\n\n{ticket.body}",
            metadata={"namespace": ticket.namespace, "project": ticket.project, "ticket_id": ticket.ticket_id},
        )


class TicketDefinition:
    name = "example-ticket"
    version = "1"
    input_class = TicketCapture
    source_class = TicketSource
    projections = (
        cast(SourceProjection[TicketSource], TicketDetails()),
        cast(SourceProjection[TicketSource], TicketText()),
    )

    async def resolve(self, value: TicketCapture, /) -> TicketSource:
        return TicketSource(
            name=value.snapshot_id(),
            materialization=SourceMaterialization.CAPTURED,
            description=f"Ticket {value.ticket_id}",
            capture=value,
        )

    async def read(self, source: TicketSource, /) -> TicketCapture:
        return source.capture
