# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Real HTTP capture, process restart, SQLite evidence readback and complete input budgets."""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    project_atomic_memory_evidence,
)
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig, RuntimeConfig
from powercontext.builtin.sources import ContentCapture, ContentSource, ContentTextEvidenceProjection
from powercontext.sources import SourceRef
from tests.e2e.dream_support import atomic_memory_pipeline
from tests.e2e.test_artifact_plugins import ADMIN_AUTHORIZATION_VALUE, create_scope, running_server
from tests.integrations.test_hermes_provider import hermes_modules as hermes_modules

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "integration_conformance/capture_coverage_cases.json").read_text(encoding="utf-8")
)
KEY = "powercontext_capture_coverage"


class RecordingExtractor:
    def __init__(self) -> None:
        self.requests: list[AtomicMemoryExtractionInput] = []

    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        self.requests.append(request)
        return GenerationResult(output=AtomicMemoryExtractionOutput())


def test_real_http_capture_restart_and_legacy_replay(tmp_path, hermes_modules):
    provider_module, _ = hermes_modules
    helpers = importlib.import_module("plugins.powercontext.helpers")
    client_module = importlib.import_module("plugins.powercontext.client")
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}")
    expected: dict[str, dict[str, Any]] = {
        f"fixture-{case['id']}": {
            "content": "Synthetic projected turn.",
            "metadata": {
                "kind": "synthetic",
                KEY: case["expected"],
                "prefix_extension": {"handle": "synthetic-opaque"},
            },
        }
        for case in FIXTURE["declarations"]
    }
    positions = {}
    http_records = {}

    async def scenario():
        async with running_server(tmp_path) as http:
            scope = await create_scope(http, "capture-coverage")
            other = await create_scope(http, "other-coverage")
            provider = provider_module.PowerContextMemoryProvider({
                "base_url": str(http.base_url),
                "token": ADMIN_AUTHORIZATION_VALUE,
                "scope_id": scope,
                "capture_pre_compress": True,
            })
            provider.initialize("synthetic-session", hermes_home=str(tmp_path / "hermes"))
            legacy_id = provider._turn_source_id("synthetic-session", "Legacy turn.", "Recorded.")
            expected[legacy_id] = {
                "content": "[user]\nLegacy turn.\n\n[assistant]\nRecorded.",
                "metadata": {"kind": "hermes-turn", "session_id": "synthetic-session"},
            }
            try:
                for source_id, payload in expected.items():
                    response = await http.post(
                        "/v1/sources/content", json={"scope_id": scope, "source_id": source_id, **payload}
                    )
                    assert response.status_code == 202, response.text
                    positions[source_id] = response.json()["position"]

                # Both attempts use the integration's real HTTP client. The original legacy row must win.
                provider.sync_turn("Legacy turn.", "Recorded.")
                provider._wait_for_background()
                provider.sync_turn("New turn.", "Recorded.")
                provider._wait_for_background()
                new_id = provider._turn_source_id("synthetic-session", "New turn.", "Recorded.")
                expected[new_id] = {
                    "content": "[user]\nNew turn.\n\n[assistant]\nRecorded.",
                    "metadata": {
                        "kind": "hermes-turn",
                        "session_id": "synthetic-session",
                        KEY: helpers.capture_coverage("hermes-turn-text.v1"),
                    },
                }
                checkpoint_window = [{"role": "user", "content": "Checkpoint turn."}]
                provider.on_pre_compress(checkpoint_window, require_checkpoint=True)
                provider.on_pre_compress(checkpoint_window, require_checkpoint=True)
                listed = await http.get(f"/v1/scopes/{scope}/sources")
                assert listed.status_code == 200, listed.text
                checkpoints = [
                    item for item in listed.json()["items"] if item["source_id"].startswith("hermes-compression:")
                ]
                assert len(checkpoints) == 1
                checkpoint_id = checkpoints[0]["source_id"]
                expected[checkpoint_id] = {
                    "content": "[user] Checkpoint turn.",
                    "metadata": {
                        "kind": "hermes-context-compression",
                        "session_id": "synthetic-session",
                        "message_count": 1,
                        KEY: helpers.capture_coverage("hermes-precompress-text.v1"),
                    },
                }
                for source_id, payload in expected.items():
                    response = await http.get(f"/v1/scopes/{scope}/sources/content/{source_id}")
                    assert response.status_code == 200, response.text
                    record = response.json()
                    assert record["content"] == payload["content"]
                    assert record["receipt_identity"] is None
                    positions.setdefault(source_id, record["position"])
                    assert record["position"] == positions[source_id]
                    http_records[source_id] = record
                assert positions[checkpoint_id] == len(expected)

                with pytest.raises(client_module.PowerContextHTTPError) as conflict:
                    provider._capture_content(
                        provider._client, scope, legacy_id, "Different content.", expected[new_id]["metadata"]
                    )
                assert conflict.value.status == 409
                with pytest.raises(client_module.PowerContextHTTPError) as conflict:
                    provider._capture_content(
                        provider._client,
                        scope,
                        legacy_id,
                        expected[legacy_id]["content"],
                        {**expected[new_id]["metadata"], "session_id": "different-session"},
                    )
                assert conflict.value.status == 409
            finally:
                provider.shutdown()

        async with running_server(tmp_path) as http:
            for source_id, payload in expected.items():
                response = await http.get(f"/v1/scopes/{scope}/sources/content/{source_id}")
                assert response.status_code == 200, response.text
                assert response.json() == http_records[source_id]
                replay = await http.post(
                    "/v1/sources/content", json={"scope_id": scope, "source_id": source_id, **payload}
                )
                assert replay.status_code == 202, replay.text
                assert replay.json()["position"] == positions[source_id]
                isolated = await http.get(f"/v1/scopes/{other}/sources/content/{source_id}")
                assert isolated.status_code == 404

        # Content get_source exposes the body. The existing repository read exposes the full stored Source.
        extractor = RecordingExtractor()
        async with open_builtin_contexts(
            BuiltinConfig(database=database), candidate_pipeline=atomic_memory_pipeline(extractor)
        ) as contexts:
            async with contexts.database.transaction() as connection:
                for source_id, payload in expected.items():
                    stored = await contexts.repositories.sources.get(
                        connection, scope, SourceRef(source_type="content", source_id=source_id)
                    )
                    assert isinstance(stored.value, ContentSource)
                    assert stored.value.content == payload["content"]
                    assert stored.value.metadata == payload["metadata"]
                    assert not stored.value.handoff_receipt
                    assert ContentTextEvidenceProjection().project(stored.value).metadata == payload["metadata"]
                assert await contexts.repositories.sources.journal_position(connection, scope) == len(expected)
            result = await contexts.process_memory(scope, len(expected))
            assert result.source_count == len(expected)
            assert result.current_cursor == len(expected)
            evidence = {item.source_ref.source_id: item for request in extractor.requests for item in request.evidence}
            assert set(evidence) == set(expected)
            for source_id, payload in expected.items():
                body = evidence[source_id].content
                assert isinstance(body, dict)
                assert body["metadata"] == payload["metadata"]
            legacy = evidence[legacy_id].content
            assert isinstance(legacy, dict)
            assert helpers.capture_coverage_status(legacy["metadata"]) == "unknown"

    asyncio.run(scenario())


