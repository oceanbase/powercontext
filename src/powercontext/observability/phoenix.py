"""Phoenix readiness and query helpers for the static local trace UI service."""

from __future__ import annotations

import time
from typing import Any

import httpx

from powercontext.observability.deployment import ObservabilityError

PROJECT = "default"
DEFAULT_PORT = 6006


def phoenix_port(state: dict[str, Any]) -> int:
    configured = state.get("phoenix_port")
    return DEFAULT_PORT if configured is None else configured


def phoenix_client(state: dict[str, Any], timeout: float = 3) -> httpx.Client:
    return httpx.Client(base_url=f"http://127.0.0.1:{phoenix_port(state)}", timeout=timeout, trust_env=False)


def wait_for_phoenix(state: dict[str, Any], trace_id: str | None = None) -> None:
    """Require a queryable project, or a specific freshly ingested trace."""
    deadline = time.monotonic() + (120 if trace_id else 60)
    path = f"/v1/projects/{PROJECT}/spans"
    params = {"trace_id": trace_id, "limit": "10"} if trace_id else {"limit": "1"}
    with phoenix_client(state) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(path, params=params)
                if response.status_code == 200:
                    if not trace_id:
                        return
                    body = response.json()
                    if any(row.get("context", {}).get("trace_id") == trace_id for row in body.get("data", [])):
                        return
            except (httpx.TransportError, ValueError):
                pass
            time.sleep(1)
    message = "Phoenix trace delivery timed out" if trace_id else "Phoenix did not become ready within 60 seconds"
    raise ObservabilityError(message + "; inspect observability status and Phoenix container logs")
