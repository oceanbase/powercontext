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

"""Collect ticket and chat snapshots through the managed Runtime."""

import asyncio
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from powercontext.errors import InvalidConnectorRunError
from powercontext.source_management import Capability, TargetIdentity
from powercontext.source_management.context import PluginContext
from powercontext.source_management.errors import PluginError
from powercontext.source_management.models import ManagementError
from powercontext.sources import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus

from .definition import ChatCapture, ChatDefinition, TicketDefinition, TicketDetails

plugin_api_version = "2"
connector_name = "example-ticket-chat"
connector_version = "1"
definitions = (TicketDefinition(), ChatDefinition())


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1)
    file: str = "events.json"
    batch_size: int = Field(default=100, ge=1, le=1000)
    interval_seconds: float = Field(default=5, gt=0)


capabilities = (
    Capability(
        name="ticket-chat.dataset",
        version="1",
        description="Collect ticket and chat snapshots from the configured JSON dataset",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
)


async def identify_target(request, config):
    return TargetIdentity(target_key=config.namespace, normalized_input={})


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"] = "1"
    namespace: str
    seen: dict[str, str] = Field(default_factory=dict)


class TicketChatConnector:
    name = connector_name
    version = connector_version
    source_definitions = frozenset({"example-ticket", "example-chat"})

    def __init__(self, ctx: PluginContext, config: Config) -> None:
        self.ctx, self.config = ctx, config
        self.path = ctx.plugin_dir / config.file
        self.target = None

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        checkpoint = (
            Checkpoint.model_validate(session.checkpoint)
            if session.checkpoint is not None
            else Checkpoint(namespace=self.config.namespace)
        )
        if checkpoint.namespace != self.config.namespace:
            raise InvalidConnectorRunError("checkpoint", "namespace changed; use a new Binding ID")
        rows = TypeAdapter(dict[str, list[dict[str, JsonValue]]]).validate_python(
            json.loads(self.path.read_text(encoding="utf-8"))
        )
        items = [
            ("example-ticket", TicketDetails.model_validate({**row, "namespace": self.config.namespace}))
            for row in rows.get("tickets", [])
        ] + [
            ("example-chat", ChatCapture.model_validate({**row, "namespace": self.config.namespace}))
            for row in rows.get("chats", [])
        ]
        identities = [
            (kind, value.ticket_id if isinstance(value, TicketDetails) else value.chat_id) for kind, value in items
        ]
        if len(set(identities)) != len(identities):
            raise InvalidConnectorRunError("duplicate-item", "fixture contains duplicate identities")
        accepted = 0
        for (kind, value), (_, identity) in zip(items, identities, strict=True):
            item_key = f"{kind}:{identity}"
            fingerprint = hashlib.sha256(
                json.dumps(value.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if checkpoint.seen.get(item_key) != fingerprint and accepted < self.config.batch_size:
                await self.ctx.targets.submit(
                    self.target, session=session, item_id=item_key, definition_name=kind, value=value
                )
                checkpoint.seen[item_key] = fingerprint
                accepted += 1
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint.model_dump(mode="json"))


async def apply(ctx: PluginContext, config: Config) -> None:
    connector = TicketChatConnector(ctx, config)
    ctx.connect(connector)
    while True:
        for target in await ctx.targets.list():
            connector.target = target
            scope_id = target.scope_id
            try:
                result = await ctx.run_batch(scope_id=scope_id, targets=(target,))
            except ManagementError as error:
                if error.code != "stale-target":
                    raise
                # A target can be stopped while another Scope's batch is running.
                break
            with (ctx.plugin_dir / "receipts.jsonl").open("a", encoding="utf-8") as receipts:
                for item in result.items:
                    if item.source_ref is not None:
                        receipts.write(
                            json.dumps(
                                {
                                    "scope_id": scope_id,
                                    "item_id": item.item_id,
                                    "source": item.source_ref.model_dump(mode="json"),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
            if result.status is not ConnectorRunStatus.COMPLETE:
                raise PluginError("batch-incomplete", "ticket/chat batch did not complete")
        await asyncio.sleep(config.interval_seconds)
