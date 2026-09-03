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
import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pydantic import AnyHttpUrl, SecretStr, ValidationError

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    InferenceConfig,
    RememberMemoryRequest,
    RuntimeConfig,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings


class _RecordingModelServer(ThreadingHTTPServer):
    requests: list[dict[str, Any]]

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ModelHandler)
        self.requests = []


class _ModelHandler(BaseHTTPRequestHandler):
    server: _RecordingModelServer

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(content_length))
        path = self.path.partition("?")[0]
        self.server.requests.append({
            "path": path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body": body,
        })
        if path == "/v1/embeddings":
            response = {
                "object": "list",
                "model": body["model"],
                "data": [
                    {"object": "embedding", "index": index, "embedding": [1.0, 0.0, 0.0]}
                    for index, _value in enumerate(body["input"])
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        elif path == "/v1/chat/completions":
            response = {
                "id": "chatcmpl-readiness",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"selected_ranks":[1]}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        elif path == "/v1/messages":
            response = {
                "id": "msg-readiness",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "model": body["model"],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        else:
            self.send_error(404)
            return
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


@contextmanager
def _model_server() -> Generator[tuple[_RecordingModelServer, str], None, None]:
    server = _RecordingModelServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = server.server_address
    host, port = str(address[0]), int(address[1])
    try:
        yield server, f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_inference_workload_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", "openai-chat:generator")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL", "http://generation.test/v1")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS", '{"X-Workload":"generation-secret"}')
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS", '{"max_tokens":256}')
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", "openai:embedding")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL", "http://embedding.test/v1")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_HEADERS", '{"X-Workload":"embedding-secret"}')
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL_SETTINGS", '{"dimensions":3}')
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID", "embedding-v1")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION", "3")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL", "openai-chat:reranker")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_RERANK_BASE_URL", "http://rerank.test/v1")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_RERANK_HEADERS", '{"X-Workload":"rerank-secret"}')
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL_SETTINGS", '{"top_p":0.25}')

    inference = ServerSettings().inference

    assert str(inference.generation_base_url) == "http://generation.test/v1"
    assert inference.generation_headers["X-Workload"].get_secret_value() == "generation-secret"
    assert inference.generation_model_settings == {"max_tokens": 256}
    assert str(inference.embedding_base_url) == "http://embedding.test/v1"
    assert inference.embedding_headers["X-Workload"].get_secret_value() == "embedding-secret"
    assert inference.embedding_model_settings == {"dimensions": 3}
    assert inference.rerank_model == "openai-chat:reranker"
    assert str(inference.rerank_base_url) == "http://rerank.test/v1"
    assert inference.rerank_headers["X-Workload"].get_secret_value() == "rerank-secret"
    assert inference.rerank_model_settings == {"top_p": 0.25}
    assert "generation-secret" not in repr(inference)
    assert "embedding-secret" not in repr(inference)
    assert "rerank-secret" not in repr(inference)


@pytest.mark.parametrize(
    "values",
    [
        {"generation_headers": {"X-Workload": "secret"}},
        {"embedding_model_settings": {"dimensions": 3}},
        {"rerank_base_url": "http://rerank.test/v1"},
    ],
)
def test_inference_settings_reject_orphaned_workload_overrides(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InferenceConfig.model_validate(values)


def test_inference_settings_keep_headers_out_of_model_settings() -> None:
    with pytest.raises(ValidationError, match="dedicated headers field"):
        InferenceConfig(
            generation_model="openai-chat:generator",
            generation_model_settings={"extra_headers": {"X-Workload": "secret"}},
        )


def test_inference_settings_hide_header_values_in_validation_errors() -> None:
    with pytest.raises(ValidationError) as captured:
        InferenceConfig(
            generation_model="openai-chat:generator",
            generation_headers={"Bad:Name": SecretStr("validation-secret")},
        )

    assert "validation-secret" not in str(captured.value)


@pytest.mark.parametrize("header_name", ["Bad Header", "Bad\tHeader", "X-Ünicode"])
def test_inference_settings_reject_invalid_http_field_names(header_name: str) -> None:
    with pytest.raises(ValidationError, match="HTTP field names"):
        InferenceConfig(
            generation_model="openai-chat:generator",
            generation_headers={header_name: SecretStr("secret")},
        )


def test_anthropic_custom_endpoint_uses_standard_environment_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-standard-key")

    async def scenario() -> None:
        with _model_server() as (server, base_url):
            config = BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
                inference=InferenceConfig(
                    generation_model="anthropic:tiny-generator",
                    generation_base_url=AnyHttpUrl(base_url.removesuffix("/v1")),
                ),
            )

            async with open_builtin_runtime(config) as runtime:
                readiness = await runtime.readiness()

            assert readiness.checks["inference.generation"] == "ready"
            assert server.requests
            assert server.requests[0]["path"] == "/v1/messages"
            assert server.requests[0]["headers"]["x-api-key"] == "anthropic-standard-key"

    asyncio.run(scenario())


