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

"""Mount the packaged HTML dashboard on a PowerContext Server."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope
from typing_extensions import override

from powercontext.server.dashboard.session import save_session


class _DashboardStaticFiles(StaticFiles):
    @override
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.endswith((".js", ".css")):
            # Revalidate dependencies as well as entry scripts after an update.
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount_dashboard(app: FastAPI) -> None:
    from powercontext.server.dashboard.routes import router

    app.mount(
        "/dashboard/static", _DashboardStaticFiles(directory=Path(__file__).parent / "static"), name="dashboard-static"
    )
    app.add_api_route(
        "/dashboard/session", save_session, methods=["POST"], include_in_schema=False, response_model=None
    )
    app.include_router(router, prefix="/dashboard", include_in_schema=False)
    app.add_api_route("/", lambda: RedirectResponse("/dashboard/home"), include_in_schema=False)
