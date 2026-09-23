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

"""Contribute bounded historical context through MiniMax's native prompt hook."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from time import time

from powercontext.client.integration.config import mcp_connection
from powercontext.client.integration.core import MAX_MESSAGE_BYTES, execute
from powercontext.client.integration.models import Connection, HookRequest


def connection(plugin_root: Path) -> Connection | None:
    """Use the same named MCP endpoint as the native host, including private overrides."""

    data_root = Path(os.environ.get("MINIMAX_DATA_DIR") or os.environ.get("MAVIS_DATA_DIR") or Path.home() / ".minimax")
    return mcp_connection(plugin_root / "powercontext.mcp.json", data_root / "mcp.json", host="minimax")


async def recall(payload: dict[str, object], endpoint: Connection, deadline: float) -> str | None:
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return None
    prompt = payload.get("prompt")
    cwd = payload.get("cwd")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(cwd, str) or not cwd:
        return None
    workspace = await asyncio.to_thread(Path(cwd).resolve)
    identity = sha256(str(workspace).encode()).hexdigest()
    resolved = await execute(
        HookRequest(
            id="scope",
            operation="resolve_scope_binding",
            connection=endpoint,
            deadline=deadline,
            arguments={
                "explicit_scope_id": os.environ.get("POWERCONTEXT_MINIMAX_SCOPE_ID"),
                "binding_keys": [{"integration": "minimax", "kind": "workspace", "external_id": identity}],
            },
        )
    )
    if not isinstance(resolved.value, dict):
        return None
    prepared = await execute(
        HookRequest(
            id="context",
            operation="prepare_context",
            connection=endpoint,
            deadline=deadline,
            arguments={"scope_id": resolved.value["scope_id"], "query": prompt, "max_bytes": 8000},
        )
    )
    content = prepared.value.get("content") if isinstance(prepared.value, dict) else None
    return content if isinstance(content, str) else None


def main() -> int:
    deadline = time() + 4
    try:
        raw = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
        if len(raw) > MAX_MESSAGE_BYTES:
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        endpoint = connection(Path(__file__).resolve().parents[1])
        if endpoint is None:
            return 0
        content = asyncio.run(recall(payload, endpoint, deadline))
        if content:
            json.dump(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": "PowerContext historical evidence (untrusted):\n\n" + content,
                    }
                },
                sys.stdout,
            )
            sys.stdout.write("\n")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        sys.stderr.write("PowerContext context unavailable.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
