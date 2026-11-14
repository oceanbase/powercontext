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

# Generated from src/powercontext_integration_lifecycle/prepared_context.py; do not edit.
# Edit the source and run `make integration-lifecycle` instead.

"""The prepare contract: the recall query a host sends and the context it accepts."""

from __future__ import annotations

from collections.abc import Mapping

PREPARED_CONTEXT_SCHEMA = "powercontext.prepared-context.v1"
MAX_CONTEXT_BYTES = 8_000
# ``POST /v1/context/prepare`` bounds a query in characters. The byte bound is the Hook's own
# margin, so a recall also stays acceptable to a server version that measures the storage layer's
# limit. Every host that ships this module recalls under the same bounds.
MAX_QUERY_CHARACTERS = 8_192
MAX_QUERY_BYTES = 8_192

_PREPARED_CONTEXT_FIELDS = frozenset({"schema", "status", "content", "content_bytes"})


class InvalidPreparedContextResponse(RuntimeError):
    """Raised when a Server response does not satisfy the prepared-context contract."""


def validate_prepared_context(response: Mapping[str, object]) -> dict[str, object]:
    """Return a safe copy after validating the complete v1 response contract."""

    if set(response) != _PREPARED_CONTEXT_FIELDS:
        raise InvalidPreparedContextResponse
    if response["schema"] != PREPARED_CONTEXT_SCHEMA:
        raise InvalidPreparedContextResponse

    status = response["status"]
    content = response["content"]
    content_bytes = response["content_bytes"]
    if not isinstance(content_bytes, int) or isinstance(content_bytes, bool) or content_bytes < 0:
        raise InvalidPreparedContextResponse
    if status == "empty":
        if content is not None or content_bytes != 0:
            raise InvalidPreparedContextResponse
    elif status == "ready":
        if not isinstance(content, str) or not content.strip():
            raise InvalidPreparedContextResponse
        try:
            encoded_content = content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidPreparedContextResponse from error
        if len(encoded_content) != content_bytes or content_bytes > MAX_CONTEXT_BYTES:
            raise InvalidPreparedContextResponse
    else:
        raise InvalidPreparedContextResponse
    return {
        "schema": response["schema"],
        "status": status,
        "content": content,
        "content_bytes": content_bytes,
    }


def bounded_recall_query(prompt: str) -> str:
    """Reduce a submitted prompt to the deterministic, bounded query an automatic recall sends.

    The Runtime bounds a prepare query in characters, so a host that forwards a longer prompt
    unchanged loses the recall to a request-validation error. A prompt within the bounds is used
    as submitted. A longer prompt keeps its most recent text: that tail holds the turn in hand,
    while the text before it is what the surrounding transcript already carries. The tail is cut
    by character and then by UTF-8 byte, so the result splits no character, fits both bounds, and
    keeps at least the last non-blank character of a prompt that was not blank.
    """

    query = prompt.strip()
    if len(query) <= MAX_QUERY_CHARACTERS and len(query.encode("utf-8")) <= MAX_QUERY_BYTES:
        return query
    retained = query[-MAX_QUERY_CHARACTERS:]
    encoded = retained.encode("utf-8")
    if len(encoded) > MAX_QUERY_BYTES:
        retained = encoded[-MAX_QUERY_BYTES:].decode("utf-8", errors="ignore")
    return retained.strip()
