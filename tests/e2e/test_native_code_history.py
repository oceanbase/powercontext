# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Persisted historical evidence and current code share HTTP preparation budgets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess

import httpx
import pytest

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.code import CodeConfig, CodeRepositoryConfig, CodeService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    ProposeExperienceRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


def test_http_code_prepare_preserves_approved_experience_when_memory_is_empty(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    files = {f"budget_{number}.py": f"def budget(amount):\n    return amount + {number}\n" for number in range(3)}
    for path, content in files.items():
        (root / path).write_text(content, encoding="utf-8")
    executable = shutil.which("git")
    assert executable
    for arguments in (("init",), ("add", ".")):
        subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)

    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    lesson = "Budget preparation must preserve approved historical evidence."

    async def exercise():
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "runtime-scheduler.db"
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Budget history", summary="Mixed context evidence", idempotency_key="code-history")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="budget-evidence",
                    content="Verified budget preparation retained the approved lesson.",
                    metadata={},
                )
            )
            candidate = await runtime.experience.for_scope(scope.scope_id).propose(
                ProposeExperienceRequest(
                    proposal=ExperienceContent(
                        situation="Budget preparation has both source code and historical evidence.",
                        action="Preserve the approved budget lesson while selecting source excerpts.",
                        outcome="Budget context contains current code and the earlier lesson.",
                        lesson=lesson,
                    ),
                    sources=(source.source_ref,),
                )
            )
            approved = await runtime.review.for_scope(scope.scope_id).approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            memory = await runtime.memory.for_scope(scope.scope_id).search(SearchMemoryRequest(query="budget"))
            assert memory.hits == ()

        code = CodeConfig(
            enabled=True, cache_dir=tmp_path / "code", repositories={scope.scope_id: CodeRepositoryConfig(root=root)}
        )
        await asyncio.to_thread(CodeService(code).index, scope.scope_id)
        app = create_server_app(
            settings=ServerSettings(
                database=database, code=code, mcp=McpConfig(enabled=False), metrics=MetricsConfig(enabled=False)
            ),
            scheduler_path=tmp_path / "http-scheduler.db",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
        ):
            artifact_id = approved.result_artifact.artifact_id
            restored = await http.get(f"/v1/scopes/{scope.scope_id}/artifacts/experience/{artifact_id}")
            restored.raise_for_status()
            assert restored.json()["content"]["lesson"] == lesson
            request = {"scope_id": scope.scope_id, "query": "budget", "assembly": {}, "max_bytes": 32768}
            for include_code in (False, True):
                response = await http.post("/v1/context/prepare", json={**request, "include_code": include_code})
                response.raise_for_status()
                result = response.json()
                assert result["status"] == "ready"
                content = result["content"]
                assert "## Experience" in content and lesson in content
                assert artifact_id in content
                assert "## Memory" not in content
                assert result["content_bytes"] == len(content.encode("utf-8")) <= request["max_bytes"]
                if not include_code:
                    assert "BEGIN_POWERCONTEXT_CODE_V1" not in content
                    continue
                block = content.split("BEGIN_POWERCONTEXT_CODE_V1", 1)[1].split("END_POWERCONTEXT_CODE_V1", 1)[0]
                citations = [json.loads(line) for line in block.splitlines() if line.lstrip().startswith("{")]
                assert 2 <= len(citations) <= 4
                assert len(citations) + content.count('Artifact: family="experience"') <= 8
                for citation in citations:
                    source_bytes = files[citation["path"]].encode("utf-8")
                    excerpt = b"".join(
                        source_bytes.splitlines(keepends=True)[citation["start_line"] - 1 : citation["end_line"]]
                    )
                    assert citation["scope_id"] == scope.scope_id
                    assert citation["file_sha256"] == hashlib.sha256(source_bytes).hexdigest()
                    assert citation["snippet_sha256"] == hashlib.sha256(excerpt).hexdigest()

    asyncio.run(exercise())
