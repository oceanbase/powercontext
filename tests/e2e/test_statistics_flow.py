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

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    CaptureContentSourceRequest,
    ExperienceProposal,
    FlushMemoryRequest,
    GetStatsRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    ScopedStats,
    StatsPeriod,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings
from tests.e2e.atomic_memory_models import independent_atomic_memory_model

_AUTH_TOKEN = "statistics-e2e-token"  # noqa: S105 - non-secret test credential.
_OCEANBASE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
_MEMORY_TEXT = "The statistics contract must stay dashboard ready."
_SOURCE_CONTENT = "\n".join(
    (
        "The statistics contract must stay dashboard ready while raw task history remains auditable.",
        "The implementation records Sources, reviewed Artifacts, Candidate states, and model usage independently.",
        "Recall compares the exact de-duplicated Source lineage with the complete prepared context envelope.",
        "Daily aggregates retain estimator identity and distinguish comparable preparations from total traffic.",
    )
    * 8
)


def _settings(database_kind: str, database: Path) -> ServerSettings:
    if database_kind == "oceanbase":
        if _OCEANBASE_URL is None:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database")
        persistence = OceanBaseConfig(url=SecretStr(_OCEANBASE_URL))
    else:
        persistence = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
    return ServerSettings(
        database=persistence,
        auth=BearerAuthConfig(token=SecretStr(_AUTH_TOKEN)),
        access=AccessControlConfig(mode="enforced"),
        inference=InferenceConfig(generation_model="test"),
        mcp=McpConfig(enabled=False),
    )


def _proposal(label: str) -> ExperienceProposal:
    return ExperienceProposal(
        situation="A statistics contract must support a dashboard.",
        action=f"Exercise the {label} review path.",
        outcome="Inventory and bounded usage remain queryable.",
        lesson=f"Keep the {label} state visible in statistics.",
    )


def _client(app) -> tuple[httpx.AsyncClient, PowerContextClient]:
    transport = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )
    return transport, PowerContextClient(
        "http://testserver",
        token=_AUTH_TOKEN,
        http_client=transport,
        trust_transport_security=True,
    )


def _stats_request(scope_id: str, period: StatsPeriod) -> GetStatsRequest:
    return GetStatsRequest.model_validate({
        "selection": {"mode": "exact", "scope_ids": [scope_id]},
        "period": period,
    })


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
def test_statistics_survive_the_authenticated_http_business_flow_and_restart(
    database_kind: str,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "statistics-flow.db"
    settings = _settings(database_kind, database)
    scope_id = f"statistics-e2e-{uuid4()}"
    monkeypatch.setattr(
        "pydantic_ai.models.infer_model",
        lambda _, **_kwargs: independent_atomic_memory_model(_MEMORY_TEXT),
    )
    first_app = create_server_app(settings=settings)

    async def scenario() -> None:
        nonlocal scope_id
        async with first_app.router.lifespan_context(first_app):
            transport, client = _client(first_app)
            async with transport:
                created_scope = await transport.post(
                    "/v1/scopes",
                    json={"title": "Statistics", "summary": "Statistics flow", "idempotency_key": scope_id},
                    headers={"Authorization": f"Bearer {_AUTH_TOKEN}"},
                )
                assert created_scope.status_code == 201
                scope_id = created_scope.json()["scope_id"]
                source = await client.capture_content_source(
                    CaptureContentSourceRequest(
                        scope_id=scope_id,
                        source_id="source-processed",
                        content=_SOURCE_CONTENT,
                        metadata={"flow": "statistics-e2e"},
                    )
                )
                flush = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
                remembered = await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope_id,
                        kind="project_note",
                        text="Keep Memory kinds open for product-specific entries.",
                    )
                )

                manual_ref = remembered.records[0].artifact
                manual_artifact = await client.get_artifact_revision(
                    scope_id, manual_ref.family, manual_ref.artifact_id, manual_ref.revision
                )
                assert len(manual_artifact.sources) == 1
                manual_source = await client.get_source(
                    scope_id, manual_artifact.sources[0].source_type, manual_artifact.sources[0].source_id
                )
                assert manual_source.content == {
                    "schema": "powercontext.atomic-memory.v1",
                    "kind": "project_note",
                    "text": "Keep Memory kinds open for product-specific entries.",
                }
                manual_source_text = json.dumps(
                    manual_source.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )

                approved_candidate = await client.propose_experience(
                    ProposeExperienceRequest(
                        scope_id=scope_id,
                        proposal=_proposal("approved"),
                        source_refs=[source.source],
                        artifact_refs=[],
                    )
                )
                await client.approve_artifact_candidate(
                    ApproveArtifactCandidateRequest(
                        scope_id=scope_id,
                        candidate_id=approved_candidate.candidate_id,
                        expected_version=approved_candidate.version,
                    )
                )
                rejected_candidate = await client.propose_experience(
                    ProposeExperienceRequest(
                        scope_id=scope_id,
                        proposal=_proposal("rejected"),
                        source_refs=[source.source],
                        artifact_refs=[],
                    )
                )
                await client.reject_artifact_candidate(
                    RejectArtifactCandidateRequest(
                        scope_id=scope_id,
                        candidate_id=rejected_candidate.candidate_id,
                        expected_version=rejected_candidate.version,
                        reason="Exercise the terminal rejected state.",
                    )
                )
                await client.propose_experience(
                    ProposeExperienceRequest(
                        scope_id=scope_id,
                        proposal=_proposal("pending"),
                        source_refs=[source.source],
                        artifact_refs=[],
                    )
                )
                await client.capture_content_source(
                    CaptureContentSourceRequest(
                        scope_id=scope_id,
                        source_id="source-pending",
                        content="This source intentionally remains beyond the Memory cursor.",
                    )
                )

                prepared = await client.prepare_context(
                    PrepareContextRequest(scope_id=scope_id, query="statistics contract")
                )
                manual_prepared = await client.prepare_context(
                    PrepareContextRequest(scope_id=scope_id, query="Memory kinds open")
                )
                empty = await client.prepare_context(
                    PrepareContextRequest(scope_id=scope_id, query="unrelated-zebra-phrase")
                )
                first = await client.get_stats(_stats_request(scope_id, StatsPeriod.TODAY))

                unauthorized = await transport.post(
                    "/v1/stats",
                    json={"selection": {"mode": "exact", "scope_ids": [scope_id]}},
                )
                raw = await transport.post(
                    "/v1/stats",
                    json={"selection": {"mode": "exact", "scope_ids": [scope_id]}, "period": "today"},
                    headers={"Authorization": f"Bearer {_AUTH_TOKEN}"},
                )

        assert flush.memory is None
        assert flush.processed_source_count == 1
        assert prepared.status == "ready"
        assert prepared.content is not None
        assert '"kind":"experience"' in prepared.content
        assert '"family":"atomic-memory"' in prepared.content
        assert _MEMORY_TEXT in prepared.content
        assert manual_prepared.status == "ready"
        assert empty.status == "empty"
        assert unauthorized.status_code == 401
        assert raw.status_code == 200
        assert raw.headers["Cache-Control"] == "no-store"
        assert raw.headers["X-PowerContext-Request-ID"]
        assert "X-Request-ID" not in raw.headers
        raw_body = raw.json()
        first_body = first.model_dump(mode="json", by_alias=True)
        assert raw_body.pop("as_of") >= first_body.pop("as_of")
        assert raw_body == first_body
        assert manual_prepared.content is not None
        _assert_first_snapshot(first, prepared.content, manual_prepared.content, manual_source_text)

        second_app = create_server_app(settings=settings)
        async with second_app.router.lifespan_context(second_app):
            transport, client = _client(second_app)
            async with transport:
                restored = await client.get_stats(_stats_request(scope_id, StatsPeriod.FIELD_7D))
                prepared_again = await client.prepare_context(
                    PrepareContextRequest(scope_id=scope_id, query="statistics contract")
                )
                updated = await client.get_stats(_stats_request(scope_id, StatsPeriod.FIELD_7D))

        assert restored.inventory == first.inventory
        assert restored.usage.totals == first.usage.totals
        assert restored.usage.by_purpose == first.usage.by_purpose
        assert restored.recall.totals == first.recall.totals
        assert len(restored.usage.daily) == 7
        assert len(restored.recall.daily) == 7
        assert prepared_again.status == "ready"
        assert prepared_again.content == prepared.content
        assert updated.recall.totals.preparations == 4
        assert updated.recall.totals.ready_preparations == 3
        assert updated.recall.totals.comparable_preparations == 3
        estimator = character_token_estimator()
        assert updated.recall.totals.baseline_tokens == (
            first.recall.totals.baseline_tokens + estimator.estimate(_SOURCE_CONTENT)
        )
        assert updated.recall.totals.recalled_tokens == (
            first.recall.totals.recalled_tokens + estimator.estimate(prepared.content)
        )
        assert updated.recall.totals.token_reduction == (
            first.recall.totals.token_reduction
            + estimator.estimate(_SOURCE_CONTENT)
            - estimator.estimate(prepared.content)
        )

    asyncio.run(scenario())


