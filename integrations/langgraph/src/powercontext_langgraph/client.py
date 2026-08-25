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

"""Build a :class:`PowerContextClient` from settings and optional run scope."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr

from powercontext.client import PowerContextClient

from .scope import PowerContextScope, resolve_scope_id
from .settings import PowerContextLangGraphSettings

# A shared HTTP client lets a long-running deployment reuse one connection pool across nodes and tools, and lets tests
# route requests to an in-process ASGI app. When set, per-operation clients borrow it and never close it.
_SHARED_HTTP_CLIENT: ContextVar[httpx.AsyncClient | None] = ContextVar(
    "powercontext_langgraph_http_client", default=None
)


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """Effective connection configuration for one operation."""

    base_url: str
    scope_id: str
    # Plain bearer token forwarded to the client for the ``Authorization`` header; hidden from the repr so it never
    # surfaces in a traceback or trace of the resolved configuration.
    token: str | None = field(repr=False)
    timeout: float
    max_bytes: int


def resolve_config(
    scope: PowerContextScope | None = None,
    *,
    settings: PowerContextLangGraphSettings | None = None,
) -> ResolvedConfig:
    """Overlay an optional run scope onto the environment settings and resolve the scope id."""

    resolved_settings = settings or PowerContextLangGraphSettings()
    scope = scope or PowerContextScope()
    # The scope token is a plain str; the settings token is a SecretStr. Unwrap to a plain str at this boundary so the
    # client can compose the ``Authorization`` header, while the stored settings/scope fields stay repr-safe.
    token = scope.token if scope.token is not None else _secret_value(resolved_settings.token)
    return ResolvedConfig(
        base_url=(scope.base_url or resolved_settings.base_url).strip(),
        scope_id=resolve_scope_id(scope.scope_id or resolved_settings.scope_id),
        token=token,
        timeout=scope.timeout if scope.timeout is not None else resolved_settings.timeout,
        max_bytes=resolved_settings.max_bytes,
    )


def _secret_value(secret: SecretStr | None) -> str | None:
    return secret.get_secret_value() if secret is not None else None


def open_client(config: ResolvedConfig) -> PowerContextClient:
    """Open an async client for one operation. The caller closes it with ``async with``.

    When a shared client is installed the operation borrows it; ``PowerContextClient.aclose`` then closes only the
    per-operation client it created, which is none, so the shared client stays open. The borrowed client also keeps
    its own timeout configuration, so ``config.timeout`` applies only when no shared client is installed.
    """

    shared = _SHARED_HTTP_CLIENT.get()
    if shared is not None:
        return PowerContextClient(config.base_url, token=config.token, http_client=shared)
    return PowerContextClient(config.base_url, token=config.token, timeout=config.timeout)


@contextmanager
def shared_http_client(client: httpx.AsyncClient) -> Iterator[None]:
    """Install a shared HTTP client for the duration of the block.

    Operations that borrow it use its timeout configuration, overriding the resolved
    ``PowerContextScope(timeout=...)`` / ``POWERCONTEXT_LANGGRAPH_TIMEOUT`` value.
    """

    token = _SHARED_HTTP_CLIENT.set(client)
    try:
        yield
    finally:
        _SHARED_HTTP_CLIENT.reset(token)
