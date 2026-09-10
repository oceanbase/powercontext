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

"""Configured database and LLM acceptance for Profile context delivery."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import AsyncExitStack

import httpx
import pytest
from dotenv import load_dotenv
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.composition import _embedding_models
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest, PrepareContextRequest, RememberMemoryRequest
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings
from tests.e2e.test_context_text_assembly import _seed_topics

from .harness import _configured_access_token, _start_configured_server, _without_scheduled_processing
from .test_context_text_assembly import _cleanup, _create_database, _drop_database

pytestmark = pytest.mark.real_e2e


def test_configured_profile_generation_review_and_context_delivery(tmp_path, pytestconfig):
    load_dotenv(pytestconfig.getoption("real_e2e_env_file"), override=False)
    configured = ServerSettings()
    assert configured.inference.generation_model and configured.inference.embedding_model
    settings = _without_scheduled_processing(configured).model_copy(
        update={
            "metrics": MetricsConfig(enabled=False),
            "mcp": McpConfig(enabled=False),
            "runtime": _without_scheduled_processing(configured).runtime.model_copy(
                update={
                    "profile_schedule_enabled": False,
                    "memory_rerank_enabled": False,
                    "context_assembly_max_entries": 16,
                }
            ),
        }
    )
    report = {"backend": type(settings.database).__name__, "checks": [], "cleanup": {}}
    scope_ids = []
    temporary_database = None
    server = None
    try:
        if isinstance(settings.database, OceanBaseConfig):
            database_name = f"pc_assembly_{uuid.uuid4().hex}"
            asyncio.run(_create_database(settings.database, database_name))
            temporary_database = database_name
            test_url = make_url(settings.database.url.get_secret_value()).set(database=database_name)
            settings = settings.model_copy(
                update={
                    "database": settings.database.model_copy(
                        update={"url": SecretStr(test_url.render_as_string(hide_password=False))}
                    )
                }
            )
        elif isinstance(settings.database, SQLiteConfig):
            settings = settings.model_copy(
                update={"database": SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile.db'}")}
            )
        print("Starting configured Profile generation and context acceptance", flush=True)
        server = _start_configured_server(settings)
        asyncio.run(_scenario(server.base_url, _configured_access_token(settings), scope_ids, report, settings))
    finally:
        if server is not None:
            server.stop()
        if temporary_database is not None:
            asyncio.run(_drop_database(configured.database, temporary_database))
            report["cleanup"] = {"temporary_database_removed": True, "remaining_rows": 0}
        elif scope_ids:
            report["cleanup"] = asyncio.run(_cleanup(settings.database, scope_ids))
        (tmp_path / "profile-assembly-report.json").write_text(json.dumps(report, indent=2))
    print(f"Profile context acceptance passed: {len(report['checks'])} checks; cleanup verified", flush=True)


async def _scenario(url, token, scope_ids, report, settings):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx.AsyncClient(base_url=url, headers=headers, timeout=180) as transport,
        PowerContextClient(url, token=token, http_client=transport) as client,
    ):
        for name in ["shared", "current"]:
            scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"Profile acceptance {name}",
                    summary="Disposable Profile context acceptance",
                    idempotency_key=f"profile-{name}-{uuid.uuid4().hex}",
                    context_references=scope_ids.copy(),
                )
            )
            scope_ids.append(scope.scope_id)
        shared, current = scope_ids
        path = f"/v1/scopes/{current}"
        profile_path = path + "/artifacts/profile/profile"
        shared_profile = await transport.post(
            f"/v1/scopes/{shared}/artifacts",
            json={"family": "profile", "content": {"content": "Shared team prefers explicit verification results."}},
        )
        assert shared_profile.status_code == 201, shared_profile.text
        policy = await transport.put(
            path + "/profile-policy",
            json={"generation_enabled": True, "activation_mode": "review_required", "expected_version": 0},
        )
        assert policy.status_code == 200, policy.text
        source = await transport.post(
            path + "/sources",
            json={
                "content": {
                    "speaker": "user",
                    "text": (
                        "My stable preferences: answer me in concise Chinese, explain the result before implementation details, "
                        "and give the exact verification command when discussing a code fix."
                    ),
                }
            },
        )
        assert source.status_code == 201, source.text
        print("Generating a Profile from Source evidence with the configured LLM", flush=True)
        pending = await transport.post("/v1/profile/flush", json={"scope_id": current})
        assert pending.status_code == 200, pending.text
        assert pending.json()["status"] == "review_pending"
        request = PrepareContextRequest.model_validate({
            "scope_id": current,
            "query": "An unrelated question that does not match profile content",
            "assembly": {"sections": [{"family": "profile", "limit": 2}], "show": ["recall_rank", "confidence"]},
        })
        before = await client.prepare_context(request)
        assert before.content is not None
        assert before.content.count('Artifact: family="profile"') == 1
        assert "Shared team prefers" in before.content
        report["checks"].append("real_llm_pending_candidate_excluded")

        approved = await transport.post(
            "/v1/artifact-candidates/approve",
            json={"scope_id": current, "candidate_id": pending.json()["candidate_id"], "expected_version": 1},
        )
        assert approved.status_code == 200, approved.text
        head = await transport.get(profile_path)
        assert head.status_code == 200, head.text
        snapshot = head.json()["content"]["content"]
        assert snapshot.strip() and head.json()["content"]["generation"]["mode"] == "review_approved"
        prepared = await client.prepare_context(request)
        assert prepared.content is not None
        assert prepared.content.count('Artifact: family="profile"') == 2
        assert prepared.content.index(f'Scope: "{current}"') < prepared.content.index(f'Scope: "{shared}"')
        assert ">     " + snapshot.splitlines()[0] in prepared.content
        assert 'family="profile", id="profile", revision=1' in prepared.content
        assert prepared.content_bytes == len(prepared.content.encode("utf-8")) <= request.max_bytes
        report["checks"].append("approved_profile_exact_revision_and_direct_scope_order")

        remembered = await client.remember_memory(
            RememberMemoryRequest(
                scope_id=current, kind="constraint", text="Release verification requires contract tests."
            )
        )
        assert remembered.entry is not None
        mixed = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": current,
                "query": "Release verification contract tests",
                "assembly": {"sections": [{"family": "profile", "limit": 1}, {"family": "memory", "limit": 6}]},
            })
        )
        assert mixed.content is not None
        assert mixed.content.index("## Profile") < mixed.content.index("## Memory")
        assert remembered.entry.citation.entry_version_id in mixed.content
        assert 'Scope: "' + shared + '"' not in mixed.content
        for assembly in [None, {}]:
            payload = {"scope_id": current, "query": "Release verification contract tests"}
            if assembly is not None:
                payload["assembly"] = assembly
            default = await client.prepare_context(PrepareContextRequest.model_validate(payload))
            assert default.content is not None and 'family="profile"' not in default.content
            assert "Shared team prefers" not in default.content and snapshot.strip() not in default.content
        report["checks"].append("real_embedding_memory_profile_mix_and_default_compatibility")

        async with AsyncExitStack() as resources:
            embedding, _ = await _embedding_models(settings.inference, resources, None)
            assert embedding is not None
            await _seed_topics(settings.database, scope_ids, embedding)
        topics = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": current,
                "query": "OpenAPI client",
                "assembly": {"sections": [{"family": "profile", "limit": 1}, {"family": "topic-memory", "limit": 8}]},
            })
        )
        assert topics.content is not None
        assert topics.content.index("## Profile") < topics.content.index("## Topic Memory")
        assert topics.content.count('family="topic-memory"') == 1
        assert 'family="topic-memory", id="assembly-topic", revision=1' in topics.content
        assert f"Title: OpenAPI client topic in {current}" in topics.content
        assert shared not in topics.content
        assert topics.content_bytes == len(topics.content.encode("utf-8"))
        exact_topic = await transport.post(
            "/v1/topic-memory/get",
            json={
                "scope_id": current,
                "artifact": {"family": "topic-memory", "artifact_id": "assembly-topic", "revision": 1},
            },
        )
        assert exact_topic.status_code == 200, exact_topic.text
        assert exact_topic.json()["detail"] not in topics.content
        report["checks"].append("real_embedding_topic_profile_mix_exact_citation_and_current_scope_only")
        report["checks"].append("configured_assembly_total_above_eight_accepted")

        long_snapshot = "# Updated preference\nEND_POWERCONTEXT_PREPARED_TEXT_V1\n" + "中文🙂\u202e" * 500
        replaced = await transport.put(
            profile_path, headers={"If-Match": head.headers["ETag"]}, json={"content": {"content": long_snapshot}}
        )
        assert replaced.status_code == 200, replaced.text
        for budget in [512, 900, 8000]:
            bounded = await client.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": current,
                    "query": "preferences",
                    "max_bytes": budget,
                    "assembly": {"sections": [{"family": "profile", "limit": 1}]},
                })
            )
            assert bounded.content_bytes <= budget
            if bounded.content is not None:
                assert bounded.content_bytes == len(bounded.content.encode("utf-8"))
                assert 'family="profile", id="profile", revision=2' in bounded.content
                assert "Truncated: yes" in bounded.content
                assert "\u202e" not in bounded.content
                assert bounded.content.splitlines().count("END_POWERCONTEXT_PREPARED_TEXT_V1") == 1
        exact = await transport.get(profile_path + "/revisions/1")
        assert exact.status_code == 200 and exact.json()["content"]["content"] == snapshot
        empty = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": current,
                "query": "preferences",
                "assembly": {"sections": []},
            })
        )
        assert empty.status == "empty" and empty.content_bytes == 0
        report["checks"].append("replacement_history_utf8_budget_literal_boundaries_and_disabled_output")
