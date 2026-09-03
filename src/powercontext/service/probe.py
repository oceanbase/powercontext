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

"""Content-free liveness probing for one registered personal Server endpoint."""

from __future__ import annotations

import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import ValidationError

from powercontext.http import HealthResponse
from powercontext.service.model import ProbeResult, ProbeState

_REQUEST_ID = re.compile(r"^[0-9a-f]{16}$")


def probe_server(endpoint: str, *, timeout: float = 2.0) -> ProbeResult:
    """Distinguish an absent listener from a live PowerContext Server or a port conflict."""

    parsed = urlsplit(endpoint)
    host = parsed.hostname
    port = parsed.port
    if parsed.scheme != "http" or host is None or port is None:
        return ProbeResult(ProbeState.CONFLICT, "registered endpoint is not a valid local HTTP endpoint")

    try:
        with socket.create_connection((host, port), timeout=min(timeout, 0.5)):
            pass
    except OSError:
        return ProbeResult(ProbeState.UNREACHABLE, f"cannot reach {endpoint}")

    request = Request(  # noqa: S310 - the endpoint is validated as loopback by the service controller.
        f"{endpoint.rstrip('/')}/health/live",
        headers={"Accept": "application/json", "User-Agent": "powercontext-service"},
    )
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            status_code = response.getcode()
            content_type = response.headers.get_content_type()
            request_id = response.headers.get("X-PowerContext-Request-ID", "")
            try:
                payload = json.load(response)
            except (UnicodeError, ValueError):
                return ProbeResult(ProbeState.CONFLICT, "listener returned invalid liveness JSON")
    except HTTPError as error:
        error.close()
        return ProbeResult(ProbeState.CONFLICT, f"listener returned HTTP {error.code} for liveness")
    except (OSError, URLError):
        return ProbeResult(ProbeState.CONFLICT, "listener did not satisfy the PowerContext liveness contract")

    if status_code != 200 or content_type != "application/json":
        return ProbeResult(ProbeState.CONFLICT, "listener did not return the PowerContext liveness media type")
    try:
        health = HealthResponse.model_validate(payload)
    except ValidationError:
        return ProbeResult(ProbeState.CONFLICT, "listener returned an invalid PowerContext liveness response")
    if health.status != "ok" or _REQUEST_ID.fullmatch(request_id) is None:
        return ProbeResult(ProbeState.CONFLICT, "listener did not satisfy the PowerContext liveness contract")
    return ProbeResult(ProbeState.LIVE, f"{endpoint} status=ok")


__all__ = ["probe_server"]
