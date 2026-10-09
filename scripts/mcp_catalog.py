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

"""Inspect the Server's public MCP tool catalog without executing domain operations."""

from __future__ import annotations

import asyncio
from functools import lru_cache

from fastmcp import Client
from mcp.types import Tool

from powercontext.server.app import create_app
from powercontext.server.mcp import create_mcp_server


@lru_cache(maxsize=1)
def server_mcp_tools() -> tuple[Tool, ...]:
    """List the complete catalog, including opt-in Handoff reporting tools."""

    async def inspect() -> tuple[Tool, ...]:
        async with Client(create_mcp_server(create_app(handoff_report_enabled=True))) as client:
            return tuple(await client.list_tools())

    return asyncio.run(inspect())
