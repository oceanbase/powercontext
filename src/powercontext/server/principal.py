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

"""Trusted request principal propagated through HTTP and the MCP ASGI bridge."""

from __future__ import annotations

import hashlib
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrincipalRef:
    """A non-secret stable authorization identity."""

    kind: str
    subject: str

    @property
    def storage_key(self) -> str:
        """Return an opaque bounded key suitable for coordination tables."""

        return hashlib.sha256(f"{self.kind}\0{self.subject}".encode()).hexdigest()


_CURRENT_PRINCIPAL: ContextVar[PrincipalRef | None] = ContextVar("powercontext_principal", default=None)


def bind_principal(principal: PrincipalRef) -> Token[PrincipalRef | None]:
    return _CURRENT_PRINCIPAL.set(principal)


def current_principal() -> PrincipalRef | None:
    return _CURRENT_PRINCIPAL.get()


def reset_principal(token: Token[PrincipalRef | None]) -> None:
    _CURRENT_PRINCIPAL.reset(token)


__all__ = ["PrincipalRef", "bind_principal", "current_principal", "reset_principal"]
