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

"""Two captured Source types with independent structured projection contracts."""

import hashlib
from typing import cast

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from powercontext.sources import Source, SourceMaterialization, SourceProjection


class TicketDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    author: str
    text: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ChatCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    room: str
    messages: list[ChatMessage]


class ChatMessages(RootModel[list[ChatMessage]]):
    pass


class TicketSource(Source):
    capture: TicketDetails


class ChatSource(Source):
    capture: ChatCapture


class TicketDetailsProjection:
    name = "example.ticket-details"
    version = "1"
    source_class = TicketSource
    output_class = TicketDetails

    def project(self, source: TicketSource, /) -> TicketDetails:
        return source.capture


class ChatMessagesProjection:
    name = "example.chat-messages"
    version = "2"
    source_class = ChatSource
    output_class = ChatMessages

    def project(self, source: ChatSource, /) -> ChatMessages:
        return ChatMessages(source.capture.messages)


class TicketDefinition:
    input_class = TicketDetails
    source_class = TicketSource
    name = "example-ticket"
    version = "2"
    projections = (cast(SourceProjection[TicketSource], TicketDetailsProjection()),)

    async def resolve(self, value: TicketDetails, /) -> TicketSource:
        return TicketSource(
            name="ticket-" + hashlib.sha256(rfc8785.dumps(value.model_dump(mode="json"))).hexdigest(),
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            description=value.title,
            capture=value,
        )

    async def read(self, source: TicketSource, /) -> TicketDetails:
        return source.capture


class ChatDefinition:
    input_class = ChatCapture
    source_class = ChatSource
    name = "example-chat"
    version = "1"
    projections = (cast(SourceProjection[ChatSource], ChatMessagesProjection()),)

    async def resolve(self, value: ChatCapture, /) -> ChatSource:
        return ChatSource(
            name="chat-" + hashlib.sha256(rfc8785.dumps(value.model_dump(mode="json"))).hexdigest(),
            materialization=SourceMaterialization.CAPTURED,
            description=value.room,
            capture=value,
        )

    async def read(self, source: ChatSource, /) -> ChatCapture:
        return source.capture
