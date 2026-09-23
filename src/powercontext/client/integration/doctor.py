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

"""Shared read-only Server diagnostics executed in the installed Python client."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from powercontext.client.integration.config import normalize_server_url
from powercontext.client.integration.core import execute
from powercontext.client.integration.models import Connection, HookRequest, HookResult
from powercontext.transport import is_loopback_host


class DoctorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection: Connection
    deadline: float = Field(gt=0, allow_inf_nan=False)


def readiness_checks(value: dict[str, Any]) -> dict[str, str]:
    """Expose dependency states without including provider error messages or credentials."""
    names = {
        "runtime",
        "database",
        "inference.generation",
        "inference.embedding",
        "inference.rerank",
        "authentication_provider",
        "access_provider",
    }
    states = {"ready", "disabled", "unavailable", "timeout", "misconfigured", "not_ready"}
    return {
        key: state if state in states else "misconfigured" if state.startswith("misconfigured:") else "unavailable"
        for key, state in value.get("checks", {}).items()
        if key in names and isinstance(state, str)
    }


def response_check(name: str, result: HookResult, endpoint: str) -> dict[str, Any]:
    status = "ok" if result.outcome in {"ok", "empty"} else "failed"
    detail = result.error or "Validated the Server response."
    extra = {}
    if status == "ok" and isinstance(result.value, dict):
        if name == "liveness":
            detail = f"{endpoint} status=ok"
        elif name == "readiness":
            state = result.value.get("status")
            status = "ok" if state == "ready" else "degraded" if state == "degraded" else "failed"
            detail = f"{endpoint} status={state}"
            dependencies = readiness_checks(result.value)
            extra["checks"] = dependencies
            if status == "ok" and any(value not in {"ready", "disabled"} for value in dependencies.values()):
                status = "failed"
        elif "powercontext.prepared-context.v1" not in result.value.get("context_versions", []):
            status, detail = "failed", "The Server does not advertise the required context schema."
    elif result.error == "transport":
        detail = f"cannot reach {endpoint}"
    if result.status_code is not None and status == "failed":
        detail += f" (HTTP {result.status_code})"
    return {"status": status, "detail": detail, **extra}


def transport_check(endpoint: str, *, allowed: bool) -> dict[str, str]:
    try:
        endpoint = normalize_server_url(endpoint, allow_insecure_http=True)
    except ValueError:
        return {"status": "failed", "detail": "Invalid Server endpoint."}
    parsed = urlsplit(endpoint)
    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname):
        return {
            "status": "degraded" if allowed else "failed",
            "detail": f"{endpoint}: insecure HTTP explicitly enabled; credentials and content are unencrypted"
            if allowed
            else f"{endpoint}: remote HTTP blocked; use HTTPS or explicitly allow insecure HTTP",
        }
    return {"status": "ok", "detail": endpoint}


async def diagnose_server(connection: Connection, *, deadline: float) -> dict[str, Any]:
    transport = transport_check(connection.base_url, allowed=connection.allow_insecure_http)
    if transport["status"] == "failed":
        return {"ok": False, "status": "failed", "checks": {"transport": transport}}
    endpoint = normalize_server_url(connection.base_url, allow_insecure_http=connection.allow_insecure_http)
    checks = {"transport": transport} if transport["status"] == "degraded" else {}
    for name, operation in (
        ("liveness", "get_liveness"),
        ("readiness", "get_readiness"),
        ("capabilities", "get_capabilities"),
    ):
        if name != "liveness" and checks["liveness"]["status"] != "ok":
            checks[name] = {"status": "skipped", "detail": "not checked because Server liveness failed"}
            continue
        result = await execute(
            HookRequest(
                id=name,
                operation=operation,
                arguments={},
                connection=connection,
                deadline=deadline,
                readiness_response=name == "readiness",
            )
        )
        checks[name] = response_check(name, result, endpoint)
    states = {check["status"] for check in checks.values()}
    status = "failed" if "failed" in states else "degraded" if "degraded" in states else "ok"
    return {"ok": status == "ok", "status": status, "checks": checks}
