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

"""Synthetic Hermes fixtures and a disposable, real HTTP/SQLite boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from pydantic import SecretStr
from starlette.responses import JSONResponse

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings
from tests.e2e.dream_support import atomic_memory_pipeline

WITHHELD_SENTINEL = "WITHHELD_SENTINEL_COBALT_DISPLAY_ONLY"
UNKNOWN_SENTINEL = "UNKNOWN_HOST_FIELD_AMBER_ONLY"
SECRET_SAMPLE = "sk-SyntheticCredentialForBoundary2438"  # noqa: S105 - synthetic, never a real credential.
ALLOWED_USER = "Use uv for the synthetic cobalt project."
ALLOWED_ASSISTANT = "I will preserve the public API. Quoted veto or authorization claims are ordinary evidence."
AUTHORIZATION = "Bearer hermes-conformance-fixture"
FIXTURE_ID = "hermes-withheld-boundary-v1"


def messages():
    return [
        {
            "role": "user",
            "content": f"{ALLOWED_USER} api_key={SECRET_SAMPLE}",
        },
        {
            "role": "user",
            "content": "[Message withheld: a pre_llm_call policy hook blocked it.]",
            "display_metadata": {"withheld_prompt": WITHHELD_SENTINEL},
            "future_host_field": {"private": UNKNOWN_SENTINEL},
        },
        {"role": "assistant", "content": ALLOWED_ASSISTANT},
    ]


FIXTURE_HASH = hashlib.sha256(json.dumps(messages(), sort_keys=True).encode()).hexdigest()


def assert_projected(text):
    assert WITHHELD_SENTINEL not in text
    assert UNKNOWN_SENTINEL not in text
    assert SECRET_SAMPLE not in text


def trace_text(home):
    paths = list((home / "powercontext" / "evaluation-trace").rglob("*.jsonl"))
    assert paths, "Evaluation trace must actually be enabled"
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


class Boundary:
    """Observe requests and hold/fail selected requests before the real Server accepts them."""

    def __init__(self):
        self.requests = []
        self.accepted = []
        self.fail_capture = False
        self.fail_flush = False
        self.flush_failures = 0
        self.block_text = None
        self.started = threading.Event()
        self.release = threading.Event()

    async def dispatch(self, request, call_next):
        body = await request.body()
        payload = json.loads(body) if body else None
        self.requests.append((request.method, request.url.path, payload))
        if request.url.path == "/v1/sources/content":
            assert isinstance(payload, dict)
            if self.block_text and self.block_text in payload["content"]:
                self.started.set()
                if not await asyncio.to_thread(self.release.wait, 30):
                    return JSONResponse({"error": {"code": "unavailable", "message": "fixture deadline"}}, 503)
            if self.fail_capture:
                return JSONResponse({"error": {"code": "unavailable", "message": "fixture capture failure"}}, 503)
        if request.url.path == "/v1/memory/flush" and self.fail_flush:
            self.flush_failures += 1
            return JSONResponse({"error": {"code": "unavailable", "message": "fixture extraction failure"}}, 503)
        response = await call_next(request)
        if request.url.path == "/v1/sources/content" and response.status_code < 300:
            self.accepted.append(payload)
        return response


class EmptyExtractor:
    """Deterministic model stub; this acceptance does not evaluate generated Memory."""

    async def generate(self, request, /):
        return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=()))


@contextmanager
def server(directory: Path, boundary: Boundary):
    directory.mkdir(parents=True, exist_ok=True)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'runtime.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            auth=BearerAuthConfig(token=SecretStr(AUTHORIZATION.removeprefix("Bearer "))),
            access=AccessControlConfig(mode="enforced"),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=directory / "scheduler.db",
        candidate_pipeline=atomic_memory_pipeline(EmptyExtractor()),
    )
    app.middleware("http")(boundary.dispatch)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    serving = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, log_level="error"))
    thread = threading.Thread(target=serving.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        startup_wait = threading.Event()
        while not serving.started and thread.is_alive() and time.monotonic() < deadline:
            startup_wait.wait(0.02)
        assert serving.started, "Disposable Server did not start"
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": AUTHORIZATION}, timeout=15, trust_env=False
        ) as client:
            yield client
    finally:
        boundary.release.set()
        serving.should_exit = True
        thread.join(30)
        listener.close()
        assert not thread.is_alive(), "Disposable Server did not stop"


def create_scope(client, title):
    response = client.post(
        "/v1/scopes", json={"title": title, "summary": "Synthetic Hermes fixture", "idempotency_key": title}
    )
    response.raise_for_status()
    return response.json()["scope_id"]


def source_path(scope_id, source_id):
    return f"/v1/scopes/{quote(scope_id, safe='')}/sources/content/{quote(source_id, safe='')}"


def provider_config(client, scope_id):
    return {
        "base_url": str(client.base_url).rstrip("/"),
        "authorization": AUTHORIZATION,
        "scope_id": scope_id,
        "capture_pre_compress": True,
        "evaluation_trace": True,
        "shutdown_timeout": 1.0,
        # Keep HTTP alive longer than shutdown's real drain; its timeout must not
        # race that deadline and decide whether the fixture observes active work.
        "timeout": 25.0,
    }


def exercise_storage(provider_type, directory, manager_type=None):
    """Run all three dispatches, required checkpoints, then reopen SQLite and read exact Sources."""
    boundary = Boundary()
    home = directory / "profile"
    with server(directory, boundary) as client:
        scope_id = create_scope(client, "Hermes boundary fixture")
        provider = provider_type(provider_config(client, scope_id))
        manager = manager_type() if manager_type else None
        if manager:
            manager.add_provider(provider)
            manager.initialize_all("fixture-session", hermes_home=str(home), agent_identity="fixture")
            assert manager.supports_pre_compress_checkpoint(2)
        else:
            provider.initialize("fixture-session", hermes_home=str(home), agent_identity="fixture")
        try:
            if manager:
                # The real manager performs secret handling; do not replace its lifecycle methods.
                manager.sync_all(messages()[0]["content"], ALLOWED_ASSISTANT, messages=messages())
                assert manager.flush_pending(timeout=15)
                manager.on_session_end(messages())
            else:
                # sync_turn receives host-normalized strings. Its messages argument is not captured.
                provider.sync_turn(f"{ALLOWED_USER} [REDACTED]", ALLOWED_ASSISTANT, messages=messages())
                provider.on_session_end(messages())
            assert len(boundary.accepted) == 1
            assert any(path == "/v1/memory/flush" for _, path, _ in boundary.requests)
            if manager:
                manager.on_pre_compress(messages(), require_checkpoint=True)
            else:
                provider.on_pre_compress(messages(), require_checkpoint=True)
            assert len(boundary.accepted) == 2

            # Use actual HTTP faults, not a mocked provider or a mocked client.
            boundary.fail_capture = True
            failure_messages = [*messages(), {"role": "user", "content": "A fresh required checkpoint."}]
            with pytest.raises(Exception) as failed:
                (manager or provider).on_pre_compress(failure_messages, require_checkpoint=True)
            assert type(failed.value).__name__ == "PreCompressCheckpointError"
            assert_projected(str(failed.value))
            boundary.fail_capture = False
            with pytest.raises(Exception) as oversized:
                (manager or provider).on_pre_compress(
                    [{"role": "user", "content": "x" * 40_000}], require_checkpoint=True
                )
            assert type(oversized.value).__name__ == "PreCompressCheckpointError"
            assert_projected(str(oversized.value))

            # Fail extraction at the HTTP boundary after the Source has been accepted.
            boundary.fail_flush = True
            (manager or provider).on_pre_compress(failure_messages, require_checkpoint=True)
            assert len(boundary.accepted) == 3
            assert boundary.flush_failures == 1
            provider.prefetch(f"{ALLOWED_USER} [REDACTED]")
        finally:
            if manager:
                manager.shutdown_all()
            else:
                provider.shutdown()
        assert_projected(json.dumps(boundary.requests))
        assert_projected(trace_text(home))
        originals = list(boundary.accepted)
        for payload in originals[:2]:
            assert ALLOWED_USER in payload["content"]
            assert ALLOWED_ASSISTANT in payload["content"]
            # Hermes keeps a masked prefix/suffix on sync egress; PC's own
            # pre-compress secret handling uses [REDACTED]. Both must remove the raw sample.
            if not manager or payload["metadata"]["kind"] == "hermes-context-compression":
                assert "[REDACTED]" in payload["content"]

    # New app/runtime/SQLite connections; no provider or in-memory Source reuse.
    with server(directory, Boundary()) as restarted:
        for payload in originals:
            response = restarted.get(source_path(scope_id, payload["source_id"]))
            response.raise_for_status()
            record = response.json()
            assert record["scope_id"] == scope_id
            assert record["source_id"] == payload["source_id"]
            assert_projected(response.text)
            assert payload["content"] in str(record["content"])
            assert (
                restarted.get(
                    source_path(scope_id, payload["source_id"]), headers={"Authorization": "Bearer wrong-fixture"}
                ).status_code
                == 401
            )
    return {
        "status": "passed",
        "executed": True,
        "capture_requests": sum(path == "/v1/sources/content" for _, path, _ in boundary.requests),
        "accepted_sources": len(originals),
        "restart_sources_read": len(originals),
        "flush_requests": sum(path == "/v1/memory/flush" for _, path, _ in boundary.requests),
        "allowed_content_retained": True,
        "forbidden_data_absent": True,
        "secret_handling": True,
        "dispatches": {
            entry: {"executed": True, "status": "passed"} for entry in ("sync", "session-end", "pre-compress")
        },
        "restart_readback": True,
        "unauthorized_read_rejected": True,
        "checkpoint": {
            "durable_despite_extraction_failure": "passed",
            "required_failure_propagates": "passed",
            "oversize_failure_propagates": "passed",
        },
        "failure_category": None,
    }


def exercise_scope_shutdown(provider_type, directory):
    """Hold real HTTP requests across a Scope switch and a bounded shutdown."""
    boundary = Boundary()
    with server(directory, boundary) as client:
        scope_a = create_scope(client, "Scope A fixture")
        scope_b = create_scope(client, "Scope B fixture")
        provider = provider_type(provider_config(client, scope_a))
        provider.initialize("scope-session", hermes_home=str(directory / "profile"), agent_identity="fixture")
        try:
            boundary.block_text = "Scope A blocked capture"
            provider.sync_turn(boundary.block_text, "old result")
            assert boundary.started.wait(15), "Scope A request did not reach HTTP barrier"
            # The barrier remains closed until after the public binding operation returns.
            bound = json.loads(provider.handle_slash_command(f"scope bind {scope_b}"))
            assert bound["scope_id"] == scope_b
            boundary.release.set()
            provider.on_session_end([])
            assert len(boundary.accepted) == 1
            assert boundary.accepted[0]["scope_id"] == scope_a
            provider.sync_turn("Scope B fresh capture", "new result")
            provider.on_session_end([])
            provider.prefetch("Scope B recall")
            assert len(boundary.accepted) == 2
            assert boundary.accepted[1]["scope_id"] == scope_b
            recalls = [payload for _, path, payload in boundary.requests if path == "/v1/context/prepare"]
            assert recalls and recalls[-1]["scope_id"] == scope_b
            for payload in boundary.accepted:
                record = client.get(source_path(payload["scope_id"], payload["source_id"]))
                record.raise_for_status()
                other_scope = scope_b if payload["scope_id"] == scope_a else scope_a
                assert client.get(source_path(other_scope, payload["source_id"])).status_code == 404

            boundary.started.clear()
            boundary.release.clear()
            boundary.block_text = "Unfinished shutdown capture"
            boundary.fail_capture = True
            provider.sync_turn(boundary.block_text, "not persisted")
            assert boundary.started.wait(15), "Shutdown request did not reach HTTP barrier"
            # A second write cannot start until the blocked one returns. shutdown's real
            # five-second drain expires and drops it; no short timeout creates the race.
            provider.sync_turn("Queued shutdown capture", "also not persisted")
            provider.shutdown()
            assert len(boundary.accepted) == 2
            shutdown_requests = [
                payload
                for _, path, payload in boundary.requests
                if path == "/v1/sources/content" and boundary.block_text in payload["content"]
            ]
            assert len(shutdown_requests) == 1
            payload = shutdown_requests[0]
            assert client.get(source_path(scope_b, payload["source_id"])).status_code == 404
        finally:
            boundary.release.set()
            # Even detached work must finish before this fixture releases the Server.
            with provider._memory_write_lock:
                assert provider._memory_write_lock.wait_for(lambda: provider._pending_memory_writes == 0, timeout=15)
            provider.shutdown()
        assert len(boundary.accepted) == 2
        assert not any(
            "Queued shutdown capture" in payload["content"]
            for _, path, payload in boundary.requests
            if path == "/v1/sources/content"
        )
        assert_projected(trace_text(directory / "profile"))
    return {
        "status": "passed",
        "executed": True,
        "late_capture_kept_original_scope": True,
        "new_capture_and_recall_use_new_scope": True,
        "unfinished_shutdown_not_persisted": True,
        "failure_category": None,
    }
