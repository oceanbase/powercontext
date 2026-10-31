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

"""The baseline fixture automatically incubates exact task evidence in a real child."""

import asyncio
import json
import os

from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveCandidateRequest,
    CaptureContentSourceRequest,
    CreateScopeRequest,
    ExperienceProposal,
    GetExperienceRequest,
    PrepareContextRequest,
)
from tests.e2e.real_experience_skill.harness import _start_server, _wait_for_experience_candidate


def test_baseline_server_incubates_task_outcomes_before_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    server = _start_server(tmp_path / "runtime.db", tmp_path / "scheduler.db")

    async def scenario():
        proposal = ExperienceProposal(
            situation="The strict configuration fixture failed validation.",
            action="Set the configuration mode to strict.",
            outcome="python test_config.py passed",
            lesson="Validate strict configuration before publishing a change.",
        )
        async with PowerContextClient(server.base_url) as client:
            scope = await client.create_scope(
                CreateScopeRequest(title="Baseline", summary="Typed task evidence", idempotency_key="baseline")
            )
            content = json.dumps({"codex_result": proposal.model_dump(mode="json")})
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope.scope_id,
                    source_id="ordinary-content",
                    content=content,
                    metadata={"kind": "note"},
                )
            )
            source = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope.scope_id,
                    source_id="producer-outcome",
                    content=content,
                    metadata={"kind": "task-outcome", "agent": "codex"},
                )
            )
            inbox = await _wait_for_experience_candidate(client, scope.scope_id, timeout_seconds=60)
            assert len(inbox.candidates) == 1
            candidate = inbox.candidates[0]
            assert candidate.proposal == proposal
            assert candidate.source_refs == [source.source]
            assert candidate.result_artifact is None
            pending = await client.prepare_context(
                PrepareContextRequest(scope_id=scope.scope_id, query=proposal.lesson)
            )
            assert pending.status == "empty"
            approved = await client.approve_candidate(
                ApproveCandidateRequest(
                    scope_id=scope.scope_id,
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            artifact = await client.get_experience(
                GetExperienceRequest(scope_id=scope.scope_id, artifact=approved.result_artifact)
            )
            assert artifact.content == proposal
            assert artifact.source_refs == [source.source]
            prepared = await client.prepare_context(
                PrepareContextRequest(scope_id=scope.scope_id, query=proposal.lesson)
            )
            assert prepared.status == "ready"
            assert prepared.content is not None
            assert approved.result_artifact.artifact_id in prepared.content

    try:
        asyncio.run(scenario())
    finally:
        server.stop()
    assert not server.thread.is_alive()
