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

"""Synchronous native adapters use the same operation executor as worker clients."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Mapping
from time import monotonic, time
from typing import Any

from powercontext.client.integration.core import execute
from powercontext.client.integration.models import Connection, HookRequest, HookResult
from powercontext.client.operations import operation_for_path
from powercontext.client.prepared_context import InvalidPreparedContextResponse


class HttpStatusError(RuntimeError):
    outcome = "failed"

    def __init__(self, status: int, path: str = "/v1/context/prepare", code: str | None = None) -> None:
        self.status, self.path, self.code = status, path, code
        super().__init__(f"PowerContext returned HTTP {status}")


class UnavailableError(RuntimeError):
    pass


class UnknownOutcomeError(UnavailableError):
    outcome = "unknown"


class ScopeBindingError(RuntimeError):
    pass


class ScopeBindingUnavailableError(ScopeBindingError):
    pass


class ScopeBindingRejectedError(ScopeBindingError):
    pass


class ScopeBindingStatusError(ScopeBindingError):
    def __init__(self, status: int, path: str) -> None:
        self.status, self.path = status, path
        super().__init__(f"PowerContext returned HTTP {status}")


def execute_sync(request: HookRequest) -> HookResult:
    def run() -> HookResult:
        return asyncio.run(execute(request))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    # Some native hosts invoke synchronous callbacks from their running event loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(run).result()


def request_json(
    path: str,
    payload: Mapping[str, Any],
    *,
    settings: Any,
    deadline: float,
    method: str = "POST",
) -> dict[str, Any]:
    authorization = settings.authorization
    if hasattr(authorization, "get_secret_value"):
        authorization = authorization.get_secret_value()
    result = execute_sync(
        HookRequest(
            id="native",
            operation=operation_for_path(method, path),
            arguments=dict(payload),
            connection=Connection(
                base_url=settings.server_url,
                authorization=authorization,
                allow_insecure_http=getattr(settings, "allow_insecure_http", False),
                request_timeout=settings.request_timeout_seconds,
            ),
            deadline=time() + deadline - monotonic(),
        )
    )
    if result.outcome == "unknown":
        if result.status_code is not None:
            error = HttpStatusError(result.status_code, path, result.code)
            error.outcome = "unknown"
            raise error
        raise UnknownOutcomeError
    if result.error == "server" or result.status_code in {401, 403}:
        raise HttpStatusError(result.status_code or 500, path, result.code)
    if result.error in {"invalid_request", "invalid_response"}:
        raise InvalidPreparedContextResponse
    if result.outcome not in {"ok", "empty"}:
        raise UnavailableError
    if not isinstance(result.value, dict):
        raise InvalidPreparedContextResponse
    return result.value


def scope_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return request_json(*args, **kwargs)
    except HttpStatusError as error:
        if error.status == 401:
            raise ScopeBindingRejectedError from error
        if error.status == 503:
            raise ScopeBindingUnavailableError from error
        raise ScopeBindingStatusError(error.status, error.path) from error
    except UnavailableError as error:
        raise ScopeBindingUnavailableError from error
    except (InvalidPreparedContextResponse, ValueError) as error:
        raise ScopeBindingError from error
