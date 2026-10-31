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

"""Bub recalls with the end of a long prompt, as the hook-based hosts do."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from bub.hooks.interception import LlmCallRequest
from powercontext_bub import plugin as plugin_module
from powercontext_bub.plugin import PowerContextPlugin, PowerContextSettings

_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((_ROOT / "tests/fixtures/recall_query_bound.json").read_text(encoding="utf-8"))


def _expand(segments: list[dict[str, Any]]) -> str:
    return "".join(segment["text"] * segment.get("repeat", 1) for segment in segments)


class RecordingClient:
    prepared: ClassVar[list[Any]] = []

    def __init__(self, base_url: str, **options: object) -> None:
        del base_url, options

    async def __aenter__(self) -> RecordingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info

    async def resolve_scope_binding(self, request: Any) -> SimpleNamespace:
        del request
        return SimpleNamespace(scope_id="scp_00000000000000000000000000")

    async def prepare_context(self, request: Any) -> SimpleNamespace:
        self.prepared.append(request)
        return SimpleNamespace(content="remembered context", content_bytes=18, status=SimpleNamespace(value="ready"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["id"] for case in FIXTURE["cases"]])
def test_recall_query_keeps_the_end_of_the_prompt(monkeypatch, tmp_path: Path, case: dict[str, Any]) -> None:
    RecordingClient.prepared.clear()
    settings = PowerContextSettings(base_url="http://127.0.0.1:8000")
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    plugin = PowerContextPlugin(SimpleNamespace(workspace=tmp_path))
    state = plugin.load_state(message=None, session_id="session-1")
    request = LlmCallRequest(
        run_id="run-1",
        model="test-model",
        messages=[{"role": "user", "content": _expand(case["prompt"])}],
    )

    result = asyncio.run(plugin.before_llm_call(request, state))

    assert result is not None
    assert [prepared.query for prepared in RecordingClient.prepared] == [_expand(case["query"])]
