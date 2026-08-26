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

"""Build a PowerContext client from LangChain settings and invocation scope."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr

from powercontext.client import PowerContextClient

from .scope import PowerContextScope, resolve_scope_id
from .settings import PowerContextLangChainSettings

_SHARED_HTTP_CLIENT: ContextVar[httpx.AsyncClient | None] = ContextVar(
    "powercontext_langchain_http_client", default=None
)


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """Effective connection configuration for one middleware operation."""

    base_url: str
    scope_id: str
    token: str | None = field(repr=False)
    timeout: float
    max_bytes: int


def resolve_config(
    scope: PowerContextScope | None = None,
    *,
    settings: PowerContextLangChainSettings | None = None,
) -> ResolvedConfig:
    """Overlay an invocation scope onto the LangChain environment settings."""

    resolved_settings = settings or PowerContextLangChainSettings()
    resolved_scope = scope or PowerContextScope()
    token = resolved_scope.token if resolved_scope.token is not None else _secret_value(resolved_settings.token)
    return ResolvedConfig(
        base_url=(resolved_scope.base_url or resolved_settings.base_url).strip(),
        scope_id=resolve_scope_id(resolved_scope.scope_id or resolved_settings.scope_id),
        token=token,
        timeout=resolved_scope.timeout if resolved_scope.timeout is not None else resolved_settings.timeout,
        max_bytes=resolved_settings.max_bytes,
    )


def _secret_value(secret: SecretStr | None) -> str | None:
    return secret.get_secret_value() if secret is not None else None


def open_client(config: ResolvedConfig) -> PowerContextClient:
    """Return a client for one operation, borrowing a shared HTTP client when installed."""

    shared = _SHARED_HTTP_CLIENT.get()
    if shared is not None:
        return PowerContextClient(config.base_url, token=config.token, http_client=shared)
    return PowerContextClient(config.base_url, token=config.token, timeout=config.timeout)


@contextmanager
def shared_http_client(client: httpx.AsyncClient) -> Iterator[None]:
    """Install a shared HTTP client for the duration of a block."""

    token = _SHARED_HTTP_CLIENT.set(client)
    try:
        yield
    finally:
        _SHARED_HTTP_CLIENT.reset(token)


__all__ = ["ResolvedConfig", "open_client", "resolve_config", "shared_http_client"]
