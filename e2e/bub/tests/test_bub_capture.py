from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bub.hooks.interception import LlmCallRequest, LlmCallResult, ToolCall, ToolCallResult
from powercontext_bub import plugin as plugin_module
from powercontext_bub.plugin import PowerContextPlugin, PowerContextSettings


def test_tool_capture_redacts_credentials_before_crossing_the_client_boundary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sensitive_value = "provider-secret-sentinel"
    captured_requests: list[Any] = []

    class RecordingClient:
        def __init__(self, base_url: str, *, timeout: float) -> None:
            del base_url, timeout

        async def __aenter__(self) -> RecordingClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            del exc_info

        async def capture_content_source(self, request: Any) -> SimpleNamespace:
            captured_requests.append(request)
            return SimpleNamespace(position=1)

    settings = PowerContextSettings(
        base_url="http://127.0.0.1:8000",
        scope_id="test:scope",
        capture_events=True,
        capture_checkpoint_every=100,
    )
    monkeypatch.setenv("BUB_API_KEY", sensitive_value)
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    plugin = PowerContextPlugin(SimpleNamespace(workspace=tmp_path))
    state = plugin.load_state(message=None, session_id="session-1")
    state["session_id"] = "session-1"

    asyncio.run(
        plugin.after_tool_call(
            ToolCall(run_id="run-1", tool="provider.request", arguments={"api_key": sensitive_value}),
            ToolCallResult(
                run_id="run-1",
                tool="provider.request",
                arguments={"api_key": sensitive_value},
                result=f"response contained {sensitive_value}",
            ),
            state,
        )
    )

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.metadata["event"] == "tool_result"
    assert sensitive_value not in request.content
    assert "[REDACTED]" in request.content


def test_llm_capture_records_bub_usage(monkeypatch, tmp_path: Path) -> None:
    captured_requests: list[Any] = []

    class RecordingClient:
        def __init__(self, base_url: str, *, timeout: float) -> None:
            del base_url, timeout

        async def __aenter__(self) -> RecordingClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            del exc_info

        async def capture_content_source(self, request: Any) -> SimpleNamespace:
            captured_requests.append(request)
            return SimpleNamespace(position=1)

    capture_log = tmp_path / "capture.jsonl"
    settings = PowerContextSettings(
        base_url="http://127.0.0.1:8000",
        scope_id="test:scope",
        capture_events=True,
        capture_checkpoint_every=100,
        capture_log=capture_log,
    )
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    plugin = PowerContextPlugin(SimpleNamespace(workspace=tmp_path))
    state = plugin.load_state(message=None, session_id="session-1")
    state["session_id"] = "session-1"
    usage = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}

    asyncio.run(
        plugin.after_llm_call(
            LlmCallRequest(run_id="run-1", model="test:model", messages=[]),
            LlmCallResult(run_id="run-1", text="done", usage=usage),
            state,
        )
    )

    assert len(captured_requests) == 1
    record = json.loads(capture_log.read_text(encoding="utf-8"))
    assert record["event"] == "llm_result"
    assert record["usage"] == usage


def test_continue_receives_recovered_task_context(monkeypatch, tmp_path: Path) -> None:
    settings = PowerContextSettings(
        base_url="http://127.0.0.1:8000",
        scope_id="test:scope",
    )
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    plugin = PowerContextPlugin(SimpleNamespace(workspace=tmp_path))
    state = plugin.load_state(message=None, session_id="session-2")

    async def prepare_context(query: str, state: Any) -> str:
        del state
        return "Recovered task state." if query != "continue" else ""

    monkeypatch.setattr(plugin, "_prepare_context", prepare_context)
    request = LlmCallRequest(
        run_id="run-2",
        model="test:model",
        messages=[{"role": "user", "content": "continue"}],
    )

    prepared = asyncio.run(plugin.before_llm_call(request, state))

    assert prepared is not None
    assert prepared.messages[0]["role"] == "system"
    assert "Recovered task state." in prepared.messages[0]["content"]
