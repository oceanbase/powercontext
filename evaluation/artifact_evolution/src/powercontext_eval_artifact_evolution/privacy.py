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

_SENSITIVE_KEY = re.compile(
    r"(?:prompt|task|evidence|authorization|token|secret|password|credential|api[_-]?key)",
    re.IGNORECASE,
)
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
        return {
            str(key): redact_payload(item, _sensitive=bool(_SENSITIVE_KEY.search(str(key))))
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(REDACTED, value)
    return value