@pytest.mark.parametrize("first_partial", [False, True])
def test_real_http_checkpoint_replays_first_coverage_after_ack_loss(tmp_path, hermes_modules, first_partial):
    provider_module, _ = hermes_modules
    client_module = importlib.import_module("plugins.powercontext.client")
    attempts = []
    lost_ack = client_module.PowerContextTransportError("Acknowledgement lost after real HTTP capture")

    async def scenario():
        async with running_server(tmp_path) as http:
            scope = await create_scope(http, "checkpoint-ack-recovery")
            actual = client_module.PowerContextClient(
                str(http.base_url), authorization=f"Bearer {ADMIN_AUTHORIZATION_VALUE}"
            )

            class LostAckClient:
                def __getattr__(self, name):
                    return getattr(actual, name)

                def capture_content(self, scope_id, **payload):
                    attempts.append(json.loads(json.dumps({"scope_id": scope_id, **payload})))
                    response = actual.capture_content(scope_id, **payload)
                    if len(attempts) == 1:
                        raise lost_ack
                    return response

            provider = provider_module.PowerContextMemoryProvider(
                {"scope_id": scope, "capture_pre_compress": True}, client_factory=lambda config: LostAckClient()
            )
            provider.initialize("synthetic-session", hermes_home=str(tmp_path / "hermes"))
            prefix = [{"role": "user", "content": "Already stored."}]
            oversized_window = [*prefix, {"role": "assistant", "content": "x" * 30_001}]
            try:
                provider.on_pre_compress(oversized_window if first_partial else prefix)
                provider.on_pre_compress(
                    prefix if first_partial else oversized_window, require_checkpoint=first_partial
                )
                provider.on_pre_compress(prefix, require_checkpoint=True)
                assert len(attempts) == 2
                assert attempts[0] == attempts[1]
                listed = await http.get(f"/v1/scopes/{scope}/sources")
                assert listed.status_code == 200, listed.text
                records = listed.json()["items"]
                assert len(records) == 1
                assert records[0]["position"] == 1
                assert records[0]["content"] == "[user] Already stored."
            finally:
                provider.shutdown()

        # Reopen SQLite after the real Server process exits; its first declaration stays unchanged.
        async with (
            open_builtin_contexts(
                BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"))
            ) as contexts,
            contexts.database.transaction() as connection,
        ):
            stored = await contexts.repositories.sources.get(
                connection, scope, SourceRef(source_type="content", source_id=attempts[0]["source_id"])
            )
            assert isinstance(stored.value, ContentSource)
            assert stored.value.metadata == attempts[0]["metadata"]
            coverage = stored.value.metadata[KEY]
            assert isinstance(coverage, dict)
            assert coverage["status"] == ("partial" if first_partial else "complete")
            assert await contexts.repositories.sources.journal_position(connection, scope) == 1

    asyncio.run(scenario())


