"""Canonical Memory encodings, hashes, validation, and search analysis."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from hashlib import sha256
from itertools import pairwise
from typing import cast

import rfc8785
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError, field_validator

from powercontext.builtin.artifacts.memory.models import MemoryContent

_ENTRY_HASH_DOMAIN = b"powercontext:entry-content:v1\0"
_EMBEDDING_HASH_DOMAIN = b"powercontext:embedding-content:v1\0"
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


class _CanonicalValueError(ValueError):
    def __init__(self, code: str, detail: object | None = None) -> None:
        messages = {
            "text-too-long": "memory entry text must not exceed 8192 UTF-8 bytes",
            "reason-too-long": "memory change reason must not exceed 512 Unicode code points",
            "identifier-empty": "memory identifiers must be non-empty strings",
            "identifier-ascii": "memory identifiers must contain only ASCII characters",
            "identifier-too-long": "memory identifiers must not exceed 128 ASCII characters",
            "hash": "content hashes must be 64 lowercase hexadecimal characters",
            "dimension-positive": "embedding dimension must be positive",
            "vector-dimension": f"embedding must contain exactly {detail} dimensions",
            "vector-finite": "embedding values must all be finite",
            "string-empty": f"{detail} must be non-empty",
            "json-key-collision": "canonical JSON object keys collide after NFC normalization",
        }
        super().__init__(messages[code])


class _CanonicalTypeError(TypeError):
    def __init__(self, code: str, detail: object | None = None) -> None:
        messages = {
            "string": f"{detail} must be a string",
            "json-key": "canonical JSON object keys must be strings",
            "json-value": f"value of type {detail} is not JSON-compatible",
        }
        super().__init__(messages[code])


def canonical_json(value: object) -> bytes:
    """Return canonical JSON bytes after recursively applying Unicode NFC."""

    return rfc8785.dumps(_validated_json(value))


def normalize_text(value: str) -> str:
    """Normalize and validate one durable entry body."""

    normalized = _normalize_non_empty_string(value, "memory entry text")
    if len(normalized.encode("utf-8")) > 8192:
        raise _CanonicalValueError("text-too-long")
    return normalized


def normalize_kind(value: str) -> str:
    """Normalize an open Memory entry kind without closing its value set."""

    return _normalize_non_empty_string(value, "memory entry kind")


def normalize_reason(value: str | None) -> str | None:
    """Normalize an optional audit reason and enforce its size limit."""

    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        return None
    if len(normalized) > 512:
        raise _CanonicalValueError("reason-too-long")
    return normalized


def validate_identifier(value: str) -> str:
    """Validate a globally unique identity representation."""

    if not isinstance(value, str) or not value:
        raise _CanonicalValueError("identifier-empty")
    if not value.isascii():
        raise _CanonicalValueError("identifier-ascii")
    if len(value) > 128:
        raise _CanonicalValueError("identifier-too-long")
    return value


def validate_content_hash(value: str) -> str:
    """Validate the lowercase hexadecimal SHA-256 wire representation."""

    if _LOWER_HEX_64.fullmatch(value) is None:
        raise _CanonicalValueError("hash")
    return value


def normalize_refs(values: Sequence[object]) -> tuple[object, ...]:
    """NFC-normalize, sort, and exactly deduplicate canonical references."""

    by_bytes: dict[bytes, object] = {}
    for value in values:
        normalized = _validated_json(value)
        encoded = rfc8785.dumps(normalized)
        by_bytes.setdefault(encoded, normalized)
    return tuple(by_bytes[key] for key in sorted(by_bytes))


def entry_content_bytes(
    *,
    kind: str,
    text: str,
    source_refs: Sequence[object],
    artifact_refs: Sequence[object],
) -> bytes:
    """Return canonical content bytes for an immutable entry version."""

    return canonical_json({
        "kind": normalize_kind(kind),
        "text": normalize_text(text),
        "source_refs": normalize_refs(source_refs),
        "artifact_refs": normalize_refs(artifact_refs),
    })


def entry_content_hash(
    *,
    kind: str,
    text: str,
    source_refs: Sequence[object],
    artifact_refs: Sequence[object],
) -> str:
    """Hash canonical entry content with the Memory domain separator."""

    content = entry_content_bytes(
        kind=kind,
        text=text,
        source_refs=source_refs,
        artifact_refs=artifact_refs,
    )
    return sha256(_ENTRY_HASH_DOMAIN + content).hexdigest()


def memory_content_bytes(content: MemoryContent) -> bytes:
    """Return canonical bytes for a complete Memory Artifact Revision."""

    manifest_entries = tuple(
        {
            "entry_id": validate_identifier(entry.entry_id),
            "entry_version_id": validate_identifier(entry.entry_version_id),
            "entry_content_hash": validate_content_hash(entry.entry_content_hash),
            "state": entry.state,
        }
        for entry in content.manifest.entries
    )
    changes = tuple(
        {
            "op": change.op,
            "entry_id": validate_identifier(change.entry_id),
            "from_entry_version_id": (
                None if change.from_entry_version_id is None else validate_identifier(change.from_entry_version_id)
            ),
            "to_entry_version_id": (
                None if change.to_entry_version_id is None else validate_identifier(change.to_entry_version_id)
            ),
            "reason": normalize_reason(change.reason),
        }
        for change in content.changes
    )
    return canonical_json({
        "schema": content.schema_version,
        "manifest": {"format": content.manifest.format, "entries": manifest_entries},
        "changes": changes,
    })


def memory_content_hash(content: MemoryContent) -> str:
    """Hash the complete canonical Memory Artifact content."""

    return sha256(memory_content_bytes(content)).hexdigest()


def embedding_content_hash(
    *,
    profile_id: str,
    model: str,
    dimension: int,
    distance: str,
    normalization: str,
    entry_content_hash: str,
) -> str:
    """Bind a vector projection to one profile and entry content hash."""

    if dimension < 1:
        raise _CanonicalValueError("dimension-positive")
    profile = {
        "profile_id": _normalize_non_empty_string(profile_id, "embedding profile ID"),
        "model": _normalize_non_empty_string(model, "embedding model"),
        "dimension": dimension,
        "distance": _normalize_non_empty_string(distance, "embedding distance"),
        "normalization": _normalize_non_empty_string(normalization, "embedding normalization"),
    }
    value = canonical_json({"profile": profile, "entry_content_hash": validate_content_hash(entry_content_hash)})
    return sha256(_EMBEDDING_HASH_DOMAIN + value).hexdigest()


def validate_embedding(values: Sequence[float], *, dimension: int) -> tuple[float, ...]:
    """Return a finite vector with exactly the deployment-fixed dimension."""

    if dimension < 1:
        raise _CanonicalValueError("dimension-positive")
    vector = tuple(float(value) for value in values)
    if len(vector) != dimension:
        raise _CanonicalValueError("vector-dimension", dimension)
    if not all(math.isfinite(value) for value in vector):
        raise _CanonicalValueError("vector-finite")
    return vector


def analyze_text(value: str) -> str:
    """Apply Analyzer v1 and return space-delimited backend-safe terms."""

    normalized = unicodedata.normalize("NFC", value).casefold()
    terms: list[str] = []
    word: list[str] = []
    cjk: list[str] = []

    def flush_word() -> None:
        if word:
            terms.append("".join(word))
            word.clear()

    def flush_cjk() -> None:
        if not cjk:
            return
        points = tuple(ord(character) for character in cjk)
        terms.extend(f"u_{point:x}" for point in points)
        terms.extend(f"b_{left:x}_{right:x}" for left, right in pairwise(points))
        cjk.clear()

    for character in normalized:
        if _is_cjk(character):
            flush_word()
            cjk.append(character)
        elif character.isalnum() or character == "_":
            flush_cjk()
            word.append(character)
        else:
            flush_word()
            flush_cjk()
    flush_word()
    flush_cjk()
    return " ".join(terms)


def fts_match_query(value: str) -> str | None:
    """Build a MATCH expression solely from Analyzer v1 output tokens."""

    analyzed = analyze_text(value)
    if not analyzed:
        return None
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in analyzed.split())


def _normalize_non_empty_string(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise _CanonicalTypeError("string", label)
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise _CanonicalValueError("string-empty", label)
    return normalized


def _validated_json(value: object) -> JsonValue:
    try:
        normalized = cast(JsonValue, _normalize_unicode(value))
        return _CanonicalJSON(value=normalized).model_dump(mode="json")["value"]
    except ValidationError as error:
        raise _CanonicalTypeError("json-value", type(value).__name__) from error


class _CanonicalJSON(BaseModel):
    """Validate one JSON value and expose Pydantic's JSON-mode projection."""

    model_config = ConfigDict(strict=True)

    value: JsonValue

    @field_validator("value", mode="before")
    @classmethod
    def normalize_unicode(cls, value: object) -> object:
        return _normalize_unicode(value)


def _normalize_unicode(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise _CanonicalTypeError("json-key")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise _CanonicalValueError("json-key-collision")
            normalized[key] = _normalize_unicode(raw_value)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | memoryview):
        return [_normalize_unicode(item) for item in value]
    raise _CanonicalTypeError("json-value", type(value).__name__)


def _is_cjk(character: str) -> bool:
    point = ord(character)
    return (
        0x3400 <= point <= 0x4DBF
        or 0x4E00 <= point <= 0x9FFF
        or 0xF900 <= point <= 0xFAFF
        or 0x20000 <= point <= 0x2FA1F
    )
