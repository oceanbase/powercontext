# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Multi-language evidence travels through HTTP query and PreparedContext."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import httpx
import pytest

from powercontext.builtin.code import CodeConfig, CodeRepositoryConfig, CodeService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings

for grammar in ("tree_sitter_javascript", "tree_sitter_typescript", "tree_sitter_go"):
    pytest.importorskip(grammar)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


def test_prepare_uses_each_language_and_drops_stale_code(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    files = {
        "web.tsx": "export function renderPage() { return <div>预算</div>; }\n",
        "client.js": "export const requestBudget = () => 100;\n",
        "server.go": "package server\nfunc ServeBudget() int { return 100 }\n",
    }
    for path, content in files.items():
        (root / path).write_text(content)
    executable = shutil.which("git")
    assert executable
    for arguments in (("init",), ("add", ".")):
        subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'database.db'}")

    async def exercise():
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "scope.db"
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Mixed repository", summary="Current source evidence", idempotency_key="mixed-code")
            )
        code = CodeConfig(
            enabled=True, cache_dir=tmp_path / "code", repositories={scope.scope_id: CodeRepositoryConfig(root=root)}
        )
        service = CodeService(code)
        await asyncio.to_thread(service.index, scope.scope_id)
        app = create_server_app(settings=ServerSettings(database=database, code=code))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
        ):
            status = await http.post(f"/v1/scopes/{scope.scope_id}/code/query", json={"operation": {"kind": "status"}})
            status.raise_for_status()
            assert set(status.json()["languages"]) == {"python", "javascript", "typescript", "go"}
            for path, name, language in (
                ("web.tsx", "renderPage", "typescript"),
                ("client.js", "requestBudget", "javascript"),
                ("server.go", "ServeBudget", "go"),
            ):
                request = {"scope_id": scope.scope_id, "query": name, "assembly": {"sections": []}}
                disabled = await http.post("/v1/context/prepare", json=request)
                assert disabled.json()["status"] == "empty"
                symbols = await http.post(
                    f"/v1/scopes/{scope.scope_id}/code/query", json={"operation": {"kind": "symbols", "query": name}}
                )
                symbols.raise_for_status()
                assert any(item["language"] == language for item in symbols.json()["items"])
                prepared = await http.post("/v1/context/prepare", json={**request, "include_code": True})
                prepared.raise_for_status()
                result = prepared.json()
                assert result["status"] == "ready"
                assert "BEGIN_POWERCONTEXT_CODE_V1" in result["content"]
                assert path in result["content"] and name in result["content"]
                (root / path).write_text(files[path] + "// changed\n")
                stale = await http.post("/v1/context/prepare", json={**request, "include_code": True})
                stale.raise_for_status()
                assert stale.json()["status"] == "empty"
                await asyncio.to_thread(service.sync, scope.scope_id)
                refreshed = await http.post("/v1/context/prepare", json={**request, "include_code": True})
                assert refreshed.json()["status"] == "ready"

    asyncio.run(exercise())
