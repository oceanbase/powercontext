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

"""Public reads filter candidates invalidated after recall without losing available evidence."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.memory import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.runtime.application import ScopedContextApplication
from powercontext.builtin.runtime.atomic_memory import ScopedAtomicMemory
from powercontext.builtin.runtime.atomic_memory_security import AtomicMemorySecurity
from powercontext.builtin.runtime.recall_sufficiency import REASON_EXPANSION_FAILED, RecallSufficiencyPolicy
from tests.e2e.test_access_control_regressions import _grant, _scope, _server

_VIEWER = {"Authorization": "Bearer viewer"}
_PREPARE = {"query": "alpha", "assembly": {"sections": [{"family": "memory", "limit": 8}]}}


async def _remember(client, scope_id, text):
    response = await client.post("/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": text})
    assert response.status_code == 200, response.text
    return response.json()["records"][0]


async def _revoke(client, binding):
    response = await client.post(
        "/v1/access/bindings/revoke",
        json={
            "binding_id": binding["binding_id"],
            "expected_version": binding["version"],
            "idempotency_key": "revoke-during-read",
        },
    )
    assert response.status_code == 200, response.text


async def _change(client, scope_id, record, operation):
    if operation == "revise":
        response = await client.put(
            f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{record['artifact']['artifact_id']}",
            headers={"If-Match": '"revision:1"'},
            json={"content": {"kind": "fact", "text": "Alpha replacement body."}},
        )
    elif operation == "forget":
        response = await client.post(
            "/v1/atomic-memory/lifecycle",
            json={
                "scope_id": scope_id,
                "target": {"artifact": record["artifact"], "state_version": record["state_version"]},
                "state": "forgotten",
            },
        )
    else:
        other = await _remember(client, scope_id, "Unrelated merge input.")
        response = await client.post(
            "/v1/atomic-memory/merges",
            json={
                "scope_id": scope_id,
                "inputs": [
                    {"artifact": item["artifact"], "state_version": item["state_version"]} for item in (record, other)
                ],
                "content": {"kind": "fact", "text": "Merged replacement body."},
            },
        )
    assert response.status_code == 200, response.text


class _Pause:
    def __init__(self):
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def wait(self):
        self.started.set()
        await self.resume.wait()


def _pause_recall(monkeypatch):
    pause = _Pause()
    recall = ScopedContextApplication._recall_round

    async def paused(*args, **kwargs):
        result = await recall(*args, **kwargs)
        await pause.wait()
        return result

    monkeypatch.setattr(ScopedContextApplication, "_recall_round", paused)
    return pause


async def _finish_read(pending, pause, mutate):
    try:
        await asyncio.wait_for(pause.started.wait(), timeout=20)
        # Public mutations must commit while inference/recall is paused.
        await asyncio.wait_for(mutate(), timeout=20)
    finally:
        pause.resume.set()
    return await asyncio.wait_for(pending, timeout=20)


@pytest.mark.parametrize("operation", ["revise", "forget", "merge"])
@pytest.mark.parametrize("retain", [False, True])
def test_prepare_drops_changed_candidates_and_keeps_available_evidence(tmp_path, monkeypatch, operation, retain):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope_id = await _scope(client)
            changed = await _remember(client, scope_id, "Alpha original body.")
            if retain:
                await _remember(client, scope_id, "Alpha retained evidence.")
            pause = _pause_recall(monkeypatch)
            pending = asyncio.create_task(client.post("/v1/context/prepare", json={"scope_id": scope_id, **_PREPARE}))
            response = await _finish_read(pending, pause, lambda: _change(client, scope_id, changed, operation))
            assert response.status_code == 200, response.text
            body = response.json()
            if retain:
                assert body["status"] == "ready"
                assert "Alpha retained evidence." in body["content"]
                assert "Alpha original body." not in body["content"]
                assert "replacement body" not in body["content"]
            else:
                assert body["status"] == "empty"
                assert body["content"] is None
                assert body["content_bytes"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("retain", [False, True])
def test_prepare_excludes_candidates_whose_read_grant_was_revoked(tmp_path, monkeypatch, retain):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope_id = await _scope(client)
            await _remember(client, scope_id, "Alpha revoked private body.")
            binding = await _grant(client, scope_id, "viewer", "scope.viewer")
            if retain:
                stable = await _remember(client, scope_id, "Alpha retained evidence.")
                await _grant(
                    client,
                    scope_id,
                    "viewer",
                    "artifact.viewer",
                    resource={
                        "type": "artifact",
                        "scope_id": scope_id,
                        "identity": {"family": "atomic-memory", "artifact_id": stable["artifact"]["artifact_id"]},
                        "selector": None,
                    },
                )
            pause = _pause_recall(monkeypatch)
            pending = asyncio.create_task(
                client.post("/v1/context/prepare", headers=_VIEWER, json={"scope_id": scope_id, **_PREPARE})
            )
            response = await _finish_read(pending, pause, lambda: _revoke(client, binding))
            assert response.status_code == 200, response.text
            body = response.json()
            if retain:
                assert body["status"] == "ready"
                assert "Alpha retained evidence." in body["content"]
                assert "revoked private body" not in body["content"]
            else:
                assert body["status"] == "empty" and body["content"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("retain", [False, True])
def test_prepare_filters_changed_round_zero_candidates_after_expansion_degrades(tmp_path, monkeypatch, retain):
    async def scenario():
        async with _server(tmp_path) as (app, client, _):
            scope_id = await _scope(client)
            changed = await _remember(client, scope_id, "Alpha beta gamma original body.")
            await _remember(client, scope_id, "Alpha solo marker.")
            if retain:
                await _remember(client, scope_id, "Alpha beta gamma retained evidence.")
            runtime = app.state.application
            runtime.recall_sufficiency_policy = RecallSufficiencyPolicy(min_candidates=100)
            efforts = []

            async def collect(effort):
                efforts.append(effort)

            runtime._recall_effort_sink = collect
            pause = _pause_recall(monkeypatch)
            pending = asyncio.create_task(
                client.post("/v1/context/prepare", json={"scope_id": scope_id, **_PREPARE, "query": "alpha beta gamma"})
            )
            response = await _finish_read(pending, pause, lambda: _change(client, scope_id, changed, "revise"))
            assert response.status_code == 200, response.text
            assert len(efforts) == 1
            assert efforts[0].assessment == REASON_EXPANSION_FAILED
            body = response.json()
            if retain:
                assert body["status"] == "ready"
                assert "Alpha beta gamma retained evidence." in body["content"]
                assert "original body" not in body["content"]
                assert "replacement body" not in body["content"]
                assert "solo marker" not in body["content"]
            else:
                assert body["status"] == "empty" and body["content"] is None

    asyncio.run(scenario())


def test_prepare_does_not_fail_for_a_changed_candidate_unused_by_the_byte_budget(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope_id = await _scope(client)
            records = [
                await _remember(client, scope_id, f"Alpha marker{number} " + "evidence " * 150) for number in range(2)
            ]
            request = {"scope_id": scope_id, "query": "alpha", "max_bytes": 1024}
            baseline = await client.post("/v1/context/prepare", json=request)
            assert baseline.status_code == 200, baseline.text
            content = baseline.json()["content"]
            assert content is not None
            unused = [record for record in records if record["artifact"]["artifact_id"] not in content]
            assert len(unused) == 1, content
            pause = _pause_recall(monkeypatch)
            pending = asyncio.create_task(client.post("/v1/context/prepare", json=request))
            response = await _finish_read(pending, pause, lambda: _change(client, scope_id, unused[0], "revise"))
            assert response.status_code == 200, response.text
            assert response.json() == baseline.json()

    asyncio.run(scenario())


class _Reranker:
    policy_id = "test.atomic-current-candidates.v1"
    supports_atomic_memory = True

    def __init__(self):
        self.inputs = []

    async def rerank(self, _query, candidates, limit, /):
        self.inputs.append(tuple(candidate.text for candidate in candidates))
        return MemoryRerankDecision(
            selected_ranks=tuple(range(1, min(limit, len(candidates)) + 1)), usage=InferenceUsage(requests=1)
        )


@pytest.mark.parametrize("operation", ["revise", "forget", "revoke"])
@pytest.mark.parametrize("retain", [False, True])
def test_rerank_filters_candidates_changed_since_the_retrieval_snapshot(tmp_path, monkeypatch, operation, retain):
    async def scenario():
        async with _server(tmp_path) as (app, client, _):
            scope_id = await _scope(client)
            changed = await _remember(client, scope_id, "Alpha original body.")
            binding = await _grant(client, scope_id, "viewer", "scope.viewer")
            if retain:
                stable = await _remember(client, scope_id, "Alpha retained evidence.")
                await _grant(
                    client,
                    scope_id,
                    "viewer",
                    "artifact.viewer",
                    resource={
                        "type": "artifact",
                        "scope_id": scope_id,
                        "identity": {"family": "atomic-memory", "artifact_id": stable["artifact"]["artifact_id"]},
                        "selector": None,
                    },
                )
            reranker = _Reranker()
            app.state.application.atomic_memory._application.reranker = reranker
            pause = _Pause()
            rerank = ScopedAtomicMemory._rerank

            async def paused(*args, **kwargs):
                await pause.wait()
                return await rerank(*args, **kwargs)

            monkeypatch.setattr(ScopedAtomicMemory, "_rerank", paused)
            pending = asyncio.create_task(
                client.post("/v1/atomic-memory/search", headers=_VIEWER, json={"scope_id": scope_id, "query": "alpha"})
            )
            mutate = (
                (lambda: _revoke(client, binding))
                if operation == "revoke"
                else (lambda: _change(client, scope_id, changed, operation))
            )
            response = await _finish_read(pending, pause, mutate)
            assert response.status_code == 200, response.text
            texts = [hit["memory"]["text"] for hit in response.json()["hits"]]
            assert texts == (["Alpha retained evidence."] if retain else [])
            assert reranker.inputs == ([("Alpha retained evidence.",)] if retain else [])

    asyncio.run(scenario())


def test_list_pins_scope_authorization_and_records_to_one_snapshot(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope_id = await _scope(client)
            original = await _remember(client, scope_id, "Alpha authorized original body.")
            binding = await _grant(client, scope_id, "viewer", "scope.viewer")
            pause = _Pause()
            filters = AtomicMemorySecurity.filters

            async def paused(*args, **kwargs):
                result = await filters(*args, **kwargs)
                await pause.wait()
                return result

            async def mutate():
                await _revoke(client, binding)
                await _remember(client, scope_id, "Alpha new private body.")

            with monkeypatch.context() as patch:
                patch.setattr(AtomicMemorySecurity, "filters", paused)
                pending = asyncio.create_task(
                    client.post("/v1/atomic-memory/list", headers=_VIEWER, json={"scope_id": scope_id})
                )
                response = await _finish_read(pending, pause, mutate)
            assert response.status_code == 200, response.text
            assert [item["artifact"] for item in response.json()["items"]] == [original["artifact"]]
            current = await client.post("/v1/atomic-memory/list", headers=_VIEWER, json={"scope_id": scope_id})
            assert current.status_code == 200, current.text
            assert current.json()["items"] == []

    asyncio.run(scenario())
