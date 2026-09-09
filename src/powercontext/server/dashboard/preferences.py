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

"""Server-rendered interface language; appearance is owned by Tabler's theme script."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from starlette.responses import Response

ROOT = Path(__file__).parent
CATALOGS = {
    "zh": json.loads((ROOT / "labels.json").read_text(encoding="utf-8")),
    "en": json.loads((ROOT / "labels.en.json").read_text(encoding="utf-8")),
}
LANGUAGE_COOKIE = "powercontext_dashboard_language"


def presentation(request: Request | None = None) -> dict[str, Any]:
    language = "zh"
    if request is not None:
        language = request.query_params.get("lang", request.cookies.get(LANGUAGE_COOKIE, "zh"))
    if language not in CATALOGS:
        language = "zh"

    def preference_url(**values: str) -> str:
        path = request.url.path if request else "/dashboard/home"
        query = dict(request.query_params) if request else {}
        query.update(values)
        return path + "?" + urlencode(query)

    return {
        "t": CATALOGS[language],
        "language": language,
        "html_language": "zh-CN" if language == "zh" else "en",
        "preference_url": preference_url,
    }


def remember_language(response: Response, request: Request) -> None:
    language = request.query_params.get("lang")
    if language in CATALOGS:
        response.set_cookie(
            LANGUAGE_COOKIE,
            language,
            max_age=31536000,
            path="/dashboard",
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
