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

"""Collect chat rooms from a JSON file on the runner; each room becomes one Source of its messages.

A room is submitted again only when its messages change. The ``example.chat-messages``
projection delivers the messages to Artifact plugins.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import cast

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from powercontext.plugins import (
    Collect,
    CollectedItem,
    CollectRequest,
    CollectResult,
    Interval,
    SourcePlugin,
    TargetSpec,
)
from powercontext.sources import Source, SourceMaterialization, SourceProjection


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


class ChatSource(Source):
    capture: ChatCapture


class ChatMessagesProjection:
    name = "example.chat-messages"
    version = "2"
    source_class = ChatSource
    output_class = ChatMessages

    def project(self, source: ChatSource, /) -> ChatMessages:
        return ChatMessages(source.capture.messages)


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


class ChatFile(BaseModel):
    file: str = Field(min_length=1, description="Path of a JSON file with a chats array on the runner")
    namespace: str = Field(default="chats", min_length=1)


class Checkpoint(BaseModel):
    # A digest per item already submitted from this target.
    seen: dict[str, str] = Field(default_factory=dict)


class ChatCollector:
    async def collect(self, request: CollectRequest) -> CollectResult:
        result = CollectResult()
        for target in request.targets:
            settings = ChatFile.model_validate(target.parameters)
            try:
                chats = json.loads(await asyncio.to_thread(Path(settings.file).read_text, encoding="utf-8"))["chats"]
            except (OSError, ValueError, KeyError) as error:
                result.fail(target, code="file_unreadable", message=type(error).__name__)
                continue
            seen = Checkpoint.model_validate(target.checkpoint or {}).seen
            items = []
            for chat in chats:
                capture = ChatCapture(namespace=settings.namespace, **chat)
                digest = hashlib.sha256(rfc8785.dumps(capture.model_dump(mode="json"))).hexdigest()
                if seen.get(capture.chat_id) != digest and len(items) < request.max_items:
                    items.append(CollectedItem(definition=ChatDefinition.name, value=capture))
                    seen[capture.chat_id] = digest
            result.add(target, items, checkpoint=Checkpoint(seen=seen).model_dump())
        return result


async def setup(ctx):
    return ChatCollector()


plugin = SourcePlugin(
    version="1.0.0",
    definitions=(ChatDefinition(),),
    collect=Collect(schedule=Interval(seconds=10)),
    target=TargetSpec(
        description="Collect chat rooms from a JSON file on the runner",
        parameters=ChatFile,
        identity={"file": ("strip",)},
    ),
    setup=setup,
)
