"""Read-only queries and explicit bounded delivery checks for user-owned backends."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from powercontext.observability.connection import SIGNALS
from powercontext.observability.deployment import ObservabilityError


def _client_headers(directory: Path) -> dict[str, dict[str, str]]:
    try:
        result = json.loads((directory / "external" / "query-headers.json").read_text())
    except (OSError, ValueError) as error:
        message = "Cannot read private external query headers; restore external/query-headers.json"
        raise ObservabilityError(message) from error
    if not isinstance(result, dict) or any(
        key not in SIGNALS
        or not isinstance(headers, dict)
        or any(not isinstance(name, str) or not isinstance(value, str) for name, value in headers.items())
        for key, headers in result.items()
    ):
        message = "External query headers have an invalid format"
        raise ObservabilityError(message)
    return result


def _get(config: dict[str, Any], headers: dict[str, dict[str, str]], signal: str, path: str, **params) -> Any:
    try:
        with httpx.Client(headers=headers.get(signal, {}), timeout=5, trust_env=False) as client:
            response = client.get(config["urls"][signal] + path, params=params)
            if response.status_code != 200:
                message = (
                    f"{signal} query returned HTTP {response.status_code}; check its URL, TLS and query credentials"
                )
                raise ObservabilityError(message)
            return response.json()
    except (httpx.HTTPError, ValueError) as error:
        if isinstance(error, ObservabilityError):
            raise
        message = f"{signal} query failed; check its URL, TLS, network and response format"
        raise ObservabilityError(message) from error


def inspect_connection(directory: Path, config: dict[str, Any]) -> tuple[list[str], bool]:
    headers = _client_headers(directory)
    lines = []
    healthy = True
    for signal in SIGNALS:
        if signal not in config["urls"]:
            lines.append(f"{signal}: NOT VERIFIED (query URL not configured)")
            continue
        try:
            lines.append(_inspect_signal(config, headers, signal))
        except (ObservabilityError, KeyError, TypeError, ValueError) as error:
            detail = str(error) if isinstance(error, ObservabilityError) else "unexpected backend response"
            lines.append(f"{signal}: FAIL ({detail})")
            healthy = False
    lines.append("Use check --exercise with all three query URLs to verify one new sampled request.")
    return lines, healthy


def _inspect_signal(config: dict[str, Any], headers: dict[str, dict[str, str]], signal: str) -> str:
    if signal == "prometheus":
        selector = 'up{job="powercontext",instance=' + json.dumps(config["metrics_target"]) + "}"
        expression = selector + " and (time() - timestamp(" + selector + ") < 45)"
        result = _get(config, headers, signal, "/api/v1/query", query=expression)["data"]["result"]
        if not result or any(float(row["value"][1]) != 1 for row in result):
            message = "Metrics target missing, stale or down; apply the scrape fragment and check target/credentials"
            raise ObservabilityError(message)
        return "prometheus: PASS (fresh successful scrape; no new traffic sent)"
    if signal == "tempo":
        result = _get(
            config,
            headers,
            signal,
            "/api/search",
            tags="service.name=" + json.dumps(config["service_name"]),
            start=int(time.time() - 900),
            end=int(time.time()) + 1,
            limit=1,
        )["traces"]
    else:
        result = _get(
            config,
            headers,
            signal,
            "/loki/api/v1/query_range",
            query="{service_name=" + json.dumps(config["service_name"]) + "}",
            start=str(int((time.time() - 900) * 1e9)),
            limit=1,
        )["data"]["result"]
    return signal + (
        ": PRESENT (last 15 minutes; not delivery proof)" if result else ": IDLE OR NOT DELIVERED (last 15 minutes)"
    )


def exercise_connection(directory: Path, config: dict[str, Any], *, timeout: float = 60) -> str:
    if set(SIGNALS) - config["urls"].keys():
        message = "Exercise needs Prometheus, Tempo and Loki query URLs; the OTLP ingestion URL is not a query API"
        raise ObservabilityError(message)
    trace_id = secrets.token_hex(16)
    request_headers = {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"}
    token = (directory / "external" / "observe-token").read_text().strip()
    if token:
        request_headers["Authorization"] = "Bearer " + token
    try:
        response = httpx.get(
            config["server_url"] + "/v1/capabilities", headers=request_headers, timeout=10, trust_env=False
        )
    except httpx.HTTPError as error:
        message = "Server read-only request failed; check its address, TLS and network"
        raise ObservabilityError(message) from error
    if response.status_code != 200:
        message = f"Server read-only request returned HTTP {response.status_code}; check observation credentials"
        raise ObservabilityError(message)
    started = time.time() - 5
    deadline = time.monotonic() + timeout
    headers = _client_headers(directory)
    pending = list(SIGNALS)
    while time.monotonic() < deadline:
        pending = _pending_signals(config, headers, trace_id, started)
        if not pending:
            return trace_id
        time.sleep(1)
    message = (
        "Fresh request delivery not verified for: "
        + ", ".join(pending)
        + "; check export, log collection and OpenMetrics/exemplar storage"
    )
    raise ObservabilityError(message)


def _pending_signals(
    config: dict[str, Any], headers: dict[str, dict[str, str]], trace_id: str, started: float
) -> list[str]:
    pending = []
    # A not-yet-ingested trace is a normal 404; authorization and protocol errors are not normalized to idle.
    with httpx.Client(headers=headers.get("tempo", {}), timeout=5, trust_env=False) as client:
        trace = client.get(config["urls"]["tempo"] + "/api/traces/" + trace_id, headers={"Accept": "application/json"})
    if trace.status_code not in (200, 404):
        message = f"tempo trace lookup returned HTTP {trace.status_code}; check query credentials"
        raise ObservabilityError(message)
    if trace.status_code != 200 or "get_capabilities" not in trace.text:
        pending.append("tempo")
    logs = _get(
        config,
        headers,
        "loki",
        "/loki/api/v1/query_range",
        query="{service_name=" + json.dumps(config["service_name"]) + '} |= "' + trace_id + '"',
        start=str(int(started * 1e9)),
        limit=5,
    )["data"]["result"]
    if not logs:
        pending.append("loki")
    selector = (
        "powercontext_server_application_operation_duration_seconds_bucket{instance="
        + json.dumps(config["metrics_target"])
        + ',operation="get_capabilities"}'
    )
    metrics = _get(
        config, headers, "prometheus", "/api/v1/query_exemplars", query=selector, start=started, end=time.time()
    )
    if not any(
        exemplar.get("labels", {}).get("trace_id") == trace_id
        for series in metrics["data"]
        for exemplar in series.get("exemplars", [])
    ):
        pending.append("prometheus")
    return pending
