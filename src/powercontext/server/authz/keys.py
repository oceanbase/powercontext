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

"""Deterministic idempotency keys for Access owner writes."""

from __future__ import annotations

import json
from hashlib import sha256

IDEMPOTENCY_KEY_LIMIT = 255


def bounded_idempotency_key(prefix: str, *identity: str) -> str:
    """Build a stable key that Access accepts for any identity width.

    Accepted keys keep their historical spelling. Identities that would exceed
    the Access limit are replaced by a digest of every component, so distinct
    identities cannot collapse into the same key.
    """
    key = f"{prefix}:{':'.join(identity)}"
    if len(key) <= IDEMPOTENCY_KEY_LIMIT:
        return key
    encoded = json.dumps((prefix, *identity), ensure_ascii=False, separators=(",", ":")).encode()
    return f"{prefix}:{sha256(encoded).hexdigest()}"


__all__ = ("IDEMPOTENCY_KEY_LIMIT", "bounded_idempotency_key")
