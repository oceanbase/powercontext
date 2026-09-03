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

"""Small, side-effect-free helpers shared by the Hermes integration modules."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MAX_BYTES = 8000
DEFAULT_RETRIEVAL_LIMIT = 8
DEFAULT_TIMEOUT = 5.0
MAX_TURN_CHARS = 50_000
MAX_PRECOMPRESS_CHARS = 30_000
PRECOMPRESS_ROLES = {"user", "assistant"}
SCOPE_SAFE_RE = re.compile(r"[^\w:./@+-]+", re.UNICODE)
SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?key|secret(?:[_ -]?key)?|password|passwd|token|authorization)\b"
        r"\s*[:=]\s*[\"']?[^\s,;\"']+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.DOTALL),
)


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts).strip()


def messages_to_text(messages: list[dict[str, Any]], *, limit: int) -> str:
    lines: list[str] = []
    total = 0
    for message in messages:
        role = str(message.get("role", "unknown"))
        text = message_text(message.get("content"))
        if not text:
            continue
        line = f"[{role}] {text}"
        remaining = limit - total
        if remaining <= 0:
            break
        lines.append(line[:remaining])
        total += min(len(line), remaining) + 1
    return "\n".join(lines).strip()


def redact_secrets(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def precompress_entries(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        role = str(message.get("role", "")).strip().lower()
        text = message_text(message.get("content"))
        if role not in PRECOMPRESS_ROLES or not text:
            continue
        fingerprint_payload = {
            "role": role,
            "content": message.get("content"),
            "name": message.get("name"),
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        entries.append((fingerprint, {"role": role, "content": redact_secrets(text)}))
    return entries


def new_precompress_entries(
    previous: list[str], current: list[tuple[str, dict[str, Any]]]
) -> list[tuple[str, dict[str, Any]]]:
    current_fingerprints = [fingerprint for fingerprint, _message in current]
    if not previous:
        return current
    if not current_fingerprints:
        return []
    if current_fingerprints == previous:
        return []

    # A repeated or shortened compression window contains no new turns.
    if len(current_fingerprints) <= len(previous):
        window_size = len(current_fingerprints)
        if any(
            previous[start : start + window_size] == current_fingerprints
            for start in range(len(previous) - window_size + 1)
        ):
            return []

    # Hermes may pass an overlapping suffix of the previous window. Capture
    # only the tail after the longest suffix/prefix overlap.
    for overlap in range(min(len(previous), len(current_fingerprints)), 0, -1):
        if previous[-overlap:] == current_fingerprints[:overlap]:
            return current[overlap:]
    return current


def safe_scope(value: str) -> str:
    value = SCOPE_SAFE_RE.sub("_", value.strip()).strip("_")
    return value[:256] or "hermes:default"


def config_path(hermes_home: str) -> Path:
    path_value = os.environ.get("POWERCONTEXT_HERMES_CONFIG", "").strip()
    return Path(path_value) if path_value else Path(hermes_home) / "powercontext" / "config.json"


def load_json_config(hermes_home: str) -> dict[str, Any]:
    path = config_path(hermes_home)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not parse PowerContext JSON configuration from %s", path)
        return {}
    return value if isinstance(value, dict) else {}


def config_value(config: dict[str, Any], key: str, env_name: str, default: Any = None) -> Any:
    env_value = os.environ.get(env_name)
    if env_value is not None and env_value.strip() != "":
        return env_value.strip()
    return config.get(key, default)


def citation_from_args(args: dict[str, Any]) -> dict[str, Any]:
    required = ("family", "artifact_id", "revision", "entry_id", "entry_version_id")
    missing = [key for key in required if key not in args]
    if missing:
        raise ValueError(f"Missing required arguments: {', '.join(missing)}")  # noqa: TRY003

    family = str(args["family"]).strip()
    artifact_id = str(args["artifact_id"]).strip()
    entry_id = str(args["entry_id"]).strip()
    entry_version_id = str(args["entry_version_id"]).strip()
    if not family or not artifact_id or not entry_id or not entry_version_id:
        raise ValueError("Citation fields must be non-empty")  # noqa: TRY003

    try:
        revision = int(args["revision"])
    except (TypeError, ValueError) as error:
        raise ValueError("revision must be an integer") from error  # noqa: TRY003
    if revision < 1:
        raise ValueError("revision must be positive")  # noqa: TRY003

    return {
        "memory_ref": {"family": family, "artifact_id": artifact_id, "revision": revision},
        "entry_id": entry_id,
        "entry_version_id": entry_version_id,
    }


def citation_from_response(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    entry = response.get("entry")
    citation = entry.get("citation") if isinstance(entry, dict) else None
    if not isinstance(citation, dict):
        return None
    memory_ref = citation.get("memory_ref")
    if not isinstance(memory_ref, dict):
        return None
    family = str(memory_ref.get("family", "")).strip()
    artifact_id = str(memory_ref.get("artifact_id", "")).strip()
    entry_id = str(citation.get("entry_id", "")).strip()
    entry_version_id = str(citation.get("entry_version_id", "")).strip()
    try:
        revision = int(memory_ref.get("revision"))
    except (TypeError, ValueError):
        return None
    if not family or not artifact_id or revision < 1 or not entry_id or not entry_version_id:
        return None
    return {
        "memory_ref": {"family": family, "artifact_id": artifact_id, "revision": revision},
        "entry_id": entry_id,
        "entry_version_id": entry_version_id,
    }


def entry_identity(citation: Any) -> dict[str, str] | None:
    if not isinstance(citation, dict):
        return None
    entry_id = str(citation.get("entry_id", "")).strip()
    entry_version_id = str(citation.get("entry_version_id", "")).strip()
    if not entry_id or not entry_version_id:
        return None
    return {"entry_id": entry_id, "entry_version_id": entry_version_id}
