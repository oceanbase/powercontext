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

"""Redact credential values shared by Client capture and Runtime projections."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

SENSITIVE_KEY_PARTS = ("api_key", "authorization", "cookie", "password", "secret", "token")
SENSITIVE_KEY_COMPACT_PARTS = tuple(part.replace("_", "") for part in SENSITIVE_KEY_PARTS)
REDACTED = "[REDACTED]"


def known_secret_values(*, codex_home: Path | None = None) -> frozenset[str]:
    """Return the known secret values used by all PowerContext redaction paths."""

    secrets = {secret for name, secret in os.environ.items() if secret and len(secret) >= 8 and is_sensitive_key(name)}
    secrets.update(_codex_auth_secrets(codex_home=codex_home))
    return frozenset(secrets)


def redact_known_secrets(
    value: str,
    *,
    codex_home: Path | None = None,
    secrets: Iterable[str] | None = None,
) -> str:
    """Redact known environment and Codex-auth secret values.

    When ``secrets`` is supplied it is treated as a complete, precomputed snapshot;
    ``codex_home`` is intentionally ignored in that mode.
    """

    values = known_secret_values(codex_home=codex_home) if secrets is None else secrets
    known: Iterable[str] = sorted(values, key=len, reverse=True)
    for secret in known:
        value = value.replace(secret, REDACTED)
    return value


def is_sensitive_key(key: str) -> bool:
    """Return whether a key name conventionally contains secret material."""

    folded = "".join(character for character in key.casefold() if character.isalnum())
    return any(part in folded for part in SENSITIVE_KEY_COMPACT_PARTS)


def _codex_auth_secrets(*, codex_home: Path | None = None) -> frozenset[str]:
    root = codex_home if codex_home is not None else Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
    auth_path = root.expanduser() / "auth.json"
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
