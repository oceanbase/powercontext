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

"""Server-owned HTML pages and their supporting endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import BaseModel, ConfigDict

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
    ),
}


class DashboardScope(BaseModel):
    """One Server scope exposed by the personal Dashboard."""

    model_config = ConfigDict(extra="forbid")

    scope_id: str
    display_name: str


def mount_web_ui(
    app: FastAPI,
    *,
    scopes: Mapping[str, str],
    dashboard_enabled: bool = False,
    handoff_report_enabled: bool = False,
) -> None:
    """Mount Server-owned pages, static assets, and UI support endpoints."""

    dashboard_scopes = tuple(DashboardScope(scope_id=scope_id, display_name=name) for scope_id, name in scopes.items())
    if dashboard_enabled and not dashboard_scopes:
        raise ValueError("Dashboard requires at least one scope")  # noqa: TRY003

    router = APIRouter(include_in_schema=False)

    async def dashboard_page(request: Request) -> Response:
        return _templates().TemplateResponse(
            request=request,
            name="pages/dashboard.html",
            context={
                "active_page": "dashboard",
                "dashboard_enabled": True,
                "handoff_report_enabled": handoff_report_enabled,
                "home_route": "dashboard_home",
            },
            headers=_PAGE_HEADERS,
        )

    async def handoff_report_page(request: Request) -> Response:
        return _templates().TemplateResponse(
            request=request,
            name="pages/handoff_report.html",
            context={
                "active_page": "handoff_report",
                "dashboard_enabled": dashboard_enabled,
                "handoff_report_enabled": True,
                "home_route": "dashboard_home" if dashboard_enabled else "handoff_report_dashboard",
            },
            headers=_PAGE_HEADERS,
        )

    async def list_dashboard_scopes(response: Response) -> tuple[DashboardScope, ...]:
        response.headers["Cache-Control"] = "no-store"
        return dashboard_scopes

    if dashboard_enabled:
        router.add_api_route(
            "/",
            dashboard_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="dashboard_home",
        )
        router.add_api_route(
            "/dashboard/scopes",
            list_dashboard_scopes,
            methods=["GET"],
            response_model=list[DashboardScope],
            name="dashboard_scopes",
        )
    if handoff_report_enabled:
        router.add_api_route(
            "/handoff-reports",
            handoff_report_page,
            methods=["GET"],
            response_class=HTMLResponse,
            name="handoff_report_dashboard",
        )

    app.include_router(router)
    app.mount(
        "/static",
        StaticFiles(packages=[("powercontext.server", "static")]),
        name="web_static",
    )


@cache
def _templates() -> Jinja2Templates:
    environment = Environment(
        loader=PackageLoader("powercontext.server"),
        autoescape=select_autoescape(),
    )
    return Jinja2Templates(env=environment)


__all__ = ["DashboardScope", "mount_web_ui"]
