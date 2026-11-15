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

"""Observation preserves provider recovery while inspecting each rendered attempt."""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx2
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.inference.protected_transport import ProtectedAsyncTransport
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GenerateExperienceRequest,
    InferenceConfig,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

_MODEL = "retry-model"
_FORBIDDEN = "synthetic-retry-evidence"
_PROPOSAL = ExperienceContent(
    situation="A model endpoint returned a temporary rate limit.",
    action="Retry according to the provider policy.",
    outcome="The following request completed.",
    lesson="Observation must preserve the configured retry behavior.",
)


class _Server(ThreadingHTTPServer):
    requests: list[bytes]


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_POST(self) -> None:
        self.server.requests.append(self.rfile.read(int(self.headers["Content-Length"])))
        status = 429 if len(self.server.requests) == 1 else 200
        if status == 429:
            response = {"type": "error", "error": {"type": "rate_limit_error", "message": "busy"}}
        elif self.path.partition("?")[0].endswith("/messages"):
            response = {
                "id": "retry-message",
                "type": "message",
                "role": "assistant",
                "model": _MODEL,
                "content": [{"type": "text", "text": json.dumps({"proposal": _PROPOSAL.model_dump()})}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 11, "output_tokens": 13},
            }
        else:
            response = {
                "id": "retry-completion",
                "object": "chat.completion",
                "created": 0,
                "model": _MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps({"proposal": _PROPOSAL.model_dump()})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 13, "total_tokens": 24},
            }
        payload = json.dumps(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if status == 429:
            self.send_header("Retry-After", "0.001")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - standard-library signature
        pass


@pytest.fixture
def upstream():
    server = _Server(("127.0.0.1", 0), _Handler)
    server.requests = []
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _protection(base_url, provider, mode):
    return InferenceProtectionConfig(
        mode=mode,
        forbidden_values=(SecretStr(_FORBIDDEN),),
        targets={
            "model": UpstreamProfile(
                endpoint=base_url + ("/v1/messages" if provider == "anthropic" else "/chat/completions"),
                model=_MODEL,
                protocol="anthropic-messages" if provider == "anthropic" else "openai-chat",
                context_window_tokens=32768,
                max_output_tokens=1024,
                validated_utf8_token_bound=True,
            )
        },
    )


@pytest.mark.parametrize("provider", ["openai-chat", "anthropic"])
@pytest.mark.parametrize("mode", ["disabled", "shadow", "advisory"])
def test_public_generation_preserves_provider_rate_limit_recovery(upstream, monkeypatch, provider, mode):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-retry-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-retry-key")

    async def scenario():
        base_url = f"http://127.0.0.1:{upstream.server_port}" + ("" if provider == "anthropic" else "/v1")
        config = BuiltinConfig(
            database=SQLiteConfig(),
            inference=InferenceConfig(
                generation_model=provider + ":" + _MODEL,
                generation_base_url=AnyHttpUrl(base_url),
                generation_model_settings={"max_tokens": 1024},
                protection=_protection(base_url, provider, mode),
            ),
            runtime=RuntimeConfig(profile_schedule_enabled=False, dream_enabled=False, artifact_processing_families=()),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Provider retry", summary="Observation reliability", idempotency_key="retry")
            )
            captured = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="retry-evidence", content=_FORBIDDEN, metadata={})
            )
            result = await runtime.experience.for_scope(scope.scope_id).generate(
                GenerateExperienceRequest(sources=(captured.source_ref,))
            )
            assert result.candidate is not None
            assert result.candidate.proposal == _PROPOSAL
            assert result.candidate.sources == (captured.source_ref,)
            assert len(upstream.requests) == 2
            assert upstream.requests[0] == upstream.requests[1]

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["openai-chat", "anthropic"])
@pytest.mark.parametrize("mode", ["shadow", "advisory"])
def test_observation_inspects_each_native_sdk_retry(upstream, provider, mode):
    async def scenario():
        base_url = f"http://127.0.0.1:{upstream.server_port}" + ("" if provider == "anthropic" else "/v1")
        observations = []
        transport = ProtectedAsyncTransport(_protection(base_url, provider, mode), observer=observations.append)
        async with httpx2.AsyncClient(transport=transport) as http:
            if provider == "anthropic":
                async with AsyncAnthropic(base_url=base_url, api_key="synthetic-retry-key", http_client=http) as sdk:
                    await sdk.messages.create(
                        model=_MODEL, messages=[{"role": "user", "content": _FORBIDDEN}], max_tokens=1024
                    )
            else:
                async with AsyncOpenAI(base_url=base_url, api_key="synthetic-retry-key", http_client=http) as sdk:
                    await sdk.chat.completions.create(
                        model=_MODEL, messages=[{"role": "user", "content": _FORBIDDEN}], max_tokens=1024
                    )
        assert len(upstream.requests) == 2
        assert [(item.phase, item.decision, item.reason_codes) for item in observations] == [
            ("before_send", "block", ("forbidden_content",)),
            ("before_send", "block", ("forbidden_content",)),
        ]

    asyncio.run(scenario())
