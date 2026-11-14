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
from typing import Any, Protocol

from powercontext.builtin.authz import GroupRef, PrincipalRef

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


class MultiBearerAuthenticationProvider:
    """Reload a protected static identity map; no identity receives an implicit role.

    The JSON file contains ``identities`` with ``credential_id``, ``token_digest``
    (SHA-256), and ``subject`` (a Principal object). Rotation takes effect on the
    next request, including every management-channel heartbeat.
    """

    def __init__(self, path) -> None:
        from pathlib import Path

        self.path = Path(path)
        self._identities()

    def _identities(self) -> list[dict[str, Any]]:
        import json
        import os

        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("Identity configuration must be a regular protected file")  # noqa: TRY003
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise ValueError("Identity configuration must have mode 600")  # noqa: TRY003
        identities = json.loads(self.path.read_text(encoding="utf-8"))["identities"]
        if not isinstance(identities, list) or not identities or len(identities) > 1000:
            raise ValueError("Invalid identity configuration")  # noqa: TRY003
        keys = set()
        digests = set()
        for value in identities:
            PrincipalRef(**value["subject"])
            digest = value["token_digest"]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Identity token digests must be SHA-256")  # noqa: TRY003
            if value["credential_id"] in keys or digest in digests:
                raise ValueError("Identity credentials must be distinct")  # noqa: TRY003
            keys.add(value["credential_id"])
            digests.add(digest)
        return identities

    def principals(self) -> tuple[PrincipalRef, ...]:
        return tuple(PrincipalRef(**value["subject"]) for value in self._identities())

    async def authenticate(self, request: AuthenticationRequest, /) -> AuthenticationResult:
        from hashlib import sha256

        authorization = request.headers.get("authorization", "")
        scheme, separator, credential = authorization.partition(" ")
        if not separator or scheme.casefold() != "bearer" or not credential:
            raise AuthenticationRejectedError
        try:
            identities = self._identities()
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AuthenticationUnavailableError from error
        digest = sha256(credential.encode()).hexdigest()
        matched = None
        for value in identities:
            if compare_digest(digest, value["token_digest"]):
                matched = value
        if matched is None:
            raise AuthenticationRejectedError
        return AuthenticationResult(subject=PrincipalRef(**matched["subject"]), credential_id=matched["credential_id"])

    async def readiness(self) -> ProviderReadiness:
        try:
            self._identities()
        except (OSError, ValueError, KeyError, TypeError):
            return ProviderReadiness(ready=False, reason="identity_configuration_unavailable")
        return ProviderReadiness(ready=True)