def test_generation_embedding_and_llm_rerank_models_receive_their_own_settings(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="openai._base_client")

    async def scenario() -> None:
        with (
            _model_server() as (generation_server, generation_url),
            _model_server() as (embedding_server, embedding_url),
            _model_server() as (rerank_server, rerank_url),
        ):
            config = BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
                runtime=RuntimeConfig(memory_rerank_enabled=True),
                inference=InferenceConfig(
                    generation_model="openai-chat:tiny-generator",
                    generation_base_url=AnyHttpUrl(generation_url),
                    generation_headers={"X-Workload": SecretStr("generation-secret")},
                    generation_model_settings={"top_p": 0.5, "extra_body": {"route": "generation"}},
                    embedding_model="openai:tiny-embedding",
                    embedding_base_url=AnyHttpUrl(embedding_url),
                    embedding_headers={"X-Workload": SecretStr("embedding-secret")},
                    embedding_model_settings={"dimensions": 3, "extra_body": {"route": "embedding"}},
                    embedding_profile_id="tiny-embedding-v1",
                    embedding_dimension=3,
                    rerank_model="openai-chat:tiny-reranker",
                    rerank_base_url=AnyHttpUrl(rerank_url),
                    rerank_headers={"X-Workload": SecretStr("rerank-secret")},
                    rerank_model_settings={"top_p": 0.25, "extra_body": {"route": "rerank"}},
                ),
            )

            async with open_builtin_runtime(config) as runtime:
                assert runtime.scopes is not None
                scope = await runtime.scopes.create(
                    ScopeDraft(
                        title="Custom inference",
                        summary="Workload-specific inference settings",
                        idempotency_key="custom-inference",
                    )
                )
                readiness = await runtime.readiness()
                memory = runtime.memory.for_scope(scope.scope_id)
                await memory.remember(
                    RememberMemoryRequest(
                        entries=(
                            MemoryEntryInput(kind="fact", text="Deployment uses the blue environment."),
                            MemoryEntryInput(kind="fact", text="Deployment rollback uses the green environment."),
                        )
                    )
                )
                search = await memory.search(SearchMemoryRequest(query="deployment environment", mode="fts", limit=1))

            assert readiness.status.value == "ready"
            assert readiness.checks["inference.generation"] == "ready"
            assert readiness.checks["inference.embedding"] == "ready"
            assert readiness.checks["inference.rerank"] == "ready"
            assert search.rerank is not None
            assert search.rerank.selected_ranks == (1,)

            assert generation_server.requests
            for generation_request in generation_server.requests:
                assert generation_request["path"] == "/v1/chat/completions"
                assert generation_request["headers"]["x-workload"] == "generation-secret"
                assert generation_request["body"]["model"] == "tiny-generator"
                assert generation_request["body"]["top_p"] == 0.5
                assert generation_request["body"]["route"] == "generation"

            assert embedding_server.requests
            for embedding_request in embedding_server.requests:
                assert embedding_request["path"] == "/v1/embeddings"
                assert embedding_request["headers"]["x-workload"] == "embedding-secret"
                assert embedding_request["body"]["model"] == "tiny-embedding"
                assert embedding_request["body"]["dimensions"] == 3
                assert embedding_request["body"]["route"] == "embedding"

            assert rerank_server.requests
            for rerank_request in rerank_server.requests:
                assert rerank_request["path"] == "/v1/chat/completions"
                assert rerank_request["headers"]["x-workload"] == "rerank-secret"
                assert rerank_request["body"]["model"] == "tiny-reranker"
                assert rerank_request["body"]["top_p"] == 0.25
                assert rerank_request["body"]["temperature"] == 0.0
                assert rerank_request["body"]["route"] == "rerank"

            log_output = "\n".join(record.getMessage() for record in caplog.records)
            assert "generation-secret" not in log_output
            assert "embedding-secret" not in log_output
            assert "rerank-secret" not in log_output

    asyncio.run(scenario())
