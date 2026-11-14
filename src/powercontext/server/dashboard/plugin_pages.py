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

"""Plugin pages: static files in a sandboxed frame that reach their plugin's routes through the Dashboard.

The page runs in an opaque origin, so it cannot read the Dashboard session cookie, and
its own requests to the Server carry no credentials. The host page forwards the page's
request messages to ``/dashboard/plugins/{name}/api/...`` with the session, only for
this plugin's declared routes, and stops forwarding once the frame navigates away.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from powercontext.server.dashboard.session import login_response

ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}
MAX_ASSET_BYTES = 5 * 1024 * 1024
CLIENT_SCRIPT = Path(__file__).parent / "static" / "plugin-client.js"
# The frame may open external sites and submit forms; it never gets same-origin access.
FRAME_SANDBOX = "allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"


def _loaded(request: Request, name: str) -> tuple[Any, Any] | None:
    manager = getattr(request.app.state, "plugin_manager", None)
    plugin = None if manager is None else manager.plugins.get(name)
    manifest = None if manager is None else manager.loaded_manifest(name)
    if plugin is None or manifest is None or manifest.spec.pages is None:
        return None
    return plugin, manifest


def plugin_pages(request: Request, language: str) -> list[dict[str, str]]:
    """Navigation entries for every loaded plugin that declares pages."""

    manager = getattr(request.app.state, "plugin_manager", None)
    if manager is None:
        return []
    entries = []
    for name in sorted(manager.plugins):
        manifest = manager.loaded_manifest(name)
        if manifest is not None and manifest.spec.pages is not None:
            title = manifest.spec.pages.title
            entries.append({
                "name": name,
                "title": title.get(language, title["en"]),
                "path": f"/dashboard/plugins/{name}",
            })
    return entries


def _asset_headers(request: Request, name: str) -> dict[str, str]:
    origin = f"{request.url.scheme}://{request.url.netloc}"
    own = f"{origin}/dashboard/plugins/{name}/ui/"
    client = f"{origin}/dashboard/plugins/_client.js"
    policy = "; ".join((
        f"sandbox {FRAME_SANDBOX}",
        "default-src 'none'",
        f"script-src {own} {client}",
        f"style-src {own} 'unsafe-inline'",
        f"img-src {own} data: https:",
        f"font-src {own}",
        # Requests to external services are allowed; they never carry the PowerContext session.
        "connect-src https:",
        "form-action https:",
        "base-uri 'none'",
        "frame-ancestors 'self'",
    ))
    return {
        "Content-Security-Policy": policy,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    }


async def plugin_asset(request: Request, name: str, path: str) -> Response:
    """Serve one static file of a plugin's pages; plugin code and assets only, never user data."""

    loaded = _loaded(request, name)
    suffix = Path(path).suffix.lower()
    if loaded is None or suffix not in ASSET_TYPES or not path or ".." in path.split("/"):
        return Response(status_code=404)
    plugin, manifest = loaded
    root = (plugin.code_directory / manifest.spec.pages.directory).resolve()
    target = (root / path).resolve()
    # Symbolic links may not lead outside the pages directory.
    if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size > MAX_ASSET_BYTES:
        return Response(status_code=404)
    return FileResponse(target, media_type=ASSET_TYPES[suffix], headers=_asset_headers(request, name))


async def plugin_client(request: Request) -> Response:
    return FileResponse(
        CLIENT_SCRIPT,
        media_type=ASSET_TYPES[".js"],
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


async def plugin_page(request: Request, name: str) -> HTMLResponse:
    """The logged-in Dashboard page that frames a plugin's entry page and forwards its requests."""

    from powercontext.server.dashboard.api import DashboardAPI, ReadError
    from powercontext.server.dashboard.routes import default_scope, initial_context, render, scope_context

    ctx = initial_context(request, "plugin")
    loaded = _loaded(request, name)
    if loaded is None:
        ctx.update(status=404, page_error=ReadError(404, "not_found", None))
        return render(request, ctx)
    _, manifest = loaded
    pages = manifest.spec.pages
    entry = next(item for item in ctx["plugin_pages"] if item["name"] == name)
    api = DashboardAPI(request)
    try:
        descriptor = await default_scope(api, ctx)
        if ctx["scope"]:
            await scope_context(api, ctx, descriptor)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        # The plugin page does not depend on a Scope; it receives none.
        ctx["scope"] = None
    finally:
        await api.client.aclose()
    ctx["page_title"] = entry["title"]
    ctx["plugin_page"] = {
        **entry,
        "src": f"/dashboard/plugins/{name}/ui/{pages.entry}",
        "api": f"/dashboard/plugins/{name}/api",
        "sandbox": FRAME_SANDBOX,
        "routes": [{"method": route.method, "path": route.path} for route in manifest.spec.api.routes],
    }
    return render(request, ctx)


def _same_origin(request: Request) -> bool:
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site == "same-origin"
    origin = request.headers.get("origin")
    return origin is not None and origin == f"{request.url.scheme}://{request.url.netloc}"


async def plugin_forward(name: str, path: str, request: Request) -> Response:
    """Call a plugin route as the Dashboard session's principal, for the host page only."""

    from powercontext.server.app import call_plugin_api

    # A page in the sandboxed frame sends Origin: null and a cross-site fetch; neither is forwarded.
    if not _same_origin(request) or request.headers.get("origin") == "null":
        return Response(status_code=403)
    if _loaded(request, name) is None:
        return Response(status_code=404)
    return await call_plugin_api(name, path, request)
