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

"""Standalone Cindy archive with upstream gateway source and real TCP Server.

The credential/message bridge and unused Host storage imports are simulated.
Opt-in native acceptance runs the upstream Codex host and a live model, without Electron.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.client import PowerContextClient
from powercontext.http import ListSourcesRequest
from tests.e2e.test_cindy_mcp_chain import cindy_provider as cindy_provider_fixture  # noqa: F401 - named fixture.
from tests.e2e.test_cindy_mcp_chain import cindy_server as cindy_server_fixture  # noqa: F401 - named fixture.

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("native_codex", [False, True], ids=["gateway", "native-codex"])
def test_standalone_cindy_hooks_recall_capture_and_isolate_sessions(
    tmp_path: Path, cindy_provider: Path, cindy_server: tuple[str, str], native_codex: bool
) -> None:
    url, token = cindy_server
    source = cindy_provider.parents[5]
    native_options: dict[str, str] = {}
    if native_codex:
        binary = os.environ.get("POWERCONTEXT_CINDY_CODEX_BINARY")
        if not binary:
            pytest.skip(
                "set POWERCONTEXT_CINDY_CODEX_BINARY to an authenticated Codex binary for live model acceptance"
            )
        runtime = source / "packages/maker-core/src/agents/codex/app-server"
        entry = tmp_path / "native-runtime.ts"
        entry.write_text(
            f"export {{ AppServerHost }} from {json.dumps(str(runtime / 'host.ts'))};\n"
            f"export {{ createStdioTransport }} from {json.dumps(str(runtime / 'stdioTransport.ts'))};\n"
        )
        build_config = tmp_path / "native-build.mjs"
        build_config.write_text(
            "import { readFileSync } from 'node:fs';\n"
            "import { dirname, resolve } from 'node:path';\n"
            "export default {\n"
            f"entry: [{json.dumps(str(entry))}], format: ['esm'], dts: false,\n"
            f"outDir: {json.dumps(str(tmp_path / 'runtime'))},\n"
            "plugins: [{ name: 'upstream-raw-prompts',\n"
            "resolveId(id, importer) { if (id.endsWith('?raw')) return resolve(dirname(importer), id); },\n"
            "load(id) { if (id.endsWith('?raw')) return 'export default ' + JSON.stringify(readFileSync(id.slice(0, -4), 'utf8')); }\n"
            "}] };\n"
        )
        pnpm = shutil.which("pnpm")
        assert pnpm is not None, "native Cindy runtime acceptance requires pnpm"
        subprocess.run(
            [
                pnpm,
                "--dir",
                str(ROOT / "integrations/dsh/plugins/powercontext"),
                "exec",
                "tsdown",
                "--config",
                str(build_config),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        native_options = {"nativeRuntime": str(tmp_path / "runtime/native-runtime.mjs"), "codexBinary": binary}
        if model := os.environ.get("POWERCONTEXT_CINDY_CODEX_MODEL"):
            native_options["model"] = model
    subprocess.run(
        [sys.executable, str(ROOT / "integrations/cindy/scripts/package.py")], check=True, capture_output=True
    )
    plugin = tmp_path / "installed"
    with ZipFile(ROOT / "dist/powercontext-cindy-hooks-0.1.0.cindy") as archive:
        archive.extractall(plugin)

    async def scenario() -> None:
        scopes = []
        async with Client(
            StreamableHttpTransport(url + "/mcp", headers={"Authorization": f"Bearer {token}"})
        ) as client:
            for session, codename in (("session-a", "QUARTZ-8413"), ("session-b", "QUARTZ-9021")):
                result = await client.call_tool(
                    "create_scope", {"title": session, "summary": "Cindy hook acceptance", "idempotency_key": session}
                )
                scope_id = result.structured_content["scope_id"]
                scopes.append(scope_id)
                await client.call_tool(
                    "set_scope_binding",
                    {"key": {"integration": "cindy", "kind": "session", "external_id": session}, "scope_id": scope_id},
                )
                await client.call_tool(
                    "remember_memory",
                    {
                        "scope_id": scope_id,
                        "kind": "fact",
                        "text": f"The Quartz application deployment codename is {codename}.",
                    },
                )
        node = shutil.which("node")
        assert node is not None
        result = await asyncio.to_thread(
            subprocess.run,
            [node, str(ROOT / "tests/e2e/cindy_hook_host.mjs"), str(source), str(plugin)],
            cwd=plugin,
            input=json.dumps({
                "url": url,
                "token": token,
                "prompt": "What is the Quartz application deployment codename?",
                **native_options,
            }),
            capture_output=True,
            text=True,
            timeout=240 if native_codex else 20,
            check=True,
        )
        verdicts = json.loads(result.stdout)
        assert verdicts["first"]["action"] == "rewrite"
        assert "QUARTZ-8413" in verdicts["first"]["text"]
        assert "QUARTZ-9021" not in verdicts["first"]["text"]
        assert verdicts["second"]["action"] == "rewrite"
        assert "QUARTZ-9021" in verdicts["second"]["text"]
        assert "QUARTZ-8413" not in verdicts["second"]["text"]
        assert verdicts["missing"]["action"] == "allow"
        assert verdicts["edited"]["action"] == "allow"
        assert verdicts["read"]["ok"] is True
        assert verdicts["read"]["result"]["scope_id"] == scopes[1]
        assert "QUARTZ-9021" in verdicts["read"]["result"]["result"]["content"]
        assert "QUARTZ-8413" not in verdicts["read"]["result"]["result"]["content"]
        assert verdicts["search"]["ok"] is True
        assert verdicts["search"]["result"]["scope_id"] == scopes[1]
        assert "QUARTZ-9021" in verdicts["search"]["result"]["result"]["hits"][0]["memory"]["text"]
        assert verdicts["unboundRead"]["ok"] is False
        if native_codex:
            assert len(verdicts["native"]) == 2
            for receipt, codename, other in zip(
                verdicts["native"], ("QUARTZ-8413", "QUARTZ-9021"), ("QUARTZ-9021", "QUARTZ-8413"), strict=True
            ):
                assert codename in receipt["answer"]
                assert other not in receipt["answer"]
            assert verdicts["native"][1]["tool_calls"] >= 1
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http_client:
            client = PowerContextClient(url, http_client=http_client)
            sources = []
            for scope_id in scopes:
                listed = await client.list_sources(scope_id, ListSourcesRequest(limit=100))
                captured = [item for item in listed.items if item.source_id.startswith("cindy-user-prompt:")]
                assert len(captured) == 1
                record = captured[0]
                source_record = await client.get_source(scope_id, "content", record.source_id)
                assert source_record.content == "What is the Quartz application deployment codename?"
                assert "powercontext:cindy-context" not in source_record.content
                sources.append({
                    "scope_id": scope_id,
                    "source_id": record.source_id,
                    "position": record.position,
                    "original_prompt_verified": True,
                    "recalled_history_captured": False,
                })
        (tmp_path / "cindy-acceptance.json").write_text(
            json.dumps(
                {
                    "cindy_version": "0.1.115",
                    "mode": "native-codex" if native_codex else "gateway",
                    "electron_verified": False,
                    "credential_bridge": "simulated",
                    "codex_receipts": verdicts.get("native"),
                    "captured_sources": sources,
                },
                indent=2,
            )
            + "\n"
        )

    asyncio.run(scenario())
