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

"""Execute notebook-supplied calls through a real MCP HTTP session in another process."""

from __future__ import annotations

import asyncio
import json
import sys

from fastmcp import Client


async def main() -> None:
    request = json.load(sys.stdin)
    async with Client(request["url"]) as client:
        available = [tool.name for tool in await client.list_tools()]
        results = []
        for operation in request["calls"]:
            result = await client.call_tool(operation["name"], operation["arguments"])
            if result.is_error:
                raise RuntimeError("MCP operation failed")  # noqa: TRY003
            results.append({"name": operation["name"], "result": result.structured_content})
    print(json.dumps({"tools": available, "calls": results}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
