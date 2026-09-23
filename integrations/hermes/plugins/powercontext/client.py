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

"""Hermes method names backed by the installed PowerContext client worker."""

from __future__ import annotations

import time
from typing import Any

from .powercontext_client_config import normalize_server_url, resolve_allow_insecure_http
from .runtime_operations import OPERATION_PATHS
from .worker import WorkerError, execute_worker


class PowerContextError(RuntimeError):
    """Base error raised by the integration client."""


class PowerContextHTTPError(PowerContextError):
    """A non-successful HTTP response."""

    def __init__(
        self,
        status: int,
        *,
        path: str = "",
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        suffix = f" ({code})" if code else ""
        super().__init__(f"PowerContext returned HTTP {status}{suffix}")
        self.status = status
        self.path = path
        self.code = code
        self.server_message = message


class PowerContextTransportError(PowerContextError):
    """A transport or timeout failure before a valid response was received."""


class PowerContextInvalidResponseError(PowerContextError):
    """A successful HTTP response that violates the PowerContext response contract."""


class PowerContextUnknownOutcomeError(PowerContextTransportError):
    outcome = "unknown"


class PowerContextClient:
    """Hermes facade for the shared installed-client operation contract."""

    def __init__(
        self,
        base_url: str,
        *,
        authorization: str | None = None,
        allow_insecure_http: bool | None = None,
        timeout: float = 5.0,
        executor=execute_worker,
    ) -> None:
        self.allow_insecure_http = resolve_allow_insecure_http(
            base_url,
            host="hermes",
            host_environment="POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP",
            explicit=allow_insecure_http,
        )
        self.base_url = normalize_server_url(base_url, allow_insecure_http=self.allow_insecure_http)
        self.authorization = authorization.strip() if authorization else None
        self.timeout = timeout
        self._executor = executor

    def request_operation(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke the installed client's operation directly."""

        try:
            path = OPERATION_PATHS[operation]
        except KeyError as error:
            message = f"unsupported PowerContext operation: {operation}"
            raise ValueError(message) from error
        try:
            value = self._executor(
                operation,
                payload or {},
                {
                    "base_url": self.base_url,
                    "authorization": self.authorization,
                    "allow_insecure_http": self.allow_insecure_http,
                    "request_timeout": self.timeout,
                },
                time.time() + self.timeout,
            )
        except WorkerError as error:
            result = error.result
            if result.get("outcome") == "unknown":
                raise PowerContextUnknownOutcomeError("PowerContext write outcome is unknown") from error  # noqa: TRY003
            if result.get("error") == "server" or result.get("status_code") in {401, 403}:
                raise PowerContextHTTPError(
                    result.get("status_code", 500), path=path, code=result.get("code"), message=result.get("message")
                ) from error
            if result.get("error") in {"invalid_request", "invalid_response"}:
                raise PowerContextInvalidResponseError("PowerContext returned an invalid response") from error  # noqa: TRY003
            raise PowerContextTransportError("PowerContext request failed") from error  # noqa: TRY003
        if not isinstance(value, dict):
            raise PowerContextInvalidResponseError("PowerContext returned a non-object response")  # noqa: TRY003
        return value

    def get_liveness(self) -> dict[str, Any]:
        return self.request_operation("get_liveness")

    def get_readiness(self) -> dict[str, Any]:
        return self.request_operation("get_readiness")

    def get_capabilities(self) -> dict[str, Any]:
        return self.request_operation("get_capabilities")

    def resolve_scope_binding(
        self,
        *,
        explicit_scope_id: str | None,
        binding_keys: list[dict[str, str]],
    ) -> dict[str, Any]:
        return self.request_operation(
            "resolve_scope_binding", {"explicit_scope_id": explicit_scope_id, "binding_keys": binding_keys}
        )

    def set_scope_binding(self, key: dict[str, str], scope_id: str) -> dict[str, Any]:
        return self.request_operation("set_scope_binding", {"key": key, "scope_id": scope_id})

    def clear_scope_binding(self, key: dict[str, str]) -> dict[str, Any]:
        return self.request_operation("clear_scope_binding", {"key": key})

    def prepare_context(
        self,
        scope_id: str,
        query: str,
        *,
        max_bytes: int,
        assembly: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.request_operation(
            "prepare_context",
            {
                "scope_id": scope_id,
                "query": query,
                "max_bytes": max_bytes,
                **({"assembly": assembly} if assembly is not None else {}),
            },
        )

    def search_memory(self, scope_id: str, query: str, *, limit: int, mode: str) -> dict[str, Any]:
        return self.request_operation(
            "search_memory", {"scope_id": scope_id, "query": query, "limit": limit, "mode": mode}
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
        return self.request_operation("remember_memory", payload)

    def get_memory_entry(self, scope_id: str, citation: dict[str, Any]) -> dict[str, Any]:
        return self.request_operation("get_memory_entry", {"scope_id": scope_id, "citation": citation})

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
        return self.request_operation("retire_memory_entry", payload)

    def capture_content(
        self,
        scope_id: str,
        *,
        source_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self.request_operation(
            "capture_content_source",
            {"scope_id": scope_id, "source_id": source_id, "content": content, "metadata": metadata},
        )

    def flush_memory(self, scope_id: str) -> dict[str, Any]:
        return self.request_operation("flush_memory", {"scope_id": scope_id})
