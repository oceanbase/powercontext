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

"""Page complete API lists and retain opaque API cursors in navigation links."""

import json
from typing import Any

from fastapi import Request

from powercontext.server.dashboard.api import ReadError

PAGE_SIZE = 6


def list_page(items: list[dict[str, Any]], raw: str | None, selected: str | None = None) -> dict[str, Any]:
    try:
        page = int(raw) if raw is not None else 1
    except ValueError as error:
        raise ReadError(422, "invalid_request") from error
    if raw is None and selected:
        page = next((index // PAGE_SIZE + 1 for index, item in enumerate(items) if item.get("entry_id") == selected), 1)
    if page < 1:
        raise ReadError(422, "invalid_request")
    if page > max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE):
        raise ReadError(404, "not_found")
    offset = (page - 1) * PAGE_SIZE
    return {
        "items": items[offset : offset + PAGE_SIZE],
        "page": page,
        "previous": page - 1 if page > 1 else None,
        "next": page + 1 if offset + PAGE_SIZE < len(items) else None,
    }


def list_links(ctx: dict[str, Any], family: str, window: dict[str, Any]) -> dict[str, Any]:
    params = {"entry": None, "memory_id": None, "memory_revision": None, "entry_version": None}
    if family == "skill":
        params["kind"] = "skill"
    return {
        "page": window["page"],
        **{
            direction: ctx["link"](**params, **{family + "_page": window[direction]}) if window[direction] else None
            for direction in ("previous", "next")
        },
    }


def cursor_links(request: Request, ctx: dict[str, Any], family: str, following: str | None) -> dict[str, Any]:
    cursor_key = "cursor" if family == "handoff" else family + "_cursor"
    history_key = family + "_history"
    try:
        history = json.loads(request.query_params.get(history_key, "[]"))
    except ValueError as error:
        raise ReadError(422, "invalid_request") from error
    if not isinstance(history, list) or any(item is not None and not isinstance(item, str) for item in history):
        raise ReadError(422, "invalid_request")
    current = request.query_params.get(cursor_key)
    params = {"kind": family} if family != "handoff" else {}
    previous = (
        ctx["link"](**params, **{cursor_key: history[-1], history_key: json.dumps(history[:-1])}) if history else None
    )
    following_url = (
        ctx["link"](
            **params,
            **{
                cursor_key: following,
                history_key: json.dumps([*history, current]),
            },
        )
        if following
        else None
    )
    return {"page": len(history) + 1, "previous": previous, "next": following_url}
