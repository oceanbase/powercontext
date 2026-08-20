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

"""Small, dependency-free PowerContext HTTP client for the Hermes plugin."""

from __future__ import annotations

import json
from collections.abc import Callable
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 1_048_576


class PowerContextError(RuntimeError):
    """Base error raised by the integration client."""


class PowerContextHTTPError(PowerContextError):
    """A non-successful HTTP response."""

    def __init__(self, status: int) -> None:
        super().__init__(f"PowerContext returned HTTP {status}")
        self.status = status


class PowerContextTransportError(PowerContextError):
    """A transport, timeout, or response decoding failure."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        return None


Transport = Callable[[Request, float], HTTPResponse]


class PowerContextClient:
    """HTTP facade for the PowerContext operations used by Hermes."""

    def __init__(
        self,
        base_url: str,
        *,
        authorization: str | None = None,
        timeout: float = 5.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.authorization = authorization.strip() if authorization else None
        self.timeout = timeout
        self._opener = build_opener(_NoRedirectHandler())
        self._transport = transport

    def _request(  # noqa: C901
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        method: str = "POST",
    ) -> dict[str, Any]:
        body = None
        if method != "GET":
            body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "User-Agent": "powercontext-hermes/0.1",
        }
        if method != "GET":
            headers["Content-Type"] = "application/json"
        if self.authorization:
            headers["Authorization"] = self.authorization
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)  # noqa: S310

        try:
            if self._transport is not None:
                response = self._transport(request, self.timeout)
                status = int(getattr(response, "status", 200))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            else:
                with self._opener.open(request, timeout=self.timeout) as response:
                    status = int(getattr(response, "status", 200))
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise PowerContextHTTPError(error.code) from error
        except (OSError, TimeoutError, URLError) as error:
            raise PowerContextTransportError("PowerContext request failed") from error  # noqa: TRY003

        if len(raw) > MAX_RESPONSE_BYTES:
            raise PowerContextTransportError("PowerContext response exceeded the size limit")  # noqa: TRY003
        if status < 200 or status >= 300:
            raise PowerContextHTTPError(status)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PowerContextTransportError("PowerContext returned invalid JSON") from error  # noqa: TRY003
        if not isinstance(decoded, dict):
            raise PowerContextTransportError("PowerContext returned a non-object response")  # noqa: TRY003
        return decoded

    def get_liveness(self) -> dict[str, Any]:
        return self._request("/health/live", method="GET")

    def get_readiness(self) -> dict[str, Any]:
        return self._request("/health/ready", method="GET")

    def get_capabilities(self) -> dict[str, Any]:
        return self._request("/v1/capabilities", method="GET")

    def prepare_context(self, scope_id: str, query: str, *, max_bytes: int) -> dict[str, Any]:
        return self._request(
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
        )

    def search_memory(self, scope_id: str, query: str, *, limit: int, mode: str) -> dict[str, Any]:
        return self._request(
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": limit, "mode": mode},
        )

    def remember_memory(
        self,
        scope_id: str,
        *,
        kind: str,
        text: str,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        return self._request("/v1/memory/remember", payload)

    def get_memory_entry(self, scope_id: str, citation: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v1/memory/entries/get", {"scope_id": scope_id, "citation": citation})

    def retire_memory_entry(
        self,
        scope_id: str,
        citation: dict[str, Any],
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"scope_id": scope_id, "citation": citation}
        if reason:
            payload["reason"] = reason
        return self._request("/v1/memory/entries/retire", payload)

    def capture_content(
        self,
        scope_id: str,
        *,
        source_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "/v1/sources/content",
            {"scope_id": scope_id, "source_id": source_id, "content": content, "metadata": metadata},
        )

    def flush_memory(self, scope_id: str) -> dict[str, Any]:
        return self._request("/v1/memory/flush", {"scope_id": scope_id})
