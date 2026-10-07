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

"""Native Jev protocol checks through the public DecisionModel contract."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from benchmark.locomo_plus.decision import open_decision_reranker
from benchmark.locomo_plus.jev import JevConfig, JevDecisionModel
from powercontext.builtin.inference import InferenceUnavailableError, InferenceUsage, InvalidInferenceOutputError
from powercontext.builtin.runtime import DecisionOutcome, DecisionRequest
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.runtime.decision_model import FailOpenDecisionModel

_KEY = "test-secret-not-a-real-key"
_REQUEST = DecisionRequest(
    "memory.write-gate",
    "Does this candidate repeat the known preference?",
    "Alice prefers an evening walk.",
    ("[D1:1] Alice: I prefer an evening walk.",),
)


def _response(outcome: str = "yes") -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "decision": {
                "type": "choice",
                "choice": outcome,
                "probabilities": {answer: 0.9 if answer == outcome else 0.05 for answer in ("yes", "no", "abstain")},
                "confidence": 0.72,
            }
        },
        "usage": {"input_tokens": 123, "output_tokens": 24},
    }


@pytest.mark.parametrize("outcome", list(DecisionOutcome))
def test_native_choice_preserves_decision_inputs_outcomes_and_usage(outcome: DecisionOutcome) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://jev.example/v1/systemone"
        assert request.headers["authorization"] == f"Bearer {_KEY}"
        payload = json.loads(request.content)
        assert payload["model"] == "jev-latest"
        assert payload["state"] == {
            "decision_kind": _REQUEST.decision_kind,
            "question": _REQUEST.question,
            "subject": _REQUEST.subject,
            "evidence": list(_REQUEST.evidence),
        }
        assert payload["questions"]["decision"]["type"] == "choice"
        assert set(payload["questions"]["decision"]["criteria"]) == {"yes", "no", "abstain"}
        instructions = payload["questions"]["decision"]["instructions"]
        assert instructions.endswith(f"Question: {_REQUEST.question}")
        assert _REQUEST.subject not in instructions
        assert all(evidence not in instructions for evidence in _REQUEST.evidence)
        assert _KEY not in request.content.decode()
        return httpx.Response(200, json=_response(outcome))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = JevDecisionModel(
                JevConfig(api_key=SecretStr(_KEY), base_url="https://jev.example/v1/"), client=client
            )
            for _ in range(2):
                result = await model.evaluate(_REQUEST)
                assert result.outcome is outcome
                assert result.policy_id == model.policy_id
                assert result.confidence == 0.72
                assert result.usage == InferenceUsage(requests=1, input_tokens=123, output_tokens=24)
                assert result.used_fallback is False
                assert not client.is_closed
            assert model.resolved_models == ("jev-1.13.0",)
        assert client.is_closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "updates",
    [
        {"choice": "keep"},
        {"type": "noul"},
        {"confidence": True},
        {"confidence": "0.9"},
        {"probabilities": {"yes": 1.0}},
        {"probabilities": {"yes": 0.0, "no": 0.9, "abstain": 0.1}},
        {"probabilities": {"yes": 0.9, "no": 0.9, "abstain": 0.9}},
    ],
)
def test_invalid_provider_choice_raises_and_runtime_abstains(updates: dict[str, Any]) -> None:
    data = _response()
    data["answers"]["decision"].update(updates)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
            model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client=client)
            with pytest.raises(InvalidInferenceOutputError, match="invalid Choice response"):
                await model.evaluate(_REQUEST)
            result = await FailOpenDecisionModel(model).evaluate(_REQUEST)
            assert result.outcome is DecisionOutcome.ABSTAIN
            assert result.used_fallback is True
            assert model.resolved_models == ()

    asyncio.run(run())


def test_missing_usage_stays_unknown_and_rounded_probabilities_are_valid() -> None:
    data = _response()
    del data["usage"]
    data["answers"]["decision"]["probabilities"] = {"yes": 0.97, "no": 0.01, "abstain": 0.01}

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
            model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client=client)
            result = await model.evaluate(_REQUEST)
            assert result.outcome is DecisionOutcome.YES
            assert result.usage == InferenceUsage(requests=1, input_tokens=None, output_tokens=None)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["tls", "tcp", "after-post", "untraced", "read", "http", "invalid"])
def test_opt_in_connection_retries_only_before_post_and_preserves_attempt_accounting(
    tmp_path: Path, failure: str
) -> None:
    from types import SimpleNamespace

    from benchmark.locomo_plus.decision import AuditedDecisionModel
    from benchmark.locomo_plus.jev_transport import JevClientPool

    async def run() -> None:
        payloads = []
        clients = []

        async def respond(request):
            payloads.append(request.content)
            trace = request.extensions["trace"]
            if len(payloads) == 1:
                if failure == "after-post":
                    await trace("http11.send_request_headers.started", {"request": SimpleNamespace(method=b"POST")})
                if failure in {"tls", "tcp", "after-post"}:
                    await trace("connection.connect_tcp.failed", {"exception": httpx.ConnectError("synthetic")})
                errors = {
                    "tls": httpx.ConnectError,
                    "tcp": httpx.ConnectTimeout,
                    "after-post": httpx.ConnectError,
                    "untraced": httpx.ConnectError,
                    "read": httpx.ReadTimeout,
                }
                if failure in errors:
                    raise errors[failure]("synthetic")
                return httpx.Response(503 if failure == "http" else 200, json={})
            assert clients[0].is_closed
            return httpx.Response(200, json=_response())

        def factory():
            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        async with JevClientPool(1, client_factory=factory) as pool:
            model = AuditedDecisionModel(
                JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client_factory=pool.lease, connect_attempts=3),
                tmp_path / "decisions.jsonl",
            )
            if failure in {"tls", "tcp"}:
                result = await model.evaluate(_REQUEST)
                assert result.usage.requests == 2
                assert result.usage.input_tokens == 123
                assert len(payloads) == 2 and payloads[0] == payloads[1]
            else:
                with pytest.raises((InferenceUnavailableError, InvalidInferenceOutputError)):
                    await model.evaluate(_REQUEST)
                assert len(payloads) == 1
            assert model.records[0]["usage"]["requests"] == len(payloads)
            assert model.records[0]["transport"]["post_invocations"] == len(payloads)
        assert all(client.is_closed for client in clients)

    asyncio.run(run())


def test_connection_retry_budget_is_bounded_and_cancellation_is_not_retried() -> None:
    async def run() -> None:
        calls = 0

        async def respond(request):
            nonlocal calls
            calls += 1
            await request.extensions["trace"]("proxy.start_tls.failed", {})
            raise httpx.ConnectError("synthetic")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client=client, connect_attempts=3)
            with pytest.raises(InferenceUnavailableError):
                await model.evaluate(_REQUEST)
            assert calls == 3

        async def cancel(request):
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError

        async with httpx.AsyncClient(transport=httpx.MockTransport(cancel)) as client:
            model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client=client, connect_attempts=3)
            with pytest.raises(asyncio.CancelledError):
                await model.evaluate(_REQUEST)
            assert calls == 4

    asyncio.run(run())


def test_http_failures_do_not_expose_provider_body_or_credentials() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, text=f"Authorization: Bearer {_KEY}"))
        ) as client:
            model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client=client)
            with pytest.raises(InferenceUnavailableError) as caught:
                await model.evaluate(_REQUEST)
            assert _KEY not in str(caught.value)
            assert caught.value.__suppress_context__ is True
            result = await FailOpenDecisionModel(model).evaluate(_REQUEST)
            assert result.outcome is DecisionOutcome.ABSTAIN
            assert result.used_fallback is True

    asyncio.run(run())


def test_runtime_deadline_cancels_jev_request_and_keeps_client_owned_by_caller() -> None:
    async def run() -> None:
        cancelled = asyncio.Event()

        async def respond(_: httpx.Request) -> httpx.Response:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client=client)
            result = await FailOpenDecisionModel(model, timeout_seconds=0.01).evaluate(_REQUEST)
            assert cancelled.is_set()
            assert result.outcome is DecisionOutcome.ABSTAIN
            assert result.used_fallback is True
            assert not client.is_closed

    asyncio.run(run())


def test_caller_cancellation_propagates_through_jev_and_runtime() -> None:
    async def run() -> None:
        started = asyncio.Event()

        async def respond(_: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client=client)
            task = asyncio.create_task(FailOpenDecisionModel(model, timeout_seconds=1).evaluate(_REQUEST))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


class _TrackedTransport(httpx.MockTransport):
    closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


@pytest.mark.parametrize("failure", [None, "timeout", "connect", "status", "invalid"])
def test_request_owned_clients_close_after_success_or_failure_and_do_not_retry(failure: str | None) -> None:
    clients = []
    transports = []
    requests = []
    transport_error = {
        "timeout": httpx.ReadTimeout(f"private {_KEY}"),
        "connect": httpx.ConnectError(f"private {_KEY}"),
    }.get(failure)

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            if transport_error is not None:
                raise transport_error
            if failure == "status":
                return httpx.Response(429, text=f"private {_KEY}")
            if failure == "invalid":
                return httpx.Response(200, json={"private": _KEY})
        return httpx.Response(200, json=_response())

    def factory():
        transport = _TrackedTransport(respond)
        client = httpx.AsyncClient(transport=transport)
        transports.append(transport)
        clients.append(client)
        return client

    async def run() -> None:
        model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client_factory=factory)
        if failure is None:
            assert (await model.evaluate(_REQUEST)).outcome is DecisionOutcome.YES
        else:
            error_type = InvalidInferenceOutputError if failure == "invalid" else InferenceUnavailableError
            with pytest.raises(error_type) as caught:
                await model.evaluate(_REQUEST)
            assert _KEY not in str(caught.value)
            if failure in {"timeout", "connect", "status"}:
                assert {"timeout": "ReadTimeout", "connect": "ConnectError", "status": "HTTP 429"}[failure] in str(
                    caught.value
                )
        assert len(requests) == 1
        assert clients[0].is_closed and transports[0].closed
        assert (await model.evaluate(_REQUEST)).outcome is DecisionOutcome.YES
        assert len(requests) == 2 and requests[0].content == requests[1].content
        assert all(client.is_closed for client in clients)
        assert all(transport.closed for transport in transports)

    asyncio.run(run())


@pytest.mark.parametrize("cancel_mode", ["caller", "deadline"])
def test_request_owned_client_closes_on_cancellation_without_interrupting_other_requests(cancel_mode: str) -> None:
    async def run() -> None:
        started = asyncio.Event()
        drained = asyncio.Event()
        clients = []
        transports = []

        def factory():
            first = not clients

            async def respond(request: httpx.Request) -> httpx.Response:
                if first:
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        drained.set()
                return httpx.Response(200, json=_response())

            transport = _TrackedTransport(respond)
            client = httpx.AsyncClient(transport=transport)
            clients.append(client)
            transports.append(transport)
            return client

        model = JevDecisionModel(JevConfig(api_key=SecretStr(_KEY)), client_factory=factory)
        timed = FailOpenDecisionModel(model, timeout_seconds=0.05)
        task = asyncio.create_task((model if cancel_mode == "caller" else timed).evaluate(_REQUEST))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert (await model.evaluate(_REQUEST)).outcome is DecisionOutcome.YES
        assert not clients[0].is_closed and clients[1].is_closed
        if cancel_mode == "caller":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).used_fallback
        assert drained.is_set()
        assert all(client.is_closed for client in clients)
        assert all(transport.closed for transport in transports)
        assert (await model.evaluate(_REQUEST)).outcome is DecisionOutcome.YES
        assert clients[-1].is_closed

    asyncio.run(run())


@pytest.mark.parametrize("status_code", [200, 429])
def test_jev_assembly_keeps_proxy_routing_reuses_success_and_retires_failure(
    tmp_path, monkeypatch, status_code: int
) -> None:
    clients = []
    settings = []
    client_type = httpx.AsyncClient

    def create_client(**kwargs):
        settings.append(kwargs)
        client = client_type(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status_code, json=_response() if status_code == 200 else {"private": _KEY})
            ),
            **kwargs,
        )
        clients.append(client)
        return client

    monkeypatch.setattr("benchmark.locomo_plus.decision.httpx.AsyncClient", create_client)

    async def run() -> None:
        async with AsyncExitStack() as resources:
            reranker = await open_decision_reranker(
                "jev",
                inference=InferenceConfig(),
                jev=JevConfig(api_key=SecretStr(_KEY)),
                timeout_seconds=120,
                request_timeout_seconds=30,
                output_directory=tmp_path,
                resources=resources,
                max_inflight=1,
            )
            for _ in range(2):
                if status_code == 200:
                    assert (await reranker.model.evaluate(_REQUEST)).outcome is DecisionOutcome.YES
                else:
                    with pytest.raises(InferenceUnavailableError):
                        await reranker.model.evaluate(_REQUEST)
                assert clients[-1].is_closed is (status_code != 200)
            assert len(clients) == (1 if status_code == 200 else 2)
            assert all(value.get("trust_env", True) for value in settings)
            assert all(value.get("verify", True) for value in settings)
            assert all(value["timeout"] == 30 for value in settings)
        assert all(client.is_closed for client in clients)
        if status_code != 200:
            rows = [json.loads(line) for line in (tmp_path / "decisions.jsonl").read_text().splitlines()]
            assert [row["error"] for row in rows] == [
                {"type": "InferenceUnavailableError", "detail": "Jev request failed (HTTP 429)"}
            ] * 2
            errors = json.dumps([row["error"] for row in rows])
            assert _KEY not in errors and "https://" not in errors

    asyncio.run(run())


def test_separate_env_file_never_overwrites_generation_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JEV_MODEL", "process-default")
    monkeypatch.setenv("OPENAI_API_KEY", "generation-key")
    path = tmp_path / "jev.env"
    path.write_text(
        f"JEV_API_KEY={_KEY}\nJEV_BASE_URL=https://jev.example/v1\nJEV_MODEL=typesafe/jev-1.13\n",
        encoding="utf-8",
    )
    config = JevConfig.from_env_file(path)
    assert config.model == "typesafe/jev-1.13"
    assert config.api_key.get_secret_value() == _KEY
    assert _KEY not in repr(config)
    assert JevConfig.from_env({"JEV_API_KEY": _KEY}).model == "jev-latest"
    assert os.environ["JEV_MODEL"] == "process-default"
    assert os.environ["OPENAI_API_KEY"] == "generation-key"
