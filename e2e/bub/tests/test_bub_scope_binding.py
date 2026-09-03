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

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

from bub.hooks.interception import LlmCallRequest
from powercontext_bub import plugin as plugin_module
from powercontext_bub import tools as tools_module
from powercontext_bub.plugin import STATE_KEY, PowerContextPlugin, PowerContextSettings
from powercontext_bub.scope import workspace_binding_key

RESOLVED_SCOPE_ID = "scp_00000000000000000000000000"


class RecordingClient:
    calls: ClassVar[list[tuple[str, Any]]] = []
    scope_ids: ClassVar[list[str]] = [RESOLVED_SCOPE_ID]

    def __init__(self, base_url: str, *, timeout: float) -> None:
        del base_url, timeout

    async def __aenter__(self) -> RecordingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info

    async def resolve_scope_binding(self, request: Any) -> SimpleNamespace:
        self.calls.append(("resolve", request))
        index = len([name for name, _ in self.calls if name == "resolve"]) - 1
        return SimpleNamespace(scope_id=self.scope_ids[min(index, len(self.scope_ids) - 1)])

    async def capture_content_source(self, request: Any) -> SimpleNamespace:
        self.calls.append(("capture", request))
        return SimpleNamespace(position=1)

    async def prepare_context(self, request: Any) -> SimpleNamespace:
        self.calls.append(("prepare", request))
        return SimpleNamespace(
            content="remembered context",
            content_bytes=18,
            status=SimpleNamespace(value="ready"),
        )

    async def search_memory(self, request: Any) -> SimpleNamespace:
        self.calls.append(("search", request))
        return SimpleNamespace(hits=[])


def _plugin(monkeypatch, tmp_path: Path, *, capture_events: bool = False) -> PowerContextPlugin:
    settings = PowerContextSettings(
        base_url="http://127.0.0.1:8000",
        scope_id="configured-scope",
        capture_events=capture_events,
        capture_checkpoint_every=100,
    )
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    return PowerContextPlugin(SimpleNamespace(workspace=tmp_path))


def test_workspace_binding_key_is_stable_and_opaque(tmp_path: Path) -> None:
    key = workspace_binding_key(tmp_path)
    equivalent_key = workspace_binding_key(tmp_path / ".")

    assert key is not None
    assert key == equivalent_key
    assert key.integration == "bub"
    assert key.kind == "workspace"
    assert str(tmp_path) not in key.external_id


def test_recall_and_capture_use_only_the_server_resolved_scope(monkeypatch, tmp_path: Path) -> None:
    RecordingClient.calls.clear()
    next_scope_id = "scp_10000000000000000000000000"
    RecordingClient.scope_ids = [RESOLVED_SCOPE_ID, next_scope_id]
    plugin = _plugin(monkeypatch, tmp_path, capture_events=True)
    state = plugin.load_state(message=None, session_id="session-1")
    state["session_id"] = "session-1"

    request = LlmCallRequest(
        run_id="run-1",
        model="test-model",
        messages=[{"role": "user", "content": "What did we decide?"}],
    )
    result = asyncio.run(plugin.before_llm_call(request, state))

    assert result is not None
    assert {call.scope_id for name, call in RecordingClient.calls if name in {"capture", "prepare"}} == {
        RESOLVED_SCOPE_ID
    }
    resolve_requests = [call for name, call in RecordingClient.calls if name == "resolve"]
    assert len(resolve_requests) == 1
    assert resolve_requests[0].explicit_scope_id == "configured-scope"
    assert resolve_requests[0].binding_keys == state[STATE_KEY]["binding_keys"]
    assert state[STATE_KEY]["scope_id"] == RESOLVED_SCOPE_ID

    asyncio.run(plugin.before_llm_call(request, state))

    next_state = plugin.load_state(message=None, session_id="session-2")
    next_state["session_id"] = "session-2"
    asyncio.run(plugin.before_llm_call(request, next_state))
    assert {call.scope_id for name, call in RecordingClient.calls[-2:] if name in {"capture", "prepare"}} == {
        next_scope_id
    }


def test_tool_resolves_and_caches_the_real_scope_before_search(monkeypatch, tmp_path: Path) -> None:
    RecordingClient.calls.clear()
    RecordingClient.scope_ids = [RESOLVED_SCOPE_ID]
    plugin = _plugin(monkeypatch, tmp_path)
    state = plugin.load_state(message=None, session_id="session-1")
    context = SimpleNamespace(state=state)

    result = asyncio.run(tools_module.search_memory.run(query="database", context=context))

    assert result == "(no matching PowerContext memory)"
    resolve_request = RecordingClient.calls[0][1]
    assert resolve_request.explicit_scope_id == "configured-scope"
    assert resolve_request.binding_keys == state[STATE_KEY]["binding_keys"]
    assert RecordingClient.calls[1][1].scope_id == RESOLVED_SCOPE_ID
