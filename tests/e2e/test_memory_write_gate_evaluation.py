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

"""The offline evaluator reads exact, authorized records through the real Server API."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from pathlib import Path

import httpx
from pydantic import SecretStr

from evaluation.memory.write_gate.dataset import ArtifactRef, Case, Dataset, Label, SourceRef
from evaluation.memory.write_gate.resolver import AuthorizedClientResolver
from evaluation.memory.write_gate.runner import EvaluationStatus, evaluate_dataset
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.decision_model import DecisionOutcome, DecisionRequest, DecisionResult
from powercontext.client import PowerContextClient
from powercontext.http import CreateArtifactRequest, ReplaceArtifactRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, MetricsConfig, ServerSettings


class _AcceptingModel:
    policy_id = "evaluation.live-read-test.v1"
    is_local_only = True

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        return DecisionResult(outcome=DecisionOutcome.NO, policy_id=self.policy_id, usage=InferenceUsage(requests=1))


def test_exact_source_projection_and_memory_revision_re_evaluate_through_server(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'write-gate-evaluation.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr("evaluation-test-token")),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        ),
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient(
                "http://testserver",
                token="evaluation-test-token",  # noqa: S106
                http_client=transport,
                trust_transport_security=True,
            )
            scope_id = (await client.get_default_scope()).scope_id
            created = await client.create_artifact(
                scope_id,
                CreateArtifactRequest.model_validate({
                    "family": "atomic-memory",
                    "content": {
                        "schema": "powercontext.atomic-memory.v1",
                        "kind": "fact",
                        "text": "The API is available.",
                    },
                }),
            )
            exact = await client.get_artifact_revision(scope_id, "atomic-memory", created.artifact_id, 1)
            citation = created.sources[0]
            source = await client.get_source(scope_id, citation.source_type, citation.source_id)
            gate_input = json.dumps(source.content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            case = Case(
                case_id="server-read",
                group_id="server-read",
                scope_id=scope_id,
                language="en",
                label=Label.ACCEPT,
                label_reason="direct_support",
                subject=ArtifactRef(
                    artifact_id=created.artifact_id,
                    revision=1,
                    content_digest=exact.content_digest,
                ),
                evidence=(
                    SourceRef(
                        source_id=citation.source_id,
                        content_digest=source.content_digest,
                        gate_input_digest=f"sha256:{sha256(gate_input.encode()).hexdigest()}",
                    ),
                ),
            )
            dataset = Dataset(dataset_id="server-read", split="calibration", cases=(case,))
            resolver = AuthorizedClientResolver(client)
            first = await evaluate_dataset(dataset, resolver, model=_AcceptingModel())
            assert first[0].status is EvaluationStatus.EVALUATED
            assert first[0].outcome is DecisionOutcome.NO

            replaced = await client.replace_artifact(
                scope_id,
                "atomic-memory",
                created.artifact_id,
                ReplaceArtifactRequest.model_validate({
                    "content": {"kind": "fact", "text": "The API is available on the new endpoint."}
                }),
                expected_etag='"revision:1"',
            )
            assert replaced.revision == 2
            again = await evaluate_dataset(dataset, resolver, model=_AcceptingModel())
            assert again[0].status is EvaluationStatus.EVALUATED

            changed_digest = case.model_copy(
                update={"evidence": (case.evidence[0].model_copy(update={"gate_input_digest": "sha256:" + "0" * 64}),)}
            )
            unsupported = await evaluate_dataset(
                dataset.model_copy(update={"cases": (changed_digest,)}), resolver, model=_AcceptingModel()
            )
            assert unsupported[0].status is EvaluationStatus.UNSUPPORTED_REFERENCE

            unauthorized_client = PowerContextClient(
                "http://testserver",
                token="revoked-test-token",  # noqa: S106
                http_client=transport,
                trust_transport_security=True,
            )
            denied = await evaluate_dataset(dataset, AuthorizedClientResolver(unauthorized_client))
            assert denied[0].status is EvaluationStatus.UNAUTHORIZED

    asyncio.run(scenario())
