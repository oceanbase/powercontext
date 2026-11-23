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

"""Bub's automatic recall and capture keep recognized credentials out, like the other automatic adapters."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx2
import pytest
from bub.channels.message import ChannelMessage
from bub.framework import BubFramework
from powercontext_bub import plugin as plugin_module
from powercontext_bub.plugin import PowerContextPlugin, PowerContextSettings
from pydantic import SecretStr

_ROOT = Path(__file__).resolve().parents[3]
CASES = json.loads((_ROOT / "tests/fixtures/automatic_prompt_privacy.json").read_text(encoding="utf-8"))["cases"]
# Every synthetic credential value in the fixture contains one of these markers.
VALUE_MARKERS = ("synthetic-value-2084", "synthetic_value_2084", "SyntheticValue2084")
ANSWER = "Noted."


class RecordingClient:
    prepared: ClassVar[list[Any]] = []
    captured: ClassVar[list[Any]] = []

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

    async def capture_content_source(self, request: Any) -> SimpleNamespace:
        self.captured.append(request)
        return SimpleNamespace(position=len(self.captured))

    async def flush_memory(self, request: Any) -> SimpleNamespace:
        del request
        position = len(self.captured)
        return SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            previous_cursor=0,
            current_cursor=position,
            high_watermark=position,
            processed_source_count=position,
            remaining_work=False,
        )


def _token(case: dict[str, Any]) -> SecretStr | None:
    authorization = case.get("authorization")
    return None if authorization is None else SecretStr(str(authorization).removeprefix("Bearer "))


def _run_turn(monkeypatch, tmp_path: Path, prompt: str, *, token: SecretStr | None) -> list[dict[str, Any]]:
    """Run one message through Bub's own turn with a stub model and return the capture log's records."""
    RecordingClient.prepared.clear()
    RecordingClient.captured.clear()
    capture_log = tmp_path / "capture.jsonl"
    settings = PowerContextSettings(
        base_url="http://127.0.0.1:8000",
        api_token=token,
        capture_events=True,
        capture_checkpoint_every=100,
        capture_log=capture_log,
    )
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)

    def model_reply(request: httpx2.Request) -> httpx2.Response:
        del request
        events = [{"choices": [{"delta": {"content": ANSWER}, "finish_reason": "stop"}]}, "[DONE]"]
        body = "".join(f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n" for event in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **options: client(**{"transport": httpx2.MockTransport(model_reply), **options})
    )
    for name, value in {
        "BUB_HOME": str(tmp_path / "home"),
        "BUB_MODEL": "openai:test-model",
        "BUB_API_KEY": "stub-model-credential",
        "BUB_CLIENT_ARGS": '{"api_format": "chat"}',
        # Keep the known-credential redaction of captured events to this test's own environment.
        "CODEX_HOME": str(tmp_path / "codex"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    framework = BubFramework(config_file=tmp_path / "bub.yml")
    framework.load_builtin_hooks()
    framework.plugin_manager.register(PowerContextPlugin(framework), name="powercontext")
    message = ChannelMessage(
        session_id="cli:local", channel="cli", chat_id="local", content=prompt, context={"sender_id": "human"}
    )

    async def turn() -> None:
        async with framework.running():
            await framework.process_inbound(message)

    asyncio.run(turn())
    return [json.loads(line) for line in capture_log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_turn_keeps_recognized_credentials_out_of_recall_and_capture(
    monkeypatch, tmp_path: Path, case: dict[str, Any]
) -> None:
    prompt = str(case["prompt"])

    records = _run_turn(monkeypatch, tmp_path, prompt, token=_token(case))

    # Recall runs either way.
    assert len(RecordingClient.prepared) == 1
    events = [captured.metadata["event"] for captured in RecordingClient.captured]
    sent = json.dumps([RecordingClient.prepared[0].query, *(captured.content for captured in RecordingClient.captured)])
    log = json.dumps(records)
    if case["sensitive"]:
        for marker in VALUE_MARKERS:
            assert marker not in sent
            assert marker not in log
        # The initial task is not stored as a redacted copy; the model's reply is still captured.
        assert events == ["llm_result"]
        skip = {"event": "user_prompt", "status": "skipped", "reason": "sensitive_content"}
        assert skip.items() <= records[0].items()
    else:
        assert RecordingClient.prepared[0].query == prompt.strip()
        assert events == ["user_prompt", "llm_result"]
        assert json.dumps(prompt)[1:-1] in RecordingClient.captured[0].content
