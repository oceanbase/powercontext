"""Server-rendered interface language; appearance is owned by Tabler's theme script."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from starlette.responses import Response

ROOT = Path(__file__).parent
CATALOGS = {
    "zh": json.loads((ROOT / "labels.json").read_text()),
    "en": json.loads((ROOT / "labels.en.json").read_text()),
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
