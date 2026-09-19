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

"""Pure byte budgets and source line handling for code delivery."""

from __future__ import annotations

import hashlib

from powercontext.builtin.code.errors import InvalidCodeRequestError
from powercontext.builtin.code.models import CodeItem, CodeQueryResponse


def clip_item(item: CodeItem, max_content_bytes: int) -> CodeItem | None:
    """Keep complete source lines and bind citations to the emitted raw bytes."""

    if item.content is None or len(item.content.encode()) <= max_content_bytes:
        return item
    if item.location is None:
        return None
    selected: list[str] = []
    size = 0
    for line in text_source_lines(item.content):
        if size + len(line.encode()) > max_content_bytes:
            break
        selected.append(line)
        size += len(line.encode())
    if not selected:
        return None
    content = "".join(selected)
    return item.model_copy(
        update={
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "truncated": True,
            "location": item.location.model_copy(update={"end_line": item.location.start_line + len(selected) - 1}),
        }
    )


def fit_response(response: CodeQueryResponse, max_bytes: int) -> CodeQueryResponse:
    """Fit the complete serialized body, preserving metadata and whole citations."""

    if len(response.model_dump_json(by_alias=True).encode()) <= max_bytes:
        return response
    empty = response.model_copy(
        update={"items": (), "status": "partial", "coverage": response.coverage.model_copy(update={"truncated": True})}
    )
    if len(empty.model_dump_json(by_alias=True).encode()) > max_bytes:
        raise InvalidCodeRequestError("budget_too_small")
    fitted = empty
    for item in response.items:
        candidate = item
        while candidate is not None:
            attempt = fitted.model_copy(update={"items": (*fitted.items, candidate)})
            if len(attempt.model_dump_json(by_alias=True).encode()) <= max_bytes:
                fitted = attempt
                break
            if candidate.content is None:
                break
            lines = text_source_lines(candidate.content)
            candidate = clip_item(candidate, len("".join(lines[:-1]).encode()))
        if candidate is None or len(fitted.items) >= len(response.items):
            break
    return fitted


def source_lines(content: bytes) -> list[bytes]:
    """Use parser line numbers (LF), preserving CRLF and Unicode separators."""

    parts = content.split(b"\n")
    return [part + b"\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def text_source_lines(content: str) -> list[str]:
    return [line.decode("utf-8") for line in source_lines(content.encode("utf-8"))]
