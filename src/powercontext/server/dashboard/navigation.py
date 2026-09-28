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

"""Validate local reading destinations independently of record selection."""

import json
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import Request

from powercontext.server.dashboard.errors import ReadError

COMMON = {"scope", "period", "lang", "theme"}
HISTORY = {"view", "profile_cursor", "profile_history"}
COLLECTION = {"cursor", "handoff_history"}


def positive_revision(raw: str | None) -> int:
    if raw is None or not raw.isascii() or not raw.isdecimal() or len(raw) > 18 or int(raw) < 1:
        raise ReadError(422, "invalid_request")
    return int(raw)


def _has_controls(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _parameters(raw: str, maximum: int) -> tuple[str, dict[str, str]]:
    if len(raw.encode()) > maximum or _has_controls(raw) or "\\" in raw:
        raise ValueError
    url = urlsplit(raw)
    if url.scheme or url.netloc or url.fragment or not url.path.startswith("/dashboard/"):
        raise ValueError
    pairs = parse_qsl(url.query, keep_blank_values=True, max_num_fields=12, errors="strict")
    params = dict(pairs)
    if len(params) != len(pairs) or any(_has_controls(value) or "\\" in value for value in params.values()):
        raise ValueError
    scope = params.get("scope", "")
    if not scope.strip() or len(scope) > 256:
        raise ValueError
    if (
        params.get("lang", "zh") not in {"zh", "en"}
        or params.get("period", "7d") not in {"today", "7d", "30d"}
        or params.get("theme", "light") not in {"light", "dark"}
    ):
        raise ValueError
    return url.path, params


def _validate_history(params: dict[str, str]) -> None:
    for key in ("cursor", "profile_cursor"):
        if key in params and not 1 <= len(params[key]) <= 4096:
            raise ValueError
    for key in ("handoff_history", "profile_history"):
        if key not in params:
            continue
        history = json.loads(params[key])
        if not isinstance(history, list) or any(
            item is not None and (not isinstance(item, str) or not 1 <= len(item) <= 4096 or _has_controls(item))
            for item in history
        ):
            raise ValueError


def collection_return(raw: str | None, scope: str, *, page: str | None = None) -> str | None:
    if not raw:
        return None
    try:
        path, params = _parameters(raw, 8192)
        if params["scope"] != scope or (page and path != f"/dashboard/{page}"):
            return None
        allowed = COMMON | COLLECTION if path == "/dashboard/handoff" else COMMON | HISTORY
        if path not in {"/dashboard/handoff", "/dashboard/profile"} or params.keys() - allowed:
            return None
        if path == "/dashboard/profile" and params.get("view") != "history":
            return None
        _validate_history(params)
    except (ValueError, UnicodeError):
        return None
    return path + "?" + urlencode(params)


def _reading_fields(path: str, params: dict[str, str]) -> set[str]:
    if path == "/dashboard/profile":
        allowed = HISTORY
        if "view" in params and (params["view"] != "history" or "revision" in params):
            raise ValueError
        _validate_history(params)
    else:
        allowed = {"artifact"}
        artifact = params.get("artifact", "")
        if not 1 <= len(artifact) <= 128 or any(not 33 <= ord(char) <= 126 for char in artifact):
            raise ValueError
        positive_revision(params.get("revision"))
    return allowed


def reading_return(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        path, params = _parameters(raw, 16384)
        if path not in {"/dashboard/profile", "/dashboard/handoff-detail"}:
            return None
        allowed = COMMON | {"revision", "return_to"}
        allowed |= _reading_fields(path, params)
        if "revision" in params:
            positive_revision(params["revision"])
        if params.keys() - allowed:
            return None
        if "return_to" in params:
            destination = collection_return(
                params.pop("return_to"), params["scope"], page="profile" if path.endswith("profile") else "handoff"
            )
            if destination:
                params["return_to"] = destination
    except (ValueError, UnicodeError, ReadError):
        return None
    return path + "?" + urlencode(params)


def request_reading_return(request: Request) -> str | None:
    path = request.url.path
    if path.startswith("/dashboard/evidence/"):
        # Evidence is loaded in a modal, but authentication recovery must return
        # to the exact record whose source was requested.  The evidence URL
        # carries the record identity and origin; keep only fields accepted by
        # the corresponding reading route before applying its normal validation.
        pairs = request.query_params.multi_items()
        if len(pairs) != len(dict(pairs)):
            return None
        query = dict(pairs)
        origin = query.get("origin")
        if origin == "profile":
            try:
                positive_revision(query.get("revision"))
            except ReadError:
                return None
            path = "/dashboard/profile"
            keys = ("scope", "period", "lang", "theme", "revision", "return_to")
        elif origin == "handoff-detail":
            path = "/dashboard/handoff-detail"
            keys = ("scope", "period", "lang", "theme", "artifact", "revision", "return_to")
        else:
            return None
        return reading_return(path + "?" + urlencode({key: query[key] for key in keys if key in query}))
    if path == "/dashboard/handoff-download":
        path = "/dashboard/handoff-detail"
    return reading_return(path + "?" + str(request.query_params))


def directory_context(request: Request, scope: str, page: str, language: str) -> str | None:
    allowed = COLLECTION if page == "handoff" else HISTORY
    params = {key: value for key, value in request.query_params.items() if key in allowed | {"period"}}
    params.update(scope=scope, lang=language)
    return collection_return(f"/dashboard/{page}?{urlencode(params)}", scope, page=page)
