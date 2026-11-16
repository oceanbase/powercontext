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

"""A wire-neutral script of tool calls that a host model fake replays turn by turn."""

from __future__ import annotations

import json
import threading
from collections.abc import Collection
from dataclasses import dataclass
from uuid import uuid4

from tests.e2e.zcode_acceptance.protocol import ToolAction

FINAL_ANSWER = "Synthetic operation complete."


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


class ScriptedTurns:
    """Call each planned tool once, in order, then answer; the host executes every call itself."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.plan()

    def plan(self, actions: list[ToolAction] | None = None) -> None:
        with self._lock:
            self._actions = list(actions or [])
            self._step = 0
            self._prefix = uuid4().hex
            self._pending: ToolCall | None = None

    def next(self, offered: Collection[str], delivered: Collection[str]) -> ToolCall | None:
        """Return the call for this request, or None for the final answer.

        `offered` holds the tool names the host sent and `delivered` the call ids whose results it returned. A request
        that does not yet carry the pending call's result repeats that call, so a host retry cannot skip a step.
        """
        with self._lock:
            if self._pending is not None and self._pending.call_id not in delivered:
                return self._pending
            if self._step >= len(self._actions):
                return None
            operation, arguments = self._actions[self._step]
            name = next((tool for tool in offered if tool == operation or tool.endswith("__" + operation)), None)
            assert name is not None, f"fixture_tool_unavailable_{operation}"
            self._step += 1
            value = arguments if isinstance(arguments, dict) else arguments()
            self._pending = ToolCall(f"call-{self._prefix}-{self._step}", name, json.dumps(value))
            return self._pending
