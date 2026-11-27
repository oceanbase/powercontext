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

"""Session Source acceptance through the capture, read and processing APIs."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference import GenerationResult, InferenceUnavailableError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.portability import BundleFormatError
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.scope.errors import ScopeNotFoundError
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentCapture
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.dream_support import atomic_memory_pipeline

CAPTURE = "/v1/sources/content"
MARKER = "powercontext.session.v1"
LIVE = [
    {"role": "user", "content": "  保留原始行\r\n😀  "},
    {"role": "assistant", "content": 'I changed it.\nuser: "Verified."'},
]
HISTORY = [*LIVE, {"role": "user", "content": "Wrong file. Continue with the original."}]


def _session(messages, *, session_id="会话-1"):
    # Preserve significant JSON whitespace, Unicode, and an escaped newline on readback.
    return (
        " \n"
        + json.dumps({"type": MARKER, "session_id": session_id, "messages": messages}, ensure_ascii=False, indent=1)
        + "\n"
    )


def _app(tmp_path, extractor=None, *, profile_generator=None):
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=None if extractor is None else atomic_memory_pipeline(extractor),
        profile_generator=profile_generator,
    )


def _scope(client):
    response = client.post(
        "/v1/scopes",
        json={"title": "Session capture", "summary": "Session capture tests", "idempotency_key": "session-tests"},
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


def _capture(client, scope, source_id, content, **extra):
    return client.post(CAPTURE, json={"scope_id": scope, "source_id": source_id, "content": content, **extra})


def _batch(client, scope, *captures):
    return client.post(
        f"{CAPTURE}/batch",
        json={
            "scope_id": scope,
            "sources": [{"source_id": source_id, "content": content} for source_id, content in captures],
        },
    )


def _detail(client, scope, source_id):
    return client.get(f"/v1/scopes/{scope}/sources/content/{source_id}")


def test_capture_preserves_session_source_through_restart_and_replay(tmp_path):
    live, history = _session(LIVE), _session(HISTORY)
    metadata = {"subject": "测试"}
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        captured = _capture(client, scope, "live", live, metadata=metadata)
        assert captured.status_code == 202, captured.text
        batch = _batch(client, scope, ("opaque", '{"type":"custom.v1"}'), ("history", history))
        assert batch.status_code == 202, batch.text
        assert [receipt["position"] for receipt in batch.json()["sources"]] == [2, 3]

    with TestClient(_app(tmp_path)) as client:
        detail = _detail(client, scope, "live")
        assert detail.status_code == 200, detail.text
        assert detail.json()["content"] == live
        assert detail.json()["metadata"] == metadata
        replayed = _capture(client, scope, "live", live, metadata=metadata)
        assert replayed.json() == captured.json()
        assert _capture(client, scope, "live", live, metadata={}).status_code == 409
        assert _capture(client, scope, "live", live.strip(), metadata=metadata).status_code == 409
        replayed_batch = _batch(client, scope, ("opaque", '{"type":"custom.v1"}'), ("history", history))
        assert replayed_batch.json() == batch.json()
        page = client.get(f"/v1/scopes/{scope}/sources").json()
        assert [item["content"] for item in page["items"]] == [live, '{"type":"custom.v1"}', history]
        assert json.loads(page["items"][2]["content"])["messages"] == HISTORY


INVALID = [
    ('{"type":"powercontext.session.v1",', "valid JSON"),
    ('{"type":"powercontext.session.v2"}', "unsupported"),
    ('{"type":"powercontext.session.v1","type":"other"}', "repeat object keys"),
    (_session([{"role": "system", "content": "SENSITIVE-BODY"}]), "user or assistant"),
    (_session([{"role": "user", "content": "   "}]), "nonblank"),
    (_session([{"role": "user", "content": "\ud800"}]).replace("\ud800", r"\ud800"), "UTF-8"),
    (_session([]), "session messages"),
    (_session(LIVE, session_id=" "), "session_id"),
    (_session(LIVE, session_id="x" * 257), "session_id"),
    (_session([LIVE[0]] * 513), "512 messages"),
    (_session([{"role": "user", "content": "x" * 48_000}]), "48000 characters"),
]


@pytest.mark.parametrize(
    ("content", "reason"),
    INVALID,
    ids=[
        "malformed",
        "version",
        "duplicate",
        "role",
        "blank",
        "surrogate",
        "empty",
        "session-id",
        "long-id",
        "message-count",
        "size",
    ],
)
def test_rejects_invalid_declared_sessions(tmp_path, content, reason):
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        response = _capture(client, scope, "invalid", content)
        assert response.status_code == 422, response.text
        assert reason in response.json()["error"]["details"]["reason"]
        assert "SENSITIVE-BODY" not in response.text
        assert _detail(client, scope, "invalid").status_code == 404


def test_batch_rolls_back_validation_and_identity_conflict(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        assert _capture(client, scope, "existing", _session(LIVE)).status_code == 202
        assert _batch(client, scope, ("plain", "plain text"), ("wrong", INVALID[0][0])).status_code == 422
        assert _batch(client, scope, ("plain", "plain text"), ("existing", _session(HISTORY))).status_code == 409
        assert _detail(client, scope, "plain").status_code == 404
        accepted = _batch(
            client,
            scope,
            ("plain", "Example of invalid JSON: {"),
            ("nested", '{"data":{"type":"powercontext.session.v2"}}'),
            ("malformed", '{"ordinary":'),
        )
        assert accepted.status_code == 202, accepted.text
        assert [item["position"] for item in accepted.json()["sources"]] == [2, 3, 4]


class _Extractor:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.inputs: list[AtomicMemoryExtractionInput] = []

    async def generate(self, value: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        self.inputs.append(value)
        if self.fail:
            raise InferenceUnavailableError("generate")
        return GenerationResult(output=AtomicMemoryExtractionOutput())


def test_processing_retries_the_same_role_aware_evidence_after_restart(tmp_path):
    raw = _session(HISTORY)
    failing = _Extractor(fail=True)
    with TestClient(_app(tmp_path, failing)) as client:
        scope = _scope(client)
        assert _batch(client, scope, ("context", raw), ("plain", "A plain source.")).status_code == 202
        failed = client.post("/v1/memory/flush", json={"scope_id": scope})
        assert failed.status_code == 503, failed.text

    succeeding = _Extractor()
    with TestClient(_app(tmp_path, succeeding)) as client:
        retried = client.post("/v1/memory/flush", json={"scope_id": scope})
        assert retried.status_code == 200, retried.text
        assert retried.json()["current_cursor"] == retried.json()["processed_source_count"] == 2
        assert _detail(client, scope, "context").json()["content"] == raw
        no_work = client.post("/v1/memory/flush", json={"scope_id": scope}).json()
        assert no_work["status"] == "idle"

    assert failing.inputs == succeeding.inputs
    (session, plain) = succeeding.inputs[0].evidence
    assert session.source_ref.source_id == "context"
    assert session.content == json.loads(raw)
    assert isinstance(session.source_metadata, dict) and "content" not in session.source_metadata
    assert isinstance(plain.content, dict) and plain.content["content"] == "A plain source."


@pytest.mark.parametrize("raw", [_session(LIVE), INVALID[3][0]], ids=["valid-shape", "invalid-shape"])
def test_existing_opaque_session_shaped_text_keeps_its_evidence_on_replay(tmp_path, raw):
    metadata = {}
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)

    async def seed_legacy_capture():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"))
        async with open_builtin_contexts(config) as contexts:
            return await contexts.records.capture_source(scope, "content", "legacy", raw, metadata)

    legacy = asyncio.run(seed_legacy_capture())
    extractor = _Extractor()
    with TestClient(_app(tmp_path, extractor)) as client:
        replay = _capture(client, scope, "legacy", raw, metadata=metadata)
        assert replay.status_code == 202, replay.text
        assert replay.json()["position"] == legacy.position
        batch = _batch(client, scope, ("legacy", raw))
        assert batch.status_code == 202 and batch.json()["sources"][0]["position"] == legacy.position
        assert _capture(client, scope, "legacy", raw, metadata={"changed": True}).status_code == 409
        assert client.post("/v1/memory/flush", json={"scope_id": scope}).status_code == 200
    (evidence,) = extractor.inputs[0].evidence
    assert isinstance(evidence.content, dict) and evidence.content["content"] == raw


def test_session_serialized_limit_counts_escaping_and_unicode(tmp_path):
    empty = _session([{"role": "user", "content": ""}])
    message = {"role": "user", "content": "界" * (48_000 - len(empty))}
    fits = _session([message])
    assert len(fits) == 48_000
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        assert _capture(client, scope, "fits", fits).status_code == 202
        assert _detail(client, scope, "fits").json()["content"] == fits
        overflow = _session([{"role": "user", "content": '"' + message["content"][1:]}])
        assert len(overflow) == 48_001
        assert _capture(client, scope, "overflow", overflow).status_code == 422


def test_session_combined_limit_does_not_block_the_default_extraction_window(tmp_path):
    empty = _session([{"role": "user", "content": ""}])
    raw = _session([{"role": "user", "content": "界" * (48_000 - len(empty))}])
    metadata = {"text": "界" * (8_000 - len('{"text":""}'))}
    extractor = _Extractor()
    with TestClient(_app(tmp_path, extractor)) as client:
        scope = _scope(client)
        oversized_metadata = {"text": metadata["text"] + "界"}
        rejected = client.post(
            f"{CAPTURE}/batch",
            json={
                "scope_id": scope,
                "sources": [
                    {"source_id": "plain", "content": "A plain source."},
                    {"source_id": "over", "content": raw, "metadata": oversized_metadata},
                ],
            },
        )
        assert rejected.status_code == 422, rejected.text
        assert "56000 characters" in rejected.json()["error"]["details"]["reason"]
        assert _detail(client, scope, "plain").status_code == 404

        captured = _capture(client, scope, "fits", raw, metadata=metadata)
        assert captured.status_code == 202 and captured.json()["position"] == 1
        assert _detail(client, scope, "fits").json()["metadata"] == metadata
        flush = client.post("/v1/memory/flush", json={"scope_id": scope})
        assert flush.status_code == 200 and flush.json()["current_cursor"] == 1
    assert extractor.inputs[0].evidence[0].content == json.loads(raw)


def test_restore_rejects_an_incompatible_marked_session_before_writes(tmp_path):
    raw = INVALID[3][0]
    archive = tmp_path / "foreign.pcb"

    async def scenario():
        async with open_builtin_contexts(BuiltinConfig()) as foreign:
            scope = (
                await foreign.scopes.create(
                    ScopeDraft(
                        title="Foreign",
                        summary="Portable fixture",
                        idempotency_key="foreign",
                    )
                )
            ).scope_id
            capture = ContentCapture(source_id="foreign-session", content=raw)
            # Simulate a producer whose session contract accepts a role unsupported by this reader.
            source = (await CONTENT_SOURCE_ADAPTER.resolve(capture)).model_copy(update={"session_input": True})
            await (await foreign.get(scope)).sources.add(source)

            async def authorize(scopes):
                assert scopes == (scope,)

            await foreign.portability.export([scope], archive, authorize=authorize)

        async with open_builtin_contexts(BuiltinConfig()) as current:
            with pytest.raises(BundleFormatError, match="does not match the target Source decoder"):
                await current.portability.restore(archive)
            with pytest.raises(ScopeNotFoundError):
                await current.scopes.get(scope)

    asyncio.run(scenario())


def test_corrupt_stored_session_reports_storage_failure(tmp_path, caplog):
    with TestClient(_app(tmp_path, _Extractor())) as client:
        scope = _scope(client)
        assert _capture(client, scope, "session", _session(LIVE)).status_code == 202
        with sqlite3.connect(tmp_path / "runtime.db") as connection:
            (payload,) = connection.execute("SELECT payload FROM pc_sources").fetchone()
            document = json.loads(payload)
            document["value"]["content"] = INVALID[3][0]
            connection.execute("UPDATE pc_sources SET payload = ?", (json.dumps(document).encode(),))
        failure = client.post("/v1/memory/flush", json={"scope_id": scope})
        assert failure.status_code == 500
        assert "SENSITIVE-BODY" not in failure.text + caplog.text
        assert "payload does not match the model" in caplog.text


def test_profile_receives_structured_roles_and_plain_text(tmp_path):
    class ProfileGenerator:
        async def generate(self, value):
            requests.append(value.model_dump(mode="json"))
            return None

    requests = []
    with TestClient(_app(tmp_path, profile_generator=ProfileGenerator())) as client:
        scope = _scope(client)
        assert (
            _batch(client, scope, ("session", _session(LIVE)), ("plain", "A plain profile observation.")).status_code
            == 202
        )
        enabled = client.put(
            f"/v1/scopes/{scope}/profile-policy", json={"generation_enabled": True, "expected_version": 0}
        )
        assert enabled.status_code == 200, enabled.text
        result = client.post("/v1/profile/flush", json={"scope_id": scope})
        assert result.status_code == 200, result.text
    session, plain = requests[0]["sources"]
    assert session == json.loads(_session(LIVE))
    assert json.loads(plain)["content"] == "A plain profile observation."
