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

"""Collect tickets from a JSON file on the runner, with text and structured projections.

Each target names one file. A ticket becomes a new Source whenever its content changes;
the checkpoint keeps a digest per ticket, so unchanged tickets are not submitted again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import cast

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.plugins import (
    Collect,
    CollectedItem,
    CollectRequest,
    CollectResult,
    Interval,
    SourcePlugin,
    TargetSpec,
)
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


class TicketFile(BaseModel):
    file: str = Field(min_length=1, description="Path of a JSON array of tickets on the runner")
    namespace: str = Field(default="tickets", min_length=1)
    project: str = Field(default="default", min_length=1)


class Checkpoint(BaseModel):
    # A digest per item already submitted from this target.
    seen: dict[str, str] = Field(default_factory=dict)


class TicketCollector:
    async def collect(self, request: CollectRequest) -> CollectResult:
        result = CollectResult()
        for target in request.targets:
            settings = TicketFile.model_validate(target.parameters)
            try:
                tickets = json.loads(await asyncio.to_thread(Path(settings.file).read_text, encoding="utf-8"))
            except (OSError, ValueError) as error:
                # Only this target fails; the others in the call still commit.
                result.fail(target, code="file_unreadable", message=type(error).__name__)
                continue
            seen = Checkpoint.model_validate(target.checkpoint or {}).seen
            items = []
            for ticket in tickets:
                capture = TicketCapture(
                    namespace=settings.namespace,
                    project=settings.project,
                    ticket_id=str(ticket["id"]),
                    title=ticket["title"],
                    body=ticket["body"],
                    labels=ticket.get("labels", []),
                    metadata=ticket.get("metadata", {}),
                )
                digest = capture.snapshot_id()
                if seen.get(capture.ticket_id) != digest and len(items) < request.max_items:
                    items.append(CollectedItem(definition=TicketDefinition.name, value=capture))
                    seen[capture.ticket_id] = digest
            result.add(target, items, checkpoint=Checkpoint(seen=seen).model_dump())
        return result


async def setup(ctx):
    return TicketCollector()


plugin = SourcePlugin(
    version="1.0.0",
    definitions=(TicketDefinition(),),
    collect=Collect(schedule=Interval(seconds=10)),
    target=TargetSpec(
        description="Collect tickets from a JSON file on the runner",
        parameters=TicketFile,
        identity={"file": ("strip",)},
    ),
    setup=setup,
)