def _assert_first_snapshot(
    statistics: ScopedStats, prepared_content: str, manual_content: str, manual_source_text: str
) -> None:
    # Captured source 1 was processed. Direct Remember adds a lineage-only
    # system Source at journal position 2; captured source 3 is also beyond
    # the Memory cursor. Inventory counts that journal coverage, including
    # the system Source, while recall compares its exact direct-write lineage.
    assert statistics.inventory.sources.model_dump() == {
        "total": 3,
        "memory_processed": 1,
        "memory_pending": 2,
    }
    assert [(item.family, item.total) for item in statistics.inventory.artifacts.by_family] == [
        ("atomic-memory", 2),
        ("experience", 1),
    ]
    assert statistics.inventory.candidates.model_dump(exclude={"by_family"}) == {
        "total": 3,
        "pending": 1,
        "approved": 1,
        "rejected": 1,
    }
    assert [(item.kind, item.total, item.active) for item in statistics.inventory.memory.entries.by_kind] == [
        ("decision", 1, 1),
        ("project_note", 1, 1),
    ]

    assert statistics.usage.totals.generation.requests == 2
    assert statistics.usage.totals.generation.input_tokens is not None
    assert statistics.usage.totals.generation.output_tokens is not None
    assert statistics.usage.totals.embedding.requests == 0
    assert {item.purpose for item in statistics.usage.by_purpose} == {
        "memory_extraction",
    }

    estimator = character_token_estimator()
    recall = statistics.recall
    assert recall.estimator is not None
    assert recall.estimator.model_dump() == estimator.profile.model_dump()
    assert recall.totals.preparations == 3
    assert recall.totals.ready_preparations == 2
    assert recall.totals.comparable_preparations == 2
    assert recall.totals.baseline_tokens == (
        estimator.estimate(_SOURCE_CONTENT) + estimator.estimate(manual_source_text)
    )
    assert recall.totals.recalled_tokens == (estimator.estimate(prepared_content) + estimator.estimate(manual_content))
    assert recall.totals.token_reduction == recall.totals.baseline_tokens - recall.totals.recalled_tokens
    assert recall.totals.token_reduction > 0
