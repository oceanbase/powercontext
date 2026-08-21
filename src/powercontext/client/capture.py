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

"""Safe, bounded rendering helpers for agent trajectory capture."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

SENSITIVE_KEY_PARTS = ("api_key", "authorization", "cookie", "password", "secret", "token")
REDACTED = "[REDACTED]"


def render_capture_event(
    event: str,
    sequence: int,
    payload: Mapping[str, Any],
    max_bytes: int,
    *,
    schema: str | None = None,
) -> str:
    """Render a redacted capture event that never exceeds ``max_bytes``."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")  # noqa: TRY003

    safe_payload = sanitize_capture_value(payload)
    event_envelope = {"event": event, "sequence": sequence, "payload": safe_payload}
    if schema is not None:
        event_envelope["schema"] = schema
    content = redact_known_secrets(
        json.dumps(
            event_envelope,
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )
    )
    if len(content.encode("utf-8")) <= max_bytes:
        return content

    envelope = {"event": event, "sequence": sequence, "payload_excerpt": "", "truncated": True}
    if schema is not None:
        envelope["schema"] = schema
    lower_bound = 0
    upper_bound = len(content)
    rendered = json.dumps(envelope, ensure_ascii=True, sort_keys=True)
    if len(rendered.encode("utf-8")) > max_bytes:
        raise ValueError("max_bytes is too small for the capture envelope")  # noqa: TRY003

    while lower_bound <= upper_bound:
        candidate_length = (lower_bound + upper_bound) // 2
        envelope["payload_excerpt"] = content[:candidate_length]
        candidate = json.dumps(envelope, ensure_ascii=True, sort_keys=True)
        if len(candidate.encode("utf-8")) <= max_bytes:
            rendered = candidate
            lower_bound = candidate_length + 1
        else:
            upper_bound = candidate_length - 1
    return rendered


def sanitize_capture_value(value: Any) -> Any:
    """Recursively replace values belonging to credential-like keys."""

    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if is_sensitive_key(str(key)) else sanitize_capture_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize_capture_value(item) for item in value]
    return value


def redact_known_secrets(value: str) -> str:
    """Redact credential-like environment values and Codex auth values."""

    secrets = {secret for name, secret in os.environ.items() if secret and len(secret) >= 8 and is_sensitive_key(name)}
    secrets.update(_codex_auth_secrets())
    for secret in secrets:
        value = value.replace(secret, REDACTED)
    return value


def is_sensitive_key(key: str) -> bool:
    """Return whether a key name conventionally contains secret material."""

    folded = key.casefold().replace("-", "_")
    return any(part in folded for part in SENSITIVE_KEY_PARTS)


def _codex_auth_secrets() -> frozenset[str]:
    auth_path = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "auth.json"
    try:
        stat = auth_path.stat()
    except OSError:
        return frozenset()
    fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return _cached_codex_auth_secrets(str(auth_path), fingerprint)


@lru_cache(maxsize=8)
def _cached_codex_auth_secrets(
    auth_path: str,
    fingerprint: tuple[int, int, int, int, int],
) -> frozenset[str]:
    """Read Codex credentials once for each observed auth-file version."""

    del fingerprint
    try:
        auth = json.loads(Path(auth_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    return frozenset(_sensitive_values(auth))


def _sensitive_values(value: Any, *, sensitive: bool = False) -> set[str]:
    if isinstance(value, Mapping):
        secrets: set[str] = set()
        for key, item in value.items():
            secrets.update(_sensitive_values(item, sensitive=sensitive or is_sensitive_key(str(key))))
        return secrets
    if isinstance(value, list | tuple):
        secrets = set()
        for item in value:
            secrets.update(_sensitive_values(item, sensitive=sensitive))
        return secrets
    if sensitive and isinstance(value, str) and len(value) >= 8:
        return {value}
    return set()
