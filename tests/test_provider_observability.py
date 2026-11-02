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

"""Provider observations count real HTTP attempts independently of model results."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families
from pydantic import AnyHttpUrl

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.inference.errors import InferenceUnavailableError
from powercontext.builtin.inference.observability import (
    ProviderObservationSource,
    observe_http_client,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.metrics import ServerMetrics


@pytest.mark.parametrize("phase", [None, "before_send", "before_release"])
def test_http_observations_preserve_the_privacy_send_and_release_boundaries(phase):
    import httpx
    from pydantic import SecretStr

    from powercontext.builtin.inference.errors import InferencePrivacyError
    from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
    from powercontext.builtin.inference.protected_transport import ProtectedAsyncTransport

    secret = "synthetic-forbidden-value"  # noqa: S105 - privacy fixture.
    endpoint = "https://example.invalid/v1/chat/completions"
    configuration = InferenceProtectionConfig(
        mode="enforcing",
        forbidden_values=(SecretStr(secret),),
        targets={
            "fixture": UpstreamProfile(
                endpoint=endpoint,
                model="test-model",
                protocol="openai-chat",
                context_window_tokens=100000,
                max_output_tokens=256,
                validated_utf8_token_bound=True,
            )
        },
    )
    observations = []

    def response(_request):
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": secret if phase == "before_release" else "safe"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=ProtectedAsyncTransport(configuration, transport=httpx.MockTransport(response))
        ) as client:
            observe_http_client(client)
            source = ProviderObservationSource("openai", "generation", observations.append)
            with source.operation():
                return await client.post(
                    endpoint,
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": secret if phase == "before_send" else "safe"}],
                        "max_tokens": 10,
                    },
                )

    if phase is None:
        assert asyncio.run(run()).status_code == 200
    else:
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(run())
        assert caught.value.phase == phase
    exchanges = [row for row in observations if row.kind == "http"]
    assert len(exchanges) == (0 if phase == "before_send" else 1)
    if exchanges:
        assert exchanges[0].status_code == 200 and exchanges[0].outcome == "success"
    operations = [row for row in observations if row.kind == "operation"]
    assert len(operations) == 1
    assert operations[0].outcome == ("success" if phase is None else "failure")
    assert secret not in repr(observations)


@contextmanager
def _provider(statuses: tuple[int, ...], *, business_code: int = 0) -> Iterator[tuple[str, list[int]]]:
    observed: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature.
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status = statuses[min(len(observed), len(statuses) - 1)]
            observed.append(status)
            if status >= 400:
                result = {"error": {"message": "private-provider-response", "type": "rate_limit"}}
            elif "texts" in body:
                result = {
                    "vectors": [[1.0, 0.0, 0.0] for _ in body["texts"]],
                    "base_resp": {"status_code": business_code, "status_msg": "private-provider-response"},
                    "total_tokens": 1,
                }
            else:
                result = {
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [1.0, 0.0, 0.0]}
                        for i, _ in enumerate(body["input"])
                    ],
                    "model": "private-model-name",
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }
            encoded = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Retry-After", "0.01")
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _count(metrics: ServerMetrics, name: str, **labels: str) -> float:
    return sum(
        sample.value
        for family in text_string_to_metric_families(metrics.render().decode())
        for sample in family.samples
        if sample.name == "powercontext_server_provider_" + name
        and all(sample.labels.get(key) == value for key, value in labels.items())
    )


def _config(tmp_path, endpoint: str, provider: str = "openai") -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'provider.db'}"),
        runtime=RuntimeConfig(artifact_processing_families=()),
        inference=InferenceConfig(
            embedding_model=provider + ":private-model-name",
            embedding_base_url=AnyHttpUrl(endpoint),
            embedding_profile_id="private-profile-name",
            embedding_dimension=3,
        ),
    )


def test_worker_reports_expose_both_window_baselines_before_the_first_report():
    metrics = ServerMetrics()

    def reports():
        return {
            sample.labels["outcome"]: sample.value
            for family in text_string_to_metric_families(metrics.render().decode())
            for sample in family.samples
            if sample.name == "powercontext_server_provider_worker_reports_total"
        }

    assert reports() == {"received": 0, "missing": 0}
    metrics.observe_worker_provider(None)
    assert reports() == {"received": 0, "missing": 1}


async def _create(runtime):
    scope = await runtime.scopes.create(ScopeDraft(title="Provider test", summary="Isolated", idempotency_key="test"))
    return await runtime.atomic_memory.for_scope(scope.scope_id).create((
        AtomicMemoryContent(kind="fact", text="private-memory-content"),
    ))


def test_sdk_retries_have_three_http_attempts_and_one_final_success(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-fixture-credential")
    metrics = ServerMetrics()
    with _provider((429, 429, 200)) as (endpoint, requests):

        async def scenario():
            async with open_builtin_runtime(
                _config(tmp_path, endpoint),
                scheduler_path=tmp_path / "scheduler.db",
                provider_observer=metrics.observe_provider,
            ) as runtime:
                result = await _create(runtime)
                assert result.changed
                assert len(result.records) == 1

        asyncio.run(scenario())
    assert requests == [429, 429, 200]
    assert _count(metrics, "http_attempts_total", execution="parent") == 3
    assert _count(metrics, "http_attempts_total", retry="retry") == 2
    assert _count(metrics, "http_responses_total", status_code="429") == 2
    assert _count(metrics, "http_responses_total", status_code="200") == 1
    assert _count(metrics, "operations_total", outcome="success") == 1
    rendered = metrics.render().decode()
    for private in (
        "private-memory-content",
        "private-model-name",
        "private-profile-name",
        "private-fixture-credential",
    ):
        assert private not in rendered


def test_minimax_http_success_and_business_failure_are_separate(tmp_path):
    metrics = ServerMetrics()
    with _provider((200,), business_code=1004) as (endpoint, requests):

        async def scenario():
            async with open_builtin_runtime(
                _config(tmp_path, endpoint, "minimax"),
                scheduler_path=tmp_path / "scheduler.db",
                provider_observer=metrics.observe_provider,
            ) as runtime:
                with pytest.raises(InferenceUnavailableError):
                    await _create(runtime)

        asyncio.run(scenario())
    assert requests == [200]
    assert _count(metrics, "http_responses_total", provider="minimax", status_code="200") == 1
    assert _count(metrics, "business_errors_total", provider="minimax") == 1
    assert _count(metrics, "operations_total", provider="minimax", outcome="failure") == 1
    assert "private-provider-response" not in metrics.render().decode()


def test_readiness_traffic_is_excluded_from_model_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-readiness-fixture")
    metrics = ServerMetrics()
    with _provider((200,)) as (endpoint, requests):

        async def scenario():
            async with open_builtin_runtime(
                _config(tmp_path, endpoint),
                scheduler_path=tmp_path / "scheduler.db",
                provider_observer=metrics.observe_provider,
            ) as runtime:
                await runtime.readiness()
                assert _count(metrics, "http_attempts_total") == 0
                assert _count(metrics, "operations_total") == 0
                await _create(runtime)
                assert _count(metrics, "operations_total", outcome="success") == 1

        asyncio.run(scenario())
    assert len(requests) >= 2


def test_failed_observer_cannot_change_a_successful_model_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-failure-fixture")

    def failed_observer(_observation):
        raise RuntimeError

    with _provider((200,)) as (endpoint, _):

        async def scenario():
            async with open_builtin_runtime(
                _config(tmp_path, endpoint),
                scheduler_path=tmp_path / "scheduler.db",
                provider_observer=failed_observer,
            ) as runtime:
                assert (await _create(runtime)).changed

        asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "connection", "cancelled", "body"])
def test_transport_failures_do_not_fabricate_response_status(failure):
    metrics = ServerMetrics()

    class FailedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadTimeout("controlled-body-failure")

    async def respond(request):
        if failure == "body":
            return httpx.Response(200, stream=FailedBody())
        if failure == "timeout":
            raise httpx.ReadTimeout("controlled-timeout")
        if failure == "connection":
            raise httpx.ConnectError("controlled-connection")
        raise asyncio.CancelledError

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            observe_http_client(client)
            error = asyncio.CancelledError if failure == "cancelled" else httpx.HTTPError
            with (
                pytest.raises(error),
                ProviderObservationSource("openai", "generation", metrics.observe_provider).operation(),
            ):
                await client.get("https://provider.invalid")

    asyncio.run(scenario())
    assert _count(metrics, "http_attempts_total") == 1
    assert _count(metrics, "operations_total") == 1
    assert _count(metrics, "http_responses_total") == (1 if failure == "body" else 0)
    if failure == "body":
        assert _count(metrics, "http_responses_total", status_code="200") == 1
        assert _count(metrics, "operations_total", outcome="timeout") == 1
    if failure == "cancelled":
        assert _count(metrics, "http_attempts_total", outcome="cancelled") == 1


def test_observing_a_client_preserves_mount_headers_timeout_and_redirects():
    metrics = ServerMetrics()
    requested = []

    async def direct(request):
        pytest.fail("A configured provider mount must not be bypassed")

    async def mounted(request):
        requested.append(request)
        if request.url.path == "/first":
            return httpx.Response(307, headers={"Location": "/second"})
        return httpx.Response(200, json={"result": "ok"})

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(direct),
            mounts={"https://provider.invalid": httpx.MockTransport(mounted)},
            timeout=httpx.Timeout(7, connect=2),
            follow_redirects=True,
            headers={"X-Fixture": "private-header"},
        ) as client:
            before = client.timeout
            observe_http_client(client)
            assert client.timeout == before
            with ProviderObservationSource("openai", "generation", metrics.observe_provider).operation():
                assert (await client.get("https://provider.invalid/first")).json() == {"result": "ok"}

    asyncio.run(scenario())
    assert len(requested) == 2
    assert all(request.headers["X-Fixture"] == "private-header" for request in requested)
    assert _count(metrics, "http_responses_total", status_code="307") == 1
    assert _count(metrics, "http_responses_total", status_code="200") == 1
    assert _count(metrics, "http_attempts_total", retry="unknown") == 2
    assert _count(metrics, "http_attempts_total", retry="retry") == 0
    assert "private-header" not in metrics.render().decode()


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_provider_log_fields_survive_both_configured_formats(log_format):
    script = f"""
