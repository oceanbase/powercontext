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

"""Two-Scope acceptance through real API storage, Grafana, Loki and Tempo."""

from __future__ import annotations

import base64
import json
import logging
import os
import runpy
import secrets
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import uvicorn
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.logging import JsonFormatter, OperationalContextFilter
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    McpConfig,
    ServerLoggingConfig,
    ServerSettings,
    TracingConfig,
)
from powercontext.server.telemetry import service_resource
from powercontext.server.tracing import ServerTracing
from tests.e2e import test_worker_otlp as workers

pytestmark = pytest.mark.skipif(
    os.environ.get("PC_OBSERVABILITY_ACCEPTANCE") != "1", reason="requires explicitly enabled reference stack"
)


def _wait(check, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    pytest.fail("Scoped storage/query observation did not arrive within its deadline")


def test_two_scope_inventory_activity_and_spawned_worker_are_queryable(tmp_path, monkeypatch):  # noqa: C901 - one real-stack lifecycle owns cleanup.
    root = Path(__file__).resolve().parents[2] / "deploy/observability"
    local = root / ".local"
    auth = ("collector", (local / "gateway-password").read_text().strip())
    token = (local / "observe-token").read_text().strip()
    proxy_token = secrets.token_urlsafe(32)
    gateway = os.environ.get("PC_OBSERVABILITY_GATEWAY", "http://127.0.0.1:14318")
    authorization = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", gateway + "/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=" + authorization.replace(" ", "%20"))
    monkeypatch.setenv("OPENAI_API_KEY", "local-scope-fixture")
    suffix = secrets.token_hex(6)
    logger = logging.getLogger("powercontext")
    original_level = logger.level
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(local / "logs" / f"scopes-{suffix}.jsonl")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(OperationalContextFilter(role="all"))
    logger.addHandler(handler)
    provider = TracerProvider(resource=service_resource("all"), shutdown_on_exit=False)
    provider.add_span_processor(
        SimpleSpanProcessor(
            workers.OTLPSpanExporter(
                endpoint=gateway + "/v1/traces",
                headers={"Authorization": authorization},
                timeout=2,
            )
        )
    )
    uid = "pc-scopes-test-" + suffix
    created_dashboards = []
    proxy_server = server = thread = proxy_thread = None
    with (
        workers._receiver() as (model_url, _),
        httpx.Client(base_url=gateway, auth=auth, timeout=15, trust_env=False) as backend,
        httpx.Client(
            base_url="http://127.0.0.1:13000",
            auth=("admin", (local / "grafana-password").read_text().strip()),
            timeout=15,
            trust_env=False,
        ) as grafana,
    ):
        try:
            app = create_server_app(
                settings=ServerSettings(
                    database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scope-stack.db'}"),
                    auth=BearerAuthConfig(token=SecretStr(token)),
                    access=AccessControlConfig(mode="enforced"),
                    inference=InferenceConfig(
                        generation_model="openai-chat:fixture", generation_base_url=model_url + "/model"
                    ),
                    runtime=RuntimeConfig(artifact_processing_families=("memory",), memory_schedule_seconds=0.05),
                    logging=ServerLoggingConfig(format="json", directory=local / "logs"),
                    tracing=TracingConfig(enabled=True),
                    mcp=McpConfig(enabled=False),
                ),
                tracing=ServerTracing(provider, instrumented=True),
            )
            server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=0, log_level="error"))  # noqa: S104 - authenticated isolated fixture.
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            _wait(lambda: server.started)
            port = server.servers[0].sockets[0].getsockname()[1]
            module = runpy.run_path(str(root / "stats-proxy.py"))
            proxy = module["StatisticsProxy"](f"http://127.0.0.1:{port}", token, proxy_token)
            proxy_server = ThreadingHTTPServer(("0.0.0.0", 0), module["handler"](proxy))  # noqa: S104 - authenticated isolated fixture.
            proxy_thread = threading.Thread(target=proxy_server.serve_forever, daemon=True)
            proxy_thread.start()
            proxy_url = f"http://host.docker.internal:{proxy_server.server_port}"
            datasource = {
                "name": uid,
                "uid": uid,
                "type": "yesoreyeram-infinity-datasource",
                "access": "proxy",
                "jsonData": {"allowedHosts": [proxy_url], "httpHeaderName1": "X-PC-Stats-Token"},
                "secureJsonData": {"httpHeaderValue1": proxy_token},
            }
            grafana.post("/api/datasources", json=datasource).raise_for_status()
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": "Bearer " + token},
                trust_env=False,
                timeout=15,
            ) as api:
                scopes = []
                for index in (1, 2):
                    created = api.post(
                        "/v1/scopes",
                        json={
                            "title": f"Scope acceptance {index}",
                            "summary": "Isolated fixture",
                            "idempotency_key": str(index),
                        },
                    )
                    created.raise_for_status()
                    scope = created.json()["scope_id"]
                    scopes.append(scope)
                    for item in range(index):
                        api.post(
                            "/v1/memory/remember",
                            json={"scope_id": scope, "kind": "fact", "text": f"Private acceptance fact {index} {item}"},
                        ).raise_for_status()
                saved = api.post(
                    "/v1/sources/content",
                    json={
                        "scope_id": scopes[0],
                        "source_id": "scope-worker",
                        "content": "Private scoped Source fixture that must never appear in operational output.",
                    },
                )
                assert saved.status_code == 202
                _wait(
                    lambda: (
                        'powercontext_server_artifact_processing_completed_total{family="memory"} 1.0'
                        in api.get("/metrics").text
                    )
                )
                business = json.loads((root / "grafana/dashboards/advanced/pc-business.json").read_text())
                results = {}
                for scope, expected in zip(scopes, (1, 2), strict=True):
                    target = business["panels"][0]["targets"][0].copy()
                    target["datasource"] = {"type": datasource["type"], "uid": uid}
                    target["url"] = (
                        target["url"]
                        .replace("http://stats:8080", proxy_url)
                        .replace("${scope_id:percentencode}", quote(scope, safe=""))
                        .replace("${period}", "30d")
                    )
                    response = grafana.post("/api/ds/query", json={"queries": [target], "from": "now-1h", "to": "now"})
                    response.raise_for_status()
                    frame = response.json()["results"]["A"]["frames"][0]
                    values = {
                        field["name"]: value[0]
                        for field, value in zip(frame["schema"]["fields"], frame["data"]["values"], strict=True)
                    }
                    assert values == {"Total": expected, "Active": expected, "Inactive": 0}
                    results[scope] = values
                # Every provisioned Business projection must query actual data, not merely parse as JSON.
                for panel in business["panels"]:
                    target = panel["targets"][0].copy()
                    target["datasource"] = {"type": datasource["type"], "uid": uid}
                    target["url"] = (
                        target["url"]
                        .replace("http://stats:8080", proxy_url)
                        .replace("${scope_id:percentencode}", quote(scopes[0], safe=""))
                        .replace("${period}", "30d")
                    )
                    result = grafana.post(
                        "/api/ds/query", json={"queries": [target], "from": "now-1h", "to": "now"}
                    ).json()["results"]["A"]
                    assert not result.get("error"), (panel["title"], result.get("error"))
                    assert result.get("frames"), panel["title"]
                selection_trace_id = secrets.token_hex(16)
                snapshot = api.post(
                    "/v1/stats",
                    json={"selection": {"mode": "exact", "scope_ids": scopes}},
                    headers={"traceparent": f"00-{selection_trace_id}-{secrets.token_hex(8)}-01"},
                ).json()
                usage = {
                    row["scope_id"]: row["usage"]["totals"]["generation"]["requests"] for row in snapshot["by_scope"]
                }
                assert usage[scopes[0]] > 0 and usage[scopes[1]] == 0
                for scope in scopes:
                    expression = (
                        '{service_name=~"powercontext.*"} | json | event="application.scope.completed" | scope='
                        + json.dumps(scope)
                    )
                    _wait(
                        lambda expression=expression: backend.get(
                            "/loki/loki/api/v1/query_range", params={"query": expression, "limit": 1000}
                        ).json()["data"]["result"]
                    )
                    query = "{ span.powercontext.scope.id = " + json.dumps(scope) + " }"
                    _wait(
                        lambda query=query: (
                            backend
                            .get(
                                "/tempo/api/search",
                                params={"q": query, "start": int(time.time() - 300), "end": int(time.time()) + 1},
                            )
                            .json()
                            .get("traces")
                        )
                    )
                for scope, expected in zip(scopes, (1, 2), strict=True):
                    query = (
                        '{service_name=~"powercontext.*"} | json | event="application.scope.completed" | operation="remember_memory" | scope='
                        + json.dumps(scope)
                    )
                    rows = backend.get("/loki/loki/api/v1/query_range", params={"query": query, "limit": 1000}).json()[
                        "data"
                    ]["result"]
                    records = [json.loads(value) for row in rows for _, value in row["values"]]
                    assert len(records) == expected
                    assert all(record["scope"] == scope for record in records)
                acknowledged = (
                    '{service_name=~"powercontext.*"} | json | event="artifact_processing.worker.acknowledged" | scope='
                    + json.dumps(scopes[0])
                )
                _wait(
                    lambda: backend.get("/loki/loki/api/v1/query_range", params={"query": acknowledged}).json()["data"][
                        "result"
                    ]
                )
                scoped = json.loads((root / "grafana/dashboards/advanced/pc-scopes.json").read_text())
                failure_ratio = (
                    scoped["panels"][1]["targets"][0]["expr"]
                    .replace("${scope_id:regex}", scopes[0])
                    .replace("$role", ".*")
                )
                ratio = backend.get("/loki/loki/api/v1/query", params={"query": failure_ratio}).json()["data"]["result"]
                assert ratio and float(ratio[0]["value"][1]) == 0, (
                    "Observed successful traffic must have zero failure ratio"
                )
                model_query = (
                    "{ span.powercontext.scope.id = "
                    + json.dumps(scopes[0])
                    + ' && span.gen_ai.operation.name = "chat" }'
                )
                _wait(
                    lambda: (
                        backend
                        .get(
                            "/tempo/api/search",
                            params={"q": model_query, "start": int(time.time() - 300), "end": int(time.time()) + 1},
                        )
                        .json()
                        .get("traces")
                    )
                )
                selection_query = (
                    '{ span.powercontext.operation.name = "get_stats" && span.powercontext.scope.ids =~ "^'
                    + scopes[1]
                    + '$" }'
                )
                _wait(
                    lambda: any(
                        trace["traceID"] == selection_trace_id
                        for trace in backend
                        .get(
                            "/tempo/api/search",
                            params={"q": selection_query, "start": int(time.time() - 300), "end": int(time.time()) + 1},
                        )
                        .json()
                        .get("traces", [])
                    )
                )
                report = {
                    "inventory": results,
                    "generation_requests": usage,
                    "checks": [
                        "Grafana exact Scope inventory",
                        "all Business projections",
                        "scoped Loki activity",
                        "scoped Tempo traces",
                        "actual spawned worker acknowledgement",
                        "scoped model usage and trace",
                    ],
                }
                (local / "scoped-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                if os.environ.get("PC_SCOPED_BROWSER_HOLD") == "1":
                    for name in ("pc-business", "pc-scopes"):
                        document = json.loads((root / f"grafana/dashboards/advanced/{name}.json").read_text())
                        text = (
                            json
                            .dumps(document)
                            .replace('"pc-stats"', json.dumps(uid))
                            .replace("http://stats:8080", proxy_url)
                        )
                        document = json.loads(text)
                        document["uid"] = name + "-test-" + suffix
                        document["title"] += " / isolated acceptance"
                        grafana.post(
                            "/api/dashboards/db", json={"dashboard": document, "overwrite": False}
                        ).raise_for_status()
                        created_dashboards.append(document["uid"])
                    ready = local / "scoped-browser-ready.json"
                    done = local / "scoped-browser-done"
                    done.unlink(missing_ok=True)
                    ready.write_text(json.dumps({"dashboards": created_dashboards, "scopes": scopes}))
                    _wait(done.exists, timeout=180)
                    ready.unlink(missing_ok=True)
                    done.unlink(missing_ok=True)
        finally:
            if created_dashboards:
                (local / "scoped-browser-ready.json").unlink(missing_ok=True)
                (local / "scoped-browser-done").unlink(missing_ok=True)
            for dashboard in created_dashboards:
                grafana.delete("/api/dashboards/uid/" + dashboard)
            grafana.delete("/api/datasources/uid/" + uid)
            if proxy_server is not None:
                proxy_server.shutdown()
                proxy_server.server_close()
                proxy_thread.join(timeout=2)
            if server is not None:
                server.should_exit = True
                thread.join(timeout=15)
            provider.shutdown()
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(original_level)
