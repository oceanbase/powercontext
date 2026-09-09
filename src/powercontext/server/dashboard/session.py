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

"""Browser credential transport, restricted to dashboard pages."""

from urllib.parse import parse_qs, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import Headers
from starlette.types import Scope

COOKIE_NAME = "powercontext_dashboard_token"


def login_response(status: int = 401, *, rejected: bool = False, request: Request | None = None) -> HTMLResponse:
    from powercontext.server.dashboard.preferences import presentation, remember_language
    from powercontext.server.dashboard.routes import ENV

    response = HTMLResponse(
        ENV.get_template("login.html").render(**presentation(request), status=status, rejected=rejected),
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Dashboard-HTML": "1"},
    )
    if request is not None:
        remember_language(response, request)
    return response


def authentication_headers(scope: Scope) -> dict[str, str]:
    headers = dict(Headers(scope=scope))
    if scope["path"].startswith("/dashboard/") and "authorization" not in headers:
        token = Request(scope).cookies.get(COOKIE_NAME)
        if token:
            headers["authorization"] = f"Bearer {token}"
    return headers


async def save_session(request: Request) -> HTMLResponse | RedirectResponse:
    origin = request.headers.get("origin")
    if (
        not origin
        or urlsplit(origin).netloc != request.headers.get("host")
        or urlsplit(origin).scheme != request.url.scheme
    ):
        return HTMLResponse(status_code=403)
    length = request.headers.get("content-length", "0")
    if not length.isdecimal() or len(length) > 6 or int(length) > 8192:
        return HTMLResponse(status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            return HTMLResponse(status_code=413)
    try:
        token = parse_qs(body.decode(), max_num_fields=1).get("token", [""])[0].strip()
    except (ValueError, UnicodeDecodeError):
        return login_response(request=request)
    if len(token) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        return login_response(rejected=True, request=request)
    response = RedirectResponse("/dashboard/home", status_code=303, headers={"Cache-Control": "no-store"})
    if token:
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=28800,
            path="/dashboard",
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
    else:
        response.delete_cookie(COOKIE_NAME, path="/dashboard")
    return response
