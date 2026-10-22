"""The monitoring adapter forwards only authorized, content-free statistics queries."""

from __future__ import annotations

import json
import runpy
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

import httpx
import pytest


@contextmanager
def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def proxy():
    module = runpy.run_path(str(resources.files("powercontext.observability").joinpath("resources/stats-proxy.py")))
    received = []
    state = {"status": 200}

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - base-class signature.
            pass

        def do_POST(self):
            assert self.path == "/v1/stats"
            assert self.headers["Authorization"] == "Bearer server-secret"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            scope_ids = payload["selection"].get("scope_ids", ["one", "two"])
            body = json.dumps(
                {
                    "scope_ids": scope_ids,
                    "as_of": "2026-10-22T00:00:00Z",
                    "inventory": {"memory": {"entries": {"total": 3, "active": 2, "inactive": 1}}},
                    "usage": {"generation": {"input_tokens": None}},
                    "recall": {},
                    "by_scope": [
                        {
                            "scope_id": scope,
                            "inventory": {},
                            "usage": {},
                            "recall": {},
                            "recurrence": {"excluded": True},
                            "processing": state.get("processing", []),
                            "bootstrap_delivery": state.get("bootstrap_delivery"),
                        }
                        for scope in scope_ids
                    ],
                    "unexpected_content": "must-not-export",
                }
                if state["status"] == 200
                else {"error": "server-secret private-provider-message"}
            ).encode()
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with _serve(Upstream) as upstream:
        instance = module["StatisticsProxy"](upstream, "server-secret", "proxy-secret")
        with _serve(module["handler"](instance)) as url:
            yield url, received, state


def test_retained_delivery_metadata_survives_proxy_without_receipt_or_package_contents(proxy):
    url, _, state = proxy
    state["bootstrap_delivery"] = {
        "period": {"preset": "30d", "start_date": "2026-10-01", "end_date": "2026-10-30", "days": 30},
        "retention_days": 30,
        "actual_adoption": "unobserved",
        "by_integration": [
            {
                "integration": "codex",
                "state": "injected",
                "receipts": 1,
                "content_bytes": 512,
                "last_prepared_at": "2026-10-30T00:00:00Z",
                "receipt_id": "private-receipt",
                "content": "private-package",
            }
        ],
        "content": "private-package",
    }
    with httpx.Client(base_url=url, headers={"X-PC-Stats-Token": "proxy-secret"}, trust_env=False) as client:
        response = client.get("/stats", params={"scope_id": "one"})
        assert response.status_code == 200
        delivery = response.json()["by_scope"][0]["bootstrap_delivery"]
        assert delivery["actual_adoption"] == "unobserved"
        assert delivery["by_integration"][0]["receipts"] == 1
        assert "private-" not in response.text


def test_exact_opaque_scope_and_unknown_usage_survive_http_round_trip(proxy):
    url, received, _ = proxy
    scope = '欢哥 / a"b`\\ .* &?'
    with httpx.Client(base_url=url, headers={"X-PC-Stats-Token": "proxy-secret"}, trust_env=False) as client:
        result = client.get("/stats", params={"scope_id": scope, "period": "7d"})
        assert result.status_code == 200
        assert result.headers["Cache-Control"] == "no-store"
        assert result.json()["scope_ids"] == [scope]
        assert result.json()["usage"]["generation"]["input_tokens"] is None
        assert "must-not-export" not in result.text
        assert "recurrence" not in result.text
        assert received == [{"selection": {"mode": "exact", "scope_ids": [scope]}, "period": "7d"}]


def test_no_cached_success_after_upstream_access_is_revoked(proxy):
    url, received, state = proxy
    with httpx.Client(base_url=url, headers={"X-PC-Stats-Token": "proxy-secret"}, trust_env=False) as client:
        assert client.get("/stats").status_code == 200
        assert received[0]["selection"] == {"mode": "all"}
        state["status"] = 403
        denied = client.get("/stats")
        assert denied.status_code == 403
        assert "server-secret" not in denied.text
        assert "private-provider-message" not in denied.text


def test_processing_failure_diagnostics_survive_proxy_without_exception_content(proxy):
    url, _, state = proxy
    state["processing"] = [
        {
            "binding": "memory-source-window",
            "family": "memory",
            "state": "retry_wait",
            "last_error_code": "access_denied",
            "last_error_stage": "authorization",
            "last_error_at": "2026-10-28T07:00:00Z",
            "last_error_worker_id": "00000000-0000-4000-8000-000000000001",
            "last_error_trace_id": "0123456789abcdef0123456789abcdef",
            "exception_message": "private-provider-message",
        }
    ]
    with httpx.Client(base_url=url, headers={"X-PC-Stats-Token": "proxy-secret"}, trust_env=False) as client:
        result = client.get("/stats", params={"scope_id": "one", "processing": "1"})
        assert result.status_code == 200
        row = result.json()["by_scope"][0]["processing"][0]
        assert row["last_error_code"] == "access_denied"
        assert row["last_error_stage"] == "authorization"
        assert row["last_error_at"] == "2026-10-28T07:00:00Z"
        assert row["last_error_worker_id"] == "00000000-0000-4000-8000-000000000001"
        assert row["last_error_trace_id"] == "0123456789abcdef0123456789abcdef"
        assert "private-provider-message" not in result.text


def test_proxy_rejects_unauthorized_invalid_and_arbitrary_upstream_requests(proxy):
    url, received, _ = proxy
    with httpx.Client(base_url=url, trust_env=False) as client:
        assert client.get("/stats").status_code == 401
        client.headers["X-PC-Stats-Token"] = "proxy-secret"
        assert client.get("/v1/memory/remember").status_code == 404
        assert client.get("/stats?upstream=https://example.com").status_code == 400
        assert client.get("/stats?period=1y").status_code == 400
        assert client.get("/stats?scope_id=a&scope_id=b").status_code == 400
        assert not received
