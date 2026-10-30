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

"""Authenticated encrypted, purpose-bound ephemeral operation metadata."""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError


class ContextBindingError(InvalidRuntimeRequestError):
    """A binding cannot grant the requested operation; errors contain no token data."""


class BindingCodec:
    """Seal small private metadata with independent deployment, generation, and purpose.

    A caller-supplied key must contain exactly 32 random bytes. Keeping the key,
    key ID, deployment and generation stable permits another trusted process to
    validate a binding; the caller still has to check current authorization.
    """

    def __init__(
        self,
        *,
        key: bytes | None = None,
        key_id: str = "ephemeral",
        deployment_id: str = "local",
        generation: str | None = None,
        max_token_bytes: int = 65536,
    ) -> None:
        if key is not None and len(key) != 32:
            raise ValueError("context binding keys must contain 32 bytes")  # noqa: TRY003
        if not key_id or not deployment_id or max_token_bytes < 256:
            raise ValueError("invalid context binding configuration")  # noqa: TRY003
        self.key_id = key_id
        self.deployment_id = deployment_id
        self.generation = generation or (secrets.token_hex(16) if key is None else key_id)
        self.max_token_bytes = max_token_bytes
        self._cipher = AESGCM(secrets.token_bytes(32) if key is None else key)

    def _audience(self, purpose: str) -> bytes:
        return json.dumps(
            ["powercontext.binding.v1", self.key_id, self.deployment_id, self.generation, purpose],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")

    def seal(self, payload: dict[str, Any], *, purpose: str, expires_at: datetime) -> str:
        if expires_at.tzinfo is None:
            raise ValueError("binding expiry must include a time zone")  # noqa: TRY003
        body = json.dumps(
            {"payload": payload, "expires_at": expires_at.astimezone(UTC).isoformat()},
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(body) > self.max_token_bytes:
            raise ContextBindingError("context-binding-too-large")
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, body, self._audience(purpose))
        token = "pc1." + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        if len(token) > self.max_token_bytes:
            raise ContextBindingError("context-binding-too-large")
        return token

    def open(self, token: str, *, purpose: str, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None:
            raise ValueError("binding validation time must include a time zone")  # noqa: TRY003
        if not isinstance(token, str) or len(token) > self.max_token_bytes or not token.startswith("pc1."):
            raise ContextBindingError("context-binding-invalid")
        try:
            raw = base64.b64decode(token[4:].encode("ascii"), altchars=b"-_", validate=True)
            if len(raw) < 29 or base64.urlsafe_b64encode(raw).decode("ascii") != token[4:]:
                raise ValueError  # noqa: TRY301 - collapse invalid authenticated envelopes to one safe error
            body = self._cipher.decrypt(raw[:12], raw[12:], self._audience(purpose))
            value = json.loads(body)
            expires = datetime.fromisoformat(value["expires_at"])
            payload = value["payload"]
            if expires.tzinfo is None or not isinstance(payload, dict):
                raise ValueError  # noqa: TRY301 - collapse invalid authenticated envelopes to one safe error
        except (InvalidTag, ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as error:
            raise ContextBindingError("context-binding-invalid") from error
        if now.astimezone(UTC) >= expires:
            raise ContextBindingError("context-binding-expired")
        return payload
