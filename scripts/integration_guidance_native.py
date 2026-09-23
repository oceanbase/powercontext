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

"""Native adapter execution with controlled client replies; never contact a Server."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from integration_guidance_handoff import HandoffFixture

NATIVE_HOSTS = {"dsh", "pi", "opencode", "openclaw"}
ROOT = Path(__file__).resolve().parent.parent


class NativeHandoffSession:
    def __init__(self, host: str) -> None:
        self.host = host
        self.process: asyncio.subprocess.Process | None = None
        self.requests: list[dict[str, Any]] = []

    async def _receive(self) -> dict[str, Any]:
        if self.process is None or self.process.stdout is None:
            message = "Native adapter stdout is unavailable"
            raise RuntimeError(message)
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=60)
        if not line:
            message = f"{self.host} evaluation adapter exited before returning a result"
            raise RuntimeError(message)
        return json.loads(line)

    async def _send(self, value: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            message = "Native adapter stdin is unavailable"
            raise RuntimeError(message)
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, name: str, arguments: dict[str, Any], fixture: HandoffFixture) -> dict[str, Any]:
        if self.process is None:
            node = os.environ.get("POWERCONTEXT_GUIDANCE_NODE") or shutil.which("node")
            if node is None:
                message = "Native guidance evaluation requires Node.js 22.19+ and installed host dependencies"
                raise RuntimeError(message)
            self.process = await asyncio.create_subprocess_exec(
                node,
                "--experimental-transform-types",
                str(Path(__file__).with_suffix(".mjs")),
                self.host,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # Leave startup diagnostics visible; never mix them into model tool replies.
                cwd=ROOT,
            )
            if (await self._receive()).get("kind") != "ready":
                message = f"{self.host} evaluation adapter did not initialize"
                raise RuntimeError(message)
        await self._send({"name": name, "arguments": arguments})
        requests_before = len(self.requests)
        while True:
            message = await self._receive()
            if message["kind"] == "request":
                self.requests.append(message)
                result = fixture.respond(message["operation"], message["payload"])
                await self._send({"kind": "response", "value": result})
            elif message["kind"] == "result":
                failed = (
                    message["value"].get("unavailable") if self.host == "openclaw" else not message["value"].get("ok")
                )
                if len(self.requests) != requests_before + 1 or failed:
                    message = f"{self.host} {name} did not complete one controlled operation: {message['value']}"
                    raise ValueError(message)
                return message["value"]
            else:
                message = f"{self.host} {name}: {message.get('message', 'invalid adapter reply')}"
                raise RuntimeError(message)

    async def close(self) -> None:
        if self.process is not None:
            if self.process.returncode is None:
                self.process.kill()
            await self.process.wait()
