"""Mount the packaged HTML dashboard on a PowerContext Server."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope
from typing_extensions import override

from powercontext.server.dashboard.routes import router
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
    app.mount(
        "/dashboard/static", _DashboardStaticFiles(directory=Path(__file__).parent / "static"), name="dashboard-static"
    )
    app.add_api_route(
        "/dashboard/session", save_session, methods=["POST"], include_in_schema=False, response_model=None
    )
    app.include_router(router, prefix="/dashboard", include_in_schema=False)
    app.add_api_route("/", lambda: RedirectResponse("/dashboard/home"), include_in_schema=False)
