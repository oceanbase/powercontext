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

"""Small, deterministic privacy and digest helpers for controlled bundles."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "authorization_header",
        "client_secret",
        "credential",
        "credentials",
        "evidence",
        "evidence_body",
        "password",
        "prompt",
        "prompt_body",
        "refresh_token",
        "secret",
        "task",
        "task_body",
        "token",
        "token_value",
    }
)
_SENSITIVE_SUFFIXES = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "evidence",
        "password",
        "prompt",
        "secret",
        "task",
        "token",
    }
)
_CONTENT_QUALIFIERS = frozenset({"body", "content", "description", "payload", "text", "value"})
_CONTENT_KEYS = frozenset({"evidence", "prompt", "task", "token"})
_SECRET_VALUE = re.compile(r"(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_]+)")
REDACTED = "[REDACTED]"


def canonical_json(value: Any) -> bytes:
    """Return a stable UTF-8 JSON representation for evaluation digests."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_json(value: Any) -> str:
    """Return a plain lowercase SHA-256 digest without content disclosure."""

    return hashlib.sha256(canonical_json(value)).hexdigest()


def redact_payload(value: Any, *, _sensitive: bool = False) -> Any:
    """Recursively redact sensitive keys and recognizable credential strings."""

    if _sensitive:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(key): redact_payload(item, _sensitive=_is_sensitive_key(str(key))) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(REDACTED, value)
    return value


def _is_sensitive_key(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    if normalized in _SENSITIVE_KEYS or any(normalized.endswith(f"_{key}") for key in _SENSITIVE_SUFFIXES):
        return True
    segments = normalized.split("_")
    return any(
        segments[index] in _CONTENT_KEYS and segments[index + 1] in _CONTENT_QUALIFIERS
        for index in range(len(segments) - 1)
    )
