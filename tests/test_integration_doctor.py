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

"""Shared diagnostics preserve Server state and never perform domain writes."""

import asyncio
import json
from functools import partial
from time import time

import httpx
import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import doctor_app
from powercontext.client.integration.doctor import diagnose_server
from powercontext.client.integration.models import Connection

CAPABILITIES = {
    "source_types": ["content"],
    "artifact_families": ["memory"],
    "memory_extraction": False,
    "handoff_generation": False,
    "search_modes": ["auto", "fts"],
    "context_versions": ["powercontext.prepared-context.v1"],
}


@pytest.fixture
def server(monkeypatch):
    requests = []
    responses = {
        "/health/live": (200, {"status": "ok"}),
        "/health/ready": (200, {"status": "ready", "checks": {"runtime": "ready", "database": "ready"}}),
        "/v1/capabilities": (200, CAPABILITIES),
    }

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        response = responses[request.url.path]
        if isinstance(response, Exception):
            raise response
        status, body = response
        return httpx.Response(status, json=body)

    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handle)))
    monkeypatch.setattr("powercontext.cli.system._local_service_diagnostics", lambda _: {})
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://127.0.0.1:8888")
    return responses, requests


def test_doctor_uses_effective_endpoint_and_checks_contract_without_scope(server):
    _, requests = server
    app = create_cli([doctor_app])
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    checks = json.loads(result.output)["checks"]
    assert {name for name in checks if name.startswith("server_")} == {
        "server_liveness",
        "server_readiness",
        "server_capabilities",
    }
    assert all(request.url.port == 8888 for request in requests)
    requests.clear()
    override = CliRunner().invoke(app, ["doctor", "--server-url", "http://127.0.0.1:9999"])
    assert override.exit_code == 0
    assert all(request.url.port == 9999 for request in requests)


@pytest.mark.parametrize(
    "state,dependency,status_code,expected",
    [
        ("not_ready", "unavailable", 503, "failed"),
        ("degraded", "misconfigured: private-provider-error", 200, "degraded"),
    ],
)
def test_doctor_preserves_dependency_failure_without_private_details(server, state, dependency, status_code, expected):
    responses, _ = server
    responses["/health/ready"] = (
        status_code,
        {
            "status": state,
            "checks": {
                "runtime": "ready",
                "database": "ready",
                "inference.embedding": dependency,
            },
        },
    )
    app = create_cli([doctor_app])
    for flags in ([], ["--json"]):
        result = CliRunner().invoke(app, ["doctor", *flags])
        assert result.exit_code == 1
        assert "private-provider-error" not in result.output
        assert "inference.embedding" in result.output
        if flags:
            report = json.loads(result.output)
            assert report["status"] == expected
            assert report["checks"]["server_readiness"]["checks"]["inference.embedding"] == dependency.split(":")[0]


def test_doctor_skips_followups_when_server_is_unreachable(server):
    responses, requests = server
    responses["/health/live"] = httpx.ConnectError("private-error")
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--json"])
    assert result.exit_code == 1
    assert "private-error" not in result.output
    checks = json.loads(result.output)["checks"]
    assert checks["server_liveness"]["status"] == "failed"
    assert checks["server_readiness"]["status"] == checks["server_capabilities"]["status"] == "skipped"
    assert [request.url.path for request in requests] == ["/health/live"]


@pytest.mark.parametrize("base_url,deadline", [("http://remote.example", 5), ("http://127.0.0.1:8000", -1)])
def test_invalid_transport_or_expired_deadline_never_connects(server, base_url, deadline):
    _, requests = server
    report = asyncio.run(diagnose_server(Connection(base_url=base_url), deadline=time() + deadline))
    assert report["ok"] is False
    assert requests == []


def test_doctor_rejects_unsupported_context_schema(server):
    responses, _ = server
    responses["/v1/capabilities"] = (200, {**CAPABILITIES, "context_versions": []})
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["checks"]["server_capabilities"]["status"] == "failed"


def test_cli_and_running_host_share_insecure_transport_checks(server):
    _, requests = server
    app = create_cli([doctor_app])
    blocked = CliRunner().invoke(app, ["doctor", "--server-url", "http://remote.example", "--json"])
    assert blocked.exit_code == 1
    assert requests == []
    allowed = CliRunner().invoke(
        app, ["doctor", "--server-url", "http://remote.example", "--allow-insecure-http", "--json"]
    )
    report = json.loads(allowed.output)
    native = asyncio.run(
        diagnose_server(Connection(base_url="http://remote.example", allow_insecure_http=True), deadline=time() + 5)
    )
    assert allowed.exit_code == 1
    assert report["status"] == native["status"] == "degraded"
    assert report["checks"]["transport"]["detail"] == native["checks"]["transport"]["detail"]
    assert native["checks"]["liveness"]["status"] == "ok"
