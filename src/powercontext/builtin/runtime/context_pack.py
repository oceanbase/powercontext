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

"""Deterministic chunked Context Packs and prefix-reusing deltas.

A full pack splits one prepared context into bounded, self-contained UTF-8
chunks and carries a digest of its own chunk list. A delta keeps a prefix of an
already delivered pack and appends the remaining chunks, so a receiver that only
holds the previous wire bytes can rebuild the current context exactly.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512


class ContextPackError(ValueError):
    """Raised when a pack or delta violates the wire protocol."""


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_of(schema: str, items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": schema, "items": [base64.b64encode(item).decode("ascii") for item in items]}
    return "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


@dataclass(frozen=True)
class ContextPack:
    """One complete, self-describing context payload."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int
    schema: str = field(default=CONTEXT_PACK_SCHEMA)

    @classmethod
    def build(cls, items: tuple[bytes, ...] | list[bytes]) -> ContextPack:
        """Build a full pack from already chunked bytes."""

        chunks = tuple(items)
        for item in chunks:
            if not isinstance(item, (bytes, bytearray, memoryview)):
                raise ContextPackError("pack items must be bytes")  # noqa: TRY003
            _require_utf8(bytes(item))
        encoded = tuple(bytes(item) for item in chunks)
        digest = _digest_of(CONTEXT_PACK_SCHEMA, encoded)
        byte_count = 0
        for _ in range(8):
            size = len(_canonical(_pack_payload(encoded, digest, byte_count)))
            if size == byte_count:
                break
            byte_count = size
        return cls(items=encoded, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split text into bounded, valid UTF-8 chunks that rejoin to the input."""

        if not isinstance(content, str):
            raise ContextPackError("pack content must be text")  # noqa: TRY003
        if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        if not content:
            return cls.build(())
        return cls.build(_chunk_text(content, chunk_bytes))

    @classmethod
    def from_bytes(cls, payload: bytes, /) -> ContextPack:
        """Decode and verify one serialized full pack."""

        document = _decode_json(payload)
        if set(document) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack must contain exactly schema, items, digest and byte_count")  # noqa: TRY003
        if document["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("unsupported pack schema")  # noqa: TRY003
        items = _decode_item_array(document["items"], "pack items")
        digest = document["digest"]
        if not isinstance(digest, str) or digest != _digest_of(CONTEXT_PACK_SCHEMA, items):
            raise ContextPackError("pack digest does not match its items")  # noqa: TRY003
        byte_count = document["byte_count"]
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise ContextPackError("pack byte_count must be a non-negative integer")  # noqa: TRY003
        if byte_count != len(bytes(payload)):
            raise ContextPackError("pack byte_count does not match the serialized size")  # noqa: TRY003
        for item in items:
            _require_utf8(item)
        return cls(items=items, digest=digest, byte_count=byte_count)

    def to_bytes(self) -> bytes:
        """Serialize the pack in canonical wire form."""

        return _canonical(_pack_payload(self.items, self.digest, self.byte_count))


@dataclass(frozen=True)
class ContextPackDelta:
    """One incremental payload that reuses a prefix of an earlier pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Reuse the common chunk prefix and carry the remaining target chunks."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta requires two context packs")  # noqa: TRY003
        prefix = 0
        limit = min(len(base.items), len(target.items))
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @classmethod
    def from_bytes(cls, payload: bytes, /) -> ContextPackDelta:
        """Decode and verify one serialized delta."""

        document = _decode_json(payload)
        if set(document) != {"schema", "base_digest", "target_digest", "prefix_items", "appended"}:
            raise ContextPackError("delta wire fields are incomplete or unexpected")  # noqa: TRY003
        if document["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("unsupported delta schema")  # noqa: TRY003
        base_digest = _require_digest(document["base_digest"], "base_digest")
        target_digest = _require_digest(document["target_digest"], "target_digest")
        prefix = document["prefix_items"]
        if not isinstance(prefix, int) or isinstance(prefix, bool) or prefix < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")  # noqa: TRY003
        appended = _decode_item_array(document["appended"], "delta appended")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix,
            appended=appended,
        )

    @property
    def byte_count(self) -> int:
        """Serialized size of this delta, including its protocol fields."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize the delta in canonical wire form."""

        return _canonical(
            {
                "schema": CONTEXT_PACK_DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from an earlier pack and one delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta application requires a pack and a delta")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest does not match the supplied pack")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base pack")  # noqa: TRY003
    rebuilt = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("delta does not rebuild the declared target pack")  # noqa: TRY003
    return rebuilt


def _pack_payload(items: tuple[bytes, ...], digest: str, byte_count: int) -> dict[str, Any]:
    return {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": _encode_items(items),
        "digest": digest,
        "byte_count": byte_count,
    }


def _decode_json(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ContextPackError("serialized payload must be bytes")  # noqa: TRY003
    try:
        document = json.loads(bytes(payload))
    except (ValueError, UnicodeDecodeError) as error:
        raise ContextPackError("payload is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ContextPackError("payload must be a JSON object")  # noqa: TRY003
    return document


def _decode_item_array(value: object, label: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{label} must be a list")  # noqa: TRY003
    decoded: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError(f"{label} must contain Base64 strings")  # noqa: TRY003
        try:
            decoded.append(base64.b64decode(entry.encode("ascii"), validate=True))
        except (binascii.Error, UnicodeEncodeError, ValueError) as error:
            raise ContextPackError(f"{label} contains invalid Base64") from error
    return tuple(decoded)


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ContextPackError(f"{label} must be a sha256 digest")  # noqa: TRY003
    try:
        int(value[len("sha256:") :], 16)
    except ValueError as error:
        raise ContextPackError(f"{label} must be a sha256 digest") from error
    return value


def _require_utf8(item: bytes) -> None:
    try:
        item.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContextPackError("pack items must be valid UTF-8") from error


def _chunk_text(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    """Pack whole lines into bounded chunks, splitting only oversized lines."""

    chunks: list[bytes] = []
    current: list[str] = []
    current_bytes = 0

    lines = content.splitlines(keepends=True)
    if not lines:
        lines = [content]

    for line in lines:
        encoded = line.encode("utf-8")
        if len(encoded) > chunk_bytes:
            if current:
                chunks.append("".join(current).encode("utf-8"))
                current, current_bytes = [], 0
            chunks.extend(_split_oversized(encoded, chunk_bytes))
            continue
        if current_bytes + len(encoded) > chunk_bytes:
            chunks.append("".join(current).encode("utf-8"))
            current, current_bytes = [], 0
        current.append(line)
        current_bytes += len(encoded)

    if current:
        chunks.append("".join(current).encode("utf-8"))
    return tuple(chunks)


def _split_oversized(line: bytes, chunk_bytes: int) -> list[bytes]:
    """Split one over-long line on character boundaries within the byte cap."""

    chunks: list[bytes] = []
    buffer = bytearray()
    for char in line.decode("utf-8"):
        encoded = char.encode("utf-8")
        if len(encoded) > chunk_bytes:
            raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")  # noqa: TRY003
        if len(buffer) + len(encoded) > chunk_bytes:
            chunks.append(bytes(buffer))
            buffer = bytearray()
        buffer.extend(encoded)
    if buffer:
        chunks.append(bytes(buffer))
    return chunks


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
