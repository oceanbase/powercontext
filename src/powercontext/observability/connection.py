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

"""Connect one Server to user-owned telemetry endpoints without deploying components."""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml

from powercontext.observability.deployment import ObservabilityError

CONNECTION_VERSION = 1
SIGNALS = ("prometheus", "tempo", "loki")


def endpoint_url(value: str) -> str:
    """Keep reverse-proxy base paths, but never embed credentials or query parameters."""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in value)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        message = "Endpoint must be an HTTP(S) URL without credentials, whitespace, query or fragment"
        raise ObservabilityError(message)
    return value.rstrip("/")


def read_headers(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        headers = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        message = "Cannot read headers JSON; provide an object of header names and string values"
        raise ObservabilityError(message) from error
    if not isinstance(headers, dict) or any(
        not isinstance(key, str)
        or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None
        or not isinstance(value, str)
        or "\r" in value
        or "\n" in value
        for key, value in headers.items()
    ):
        message = "Invalid headers JSON; names must be HTTP header tokens and values must be single-line strings"
        raise ObservabilityError(message)
    return headers


def _write_private(path: Path, text: str) -> None:
    # Do not follow a pre-existing symlink or overwrite an operator-owned file.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(text)


def load_connection(directory: Path) -> dict[str, Any]:
    try:
        config = json.loads((directory / "connection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        message = "External connection is unreadable; restore connection.json or use a separate observability directory"
        raise ObservabilityError(message) from error
    if (
        not isinstance(config, dict)
        or config.get("schema_version") != CONNECTION_VERSION
        or config.get("mode") != "external"
        or not isinstance(config.get("service_name"), str)
        or not config["service_name"]
        or not isinstance(config.get("urls"), dict)
        or any(key not in SIGNALS or not isinstance(value, str) for key, value in config["urls"].items())
        or not all(isinstance(config.get(key), str) for key in ("server_url", "otlp_endpoint", "metrics_target"))
    ):
        message = "External connection schema is unsupported or incomplete"
        raise ObservabilityError(message)
    for url in (config["server_url"], config["otlp_endpoint"], *config["urls"].values()):
        endpoint_url(url)
    return config


def _metrics_target(server_url: str, selected: str | None) -> str:
    parsed = urlsplit(server_url)
    host = parsed.hostname or "localhost"
    host = f"[{host}]" if ":" in host else host
    target = selected or f"{host}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    try:
        target_url = urlsplit("//" + target)
        valid = (
            target_url.hostname
            and target_url.port
            and not target_url.username
            and not target_url.password
            and not target_url.path
            and not target_url.query
            and not target_url.fragment
            and not any(character.isspace() for character in target)
        )
    except ValueError:
        valid = False
    if not valid:
        message = "Metrics target must be host:port reachable from the existing Prometheus"
        raise ObservabilityError(message)
    return target


def create_connection(
    directory: Path,
    *,
    otlp_endpoint: str,
    server_url: str,
    token: str,
    urls: dict[str, str],
    header_files: dict[str, Path],
    metrics_target: str | None = None,
    log_directory: Path | None = None,
    service_name: str = "powercontext-server",
) -> dict[str, Any]:
    """Generate private local artifacts; no external requests, installation or business edits."""
    if any(
        (directory / name).exists()
        for name in ("state.json", "compose.yaml", "connection.json", "server.env", "external")
    ):
        message = (
            "Observability configuration already exists; reuse it or choose a separate POWERCONTEXT_OBSERVABILITY_HOME"
        )
        raise ObservabilityError(message)
    server_url = endpoint_url(server_url)
    if urlsplit(server_url).path not in ("", "/"):
        message = "Server URL must be an origin without a path"
        raise ObservabilityError(message)
    otlp_endpoint = endpoint_url(otlp_endpoint)
    if otlp_endpoint.endswith("/v1/traces"):
        message = "Use the Collector base endpoint; the OTLP/HTTP exporter appends /v1/traces"
        raise ObservabilityError(message)
    if not service_name or any(character in service_name for character in ("\r", "\n")):
        message = "Service name must be nonempty and single-line"
        raise ObservabilityError(message)
    resolved_urls = {signal: endpoint_url(url) for signal, url in urls.items()}
    if any(signal not in SIGNALS for signal in urls) or any(
        signal not in (*SIGNALS, "otlp") for signal in header_files
    ):
        message = "Unsupported telemetry endpoint type"
        raise ObservabilityError(message)
    if any(signal != "otlp" and signal not in urls for signal in header_files):
        message = "Query headers require the corresponding backend URL"
        raise ObservabilityError(message)
    headers = {signal: read_headers(path) for signal, path in header_files.items()}
    target = _metrics_target(server_url, metrics_target)
    profile = {
        "POWERCONTEXT_SERVER_TRACING_ENABLED": "true",
        "POWERCONTEXT_SERVER_LOGGING_FORMAT": "json",
        "POWERCONTEXT_SERVER_LOGGING_OTLP_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint,
        "OTEL_EXPORTER_OTLP_TIMEOUT": "2",
        "OTEL_BSP_MAX_QUEUE_SIZE": "512",
        "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "128",
        "OTEL_BSP_SCHEDULE_DELAY": "1000",
        "OTEL_BLRP_MAX_QUEUE_SIZE": "512",
        "OTEL_BLRP_MAX_EXPORT_BATCH_SIZE": "128",
        "OTEL_BLRP_SCHEDULE_DELAY": "1000",
        "OTEL_BLRP_EXPORT_TIMEOUT": "2000",
        "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
        "OTEL_TRACES_SAMPLER_ARG": "0.1",
        "OTEL_SERVICE_NAME": service_name,
        "OTEL_RESOURCE_ATTRIBUTES": f"powercontext.service.target={target}",
    }
    if headers.get("otlp"):
        profile["OTEL_EXPORTER_OTLP_HEADERS"] = ",".join(
            f"{quote(key, safe='')}={quote(value, safe='')}" for key, value in headers["otlp"].items()
        )
    if log_directory is not None:
        profile["POWERCONTEXT_SERVER_LOGGING_DIRECTORY"] = str(log_directory.expanduser().resolve())
    config = {
        "schema_version": CONNECTION_VERSION,
        "mode": "external",
        "server_url": server_url,
        "otlp_endpoint": otlp_endpoint,
        "urls": resolved_urls,
        "metrics_target": target,
        "service_name": service_name,
    }
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    external = directory / "external"
    external.mkdir(mode=0o700)
    _write_private(
        external / "query-headers.json",
        json.dumps({signal: value for signal, value in headers.items() if signal != "otlp"}),
    )
    _write_private(external / "observe-token", token)
    _write_private(directory / "server.env", "".join(f"{key}={shlex.quote(value)}\n" for key, value in profile.items()))
    _export_artifacts(external, config, token=bool(token), file_logs=log_directory is not None)
    # Commit the connection record last; an interrupted generation is not a valid saved connection.
    _write_private(directory / "connection.json", json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return config


def _export_artifacts(directory: Path, config: dict[str, Any], *, token: bool, file_logs: bool) -> None:
    root = resources.files("powercontext.observability").joinpath("resources")
    proxy_token = secrets.token_urlsafe(32)
    _write_private(directory / "stats-proxy.py", root.joinpath("stats-proxy.py").read_text())
    _write_private(
        directory / "stats.json",
        json.dumps({
            "server_url": config["server_url"],
            "server_token": (directory / "observe-token").read_text(),
            "proxy_token": proxy_token,
        }),
    )
    _write_private(
        directory / "stats-datasource.yaml",
        yaml.safe_dump({
            "apiVersion": 1,
            "datasources": [
                {
                    "name": "PowerContext statistics",
                    "uid": "pc-stats",
                    "type": "yesoreyeram-infinity-datasource",
                    "access": "proxy",
                    "jsonData": {
                        "allowedHosts": ["http://127.0.0.1:18083"],
                        "httpHeaderName1": "X-PC-Stats-Token",
                        "timeout": 10,
                    },
                    "secureJsonData": {"httpHeaderValue1": proxy_token},
                }
            ],
        }),
    )
    prometheus_url = config["urls"].get("prometheus", "http://127.0.0.1:9090")
    _export_alert_datasource(directory, prometheus_url)
    scrape: dict[str, Any] = {
        "job_name": "powercontext",
        "scrape_interval": "15s",
        "scheme": urlsplit(config["server_url"]).scheme,
        "metrics_path": "/metrics",
        "scrape_protocols": ["OpenMetricsText1.0.0", "PrometheusText0.0.4"],
        "static_configs": [{"targets": [config["metrics_target"]], "labels": {"role": "all"}}],
    }
    if token:
        scrape["authorization"] = {"credentials_file": "/run/secrets/powercontext-observe-token"}
    _write_private(directory / "prometheus-scrape.yaml", yaml.safe_dump({"scrape_configs": [scrape]}, sort_keys=False))
    dashboards = directory / "dashboards"
    dashboards.mkdir()
    pending = list(root.joinpath("grafana/dashboards").iterdir())
    while pending:
        path = pending.pop()
        if path.is_dir():
            pending.extend(path.iterdir())
            continue
        if not path.is_file() or not path.name.endswith(".json"):
            continue
        dashboard = _portable_dashboard(
            json.loads(path.read_text()), service_name=config["service_name"], prometheus_url=prometheus_url
        )
        _write_private(
            dashboards / path.name,
            json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n",
        )
    rules = yaml.safe_load(root.joinpath("prometheus/alerts.yaml").read_text())
    # Existing hosts may not run the reference stack's collector/tempo/loki scrape jobs.
    for group in rules["groups"]:
        group["rules"] = [rule for rule in group["rules"] if not rule.get("alert", "").startswith("Telemetry")]
    _write_private(directory / "alerts.yaml", yaml.safe_dump(rules, sort_keys=False))
    collector = yaml.safe_load(root.joinpath("collector/config.yaml").read_text())
    processor = collector["processors"]["transform/logs"]
    # Preserve runtime-configured service identity for nondefault service names.
    for statement_index, statement in enumerate(processor["log_statements"][0]["statements"]):
        if statement == 'set(resource.attributes["service.name"], "powercontext-server")':
            processor["log_statements"][0]["statements"][statement_index] = (
                'set(resource.attributes["service.name"], ' + json.dumps(config["service_name"]) + ")"
            )
    fragment: dict[str, Any] = {"processors": {"transform/powercontext": processor}}
    _write_private(directory / "collector-logs.fragment.yaml", yaml.safe_dump(fragment, sort_keys=False))
    _write_private(directory / "README.md", _instructions(config, file_logs=file_logs, token=token))


def _export_alert_datasource(directory: Path, prometheus_url: str) -> None:
    """Provision one fixed read-only rule endpoint with separately protected query credentials."""
    datasource: dict[str, Any] = {
        "name": "PowerContext current alerts",
        "uid": "pc-alerts",
        "type": "yesoreyeram-infinity-datasource",
        "access": "proxy",
        "jsonData": {"allowedHosts": [prometheus_url], "timeout": 5},
    }
    headers = json.loads((directory / "query-headers.json").read_text()).get("prometheus", {})
    if headers:
        datasource["secureJsonData"] = {}
        for index, (name, value) in enumerate(headers.items(), 1):
            datasource["jsonData"][f"httpHeaderName{index}"] = name
            datasource["secureJsonData"][f"httpHeaderValue{index}"] = value
    _write_private(
        directory / "alerts-datasource.yaml",
        yaml.safe_dump({"apiVersion": 1, "datasources": [datasource]}, sort_keys=False),
    )


def _portable_dashboard(
    dashboard: dict[str, Any], *, service_name: str, prometheus_url: str = "http://127.0.0.1:9090"
) -> dict[str, Any]:
    inputs = {}
    # External proxy defaults to loopback on the Grafana host; operators adapt both the query URL and allowed host.
    dashboard = json.loads(json.dumps(dashboard).replace("http://stats:8080", "http://127.0.0.1:18083"))
    dashboard = json.loads(json.dumps(dashboard).replace("http://prometheus:9090", prometheus_url))

    def rewrite_panels(panels: list[dict[str, Any]]) -> None:
        for panel in panels:
            datasource_uid = (panel.get("datasource") or {}).get("uid")
            for target in panel.get("targets", []):
                if "expr" in target:
                    target["expr"] = target["expr"].replace(
                        'service_name=~"powercontext.*"', "service_name=" + json.dumps(service_name, ensure_ascii=False)
                    )
                if datasource_uid == "tempo" and "query" in target:
                    target["query"] = target["query"].replace(
                        "{ ", "{ resource.service.name = " + json.dumps(service_name, ensure_ascii=False) + " && ", 1
                    )
            rewrite_panels(panel.get("panels", []))

    rewrite_panels(dashboard["panels"])

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            if (value.get("type") in SIGNALS and value.get("uid") == value["type"]) or (
                value.get("type") == "yesoreyeram-infinity-datasource" and value.get("uid") in {"pc-stats", "pc-alerts"}
            ):
                plugin = value["type"]
                name = (
                    "DS_PC_" + value["uid"][3:].upper()
                    if value["uid"] in {"pc-stats", "pc-alerts"}
                    else "DS_" + plugin.upper()
                )
                inputs[name] = {
                    "name": name,
                    "label": plugin.title(),
                    "type": "datasource",
                    "pluginId": plugin,
                    "pluginName": plugin.title(),
                }
                return {**value, "uid": "${" + name + "}"}
            return {key: rewrite(item) for key, item in value.items()}
        return value

    result = rewrite(dashboard)
    result["__inputs"] = list(inputs.values())
    return result


def _instructions(config: dict[str, Any], *, file_logs: bool, token: bool) -> str:
    prefix = """# Connect to an existing observability platform

No remote configuration was changed and no components were started.

1. Restart the same Server with its existing configuration and `--observability`. For containers, load the generated
   `server.env` as an additional env file. OTLP uses HTTP/protobuf; port 4317 gRPC is not supported by this profile.
   Container endpoint names must resolve inside the Server container. Keep TLS verification enabled. Private CAs
   must be installed in the Server/CLI trust store or configured explicitly for manual telemetry export.
2. Merge `prometheus-scrape.yaml` into the existing Prometheus configuration. Do not replace existing scrape jobs.
   The target must be reachable from Prometheus. Import `alerts.yaml` only after reviewing existing jobs and rules;
   reference-stack telemetry alerts were excluded. Alert notification routing belongs to the existing platform.
3. Import each JSON in `dashboards/` through Grafana Import and select existing datasources when prompted.
   Current alerts, Business and Scope activity also need Infinity 4.1.3.
   Provision `alerts-datasource.yaml` for current rule states; adapt its fixed Prometheus URL for Grafana's network.
   This file can contain private query headers and must remain private.
   Run the generated read-only statistics adapter on the Grafana host:
   `python stats-proxy.py --config stats.json --port 18083`. Provision `stats-datasource.yaml` in Grafana.
   Adapt the adapter's fixed Server URL and the datasource/query address for your network before starting it.
   The adapter binds loopback by default; do not publish it. Grafana readers share the configured statistics identity.
   The Dependencies dashboard includes optional reference-stack telemetry panels; adapt those to your own collectors.
   Configure Prometheus exemplar trace_id → your Tempo datasource and Tempo trace-to-logs → your Loki datasource
   in the existing datasource settings. These settings were not changed automatically.
4. The Server sends logs directly through OTLP/HTTP. Add `otlp` to the existing Collector logs pipeline and include
   `transform/powercontext` from `collector-logs.fragment.yaml` before exporting. It keeps approved fields only and
   preserves trace correlation. No exporter or existing pipeline was overwritten.
5. Run `powercontext observability check`. Backend URLs are optional and may include reverse-proxy prefixes.
   Without all three query URLs, only configured signals are checked; missing signals remain unverified.
   `check --exercise` requires all three URLs and sends a sampled read-only request. It writes normal telemetry,
   not business records. It requires OpenMetrics scraping and exemplar storage in the existing Prometheus.

"""
    access_note = (
        "Mount `observe-token` read-only into Prometheus at `/run/secrets/powercontext-observe-token`; grant read access\n"
        "to the Prometheus container user. It contains the existing Server token, not a new business credential.\n\n"
        if token
        else "The generated scrape uses anonymous access because the selected Server has no token.\n\n"
    )
    file_note = (
        "A local JSON file remains for offline audit. It is not the Collector input; do not add filelog for the same\n"
        "Server or logs will be duplicated. Use the OTLP logs pipeline described above.\n"
        if file_logs
        else "No local JSON audit file was requested. The Server still sends approved structured fields through OTLP/HTTP.\n"
    )
    return "\n".join((prefix, access_note, file_note))
