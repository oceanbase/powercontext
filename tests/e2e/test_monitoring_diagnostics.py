"""Opt-in diagnostic paths through actual Server processes and provisioned query backends."""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from powercontext.observability.deployment import backend_client, load_state, state_directory
from tests.e2e.test_provider_observability import _provider

pytestmark = pytest.mark.skipif(
    os.environ.get("PC_OBSERVABILITY_ACCEPTANCE") != "1",
    reason="requires explicitly enabled real monitoring backends",
)
_DASHBOARDS = Path(__file__).resolve().parents[2] / "src/powercontext/observability/resources/grafana/dashboards"


def _wait(check, *, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (httpx.TransportError, KeyError, IndexError):
            pass
        time.sleep(0.5)
    pytest.fail("Monitoring evidence did not arrive within its bounded deadline")


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def _server(tmp_path, home, state, port, model=None):
    (tmp_path / f"workspace-{port}").mkdir(exist_ok=True)
    (tmp_path / f"home-{port}").mkdir(exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_SERVER_", "OTEL_", "OPENAI_"))
    }
    password = (home / ".local/gateway-password").read_text().strip()
    authorization = base64.b64encode(("collector:" + password).encode()).decode()
    env.update({
        "POWERCONTEXT_HOME": str(tmp_path / f"home-{port}"),
        "POWERCONTEXT_SERVER_WORKSPACE": str(tmp_path / f"workspace-{port}"),
        "POWERCONTEXT_SERVER_DATABASE": json.dumps({
            "kind": "sqlite",
            "url": f"sqlite+aiosqlite:///{tmp_path / f'{port}.db'}",
        }),
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": (home / ".local/observe-token").read_text().strip(),
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_LOGGING_DIRECTORY": str(home / ".local/logs" / tmp_path.name),
        "POWERCONTEXT_SERVER_LOGGING_FORMAT": "json",
        "POWERCONTEXT_SERVER_LOGGING_OTLP_ENABLED": "true",
        "POWERCONTEXT_SERVER_TRACING_ENABLED": "true",
        "POWERCONTEXT_SERVER_RUNTIME": json.dumps({
            "artifact_processing_families": ["memory"] if model is not None else [],
            "memory_schedule_seconds": 0.1 if model is not None else None,
        }),
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{state['gateway_port']}",
        "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20" + authorization,
        "OTEL_EXPORTER_OTLP_TIMEOUT": "2",
        "OTEL_BSP_SCHEDULE_DELAY": "100",
        "OTEL_BLRP_SCHEDULE_DELAY": "100",
        "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
        "OTEL_TRACES_SAMPLER_ARG": "1",
        "OTEL_RESOURCE_ATTRIBUTES": f"powercontext.service.target=host.docker.internal:{port}",
    })
    if model is not None:
        env["OPENAI_API_KEY"] = "isolated-monitoring-fixture"
        env["POWERCONTEXT_SERVER_INFERENCE"] = json.dumps({
            "generation_model": "openai-chat:fixture",
            "generation_base_url": model,
        })
    with (tmp_path / f"{port}.log").open("ab") as output:
        process = subprocess.Popen(
            [
                str(Path(sys.executable).parent / "powercontext"),
                "server",
                "run",
                "--no-env-file",
                "--host",
                "0.0.0.0",  # noqa: S104 - authenticated Docker scrape fixture.
                "--port",
                str(port),
            ],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": "Bearer " + env["POWERCONTEXT_SERVER_AUTH_TOKEN"]},
                timeout=10,
                trust_env=False,
            ) as api:

                def started():
                    assert process.poll() is None, f"Isolated Server exited; inspect {tmp_path / f'{port}.log'}"
                    return api.get("/health/live").status_code == 200

                _wait(started)
                yield api
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _query(backend, expression):
    response = backend.get("/prometheus/api/v1/query", params={"query": expression})
    response.raise_for_status()
    return response.json()["data"]["result"]


def _frame(grafana, target):
    response = grafana.post("/api/ds/query", json={"queries": [target], "from": "now-15m", "to": "now"})
    response.raise_for_status()
    result = response.json()["results"]["A"]
    assert not result.get("error"), result.get("error")
    return result["frames"]


def _alert_rows(grafana, instance):
    document = json.loads((_DASHBOARDS / "main/pc-overview.json").read_text())
    target = copy.deepcopy(next(p for p in document["panels"] if p["id"] == 4)["targets"][0])
    target["datasource"] = {"type": "yesoreyeram-infinity-datasource", "uid": "pc-alerts"}
    target["root_selector"] = (
        target["root_selector"]
        .replace("${role:regex}", ".*")
        .replace(
            "${instance:regex}",
            re.escape(instance),
        )
    )
    rows = []
    for frame in _frame(grafana, target):
        names = [field["name"] for field in frame["schema"]["fields"]]
        rows.extend(dict(zip(names, values, strict=True)) for values in zip(*frame["data"]["values"], strict=True))
    return rows


def test_actual_requests_workers_alerts_and_recovery_are_correlated(tmp_path):
    home = state_directory()
    state = load_state(home)
    targets_path = home / ".local/targets.json"
    original_targets = targets_path.read_bytes()
    targets = json.loads(original_targets)
    ports = [_port(), _port()]
    instances = [f"host.docker.internal:{port}" for port in ports]
    for instance in instances:
        targets.append({"targets": [instance], "labels": {"role": "all"}})
    log_directory = home / ".local/logs" / tmp_path.name
    suffix = secrets.token_hex(6)
    dashboard_uid = "pc-diagnostics-acceptance-" + suffix
    statuses = [200]
    report = {"instances": instances, "checks": []}
    with (
        backend_client(home, state) as backend,
        httpx.Client(
            base_url=f"http://127.0.0.1:{state['grafana_port']}",
            auth=("admin", (home / ".local/grafana-password").read_text().strip()),
            timeout=20,
            trust_env=False,
        ) as grafana,
        _provider(statuses) as (model, _, _),
    ):
        try:
            targets_path.write_text(json.dumps(targets))
            with _server(tmp_path, home, state, ports[0], model) as first:
                scope = first.get("/v1/scopes/default").json()["scope_id"]
                first.get("/v1/scopes/default").raise_for_status()
                _wait(
                    lambda: all(
                        _query(backend, f'up{{job="powercontext",instance="{instance}"}} == 1')
                        for instance in instances[:1]
                    )
                )
                with _server(tmp_path, home, state, ports[1]) as second:
                    second.get("/v1/scopes/default").raise_for_status()
                    _wait(lambda: _query(backend, f'up{{instance="{instances[1]}"}} == 1'))
                    for _ in range(3):
                        first.get("/v1/scopes/default").raise_for_status()
                    overview = json.loads((_DASHBOARDS / "main/pc-overview.json").read_text())
                    readiness = first.get("/health/ready").json()["status"]
                    ready_value = {"ready": 1, "degraded": 2, "not_ready": 3}[readiness]
                    for panel_id, expected in [(2, ready_value), (3, 1), (15, 0), (16, 0)]:
                        expression = next(p for p in overview["panels"] if p["id"] == panel_id)["targets"][0]["expr"]
                        expression = expression.replace('"$role"', '"all"').replace(
                            '"$instance"',
                            json.dumps(re.escape(instances[0])),
                        )
                        values = _query(backend, expression)
                        assert values and all(float(row["value"][1]) == expected for row in values)
                    report["checks"].append(
                        "actual overview: readiness agrees with HTTP, reachable target and idle backlog"
                    )
                    trace_ids = [secrets.token_hex(16), secrets.token_hex(16)]
                    for api, trace_id in zip((first, second), trace_ids, strict=True):
                        response = api.get(
                            "/v1/scopes/nonexistent-monitoring-fixture",
                            headers={"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"},
                        )
                        assert response.status_code == 404
                        assert response.headers.get("X-PowerContext-Request-ID")
                    for trace_id in trace_ids:
                        _wait(lambda trace_id=trace_id: backend.get("/tempo/api/traces/" + trace_id).status_code == 200)
                    service = json.loads((_DASHBOARDS / "main/pc-service.json").read_text())
                    logs = next(p for row in service["panels"] for p in row.get("panels", []) if p["id"] == 6)
                    expression = logs["targets"][0]["expr"]
                    for name, value in {
                        "instance": instances[0],
                        "role": "all",
                        "operation": "get_scope",
                        "family": ".*",
                        "error_category": ".*",
                        "log_outcome": ".*",
                        "status_code": ".*",
                    }.items():
                        escaped = value if value == ".*" else re.escape(value)
                        expression = expression.replace("${" + name + ":regex}", escaped)
                        expression = expression.replace("${" + name + ":raw}", value)

                    def matching_logs():
                        frames = _frame(
                            grafana,
                            {
                                "refId": "A",
                                "expr": expression,
                                "queryType": "range",
                                "maxLines": 100,
                                "datasource": {"type": "loki", "uid": "loki"},
                            },
                        )
                        content = json.dumps(frames, ensure_ascii=False)
                        return content if trace_ids[0] in content else None

                    content = _wait(matching_logs)
                    assert trace_ids[1] not in content
                    report["checks"].append("two actual instances: exact logs and stored request traces")
                    rejected_trace = secrets.token_hex(16)
                    rejected = second.get(
                        "/v1/capabilities",
                        headers={
                            "Authorization": "Bearer isolated-invalid-credential",
                            "traceparent": f"00-{rejected_trace}-{secrets.token_hex(8)}-01",
                        },
                    )
                    assert rejected.status_code == 401
                    failure_query = (
                        '{service_name=~"powercontext.*"} | json '
                        f'| service_target="{instances[1]}" | operation="get_capabilities" '
                        f'| outcome="failure" | status_code="401" | trace_id="{rejected_trace}"'
                    )
                    _wait(
                        lambda: (
                            rejected_trace
                            in json.dumps(
                                _frame(
                                    grafana,
                                    {
                                        "refId": "A",
                                        "expr": failure_query,
                                        "queryType": "range",
                                        "maxLines": 10,
                                        "datasource": {"type": "loki", "uid": "loki"},
                                    },
                                )
                            )
                        )
                    )
                    _wait(
                        lambda: _query(
                            backend,
                            "powercontext_server_http_responses_total"
                            f'{{instance="{instances[1]}",operation="get_capabilities",status_code="401"}} > 0',
                        )
                    )
                    report["checks"].append("actual 401: metric and stored OTLP log share operation, status and trace")
                    first.post(
                        "/v1/sources/content",
                        json={
                            "scope_id": scope,
                            "source_id": "monitoring-success",
                            "content": "Private monitoring fixture that must not enter telemetry.",
                        },
                    ).raise_for_status()
                    _wait(
                        lambda: re.search(
                            r'powercontext_server_artifact_processing_completed_total\{family="memory"\} [1-9]',
                            first.get("/metrics").text,
                        )
                    )
                    statuses[:] = [403]
                    first.post(
                        "/v1/sources/content",
                        json={
                            "scope_id": scope,
                            "source_id": "monitoring-failure",
                            "content": "Private monitoring fixture that must not enter telemetry.",
                        },
                    ).raise_for_status()
                    _wait(
                        lambda: re.search(
                            r'powercontext_server_artifact_processing_failed_total\{family="memory"\} [1-9]',
                            first.get("/metrics").text,
                        )
                    )
                    observation = first.post(
                        "/v1/stats",
                        json={
                            "selection": {"mode": "exact", "scope_ids": [scope]},
                            "include_processing": True,
                        },
                    ).json()["by_scope"][0]["processing"]
                    failure = next(row for row in observation if row.get("last_error_trace_id"))
                    _wait(lambda: backend.get("/tempo/api/traces/" + failure["last_error_trace_id"]).status_code == 200)
                    report["worker_failure"] = {
                        key: failure[key] for key in ["last_error_trace_id", "last_error_worker_id", "last_error_code"]
                    }
                    statuses[:] = [200]
                    _wait(
                        lambda: re.search(
                            r'powercontext_server_artifact_processing_completed_total\{family="memory"\} [2-9]',
                            first.get("/metrics").text,
                        )
                    )
                    report["checks"].append(
                        "actual spawned Worker: provider failure, exact trace and acknowledgement recovery"
                    )
                    document = json.loads((_DASHBOARDS / "main/pc-overview.json").read_text())
                    document["uid"] = dashboard_uid
                    document["title"] = "PowerContext · 隔离验收"
                    document["templating"]["list"][1]["current"] = {"text": instances[0], "value": instances[0]}
                    grafana.post(
                        "/api/dashboards/db", json={"dashboard": document, "overwrite": False}
                    ).raise_for_status()
                    report["dashboard_uid"] = dashboard_uid
                    report["trace_ids"] = trace_ids
                    gate = os.environ.get("PC_MONITORING_BROWSER_GATE")
                    if gate:
                        ready = Path(gate)
                        ready.write_text(json.dumps(report, ensure_ascii=False, indent=2))

                        def browser_done():
                            first.get("/v1/scopes/default").raise_for_status()
                            return ready.with_suffix(".done").exists()

                        _wait(browser_done, timeout=600)
                _wait(lambda: _query(backend, f'up{{instance="{instances[1]}"}} == 0'))
                rows = _wait(
                    lambda: [
                        row
                        for row in _alert_rows(grafana, instances[1])
                        if row.get("异常") == "PowerContextTargetDown" and row.get("状态") == "firing"
                    ],
                    timeout=165,
                )
                assert rows[0]["实例"] == instances[1] and rows[0]["建议检查"]
                report["checks"].append("actual endpoint outage: named current alert and affected instance")
                with _server(tmp_path, home, state, ports[1]) as recovered:
                    recovered.get("/v1/scopes/default").raise_for_status()
                    _wait(lambda: _query(backend, f'up{{instance="{instances[1]}"}} == 1'))
                    _wait(
                        lambda: (
                            not any(
                                row.get("异常") == "PowerContextTargetDown"
                                for row in _alert_rows(grafana, instances[1])
                            )
                        )
                    )
                report["checks"].append("actual endpoint restart: fresh scrape and alert resolution")
        finally:
            targets_path.write_bytes(original_targets)
            grafana.delete("/api/dashboards/uid/" + dashboard_uid)
            (tmp_path / "monitoring-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            shutil.rmtree(log_directory, ignore_errors=True)
