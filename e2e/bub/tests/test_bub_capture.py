from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bub.hooks.interception import ToolCall, ToolCallResult
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
