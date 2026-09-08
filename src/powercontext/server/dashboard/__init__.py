"""Mount the packaged HTML dashboard on a PowerContext Server."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from powercontext.server.dashboard.routes import router
from powercontext.server.dashboard.session import save_session


def mount_dashboard(app: FastAPI) -> None:
    app.mount("/dashboard/static", StaticFiles(directory=Path(__file__).parent / "static"), name="dashboard-static")
    app.add_api_route(
        "/dashboard/session", save_session, methods=["POST"], include_in_schema=False, response_model=None
    )
    app.include_router(router, prefix="/dashboard", include_in_schema=False)
    app.add_api_route("/", lambda: RedirectResponse("/dashboard/home"), include_in_schema=False)
