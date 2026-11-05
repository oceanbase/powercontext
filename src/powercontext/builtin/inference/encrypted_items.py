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
payload that the parent's model produced inside ``spawn_agent`` arguments. Codex declares such arguments with
``"encrypted": true`` in the tool definition. Core cannot inspect these values. For a target that opts in, they are
admitted only at these exact positions and only when a receipt shows that Core released them earlier; everything
else stays opaque.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from hashlib import sha256
from typing import Any, Literal, cast

EncryptedItemKind = Literal["reasoning", "agent_message"]

# Values one receipt holds: the encrypted values one request carries, or one response releases. With 16-byte
# digests a full receipt seals to about 30 KB, inside the binding codec's 64 KiB token limit.
MAX_RECEIPT_VALUES = 1024
# Encrypted argument fields one request may declare.
MAX_ENCRYPTED_FIELDS = 32
_DIGEST_BYTES = 16

EncryptedField = tuple[str, str, str]
"""A declared encrypted argument: tool namespace (empty when none), tool name and top-level parameter."""


def value_digest(value: str) -> bytes:
    return sha256(value.encode("utf-8")).digest()[:_DIGEST_BYTES]


def pack_digests(digests: Iterable[bytes]) -> str:
    return base64.urlsafe_b64encode(b"".join(sorted(set(digests)))).decode("ascii")


def unpack_digests(packed: object) -> frozenset[bytes]:
    if not isinstance(packed, str):
        return frozenset()
    try:
        raw = base64.urlsafe_b64decode(packed.encode("ascii"))
    except (ValueError, UnicodeError):
        return frozenset()
    if len(raw) % _DIGEST_BYTES:
        return frozenset()
    return frozenset(raw[i : i + _DIGEST_BYTES] for i in range(0, len(raw), _DIGEST_BYTES))


def admitted_encrypted(
    value: object, kinds: tuple[EncryptedItemKind, ...]
) -> tuple[object, tuple[tuple[EncryptedItemKind, str], ...]]:
    """Return a copy of a Responses body without encrypted values at admitted positions, and those values by kind.

    Positions: the ``encrypted_content`` string of a root-level ``reasoning`` item, and an ``encrypted_content``
    part of a root-level ``agent_message`` item, in the request ``input`` or the response ``output``. Encrypted
    values anywhere else are left in place, so they remain opaque to the normal inspection.
    """

    if not kinds or not isinstance(value, dict):
        return value, ()
    found: list[tuple[EncryptedItemKind, str]] = []
    copy = dict(value)
    for field in ("input", "output"):
        items = value.get(field)
        if isinstance(items, list):
            copy[field] = [_strip_item(item, kinds, found) for item in items]
    return copy, tuple(found)


def _strip_item(
    item: object, kinds: tuple[EncryptedItemKind, ...], found: list[tuple[EncryptedItemKind, str]]
) -> object:
    if not isinstance(item, dict):
        return item
    kind = item.get("type")
    encrypted = item.get("encrypted_content")
    if kind == "reasoning" and "reasoning" in kinds and isinstance(encrypted, str):
        found.append(("reasoning", encrypted))
        return {key: child for key, child in item.items() if key != "encrypted_content"}
    content = item.get("content")
    if kind == "agent_message" and "agent_message" in kinds and isinstance(content, list):
        kept = []
        for part in content:
            # Only a part that is exactly the ciphertext is set aside; any other field keeps the whole part in the
            # normal inspection.
            fields = cast("dict[str, Any]", part) if isinstance(part, dict) else {}
            payload = fields.get("encrypted_content")
            if (
                set(fields) == {"type", "encrypted_content"}
                and fields.get("type") == "encrypted_content"
                and isinstance(payload, str)
            ):
                found.append(("agent_message", payload))
            else:
                kept.append(part)
        return {**item, "content": kept}
    return item


def declared_encrypted_fields(value: object) -> tuple[EncryptedField, ...] | None:
    """Argument fields a request's tool definitions declare ``"encrypted": true``.

    Tools are read from the top-level ``tools`` and from Codex's ``additional_tools`` input items, including tools
    grouped in a ``namespace``. Returns ``None`` when more than :data:`MAX_ENCRYPTED_FIELDS` are declared.
    """

    if not isinstance(value, dict):
        return ()
    groups: list[object] = [value.get("tools")]
    items = value.get("input")
    if isinstance(items, list):
        groups.extend(
            item.get("tools") for item in items if isinstance(item, dict) and item.get("type") == "additional_tools"
        )
    found: set[EncryptedField] = set()
    for group in groups:
        _collect_fields(group, "", found)
    return None if len(found) > MAX_ENCRYPTED_FIELDS else tuple(sorted(found))


def _collect_fields(tools: object, namespace: str, found: set[EncryptedField]) -> None:
    if not isinstance(tools, list):
        return
    for tool in tools:
        definition = cast("dict[str, Any]", tool) if isinstance(tool, dict) else {}
        name = definition.get("name")
        if not isinstance(name, str):
            continue
        if definition.get("type") == "namespace" and not namespace:
            _collect_fields(definition.get("tools"), name, found)
            continue
        parameters = definition.get("parameters")
        properties: dict[str, Any] = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        if isinstance(properties, dict):
            found.update(
                (namespace, name, field)
                for field, schema in properties.items()
                if isinstance(schema, dict) and schema.get("encrypted") is True
            )


def released_argument_values(value: object, fields: Iterable[EncryptedField]) -> tuple[str, ...]:
    """String values a response's function calls carry in declared encrypted argument fields.

    A provider returns such a value encrypted inside ordinary arguments, for example the task of Codex's
    ``spawn_agent``, which the client later sends back as an ``encrypted_content`` part. Recording it in the receipt
    lets that later request prove the value came from a released response. Other argument values are not recorded.
    """

    return _argument_values(value.get("output") if isinstance(value, dict) else None, fields)


def replayed_argument_values(value: object, fields: Iterable[EncryptedField]) -> tuple[str, ...]:
    """String values a request's replayed function calls carry in declared encrypted argument fields.

    The conversation history replays released calls verbatim, so their encrypted values stay in ordinary
    arguments while the client keeps talking. A value here holds its proof only through the receipts the request
    presents; consolidation re-covers the proven ones and leaves unproven ones unreceipted.
    """

    return _argument_values(value.get("input") if isinstance(value, dict) else None, fields)


def _argument_values(items: object, fields: Iterable[EncryptedField]) -> tuple[str, ...]:
    declared: dict[tuple[str, str], set[str]] = {}
    for namespace, name, field in fields:
        declared.setdefault((namespace, name), set()).add(field)
    if not declared or not isinstance(items, list):
        return ()
    found: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        namespace, name, raw = item.get("namespace"), item.get("name"), item.get("arguments")
        if not isinstance(name, str) or not isinstance(raw, str):
            continue
        wanted = declared.get((namespace if isinstance(namespace, str) else "", name))
        if not wanted:
            continue
        try:
            arguments = json.loads(raw)
        except ValueError:
            continue
        if isinstance(arguments, dict):
            found.extend(argument for field in wanted if isinstance(argument := arguments.get(field), str))
    return tuple(found)
