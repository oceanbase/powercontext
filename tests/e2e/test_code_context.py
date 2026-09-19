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

"""Real Git/CodeGraph/SQLite journeys through Runtime and HTTP contracts."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.code.config import CodeConfig, CodeGraphConfig
from powercontext.builtin.code.service import CodeService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    InferenceConfig,
    PrepareContextRequest,
    ProposeExperienceRequest,
    RememberMemoryRequest,
    RuntimeConfig,
    StatisticsPeriod,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, ServerSettings


@pytest.mark.parametrize("recall_gate", [False, True])
def test_code_fallback_preserves_default_topic_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recall_gate: bool
) -> None:
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(name)
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topics.db'}"),
        runtime=RuntimeConfig(artifact_processing_families=(), recall_gate_enabled=recall_gate),
        inference=InferenceConfig(),
        access=AccessControlConfig(mode="disabled"),
        mcp=McpConfig(enabled=False),
    )
    with TestClient(create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")) as client:
        scope = client.post("/v1/scopes", json={"title": "Budget", "summary": "Budget", "idempotency_key": "budget"})
        assert scope.status_code == 201, scope.text
        scope_id = scope.json()["scope_id"]
        topic = client.post(
            f"/v1/scopes/{scope_id}/artifacts",
            json={
                "family": "topic-memory",
                "content": {"title": "Budget", "summary": "Budget constraint", "detail": "Preserve the byte budget."},
            },
        )
        assert topic.status_code == 201, topic.text
        request = {"scope_id": scope_id, "query": "Budget", "max_bytes": 8000}
        for include_code in (False, True):
            response = client.post("/v1/context/prepare", json={**request, "include_code": include_code})
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "ready"
            assert topic.json()["artifact_id"] in response.json()["content"]
        for assembly in ({}, {"sections": []}):
            response = client.post("/v1/context/prepare", json={**request, "include_code": True, "assembly": assembly})
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "empty"


@pytest.fixture
def configured_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ServerSettings, str, str]:
    executable = os.environ.get("POWERCONTEXT_TEST_CODEGRAPH_EXECUTABLE")
    if not executable:
        pytest.skip("Deploy CodeGraph and set POWERCONTEXT_TEST_CODEGRAPH_EXECUTABLE for code acceptance")
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(name)
    root = tmp_path / "repository"
    root.mkdir()
    (root / "budget.py").write_text("def allocate_budget(total):\n    return total // 2\n")
    (root / "test_budget.py").write_text(
        "from budget import allocate_budget\n\ndef test_budget():\n    assert allocate_budget(8) == 4\n"
    )
    git = shutil.which("git")
    assert git
    for arguments in (
        ("init", "--quiet"),
        ("config", "user.name", "Acceptance"),
        ("config", "user.email", "acceptance@example.invalid"),
        ("add", "."),
        ("commit", "--quiet", "-m", "fixture"),
    ):
        subprocess.run((git, "-C", str(root), *arguments), capture_output=True, check=True)
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    policy = RuntimeConfig(context_assembly_max_entries=4)

    async def prepare() -> tuple[CodeConfig, str, str]:
        async with open_builtin_runtime(BuiltinConfig(database=database, runtime=policy)) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Code", summary="Code fixture", idempotency_key="code")
            )
            other = await runtime.scopes.create(
                ScopeDraft(
                    title="Other",
                    summary="Reference only",
                    idempotency_key="other",
                    context_references=(scope.scope_id,),
                )
            )
            await runtime.memory.for_scope(scope.scope_id).remember(
                RememberMemoryRequest(
                    entries=(
                        MemoryEntryInput(
                            kind="fact", text="allocate_budget must preserve the caller's total byte budget."
                        ),
                    )
                )
            )

        config = CodeConfig(
            enabled=True,
            repositories={scope.scope_id: root},
            cache_dir=tmp_path / "cache",
            provider=CodeGraphConfig(executable=executable),
        )
        assert (await CodeService(config).index(scope.scope_id)).status == "ready"
        return config, scope.scope_id, other.scope_id

    config, scope_id, other_id = asyncio.run(prepare())
    return (
        ServerSettings(
            database=database,
            runtime=policy,
            code=config,
            inference=InferenceConfig(),
            workspace=tmp_path,
            access=AccessControlConfig(mode="disabled"),
            mcp=McpConfig(enabled=False),
        ),
        scope_id,
        other_id,
    )


@pytest.mark.parametrize("recall_gate", [False, True])
def test_real_prepare_query_and_no_implicit_persistence(
    configured_repository, tmp_path: Path, recall_gate: bool
) -> None:
    settings, scope_id, other_id = configured_repository
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"recall_gate_enabled": recall_gate})}
    )
    with TestClient(create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")) as client:
        source_path = f"/v1/scopes/{scope_id}/sources"
        before = client.get(source_path).json()
        query_path = f"/v1/scopes/{scope_id}/code/query"
        status = client.post(query_path, json={"operation": {"kind": "status"}})
        assert status.status_code == 200, status.text
        assert status.json()["status"] == "ready"
        assert "read" in status.json()["capabilities"]
        query = client.post(query_path, json={"operation": {"kind": "symbols", "query": "allocate_budget"}})
        assert query.status_code == 200, query.text
        assert len(query.content) <= 16000
        assert query.json()["items"][0]["path"] == "budget.py"
        base = {"scope_id": scope_id, "query": "allocate_budget", "max_bytes": 8000}
        for assembly in (None, {}, {"sections": []}):
            payload = {**base, "include_code": True}
            if assembly is not None:
                payload["assembly"] = assembly
            prepared = client.post("/v1/context/prepare", json=payload)
            assert prepared.status_code == 200, prepared.text
            result = prepared.json()
            assert result["status"] == "ready"
            assert "Current code references" in result["content"]
            assert "budget.py" in result["content"]
            assert result["content_bytes"] == len(result["content"].encode()) <= 8000
            if assembly == {"sections": []}:
                assert "## Memory" not in result["content"]
            else:
                assert "## Memory" in result["content"]
        for switch in (None, False):
            payload = dict(base)
            if switch is not None:
                payload["include_code"] = switch
            prepared = client.post("/v1/context/prepare", json=payload)
            assert prepared.status_code == 200, prepared.text
            assert "Current code references" not in (prepared.json()["content"] or "")
        assert client.get(source_path).json() == before
        unauthorized_reference = client.post(
            "/v1/context/prepare",
            json={
                "scope_id": other_id,
                "query": "allocate_budget",
                "include_code": True,
                "assembly": {"sections": []},
            },
        )
        assert unauthorized_reference.status_code == 200, unauthorized_reference.text
        assert unauthorized_reference.json()["status"] == "empty"
        for invalid in (None, 0, "true"):
            response = client.post("/v1/context/prepare", json={**base, "include_code": invalid})
            assert response.status_code == 422
        bad_path = client.post(query_path, json={"operation": {"kind": "tree", "path": "../outside"}})
        assert bad_path.status_code == 422
        (settings.code.repositories[scope_id] / "budget.py").write_text(
            "def allocate_budget(total):\n    return total\n"
        )
        changed = client.post(query_path, json={"operation": {"kind": "symbols", "query": "allocate_budget"}})
        assert changed.status_code == 409, changed.text
        fallback = client.post("/v1/context/prepare", json={**base, "include_code": True})
        assert fallback.status_code == 200, fallback.text
        assert "## Memory" in fallback.json()["content"]
        assert "Code reference 1" not in fallback.json()["content"]
        assert "code_changed" in fallback.json()["content"]


def test_code_context_does_not_claim_savings_against_historical_sources(configured_repository) -> None:
    settings, existing_id, _ = configured_repository

    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=settings.database)) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Code statistics", summary="Evidence baseline", idempotency_key="statistics")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="constraint",
                    content="allocate_budget must preserve byte limits.",
                    metadata={},
                )
            )
            candidate = await runtime.experience.for_scope(scope.scope_id).propose(
                ProposeExperienceRequest(
                    proposal=ExperienceContent(
                        situation="allocate_budget had a byte limit",
                        action="Check allocate_budget",
                        outcome="allocate_budget remains bounded",
                        lesson="Preserve allocate_budget byte limits",
                    ),
                    sources=(source.source_ref,),
                )
            )
            await runtime.review.for_scope(scope.scope_id).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
        code = settings.code.model_copy(
            update={"repositories": {scope.scope_id: settings.code.repositories[existing_id]}}
        )
        async with open_builtin_runtime(BuiltinConfig(database=settings.database, code=code)) as runtime:
            await runtime.code.for_scope(scope.scope_id).index()
            context = runtime.context.for_scope(scope.scope_id)
            request = {"query": "allocate_budget", "assembly": {"sections": [{"family": "experience", "limit": 1}]}}
            historical = await context.prepare(PrepareContextRequest.model_validate(request))
            assert historical.status == "ready"
            before = await runtime.statistics.for_scope(scope.scope_id).overview(period=StatisticsPeriod.TODAY)
            assert before.recall.totals.comparable_preparations == 1
            combined = await context.prepare(PrepareContextRequest.model_validate({**request, "include_code": True}))
            assert combined.content and "Current code references" in combined.content
            after = await runtime.statistics.for_scope(scope.scope_id).overview(period=StatisticsPeriod.TODAY)
            assert after.recall.totals.preparations == 2
            assert after.recall.totals.comparable_preparations == 1
            assert after.recall.totals.baseline_tokens == before.recall.totals.baseline_tokens
            assert after.recall.totals.recalled_tokens == before.recall.totals.recalled_tokens

    asyncio.run(scenario())
