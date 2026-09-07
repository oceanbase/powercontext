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

"""Request-local context shared by Server transport adapters."""

from __future__ import annotations

from contextvars import ContextVar, Token

from powercontext.server.authentication import AuthenticationResult
from powercontext.server.authz import PrincipalRef

_internal_bridge: ContextVar[bool] = ContextVar("powercontext_internal_bridge", default=False)
_request_id: ContextVar[str | None] = ContextVar("powercontext_request_id", default=None)
_principal: ContextVar[PrincipalRef | None] = ContextVar("powercontext_principal", default=None)
_authentication: ContextVar[AuthenticationResult | None] = ContextVar("powercontext_authentication", default=None)


def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def bind_principal(principal: PrincipalRef) -> Token[PrincipalRef | None]:
    return _principal.set(principal)


def reset_principal(token: Token[PrincipalRef | None]) -> None:
    _principal.reset(token)


def current_principal() -> PrincipalRef | None:
    return _principal.get()


def bind_authentication(
    result: AuthenticationResult,
) -> tuple[Token[AuthenticationResult | None], Token[PrincipalRef | None]]:
    """Bind one immutable trusted authentication result for the request lifetime."""

    return _authentication.set(result), _principal.set(result.subject)


def reset_authentication(tokens: tuple[Token[AuthenticationResult | None], Token[PrincipalRef | None]]) -> None:
    authentication_token, principal_token = tokens
    _principal.reset(principal_token)
    _authentication.reset(authentication_token)


def current_authentication() -> AuthenticationResult | None:
    return _authentication.get()


def bind_internal_bridge() -> Token[bool]:
    return _internal_bridge.set(True)


def reset_internal_bridge(token: Token[bool]) -> None:
    _internal_bridge.reset(token)


def is_internal_bridge() -> bool:
    return _internal_bridge.get()


__all__ = [
    "bind_authentication",
    "bind_internal_bridge",
    "bind_principal",
    "bind_request_id",
    "current_authentication",
    "current_principal",
    "current_request_id",
    "is_internal_bridge",
    "reset_authentication",
    "reset_internal_bridge",
    "reset_principal",
    "reset_request_id",
]
