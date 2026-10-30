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

"""HTTP helpers for authenticated Source instance deployment."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from powercontext.source_management.models import ManagementError
from powercontext.source_management.storage import canonical


def request(client: httpx.Client, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = client.request(method, path, json=body) if body is not None else client.request(method, path)
    except httpx.HTTPError as error:
        raise ManagementError("server-unreachable", "Deployment cannot contact the configured Server", 503) from error
    if response.is_error:
        # Error bodies may contain plugin data; expose the stable code only.
        try:
            code = response.json().get("error", {}).get("code", "request-failed")
        except (ValueError, AttributeError):
            code = "request-failed"
        raise ManagementError(
            str(code), f"Deployment request {path} failed (HTTP {response.status_code})", response.status_code
        )
    return response.json()


def _principal(client: httpx.Client) -> dict[str, Any]:
    result = request(client, "GET", "/v1/access/me")
    if result["mode"] != "enforced":
        raise ManagementError(
            "access-required", "Shared Source deployment requires enforced Access and distinct identities"
        )
    return result["principal"]


def _allowed(client: httpx.Client, action: str, resource: dict[str, Any]) -> bool:
    result = request(
        client,
        "POST",
        "/v1/access/check",
        {
            "match": "all",
            "requirements": [{"action": action, "resource": resource}],
        },
    )
    return result["allowed"]


def _grant(admin: httpx.Client, principal: dict[str, Any], scope: str) -> None:
    subject = {key: principal[key] for key in ("type", "id")}
    identity = hashlib.sha256(canonical([subject, scope, "scope.contributor"]).encode()).hexdigest()
    request(
        admin,
        "POST",
        "/v1/access/bindings/create",
        {
            "subject": subject,
            "resource": {"type": "scope", "scope_id": scope},
            "role": "scope.contributor",
            "idempotency_key": f"source-up-{identity}",
            "reason": "Source workspace deployment",
        },
    )
