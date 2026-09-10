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

"""Bub's standalone entry point supports cores with and without text assembly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_COMPATIBILITY_SCRIPT = """
import asyncio
import importlib.metadata
import json
import os
import sys
from types import SimpleNamespace

import httpx
from pydantic import ValidationError
import powercontext.http as http

sys.path.insert(0, sys.argv[2])
assembly_available = sys.argv[1] == "true"
if not assembly_available and hasattr(http, "ContextAssembly"):
    del http.ContextAssembly
for key in list(os.environ):
    if key.startswith("POWERCONTEXT_BUB_"):
        del os.environ[key]

entry = next(e for e in importlib.metadata.entry_points(group="bub") if e.name == "powercontext")
plugin_type = entry.load()
from bub.hooks.interception import LlmCallRequest
from powercontext.client import PowerContextClient
from powercontext_bub import plugin, tools

assert plugin.PowerContextSettings().context_assembly is None
assert plugin.PowerContextSettings(context_assembly=None).context_assembly is None

payloads = []
def respond(request):
    assert request.url.path == "/v1/context/prepare"
    payloads.append(json.loads(request.content))
    return httpx.Response(200, json={
        "schema": "powercontext.prepared-context.v1", "status": "ready",
        "content": "context", "content_bytes": 7,
    })

async def exercise(settings):
    plugin.ensure_config = lambda _: settings
    instance = plugin_type(SimpleNamespace(workspace=None))
    state = instance.load_state(message=None, session_id="compatibility")
    state[plugin.STATE_KEY]["scope_id"] = "test"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        def open_client(base_url, **kwargs):
            return PowerContextClient(base_url, http_client=transport, trust_transport_security=True)
        plugin.open_client = tools.open_client = open_client
        result = await instance.before_llm_call(
            LlmCallRequest(run_id="compatibility", model="test", messages=[{"role": "user", "content": "test"}]),
            state,
        )
        assert result is not None and result.messages[0]["content"].endswith("context")
        assert await tools.prepare_context.run(query="test", context=SimpleNamespace(state=state)) == "context"

for value in [None, {}, {"sections": []}, {"sections": [{"family": "memory", "limit": 3}]}]:
    for source in ["argument", "environment"]:
        os.environ.pop("POWERCONTEXT_BUB_CONTEXT_ASSEMBLY", None)
        kwargs = {"context_assembly": value}
        if source == "environment":
            os.environ["POWERCONTEXT_BUB_CONTEXT_ASSEMBLY"] = "" if value is None else json.dumps(value)
            kwargs = {}
        if not assembly_available and value is not None:
            try:
                plugin.PowerContextSettings(**kwargs)
            except ValidationError as error:
                assert "context_assembly requires a PowerContext core with text assembly support" in str(error)
                assert "install the core and adapter from the same checkout" in str(error)
            else:
                raise AssertionError("Unsupported assembly must produce an upgrade message")
            continue
        settings = plugin.PowerContextSettings(**kwargs)
        payloads.clear()
        asyncio.run(exercise(settings))
        assert len(payloads) == 2
        for payload in payloads:
            assert payload["max_bytes"] == 8000
            if value is None:
                assert "assembly" not in payload
            else:
                assert payload["assembly"] == settings.context_assembly.model_dump(mode="json")
"""


@pytest.mark.parametrize("assembly_available", [False, True])
def test_bub_entry_point_and_prepare_with_optional_core_assembly_api(assembly_available):
    result = subprocess.run(  # noqa: S603 - fixed script and interpreter; no user-provided command.
        [
            sys.executable,
            "-I",
            "-c",
            _COMPATIBILITY_SCRIPT,
            str(assembly_available).lower(),
            str(_ROOT / "integrations/bub/src"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