def test_capture_coverage_cost_obeys_existing_budget_and_window_reduction(tmp_path):
    sample = FIXTURE["budget_sample"]
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    extractor = RecordingExtractor()
    pipeline = atomic_memory_pipeline(extractor)

    async def scenario():
        inputs = {}
        async with open_builtin_contexts(BuiltinConfig(database=database), candidate_pipeline=pipeline) as contexts:
            for scope, metadata in [
                ("without", sample["metadata"]),
                ("with", {**sample["metadata"], KEY: sample["coverage"]}),
            ]:
                context = await contexts.get(scope)
                await context.sources.capture(
                    ContentCapture(source_id=sample["source_id"], content=sample["content"], metadata=metadata)
                )
                async with contexts.database.transaction() as connection:
                    stored = await contexts.repositories.sources.get(
                        connection, scope, SourceRef(source_type="content", source_id=sample["source_id"])
                    )
                evidence = await project_atomic_memory_evidence(
                    stored, contexts.repositories.sources, evidence_id="source:1"
                )
                inputs[scope] = AtomicMemoryExtractionInput(evidence=(evidence,))

        def estimate(value):
            return pipeline.estimator.estimate(f"{pipeline.extraction_instructions}\n{value.model_dump_json()}")

        baseline = estimate(inputs["without"])
        covered = estimate(inputs["with"])
        assert covered > baseline
        async with open_builtin_contexts(
            BuiltinConfig(database=database, runtime=RuntimeConfig(atomic_memory_input_tokens_limit=baseline)),
            candidate_pipeline=pipeline,
        ) as contexts:
            assert (await contexts.process_memory("without", 1)).current_cursor == 1
            with pytest.raises(InvalidInferenceOutputError, match="atomic-memory-input-budget"):
                await contexts.process_memory("with", 1)
            context = await contexts.get("with")
            assert (await context.triggers.cursor()).sequence == 0
            async with contexts.database.transaction() as connection:
                stored = await contexts.repositories.sources.get(
                    connection, "with", SourceRef(source_type="content", source_id=sample["source_id"])
                )
                assert isinstance(stored.value, ContentSource)
                assert stored.value.metadata[KEY] == sample["coverage"]
        assert len(extractor.requests) == 1

        extractor.requests.clear()
        async with open_builtin_contexts(
            BuiltinConfig(database=database, runtime=RuntimeConfig(atomic_memory_input_tokens_limit=covered + 10)),
            candidate_pipeline=pipeline,
        ) as contexts:
            context = await contexts.get("window")
            for index in range(2):
                await context.sources.capture(
                    ContentCapture(
                        source_id=f"turn-{index}",
                        content=sample["content"],
                        metadata={**sample["metadata"], KEY: sample["coverage"]},
                    )
                )
            with pytest.raises(InvalidInferenceOutputError, match="atomic-memory-input-budget"):
                await contexts.process_memory("window", 2)
            assert (await context.triggers.cursor()).sequence == 0
            assert (await contexts.process_memory("window", 2)).source_count == 1
            assert (await contexts.process_memory("window", 2)).current_cursor == 2
            assert all(
                isinstance(item.content, dict) and item.content["metadata"][KEY] == sample["coverage"]
                for request in extractor.requests
                for item in request.evidence
            )

        print(
            json.dumps({
                "coverage_object_bytes": len(
                    json.dumps(sample["coverage"], ensure_ascii=False, separators=(",", ":")).encode()
                ),
                "before_input_bytes": len(inputs["without"].model_dump_json().encode()),
                "after_input_bytes": len(inputs["with"].model_dump_json().encode()),
                "before_estimated_tokens": baseline,
                "after_estimated_tokens": covered,
                "estimator": pipeline.estimator.profile.model_dump(),
            })
        )

    asyncio.run(scenario())
