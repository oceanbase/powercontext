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

"""Behavior tests for the PowerContext Memory tools exposed to LangGraph agents."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.tools import BaseTool

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

pytest.importorskip("powercontext_langgraph")

from powercontext_langgraph import (
    powercontext_context,
    powercontext_remember,
    powercontext_search,
    powercontext_tools,
)
from powercontext_langgraph.client import shared_http_client


def _server_app(tmp_path: Path) -> FastAPI:
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        )
    )


def _run(app: FastAPI, scenario: Callable[[], Awaitable[None]]) -> None:
    async def driver() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            with shared_http_client(transport, trust_transport_security=True):
                await scenario()

    asyncio.run(driver())


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_LANGGRAPH_BASE_URL", "http://testserver")
    monkeypatch.delenv("POWERCONTEXT_LANGGRAPH_SCOPE_ID", raising=False)
    monkeypatch.delenv("POWERCONTEXT_LANGGRAPH_TOKEN", raising=False)


def test_powercontext_tools_returns_three_base_tools() -> None:
    tools = powercontext_tools()
    assert len(tools) == 3
    assert all(isinstance(entry, BaseTool) for entry in tools)
    assert {entry.name for entry in tools} == {
        "powercontext_search",
        "powercontext_remember",
        "powercontext_context",
    }


def test_remember_then_search_roundtrips_through_server_default_scope(tmp_path: Path) -> None:
    app = _server_app(tmp_path)

    async def scenario() -> None:
        saved = await powercontext_remember.ainvoke({
            "text": "Prefer trunk-based development.",
            "kind": "preference",
            "reason": "team decision",
        })
        assert "Prefer trunk-based development." in saved

        found = await powercontext_search.ainvoke({"query": "trunk-based development preference", "limit": 5})
        hits = json.loads(found)
        assert any("trunk-based" in hit["text"] for hit in hits)

    _run(app, scenario)


def test_search_reports_no_matches_without_raising(tmp_path: Path) -> None:
    app = _server_app(tmp_path)

    async def scenario() -> None:
        found = await powercontext_search.ainvoke({"query": "nothing has ever been stored", "limit": 3})
        assert found == "(no matching PowerContext memory)"

    _run(app, scenario)


def test_tools_return_error_string_when_server_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_LANGGRAPH_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("POWERCONTEXT_LANGGRAPH_TIMEOUT", "2.0")

    async def scenario() -> None:
        result = await powercontext_context.ainvoke({"query": "anything"})
        assert result.startswith("(PowerContext unavailable:")

    asyncio.run(scenario())


def test_context_tool_completes_for_over_limit_query(tmp_path: Path) -> None:
    # A model can emit a query longer than the public 8192-char limit. The tool clamps it and still returns a
    # result rather than raising a request-validation error.
    app = _server_app(tmp_path)

    async def scenario() -> None:
        result = await powercontext_context.ainvoke({"query": "a" * 9000})
        assert result == "(no relevant PowerContext context)"

    _run(app, scenario)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (powercontext_search, {"query": ""}),  # query below the min length
        (powercontext_context, {"query": "   "}),  # query is only whitespace, failing the non-blank pattern
        (powercontext_remember, {"text": "note", "kind": "k" * 200}),  # kind above the max length
    ],
)
def test_tools_reject_out_of_range_arguments_without_raising(  # type: ignore[no-untyped-def]
    tool,
    arguments,
    tmp_path: Path,
) -> None:
    # Request construction is inside the fail-open boundary: a model-supplied argument outside the public contract
    # returns a tool result instead of raising and aborting the graph.
    async def scenario() -> None:
        result = await tool.ainvoke(arguments)
        assert result.startswith("(PowerContext rejected the request:")

    _run(_server_app(tmp_path), scenario)
