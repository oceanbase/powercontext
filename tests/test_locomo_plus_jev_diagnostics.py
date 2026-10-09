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

"""Content-free, per-decision transport evidence using in-memory transports only."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import socket
import ssl
from pathlib import Path
from typing import Any, Literal

import httpcore
import httpx
import pytest
from httpcore._backends.mock import AsyncMockBackend
from pydantic import SecretStr

from evaluation.memory.locomo_plus.decision import AuditedDecisionModel
from evaluation.memory.locomo_plus.jev import JevConfig, JevDecisionModel
from evaluation.memory.locomo_plus.jev_diagnostics import CURRENT_JEV_TRACE, JevTransportTrace, safe_exception_chain
from evaluation.memory.locomo_plus.jev_transport import JevClientPool
from powercontext.builtin.inference import InferenceUnavailableError
from powercontext.builtin.runtime import DecisionOutcome, DecisionRequest

_PRIVATE = "private-provider-message-request-id-credential"
_ENDPOINT = "https://private-endpoint.invalid/v1"
_REQUEST = DecisionRequest("memory.rerank", "fixture question", "fixture candidate", ("fixture evidence",))


def _response() -> dict[str, Any]:
    return {
        "model": "fixture-jev",
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "yes",
                "probabilities": {"yes": 0.8, "no": 0.1, "abstain": 0.1},
                "confidence": 0.8,
            }
        },
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


def _assert_sanitized(value: Any) -> None:
    serialized = json.dumps(value)
    assert _PRIVATE not in serialized
    assert "private-endpoint.invalid" not in serialized


def _model(client: httpx.AsyncClient) -> JevDecisionModel:
    return JevDecisionModel(JevConfig(api_key=SecretStr(_PRIVATE), base_url=_ENDPOINT), client=client)


def test_exception_chain_keeps_cause_context_and_known_network_codes_without_content() -> None:
    root = httpx.ConnectError(f"{_PRIVATE} {_ENDPOINT}")
    connection = ConnectionRefusedError(errno.ECONNREFUSED, _PRIVATE, _ENDPOINT)
    dns = socket.gaierror(socket.EAI_AGAIN, f"{_PRIVATE} {_ENDPOINT}")
    root.__cause__ = connection
    root.__context__ = dns
    connection.__cause__ = dns
    dns.__context__ = root

    chain = safe_exception_chain(root)

    assert chain == [
        {"type": "ConnectError", "relation": "root", "parent": None},
        {
            "type": "ConnectionRefusedError",
            "relation": "cause",
            "parent": 0,
            "errno": errno.ECONNREFUSED,
            "errno_name": "ECONNREFUSED",
        },
        {"type": "gaierror", "relation": "context", "parent": 0, "errno": socket.EAI_AGAIN, "errno_name": "EAI_AGAIN"},
    ]
    assert socket.EAI_AGAIN < 0
    _assert_sanitized(chain)


def test_exception_chain_discards_arbitrary_class_names_and_unknown_codes() -> None:
    unknown = type(_PRIVATE, (Exception,), {})(f"{_PRIVATE} {_ENDPOINT}")
    unknown.__cause__ = OSError(987654321, _PRIVATE, _ENDPOINT)

    chain = safe_exception_chain(unknown)

    assert chain == [
        {"type": "OtherError", "relation": "root", "parent": None},
        {"type": "OSError", "relation": "cause", "parent": 0},
    ]
    _assert_sanitized(chain)


@pytest.mark.parametrize("recognized", [True, False])
def test_tls_diagnostics_keep_only_allowlisted_reason_library_and_verification_code(recognized: bool) -> None:
    error = ssl.SSLCertVerificationError(1, f"{_PRIVATE} {_ENDPOINT}")
    error.library = "SSL" if recognized else _PRIVATE
    error.reason = "CERTIFICATE_VERIFY_FAILED" if recognized else _ENDPOINT
    error.verify_code = 20 if recognized else 987654321
    error.verify_message = f"{_PRIVATE} {_ENDPOINT}"

    chain = safe_exception_chain(error)

    assert chain[0]["library"] == ("SSL" if recognized else "OTHER")
    assert chain[0]["reason"] == ("CERTIFICATE_VERIFY_FAILED" if recognized else "OTHER")
    assert chain[0]["ssl_errno"] == ssl.SSL_ERROR_SSL
    assert chain[0]["ssl_errno_name"] == "SSL_ERROR_SSL"
    assert "errno" not in chain[0]
    assert "errno_name" not in chain[0]
    if recognized:
        assert chain[0]["verify_code"] == 20
    else:
        assert "verify_code" not in chain[0]
    assert "verify_message" not in chain[0]
    _assert_sanitized(chain)


def test_legacy_dns_error_is_not_misclassified_as_posix_permission_error() -> None:
    chain = safe_exception_chain(socket.herror(1, _PRIVATE))

    assert chain[0]["type"] == "herror"
    assert "errno" not in chain[0]
    assert "errno_name" not in chain[0]
    _assert_sanitized(chain)


def test_trace_retains_connect_tls_http_stages_but_not_event_payloads() -> None:
    async def run() -> None:
        trace = JevTransportTrace()
        connect = httpcore.Request("CONNECT", _ENDPOINT)
        post = httpcore.Request("POST", _ENDPOINT)
        stages = [
            ("connection.connect_tcp.started", {"host": _PRIVATE, "port": 443}),
            ("connection.connect_tcp.complete", {"return_value": _PRIVATE}),
            ("http11.send_request_headers.started", {"request": connect}),
            ("http11.send_request_headers.complete", {}),
            ("http11.receive_response_headers.complete", {"return_value": (b"HTTP/1.1", 200, _PRIVATE)}),
            ("proxy.start_tls.started", {"server_hostname": _PRIVATE, "ssl_context": _PRIVATE}),
            ("proxy.start_tls.complete", {"return_value": _PRIVATE}),
            ("http11.send_request_headers.started", {"request": post}),
            ("http11.send_request_body.started", {"request": post, "body": _PRIVATE}),
            ("http11.send_request_body.complete", {}),
            ("http11.receive_response_headers.complete", {"return_value": (b"HTTP/1.1", 503, _PRIVATE)}),
            ("http11.receive_response_body.started", {"request": post}),
            ("http11.receive_response_body.failed", {"exception": httpx.ReadError(f"{_PRIVATE} {_ENDPOINT}")}),
            ("http11.response_closed.complete", {}),
        ]
        for name, info in stages:
            await trace.trace(name, info)
        await trace.trace(f"{_PRIVATE}.started", {"message": _PRIVATE})
        await trace.trace("connection.connect_tcp.private", {"message": _PRIVATE})

        snapshot = trace.snapshot()
        events = snapshot["events"]
        assert [event["event"] for event in events] == [name for name, _ in stages]
        assert [event["elapsed_ms"] for event in events] == sorted(event["elapsed_ms"] for event in events)
        assert all(event["elapsed_ms"] >= 0 for event in events)
        assert events[0]["method"] is None
        assert events[4]["method"] == "CONNECT"
        assert events[4]["status"] == 200
        assert events[10]["method"] == "POST"
        assert events[10]["status"] == 503
        assert events[12]["exception_chain"][0]["type"] == "ReadError"
        assert snapshot["post_invocations"] == 0
        _assert_sanitized(snapshot)

    asyncio.run(run())


def test_real_httpcore_proxy_events_reach_trace_without_network_access() -> None:
    async def run() -> None:
        trace = JevTransportTrace()
        backend = AsyncMockBackend([
            b"HTTP/1.1 200 Connection established\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
        ])
        async with httpcore.AsyncHTTPProxy(proxy_url="http://fixture-proxy.invalid", network_backend=backend) as pool:
            response = await pool.request("POST", _ENDPOINT, content=b"fixture", extensions={"trace": trace.trace})
            assert response.status == 200

        snapshot = trace.snapshot()
        events = snapshot["events"]
        names = {event["event"] for event in events}
        assert {
            "connection.connect_tcp.started",
            "connection.connect_tcp.complete",
            "proxy.start_tls.started",
            "proxy.start_tls.complete",
            "http11.send_request_headers.started",
            "http11.send_request_body.complete",
            "http11.receive_response_headers.complete",
            "http11.receive_response_body.complete",
            "http11.response_closed.complete",
        } <= names
        headers = [event for event in events if event["event"] == "http11.receive_response_headers.complete"]
        assert [(event["method"], event["status"]) for event in headers] == [("CONNECT", 200), ("POST", 200)]
        _assert_sanitized(snapshot)

    asyncio.run(run())


def test_http2_status_and_response_identifiers_are_retained_only_as_hashes() -> None:
    async def run() -> None:
        trace = JevTransportTrace()
        await trace.trace("http2.receive_response_headers.complete", {"return_value": (202, [(b"secret", b"body")])})
        headers = {
            name: f"{_PRIVATE}-{name}"
            for name in ("x-request-id", "request-id", "x-correlation-id", "cf-ray", "x-amzn-trace-id")
        }
        trace.response_received(httpx.Response(202, headers={**headers, "authorization": _PRIVATE}, text=_PRIVATE))

        snapshot = trace.snapshot()
        assert snapshot["events"][0]["status"] == 202
        assert snapshot["response"] == {
            "status": 202,
            "request_id_sha256": {name: hashlib.sha256(value.encode()).hexdigest() for name, value in headers.items()},
        }
        _assert_sanitized(snapshot)

    asyncio.run(run())


@pytest.mark.parametrize("status", [200, 503])
def test_audit_keeps_response_trace_on_success_and_http_failure_without_retry(tmp_path: Path, status: int) -> None:
    async def run() -> None:
        calls = []

        async def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            trace = request.extensions["trace"]
            await trace("http11.send_request_headers.started", {"request": httpcore.Request("POST", _ENDPOINT)})
            await trace("http11.receive_response_headers.complete", {"return_value": (b"HTTP/1.1", status, _PRIVATE)})
            return httpx.Response(status, headers={"x-request-id": _PRIVATE}, json=_response())

        path = tmp_path / "decisions.jsonl"
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            audited = AuditedDecisionModel(_model(client), path)
            if status == 200:
                assert (await audited.evaluate(_REQUEST)).outcome is DecisionOutcome.YES
            else:
                with pytest.raises(InferenceUnavailableError):
                    await audited.evaluate(_REQUEST)

        assert calls == ["POST"]
        row = json.loads(path.read_text())
        assert row["transport"] == audited.records[0]["transport"]
        assert row["transport"]["post_invocations"] == 1
        assert row["transport"]["response"] == {
            "status": status,
            "request_id_sha256": {"x-request-id": hashlib.sha256(_PRIVATE.encode()).hexdigest()},
        }
        assert row["transport"]["events"][-1]["status"] == status
        assert row["usage"]["requests"] == 1
        _assert_sanitized(row)
        assert CURRENT_JEV_TRACE.get() is None

    asyncio.run(run())


def test_audit_retains_underlying_error_chain_before_public_normalization(tmp_path: Path) -> None:
    async def run() -> None:
        calls = []

        async def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            error = httpx.ConnectError(f"{_PRIVATE} {_ENDPOINT}", request=request)
            error.__cause__ = socket.gaierror(socket.EAI_NONAME, _PRIVATE)
            await request.extensions["trace"]("connection.connect_tcp.failed", {"exception": error, "host": _PRIVATE})
            raise error

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            audited = AuditedDecisionModel(_model(client), tmp_path / "decisions.jsonl")
            with pytest.raises(InferenceUnavailableError):
                await audited.evaluate(_REQUEST)

        row = audited.records[0]
        assert calls == ["POST"]
        assert row["error"]["type"] == "InferenceUnavailableError"
        assert row["transport"]["post_invocations"] == 1
        assert row["transport"]["response"] is None
        chain = row["transport"]["client_error_chain"]
        assert [node["type"] for node in chain] == ["ConnectError", "gaierror"]
        assert chain[1]["errno"] == socket.EAI_NONAME
        assert chain[1]["errno_name"] == "EAI_NONAME"
        assert row["transport"]["events"][0]["exception_chain"] == chain
        _assert_sanitized(row)

    asyncio.run(run())


def test_queue_only_cancellation_records_no_http_invocation(tmp_path: Path) -> None:
    async def run() -> None:
        waiting = asyncio.Event()

        class WaitingSemaphore(asyncio.Semaphore):
            async def acquire(self) -> Literal[True]:
                waiting.set()
                return await super().acquire()

        def unexpected_request(_: httpx.Request) -> httpx.Response:
            pytest.fail("a decision cancelled in the admission queue must not send HTTP")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
            audited = AuditedDecisionModel(_model(client), tmp_path / "decisions.jsonl", slots=WaitingSemaphore(0))
            task = asyncio.create_task(audited.evaluate(_REQUEST))
            await asyncio.wait_for(waiting.wait(), timeout=1)
            task.cancel(_PRIVATE)
            with pytest.raises(asyncio.CancelledError):
                await task

        row = audited.records[0]
        assert row["error"] == {"type": "CancelledError"}
        assert row["usage"]["requests"] == 0
        assert row["provider_latency_ms"] is None
        assert row["transport"]["post_invocations"] == 0
        assert row["transport"]["events"] == []
        assert row["transport"]["client_error_chain"] == []
        assert row["transport"]["response"] is None
        _assert_sanitized(row)

    asyncio.run(run())


def test_cancelled_post_preserves_started_stage_and_cancellation_without_message(tmp_path: Path) -> None:
    async def run() -> None:
        started = asyncio.Event()

        async def respond(request: httpx.Request) -> httpx.Response:
            await request.extensions["trace"]("proxy.start_tls.started", {"server_hostname": _PRIVATE})
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            audited = AuditedDecisionModel(_model(client), tmp_path / "decisions.jsonl")
            task = asyncio.create_task(audited.evaluate(_REQUEST))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel(_PRIVATE)
            with pytest.raises(asyncio.CancelledError):
                await task

        row = audited.records[0]
        assert row["error"] == {"type": "CancelledError"}
        assert row["usage"]["requests"] == 1
        assert row["provider_latency_ms"] is not None
        assert row["transport"]["post_invocations"] == 1
        assert [event["event"] for event in row["transport"]["events"]] == ["proxy.start_tls.started"]
        assert row["transport"]["client_error_chain"] == [
            {"type": "CancelledError", "relation": "root", "parent": None}
        ]
        _assert_sanitized(row)

    asyncio.run(run())


def test_concurrent_audits_have_isolated_traces_and_restore_outer_context(tmp_path: Path) -> None:
    async def run() -> None:
        entered = asyncio.Queue()
        release = asyncio.Event()
        outer = JevTransportTrace()
        token = CURRENT_JEV_TRACE.set(outer)

        async def respond(request: httpx.Request) -> httpx.Response:
            subject = json.loads(request.content)["state"]["subject"]
            trace = request.extensions["trace"]
            await trace("http11.send_request_headers.started", {"request": httpcore.Request("POST", _ENDPOINT)})
            entered.put_nowait(subject)
            await release.wait()
            status = 200 if subject == "healthy" else 503
            await trace("http11.receive_response_headers.complete", {"return_value": (b"HTTP/1.1", status, _PRIVATE)})
            return httpx.Response(status, headers={"x-request-id": f"{_PRIVATE}-{subject}"}, json=_response())

        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                audited = AuditedDecisionModel(_model(client), tmp_path / "decisions.jsonl")

                async def evaluate(subject: str) -> None:
                    try:
                        await audited.evaluate(DecisionRequest("memory.rerank", "fixture question", subject, ()))
                    except InferenceUnavailableError:
                        assert subject == "unhealthy"
                    finally:
                        assert CURRENT_JEV_TRACE.get() is outer

                tasks = [asyncio.create_task(evaluate(subject)) for subject in ("healthy", "unhealthy")]
                async with asyncio.timeout(1):
                    assert {await entered.get(), await entered.get()} == {"healthy", "unhealthy"}
                    release.set()
                    await asyncio.gather(*tasks)

            records = {row["request"]["subject"]: row for row in audited.records}
            assert set(records) == {"healthy", "unhealthy"}
            for subject, row in records.items():
                trace = row["transport"]
                expected_status = 200 if subject == "healthy" else 503
                assert trace["post_invocations"] == 1
                assert len(trace["events"]) == 2
                assert trace["events"][-1]["status"] == expected_status
                assert trace["response"] == {
                    "status": expected_status,
                    "request_id_sha256": {"x-request-id": hashlib.sha256(f"{_PRIVATE}-{subject}".encode()).hexdigest()},
                }
                _assert_sanitized(row)
            assert outer.snapshot()["events"] == []
            assert outer.snapshot()["post_invocations"] == 0
            assert CURRENT_JEV_TRACE.get() is outer
        finally:
            CURRENT_JEV_TRACE.reset(token)

    asyncio.run(run())


def test_cancellation_waiting_for_client_lease_does_not_count_as_post(tmp_path: Path) -> None:
    async def run() -> None:
        waiting = asyncio.Event()

        def unexpected_request(_: httpx.Request) -> httpx.Response:
            pytest.fail("a decision cancelled waiting for a client must not send HTTP")

        async with (
            JevClientPool(
                1, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request))
            ) as pool,
            pool.lease(),
        ):
            model = JevDecisionModel(
                JevConfig(api_key=SecretStr(_PRIVATE), base_url=_ENDPOINT), client_factory=pool.lease
            )
            audited = AuditedDecisionModel(model, tmp_path / "decisions.jsonl")

            async def evaluate() -> None:
                waiting.set()
                await audited.evaluate(_REQUEST)

            task = asyncio.create_task(evaluate())
            await asyncio.wait_for(waiting.wait(), timeout=1)
            task.cancel(_PRIVATE)
            with pytest.raises(asyncio.CancelledError):
                await task

        row = audited.records[0]
        assert row["error"] == {"type": "CancelledError"}
        assert row["usage"]["requests"] == 0
        assert row["transport"]["post_invocations"] == 0
        assert row["transport"]["events"] == []
        assert row["transport"]["response"] is None
        _assert_sanitized(row)

    asyncio.run(run())
