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

"""Preserve explicit temporal evidence without deciding which facts are current."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

from pydantic import JsonValue

# The date alternative must not turn an unsupported timestamp suffix into a shorter valid literal.
_ISO_LITERAL = re.compile(
    r"(?<![\w])\d{4}-\d{2}-\d{2}"
    r"(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:[Zz]|[+-]\d{2}(?::?\d{2})?)?"
    r"(?![\w]|[.,:+-]\d)|(?![\w]|[Tt ]\d{2}:))"
)
_STATEMENT_FIELDS = frozenset({"statement", "subject", "effective_at", "event_at", "recorded_at"})
_CARRIER_FIELDS = frozenset({"content", "source_type", "source_id", "metadata"})


def _time_key(value: str) -> str | None:
    if _ISO_LITERAL.fullmatch(value) is None:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value.replace("z", "Z"))
        fraction = re.search(r"[.,](\d+)", value)
        if fraction is not None and len(fraction[1]) > 6:
            # datetime silently truncates finer precision; keep the complete value instead.
            return f"literal:{value}"
        if parsed.tzinfo is not None:
            try:
                parsed = parsed.astimezone(UTC)
            except OverflowError:
                # Valid local dates at the supported year bounds can exceed UTC's range.
                return f"literal:{value}"
            if parsed.time().isoformat() == "00:00:00":
                return parsed.date().isoformat()
        return parsed.isoformat()
    except ValueError:
        return None


def text_time_literals(text: str) -> tuple[str, ...]:
    """Return valid ISO literals, without interpreting their role or ordering them."""

    return tuple(dict.fromkeys(value for value in _ISO_LITERAL.findall(text) if _time_key(value) is not None))


def source_time_literals(content: JsonValue) -> tuple[str, ...]:
    """Read explicit times from a single-statement payload, including its native text carrier.

    Arbitrary nested metadata and multi-statement objects do not establish which fact a timestamp qualifies.
    """

    value = content
    for _ in range(2):
        if isinstance(value, dict) and "content" in value and set(value) <= _CARRIER_FIELDS:
            value = value["content"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return ()
        else:
            break
    if not isinstance(value, dict) or not set(value) <= _STATEMENT_FIELDS:
        return ()
    if not isinstance(value.get("statement"), str) or not value["statement"].strip():
        return ()
    keys = ("effective_at", "event_at") if "effective_at" in value or "event_at" in value else ("recorded_at",)
    return tuple(
        dict.fromkeys(
            literal.strip()
            for key in keys
            if isinstance(literal := value.get(key), str) and _time_key(literal.strip()) is not None
        )
    )


def preserves_time_literals(text: str, literals: tuple[str, ...]) -> bool:
    """Accept equivalent ISO instants and the date form of midnight UTC, without losing other time precision."""

    retained = {_time_key(value) for value in text_time_literals(text)}
    return all(_time_key(value) in retained for value in literals)
