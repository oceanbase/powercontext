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

"""Call the selected memory tools without giving the model control of identity."""

from __future__ import annotations

from typing import Any

from dify_plugin.core.runtime import Session
from dify_plugin.entities.tool import ToolInvokeMessage
from dify_plugin.interfaces.agent import ToolEntity
from gevent import Timeout


class MemorySession:
    def __init__(self, session: Session, prepare: ToolEntity, observe: ToolEntity, *, identity: dict, run_id: str):
        self.session = session
        self.prepare_tool = prepare
        self.observe_tool = observe
        self.identity = identity
        self.run_id = run_id
        self.sequence = 0
        self.diagnostics: set[str] = set()
        self.unavailable: set[str] = set()

    def invoke(self, tool: ToolEntity, request: dict[str, Any]) -> dict[str, Any] | None:
        if tool.identity.name in self.unavailable:
            return None
        try:
            with Timeout(10, TimeoutError("Memory callback timed out")):
                messages = list(
                    self.session.tool.invoke(
                        provider_type=tool.provider_type,
                        provider=tool.identity.provider,
                        tool_name=tool.identity.name,
                        parameters={**tool.runtime_parameters, "memory_context": self.identity, "request": request},
                        credential_id=tool.credential_id,
                    )
                )
            values = [
                item.message.json_object
                for item in messages
                if item.type.value == "json" and isinstance(item.message, ToolInvokeMessage.JsonMessage)
            ]
            if len(values) != 1 or not isinstance(values[0], dict) or values[0].get("status") == "error":
                self.diagnostics.add("provider_error")
                self.unavailable.add(tool.identity.name)
                return None
            return values[0]
        except Exception:
            self.diagnostics.add("server_unavailable")
            self.unavailable.add(tool.identity.name)
            return None

    def prepare(self, query: str, max_bytes: int) -> str | None:
        result = self.invoke(self.prepare_tool, {"query": query, "max_bytes": max_bytes})
        if result is None:
            return None
        content = result.get("content")
        content_bytes = result.get("content_bytes")
        if result.get("status") == "empty" and content is None and result.get("content_bytes") == 0:
            return None
        if (
            result.get("status") == "ready"
            and isinstance(content, str)
            and isinstance(content_bytes, int)
            and len(content.encode()) == content_bytes <= max_bytes
        ):
            return content
        self.diagnostics.add("invalid_response")
        return None

    def observe(self, event: str, payload: dict[str, Any]) -> None:
        self.sequence += 1
        result = self.invoke(
            self.observe_tool,
            {
                "event_id": f"{self.run_id}:{self.sequence}",
                "event": event,
                "sequence": self.sequence,
                "payload": payload,
                "metadata": {"run_id": self.run_id},
            },
        )
        if result is not None and result.get("status") != "accepted":
            self.diagnostics.add("invalid_response")
            self.unavailable.add(self.observe_tool.identity.name)
