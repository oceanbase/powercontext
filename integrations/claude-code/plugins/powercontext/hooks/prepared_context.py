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

"""Strict parsing for final context prepared by the PowerContext Runtime."""

from __future__ import annotations

from collections.abc import Mapping

PREPARED_CONTEXT_SCHEMA = "powercontext.prepared-context.v1"
MAX_CONTEXT_BYTES = 8_000

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
