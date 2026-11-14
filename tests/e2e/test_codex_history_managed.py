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

"""A rejected history item must not terminate the independently deployed Runtime."""

from __future__ import annotations

import asyncio
import json
import shutil
from hashlib import sha256

import yaml
from fastmcp import Client

from powercontext.client import PowerContextClient
from powercontext.http import CaptureContentSourceRequest
from tests.e2e.source_collect_helpers import (
    bind_server,
    cli,
    grant_scope,
    instance_state,
    prepare_environment,
    stop_services,
)
from tests.e2e.test_codex_history_source import write_session
from tests.e2e.test_codex_memory_managed import wait_sources
from tests.source_helpers import ROOT, TOKENS, call, create_scope, grant, requires_linux, running_server


def append_prompt(path, turn, content):
    with path.open("a") as stream:
        stream.write(
            json.dumps({
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": content, "turn_id": turn},
            })
            + "\n"
        )


@requires_linux
def test_live_capture_conflict_keeps_managed_history_polling(tmp_path):
    asyncio.run(_managed(tmp_path))


async def _managed(directory):
    envfile, state = prepare_environment(directory)
    plugins = directory / "source-plugins"
    plugins.mkdir()
    home = directory / "codex"
    credential = "synthetic-managed-history-secret"
    raw = f"credential {credential}"
    path = write_session(home, content=raw)
    (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": credential}))
    append_prompt(path, "turn-b", "later ordinary prompt")
    plugin = directory / "history-plugin"
    shutil.copytree(ROOT / "integrations/codex/source_plugin", plugin, ignore=shutil.ignore_patterns("__pycache__"))
    environment = {
        "POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR": str(plugins),
        "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": json.dumps({"state_dir": str(state)}),
    }
    async with running_server(directory, environment=environment) as (admin, url, _):
        bind_server(envfile, url)
        scope = await create_scope(admin, "managed-conflicting-history")
        await grant_scope(admin, scope)
        await grant(admin, scope)
        await grant(admin, scope, "deployer")
        source_id = "codex-user-prompt:" + sha256("\0".join((scope, "session-a", "turn-a", raw)).encode()).hexdigest()
        async with PowerContextClient(url, token=TOKENS["deployer"], allow_insecure_http=True) as client:
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope,
                    source_id=source_id,
                    content=raw,
                    metadata={
                        "origin": "codex",
                        "event": "user_prompt_submit",
                        "cwd": "/project",
                        "session_id": "session-a",
                        "turn_id": "turn-a",
                    },
                )
            )
            manifest = plugin / "powercontext.yml"
            manifest.write_text(
                yaml.safe_dump({
                    "id": "codex-history",
                    "plugin": ".",
                    "scope_ids": [scope],
                    "config": {"codex_home": str(home), "poll_interval": 0.2},
                })
            )
            try:
                await cli(directory, "source", "load", "--config", str(manifest), "--env-file", str(envfile))
                await instance_state(admin, "codex-history", "running")
                async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
                    await call(
                        host,
                        "pc_source_add",
                        {
                            "scope_id": scope,
                            "instance_id": "codex-history",
                            "capability": "codex.session-history",
                            "capability_version": "1",
                            "input": {"session_file": "rollout.jsonl"},
                        },
                    )
                    await wait_sources(admin, scope, 2)
                    # This prompt appears only after the first conflicting batch has finished.
                    append_prompt(path, "turn-c", "captured by a later poll")
                    await wait_sources(admin, scope, 3)
                    current = await instance_state(admin, "codex-history")
                    assert current["actual"] == "running"
                    assert (await client.get_source(scope, "content", source_id)).content == raw
            finally:
                await stop_services(directory, state)