from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import ServerLoggingConfig
from powercontext.builtin.inference.observability import ProviderObservationSource
configure_server_logging(ServerLoggingConfig(format={log_format!r}))
source = ProviderObservationSource("minimax", "embedding", lambda observation: None)
source.emit("http", outcome="failure", status_code=429, retry="retry")
source.emit("business", outcome="failure", business_code=1004)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True, timeout=15)
    output = result.stdout + result.stderr
    if log_format == "json":
        rows = [json.loads(line) for line in output.splitlines()]
        assert rows[0]["provider"] == "minimax"
        assert rows[0]["model_capability"] == "embedding"
        assert rows[0]["provider_retry"] == "retry"
        assert rows[0]["status_code"] == 429
        assert rows[1]["provider_error_code"] == 1004
    else:
        assert 'provider="minimax"' in output
        assert 'model_capability="embedding"' in output
        assert 'provider_retry="retry"' in output
        assert "provider_error_code=1004" in output


def test_bounded_worker_aggregation_preserves_histograms_and_reports_loss():
    from powercontext.builtin.inference.observability import ProviderObservation, ProviderObservationCollector

    collector = ProviderObservationCollector(max_keys=1)
    collector.observe(ProviderObservation("http", "openai", "embedding", "success", 0.01, 200, "initial"))
    collector.observe(ProviderObservation("http", "openai", "embedding", "success", 0.2, 200, "initial"))
    collector.observe(ProviderObservation("http", "openai", "embedding", "failure", 0.1, 429, "retry"))
    metrics = ServerMetrics()
    metrics.observe_worker_provider(collector.snapshot())
    assert _count(metrics, "http_attempts_total", execution="worker") == 2
    assert _count(metrics, "http_header_duration_seconds_count", execution="worker") == 2
    assert _count(metrics, "http_header_duration_seconds_sum", execution="worker") == pytest.approx(0.21)
    assert _count(metrics, "http_header_duration_seconds_bucket", execution="worker", le="+Inf") == 2
    assert _count(metrics, "observations_dropped_total", execution="worker") == 1
    assert _count(metrics, "worker_reports_total", outcome="received") == 1


def test_transport_observation_bootstrap_failure_keeps_minimax_usable(tmp_path, monkeypatch):
    def failed_bootstrap(_client):
        raise RuntimeError

    monkeypatch.setattr("powercontext.builtin.inference.minimax.observe_http_client", failed_bootstrap)
    metrics = ServerMetrics()
    with _provider((200,)) as (endpoint, _):

        async def scenario():
            async with open_builtin_runtime(
                _config(tmp_path, endpoint, "minimax"),
                scheduler_path=tmp_path / "scheduler.db",
                provider_observer=metrics.observe_provider,
            ) as runtime:
                assert (await _create(runtime)).changed

        asyncio.run(scenario())
    assert _count(metrics, "http_observation_supported", provider="minimax") == 0
    assert _count(metrics, "operations_total", outcome="success") == 1
    assert _count(metrics, "http_attempts_total") == 0
