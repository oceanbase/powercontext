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

"""Signed opaque cursor codec shared by relational collection APIs."""

from __future__ import annotations

import base64
import binascii
import hmac
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import rfc8785
from pydantic import JsonValue, TypeAdapter, ValidationError

from powercontext.builtin.records import CursorExpiredError, InvalidBaseAccessRequestError, InvalidCursorError

Clock = Callable[[], datetime]

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class SignedCursorCodec:
    """Encode and validate endpoint-bound cursor positions."""

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        clock: Clock | None = None,
        ttl_seconds: int = 3_600,
    ) -> None:
        if isinstance(ttl_seconds, bool) or ttl_seconds < 1:
            raise ValueError("ttl_seconds must be a positive integer")  # noqa: TRY003
        if secret is not None and not secret:
            raise ValueError("secret must not be empty")  # noqa: TRY003
        self.secret = secrets.token_bytes(32) if secret is None else secret
        self.clock = _utc_now if clock is None else clock
        self.ttl = timedelta(seconds=ttl_seconds)

    def encode(self, expected: Mapping[str, JsonValue], after: int | str) -> str:
        expires_at = _aware_datetime(self.clock()) + self.ttl
        payload = {**expected, "after": after, "expires_at": int(expires_at.timestamp())}
        encoded = rfc8785.dumps(cast(Any, payload))
        signature = hmac.digest(self.secret, encoded, "sha256")
        return f"{_encode_token_part(encoded)}.{_encode_token_part(signature)}"

    def after_text(self, cursor: str | None, expected: Mapping[str, JsonValue]) -> str:
        payload = self._payload(cursor, expected)
        if payload is None:
            return ""
        after = payload.get("after")
        if not isinstance(after, (int, str)) or isinstance(after, bool):
            raise InvalidCursorError
        return str(after)

    def _payload(
        self,
        cursor: str | None,
        expected: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        if cursor is None:
            return None
        try:
            encoded_payload, encoded_signature = cursor.split(".")
            decoded = _decode_token_part(encoded_payload)
            signature = _decode_token_part(encoded_signature)
            payload = _JSON_OBJECT.validate_json(decoded, strict=True)
        except (binascii.Error, UnicodeEncodeError, ValueError, ValidationError) as error:
            raise InvalidCursorError from error
        if not hmac.compare_digest(signature, hmac.digest(self.secret, decoded, "sha256")):
            raise InvalidCursorError
        expires_at = payload.get("expires_at")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool):
            raise InvalidCursorError
        if any(payload.get(key) != value for key, value in expected.items()):
            raise InvalidCursorError
        if set(payload) != {*expected, "after", "expires_at"}:
            raise InvalidCursorError
        if int(_aware_datetime(self.clock()).timestamp()) >= expires_at:
            raise CursorExpiredError
        return payload


def _encode_token_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_token_part(value: str) -> bytes:
    encoded = value.encode("ascii")
    return base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidBaseAccessRequestError("timestamp", "must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["Clock", "SignedCursorCodec"]
