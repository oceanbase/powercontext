# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Authentication Provider SPI kept outside Runtime domain models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from secrets import compare_digest
from typing import Protocol

from powercontext.server.authz import GroupRef, PrincipalRef

_MAX_AUTHENTICATED_GROUPS = 100


@dataclass(frozen=True, slots=True)
class AuthenticationRequest:
    """Transport credential carrier supplied only to an Authentication Provider."""

    transport: str
    headers: Mapping[str, str]
    client_host: str | None


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """Immutable trusted identity facts for one request."""

    subject: PrincipalRef
    actor: PrincipalRef | None = None
    subject_groups: tuple[GroupRef, ...] = ()
    credential_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subject, PrincipalRef) or (
            self.actor is not None and not isinstance(self.actor, PrincipalRef)
        ):
            raise ValueError("Authentication Provider returned an invalid Principal")  # noqa: TRY003
        if not isinstance(self.subject_groups, tuple) or len(self.subject_groups) > _MAX_AUTHENTICATED_GROUPS:
            raise ValueError("Authentication Provider returned invalid groups")  # noqa: TRY003
        group_ids = [group.id for group in self.subject_groups if isinstance(group, GroupRef)]
        if (
            len(group_ids) != len(self.subject_groups)
            or len(group_ids) != len(set(group_ids))
            or self.subject.id in group_ids
            or (self.actor is not None and self.actor.id in group_ids)
        ):
            raise ValueError("Authentication Provider returned invalid groups")  # noqa: TRY003
        if self.credential_id is not None and (
            not self.credential_id.strip()
            or self.credential_id != self.credential_id.strip()
            or len(self.credential_id) > 255
        ):
            raise ValueError("Authentication Provider returned an invalid credential ID")  # noqa: TRY003


@dataclass(frozen=True, slots=True)
class ProviderReadiness:
    """Low-sensitivity readiness result for a required authentication dependency."""

    ready: bool
    reason: str | None = None


class AuthenticationRejectedError(PermissionError):
    """The request did not carry a valid credential."""


class AuthenticationUnavailableError(RuntimeError):
    """The configured Authentication Provider cannot currently decide."""


class AuthenticationProvider(Protocol):
    """Authenticate transport credentials into canonical deployment identities."""

    async def authenticate(self, request: AuthenticationRequest, /) -> AuthenticationResult: ...

    async def readiness(self) -> ProviderReadiness: ...


class StaticBearerAuthenticationProvider:
    """Map one constant-time validated bearer token to one fixed service Principal."""

    def __init__(self, token: str, principal: PrincipalRef) -> None:
        if not token:
            raise ValueError("Bearer token must not be empty")  # noqa: TRY003
        self._token = token.encode()
        self._principal = principal

    async def authenticate(self, request: AuthenticationRequest, /) -> AuthenticationResult:
        authorization = request.headers.get("authorization")
        if authorization is None:
            raise AuthenticationRejectedError
        scheme, separator, credential = authorization.partition(" ")
        if (
            not separator
            or scheme.casefold() != "bearer"
            or not credential
            or not compare_digest(credential.encode(), self._token)
        ):
            raise AuthenticationRejectedError
        return AuthenticationResult(subject=self._principal, credential_id="static-bearer")

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True)


__all__ = (
    "AuthenticationProvider",
    "AuthenticationRejectedError",
    "AuthenticationRequest",
    "AuthenticationResult",
    "AuthenticationUnavailableError",
    "ProviderReadiness",
    "StaticBearerAuthenticationProvider",
)
