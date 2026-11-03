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

"""External-platform integration keeps lifecycle ownership outside PowerContext."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from typer.testing import CliRunner

from powercontext.observability.cli import app
from powercontext.observability.connection import load_connection
from powercontext.observability.connection_checks import exercise_connection
from powercontext.observability.deployment import ObservabilityError
from powercontext.paths import powercontext_data_dir
from powercontext.server.configuration import server_settings_context


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import os

    for key in tuple(os.environ):
        if key.startswith(("POWERCONTEXT_SERVER_", "OTEL_")):
            monkeypatch.delenv(key)
    directory = tmp_path / "external profile"
    monkeypatch.setenv("POWERCONTEXT_OBSERVABILITY_HOME", str(directory))
    return directory


def _connect(directory: Path, *options: str):
    result = CliRunner().invoke(app, ["connect", "--otlp-endpoint", "http://127.0.0.1:4318", *options])
    assert result.exit_code == 0, result.output
    return load_connection(directory)


def test_packaged_dashboards_separate_main_and_advanced_folders():
    grafana = Path(__file__).resolve().parents[1] / "src/powercontext/observability/resources/grafana"
    main = {path.name for path in (grafana / "dashboards/main").glob("*.json")}
    advanced = {path.name for path in (grafana / "dashboards/advanced").glob("*.json")}
    assert main == {
        "pc-flow.json",
        "pc-overview.json",
        "pc-processing.json",
        "pc-providers.json",
        "pc-service.json",
    }
    assert advanced == {"pc-business.json", "pc-dependencies.json", "pc-scopes.json"}
    providers = yaml.safe_load((grafana / "provisioning/dashboards/dashboards.yaml").read_text())["providers"]
    assert [(provider["folder"], provider["options"]["path"]) for provider in providers] == [
        ("PowerContext", "/var/lib/grafana/dashboards/main"),
        ("PowerContext 高级明细", "/var/lib/grafana/dashboards/advanced"),
    ]


def test_flow_dashboard_prioritizes_current_state_and_precise_failure_evidence():
    path = (
        Path(__file__).resolve().parents[1]
        / "src/powercontext/observability/resources/grafana/dashboards/main/pc-flow.json"
    )
    dashboard = json.loads(path.read_text())

    assert [panel["id"] for panel in dashboard["panels"][:3]] == [20, 100, 2]
    flow_row = next(panel for panel in dashboard["panels"] if panel["id"] == 104)
    assert flow_row["collapsed"]
    assert [panel["id"] for panel in flow_row["panels"]] == [1]

    status_table = next(panel for panel in dashboard["panels"] if panel["id"] == 2)
    state_override = next(
        override for override in status_table["fieldConfig"]["overrides"] if override["matcher"]["options"] == "状态"
    )
    mapping = next(prop for prop in state_override["properties"] if prop["id"] == "mappings")
    assert mapping["value"][0]["options"]["acknowledged"] == {"text": "已确认处理", "color": "green"}
    assert "viewPanel=21" in status_table["targets"][0]["root_selector"]

    evidence_row = next(panel for panel in dashboard["panels"] if panel["id"] == 103)
    failure_logs = next(panel for panel in evidence_row["panels"] if panel["id"] == 21)
    expression = failure_logs["targets"][0]["expr"]
    assert 'scope=~"^${scope_id:regex}$"' in expression
    assert 'worker_id=~"^${failed_worker_id:regex}$"' in expression
    assert 'family=~"^${failed_family:regex}$"' in expression

    traces = next(panel for panel in evidence_row["panels"] if panel["id"] == 19)
    assert "failed_trace_id" not in traces["targets"][0]["query"]


def test_dashboard_legends_use_grafana_label_syntax():
    grafana = Path(__file__).resolve().parents[1] / "src/powercontext/observability/resources/grafana/dashboards"
    offenders: list[tuple[str, int, str]] = []

    def walk(panels):
        for panel in panels:
            for target in panel.get("targets", []):
                legend = target.get("legendFormat", "")
                if "{{." in legend:
                    offenders.append((panel["title"], panel["id"], legend))
            yield from walk(panel.get("panels", []))

    for path in grafana.rglob("*.json"):
        document = json.loads(path.read_text())
        list(walk(document["panels"]))
    assert offenders == []

    flow = json.loads((grafana / "main/pc-flow.json").read_text())
    retrieval_row = next(panel for panel in flow["panels"] if panel["id"] == 102)
    retrieval_volume = next(panel for panel in retrieval_row["panels"] if panel["id"] == 7)
    assert retrieval_volume["targets"][0]["legendFormat"] == "{{operation}} · {{family}} · {{outcome}}"


def test_service_dashboard_summarizes_series_and_keeps_operation_detail_in_table():
    path = (
        Path(__file__).resolve().parents[1]
        / "src/powercontext/observability/resources/grafana/dashboards/main/pc-service.json"
    )
    dashboard = json.loads(path.read_text())
    panels = {panel["id"]: panel for panel in dashboard["panels"]}

    request_volume = panels[3]["targets"][0]
    assert "sum by(instance,role,transport,outcome)" in request_volume["expr"]
    assert request_volume["legendFormat"] == "{{instance}} · {{transport}} · {{outcome}}"

    latency = panels[4]["targets"][0]
    assert "sum by(le,instance,role,transport)" in latency["expr"]
    assert latency["legendFormat"] == "{{instance}} · {{transport}}"

    in_progress = panels[7]["targets"][0]
    assert "sum by(instance,role)" in in_progress["expr"]
    assert "operation" not in in_progress["expr"]
    assert in_progress["instant"]

    for panel_id in (3, 4, 5, 15):
        for link in panels[panel_id]["fieldConfig"]["defaults"]["links"]:
            assert "var-operation=.*" in link["url"]
            assert "var-family=.*" in link["url"]
            assert "time=${__value.time}&time.window=600000" in link["url"]
    assert panels[3]["fieldConfig"]["defaults"]["unit"] == "reqps"
    assert panels[4]["fieldConfig"]["defaults"]["unit"] == "s"
    assert panels[21]["gridPos"]["y"] < panels[3]["gridPos"]["y"]
    alerts = panels[21]["targets"][0]["root_selector"]
    assert "viewPanel=6&refresh=" in alerts
    assert "$encodeUrlComponent($a.labels.role)" in alerts
    assert "var-role=.*" not in alerts

    cumulative = panels[16]
    organize = cumulative["transformations"][0]
    assert organize["options"]["excludeByName"]["role"]
    assert any(transform["id"] == "sortBy" for transform in cumulative["transformations"])
    operation = next(
        override for override in cumulative["fieldConfig"]["overrides"] if override["matcher"]["options"] == "操作"
    )
    links = next(prop for prop in operation["properties"] if prop["id"] == "links")["value"]
    assert "${__value.raw:percentencode}" in links[0]["url"]
    assert '${__data.fields["实例"]:percentencode}' in links[0]["url"]


def test_alert_diagnostics_distinguish_failed_requests_from_processing_evidence():
    grafana = Path(__file__).resolve().parents[1] / "src/powercontext/observability/resources/grafana/dashboards"
    for name, panel_id in (("main/pc-service", 21), ("main/pc-overview", 4), ("advanced/pc-dependencies", 5)):
        document = json.loads((grafana / f"{name}.json").read_text())
        alerts = next(panel for panel in document["panels"] if panel["id"] == panel_id)
        selector = alerts["targets"][0]["root_selector"]
        assert 'labels.alertname="PowerContextSustainedTransportErrors"' in selector
        assert 'labels.alertname="PowerContextProcessingStalled"' in selector
        assert "viewPanel=40" in selector
        assert "viewPanel=41" in selector
        assert '"guide_url": "/d/pc-service?viewPanel=42' in selector
        assert "annotations.runbook_url" not in [column["selector"] for column in alerts["targets"][0]["columns"]]
        for override in alerts["fieldConfig"]["overrides"]:
            if override["matcher"]["options"] == "处理指南":
                for prop in override["properties"]:
                    if prop["id"] == "links":
                        assert all(link["url"] == "${__value.raw:raw}" for link in prop["value"])

    service = json.loads((grafana / "main/pc-service.json").read_text())
    panels = {panel["id"]: panel for panel in service["panels"] for panel in [panel, *panel.get("panels", [])]}
    assert 'status_code=~"4..|5.."' in panels[40]["targets"][0]["expr"]
    assert '"diagnosis", "$1", "status_code"' in panels[40]["targets"][0]["expr"]
    status = next(
        override for override in panels[40]["fieldConfig"]["overrides"] if override["matcher"]["options"] == "状态码"
    )
    assert all(prop["id"] != "mappings" for prop in status["properties"])
    assert "var-log_outcome=failure" in json.dumps(panels[40], ensure_ascii=False)
    logs = panels[6]["targets"][0]["expr"]
    assert "status_code=~`${status_code:raw}`" in logs
    assert "{{.status_code}}" in logs
    assert "outcome=~`${log_outcome:raw}`" in logs
    assert panels[41]["targets"][0]["expr"].startswith('{service_name=~"powercontext.*"}')
    assert "family=~`${family:raw}`" in panels[41]["targets"][0]["expr"]
    assert service.get("refresh", "") == ""


@pytest.mark.usefixtures("otlp_exporter")
def test_connect_generates_portable_imports_without_docker_or_network(isolated, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("External connection must not invoke Docker or make external requests")

    monkeypatch.setattr("powercontext.observability.cli.docker_command", forbidden)
    monkeypatch.setattr("httpx.Client.send", forbidden)
    existing = tmp_path / "existing.env"
    existing.write_text(
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        f"POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{tmp_path / 'keep.db'}\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
        "POWERCONTEXT_SERVER_AUTH_TOKEN=keep-business-auth\n"
        "POWERCONTEXT_SERVER_HTTP_PORT=18089\n"
    )
    original = existing.read_bytes()
    _connect(isolated, "--server-env", str(existing), "--metrics-target", "existing-server:8000")
    assert existing.read_bytes() == original
    with server_settings_context(env_file=existing, observability=True) as settings:
        assert settings.http.port == 18089
        assert settings.auth.token is not None
        assert settings.auth.token.get_secret_value() == "keep-business-auth"
        assert settings.tracing.enabled
        assert settings.logging.format == "json"
        assert not settings.logging.file
        assert settings.logging.directory == powercontext_data_dir() / "logs"
    assert not (isolated / "compose.yaml").exists()
    assert not (isolated / "state.json").exists()
    scrape = yaml.safe_load((isolated / "external/prometheus-scrape.yaml").read_text())["scrape_configs"][0]
    assert scrape["static_configs"][0]["targets"] == ["existing-server:8000"]
    assert scrape["authorization"]["credentials_file"] == "/run/secrets/powercontext-observe-token"
    assert (isolated / "external/observe-token").read_text() == "keep-business-auth"
    for path in (isolated / "external/dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text())
        assert dashboard["__inputs"]
        for input_ in dashboard["__inputs"]:
            assert input_["type"] == "datasource"
        assert '"uid": "prometheus"' not in path.read_text()
        assert '"uid": "loki"' not in path.read_text()
    assert "keep-business-auth" not in (isolated / "external/prometheus-scrape.yaml").read_text()
    assert (isolated / "server.env").stat().st_mode & 0o077 == 0
    alerts = yaml.safe_load((isolated / "external/alerts-datasource.yaml").read_text())["datasources"][0]
    assert alerts["uid"] == "pc-alerts"
    overview = json.loads((isolated / "external/dashboards/pc-overview.json").read_text())
    assert any(input_["name"] == "DS_PC_ALERTS" for input_ in overview["__inputs"])


def test_private_headers_are_encoded_and_never_printed(isolated, tmp_path):
    headers = tmp_path / "headers.json"
    secret = "Bearer token,with=separator & space"  # noqa: S105 - encoding fixture, not a real credential.
    headers.write_text(json.dumps({"Authorization": secret, "X-Scope-OrgID": "tenant-a"}))
    result = CliRunner().invoke(
        app,
        [
            "connect",
            "--otlp-endpoint",
            "https://collector.example/otlp",
            "--otlp-headers-file",
            str(headers),
            "--prometheus-url",
            "https://metrics.example/prefix",
            "--prometheus-headers-file",
            str(headers),
        ],
    )
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert secret not in (isolated / "connection.json").read_text()
    assert "Authorization=Bearer%20token%2Cwith%3Dseparator%20%26%20space" in (isolated / "server.env").read_text()
    assert (isolated / "external/query-headers.json").stat().st_mode & 0o077 == 0
    alerts_path = isolated / "external/alerts-datasource.yaml"
    alerts = yaml.safe_load(alerts_path.read_text())["datasources"][0]
    assert alerts["jsonData"]["allowedHosts"] == ["https://metrics.example/prefix"]
    assert alerts["jsonData"]["httpHeaderName1"] == "Authorization"
    assert alerts["secureJsonData"]["httpHeaderValue1"] == secret
    assert alerts_path.stat().st_mode & 0o077 == 0
    overview = (isolated / "external/dashboards/pc-overview.json").read_text()
    assert "https://metrics.example/prefix/api/v1/alerts" in overview
    assert secret not in overview


@pytest.mark.parametrize(
    "endpoint", ["http://user:secret@host", "http://host?token=secret", "http://host/v1/traces", "file:///tmp/data"]
)
def test_invalid_endpoint_does_not_create_profile(isolated, endpoint):
    result = CliRunner().invoke(app, ["connect", "--otlp-endpoint", endpoint])
    assert result.exit_code == 1
    assert not (isolated / "server.env").exists()
    assert "secret" not in result.output


def test_managed_deployment_and_external_connection_cannot_overwrite_each_other(isolated, monkeypatch):
    isolated.mkdir()
    (isolated / "state.json").write_text('{"owner":"keep"}')
    result = CliRunner().invoke(app, ["connect", "--otlp-endpoint", "http://127.0.0.1:4318"])
    assert result.exit_code == 1
    assert (isolated / "state.json").read_text() == '{"owner":"keep"}'
    assert not (isolated / "connection.json").exists()
    (isolated / "state.json").unlink()
    _connect(isolated)
    before = (isolated / "connection.json").read_bytes()

    def forbidden():
        pytest.fail("Managed lifecycle must reject external mode before invoking Docker")

    monkeypatch.setattr("powercontext.observability.cli.docker_command", forbidden)
    for command in ("up", "down", "status", "connect"):
        result = CliRunner().invoke(app, [command])
        assert result.exit_code == (1 if command in ("up", "down") else 0), result.output
    assert (isolated / "connection.json").read_bytes() == before


def test_missing_query_endpoints_are_unverified_and_exercise_does_not_send_request(isolated, monkeypatch):
    _connect(isolated)
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert result.output.count("NOT VERIFIED") == 3
    assert "PASS" not in result.output

    def forbidden(*args, **kwargs):
        pytest.fail("Exercise must validate required query URLs before sending requests")

    monkeypatch.setattr("httpx.get", forbidden)
    result = CliRunner().invoke(app, ["check", "--exercise"])
    assert result.exit_code == 1
    assert "needs Prometheus, Tempo and Loki" in result.output


@pytest.mark.usefixtures("otlp_exporter")
def test_otlp_log_fragment_preserves_custom_service_identity(isolated, tmp_path):
    _connect(isolated, "--log-directory", str(tmp_path / "logs"), "--service-name", "custom-service")
    with server_settings_context(observability=True) as settings:
        assert settings.logging.directory == tmp_path / "logs"
        assert settings.logging.otlp_enabled
    fragment = yaml.safe_load((isolated / "external/collector-logs.fragment.yaml").read_text())
    assert "processors" in fragment
    assert "receivers" not in fragment
    assert "extensions" not in fragment
    assert "custom-service" in json.dumps(fragment)
    assert "service" not in fragment
    assert "exporters" not in fragment
    dashboard = json.loads((isolated / "external/dashboards/pc-scopes.json").read_text())
    for panel in (panel for panel in dashboard["panels"] if panel.get("targets")):
        target = panel["targets"][0]
        expression = target.get("expr", target.get("query", ""))
        assert "custom-service" in expression
        assert "powercontext.*" not in expression
    provider_dashboard = json.loads((isolated / "external/dashboards/pc-providers.json").read_text())

    def all_panels(panels):
        for panel in panels:
            yield panel
            yield from all_panels(panel.get("panels", []))

    provider_logs = next(panel for panel in all_panels(provider_dashboard["panels"]) if panel["type"] == "logs")
    assert 'service_name="custom-service"' in provider_logs["targets"][0]["expr"]
    rules = yaml.safe_load((isolated / "external/alerts.yaml").read_text())
    assert all(
        not rule["alert"].startswith("PowerContextTelemetry") for group in rules["groups"] for rule in group["rules"]
    )


@pytest.fixture
def query_server():
    class Handler(BaseHTTPRequestHandler):
        seen: ClassVar[list[tuple[str, str | None, str | None]]] = []
        trace_id = ""
        idle = False
        broken_metrics = False
        wrong_exemplar = False

        def do_GET(self):
            cls = type(self)
            cls.seen.append((self.path, self.headers.get("Authorization"), self.headers.get("X-Scope-OrgID")))
            if self.path == "/v1/capabilities":
                cls.trace_id = self.headers["traceparent"].split("-")[1]
                data = {"capabilities": []}
            elif self.path.startswith("/tenant/prom/api/v1/query_exemplars"):
                data = {
                    "data": [{"exemplars": [{"labels": {"trace_id": "wrong" if cls.wrong_exemplar else cls.trace_id}}]}]
                }
            elif self.path.startswith("/tenant/prom/api/v1/query"):
                data = {"data": {"result": [] if cls.broken_metrics else [{"value": [0, "1"]}]}}
            elif self.path.startswith("/tenant/tempo/api/traces/"):
                data = {"batches": [{"name": "get_capabilities"}]}
            elif self.path.startswith("/tenant/tempo/api/search"):
                data = {"traces": [] if cls.idle else [{"traceID": "historical"}]}
            elif self.path.startswith("/tenant/loki/loki/api/v1/query_range"):
                data = {"data": {"result": [] if cls.idle else [{"stream": {}, "values": [["1", "log"]]}]}}
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - match the HTTP server API.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _query_connection(isolated, tmp_path, query_server):
    base, handler = query_server
    options = ["--server-url", base]
    for signal, prefix in (("prometheus", "prom"), ("tempo", "tempo"), ("loki", "loki")):
        headers = tmp_path / (signal + ".json")
        headers.write_text(json.dumps({"Authorization": "Bearer " + signal, "X-Scope-OrgID": signal}))
        options.extend([
            "--" + signal + "-url",
            base + "/tenant/" + prefix,
            "--" + signal + "-headers-file",
            str(headers),
        ])
    config = _connect(isolated, *options)
    return config, handler


def test_checks_keep_url_prefixes_separate_credentials_and_do_not_send_business_traffic(
    isolated, tmp_path, query_server
):
    _config, handler = _query_connection(isolated, tmp_path, query_server)
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert "fresh successful scrape" in result.output
    assert all(path != "/v1/capabilities" for path, *_ in handler.seen)
    for path, auth, tenant in handler.seen:
        signal = "prometheus" if "/prom/" in path else "tempo" if "/tempo/" in path else "loki"
        assert auth == "Bearer " + signal
        assert tenant == signal


def test_idle_signals_are_not_reported_as_delivery_success(isolated, tmp_path, query_server):
    _config, handler = _query_connection(isolated, tmp_path, query_server)
    handler.idle = True
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert result.output.count("IDLE OR NOT DELIVERED") == 2
    handler.broken_metrics = True
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 1
    assert "prometheus: FAIL" in result.output


def test_explicit_exercise_requires_the_same_trace_id_in_metric_exemplar(isolated, tmp_path, query_server):
    config, handler = _query_connection(isolated, tmp_path, query_server)
    trace_id = exercise_connection(isolated, config, timeout=2)
    assert trace_id == handler.trace_id
    assert sum(path == "/v1/capabilities" for path, *_ in handler.seen) == 1
    handler.wrong_exemplar = True
    with pytest.raises(ObservabilityError, match="not verified for: prometheus"):
        exercise_connection(isolated, config, timeout=0.1)


def test_tempo_search_includes_the_current_fractional_second(isolated, tmp_path, query_server, monkeypatch):
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit

    _config, handler = _query_connection(isolated, tmp_path, query_server)
    monkeypatch.setattr("powercontext.observability.connection_checks.time", SimpleNamespace(time=lambda: 1000.75))
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    path = next(path for path, *_ in handler.seen if "/tempo/api/search" in path)
    end = int(parse_qs(urlsplit(path).query)["end"][0])
    assert end > 1000.75


def test_external_alerts_exclude_managed_collectors_but_keep_server_alerts(isolated):
    _connect(isolated)
    rules = yaml.safe_load((isolated / "external/alerts.yaml").read_text())
    alerts = {rule["alert"] for group in rules["groups"] for rule in group["rules"]}
    assert "TelemetryTargetDown" not in alerts
    assert "TelemetryExportFailure" not in alerts
    assert {"PowerContextTargetDown", "PowerContextDependencyFailure", "PowerContextProcessingStalled"} <= alerts
