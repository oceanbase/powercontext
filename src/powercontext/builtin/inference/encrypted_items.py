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

"""Provider-encrypted Responses items that an opted-in target may carry.

Codex sends ``store: false`` with ``include: ["reasoning.encrypted_content"]``, so every response carries
encrypted reasoning that later requests replay, and a subagent receives its task as an encrypted ``agent_message``
payload that the parent's model produced inside ``spawn_agent`` arguments. Core cannot inspect these values. For a
target that opts in, they are admitted only at these exact positions and only when a receipt shows that Core
released them earlier; everything else stays opaque.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal

EncryptedItemKind = Literal["reasoning", "agent_message"]

# Bounds for one receipt: values recorded from one released response.
MAX_RECEIPT_VALUES = 64
_MAX_ARGUMENT_DEPTH = 8


def value_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def admitted_encrypted(value: object, kinds: tuple[EncryptedItemKind, ...]) -> tuple[object, tuple[str, ...]]:
    """Return a copy of a Responses body without encrypted values at admitted positions, and those values.

    Positions: the ``encrypted_content`` string of a root-level ``reasoning`` item, and an ``encrypted_content``
    part of a root-level ``agent_message`` item, in the request ``input`` or the response ``output``. Encrypted
    values anywhere else are left in place, so they remain opaque to the normal inspection.
    """

    if not kinds or not isinstance(value, dict):
        return value, ()
    found: list[str] = []
    copy = dict(value)
    for field in ("input", "output"):
        items = value.get(field)
        if isinstance(items, list):
            copy[field] = [_strip_item(item, kinds, found) for item in items]
    return copy, tuple(found)


def _strip_item(item: object, kinds: tuple[EncryptedItemKind, ...], found: list[str]) -> object:
    if not isinstance(item, dict):
        return item
    kind = item.get("type")
    encrypted = item.get("encrypted_content")
    if kind == "reasoning" and "reasoning" in kinds and isinstance(encrypted, str):
        found.append(encrypted)
        return {key: child for key, child in item.items() if key != "encrypted_content"}
    content = item.get("content")
    if kind == "agent_message" and "agent_message" in kinds and isinstance(content, list):
        kept = []
        for part in content:
            payload = part.get("encrypted_content") if isinstance(part, dict) else None
            if isinstance(part, dict) and part.get("type") == "encrypted_content" and isinstance(payload, str):
                found.append(payload)
            else:
                kept.append(part)
        return {**item, "content": kept}
    return item


def released_argument_strings(value: object) -> tuple[str, ...]:
    """String values in the arguments of a response's function calls.

    A provider can return a value encrypted inside ordinary arguments, for example the task of Codex's
    ``spawn_agent``, which the client later sends back as an ``encrypted_content`` part. Recording these strings in
    the receipt lets that later request prove the value came from a released response.
    """

    output = value.get("output") if isinstance(value, dict) else None
    if not isinstance(output, list):
        return ()
    found: list[str] = []
    for item in output:
        raw = item.get("arguments") if isinstance(item, dict) and item.get("type") == "function_call" else None
        if isinstance(raw, str):
            try:
                arguments = json.loads(raw)
            except ValueError:
                continue
            _collect_strings(arguments, found, 0)
    return tuple(found)


def _collect_strings(value: Any, found: list[str], depth: int) -> None:
    if depth > _MAX_ARGUMENT_DEPTH:
        return
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            _collect_strings(child, found, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _collect_strings(child, found, depth + 1)
