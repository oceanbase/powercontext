"""FastAPI application factory for the runtime-independent Server slice."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from re import fullmatch
from typing import TypeVar
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import RequestResponseEndpoint

from powercontext.api import Capabilities, HealthResponse, ReadinessResponse, ReadinessStatus
from powercontext.api.generated.operations import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    GET_CAPABILITIES,
    GET_LIVENESS,
    GET_READINESS,
    OPENAPI_VERSION,
    Operation,
)
from powercontext.api.generated.schema import OPENAPI_SCHEMA
from powercontext.server.settings import ServerSettings

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = r"[A-Za-z0-9._:-]{1,128}"
logger = logging.getLogger(__name__)

CapabilityProvider = Callable[[], Capabilities]
ReadinessProbe = Callable[[], Awaitable[ReadinessResponse]]
_ResponseT = TypeVar("_ResponseT")


def create_app(
    *,
    settings: ServerSettings | None = None,
    capability_provider: CapabilityProvider | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    """Build an API process without creating databases, workers, or runtime state."""

    resolved_settings = settings if settings is not None else ServerSettings()
    resolved_capability_provider = capability_provider if capability_provider is not None else _empty_capabilities
    resolved_readiness_probe = readiness_probe if readiness_probe is not None else _api_readiness

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
    )
    app.openapi_version = OPENAPI_VERSION
    app.state.settings = resolved_settings

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _request_id(request.headers.get(REQUEST_ID_HEADER))
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("PowerContext request failed", extra={"request_id": request_id})
            response = PlainTextResponse(
                "Internal Server Error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    async def get_liveness() -> HealthResponse:
        return HealthResponse(status="ok")

    async def get_readiness() -> JSONResponse:
        readiness = await resolved_readiness_probe()
        response_status = (
            status.HTTP_200_OK if readiness.status is ReadinessStatus.READY else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return JSONResponse(
            content=readiness.model_dump(mode="json"),
            status_code=response_status,
        )

    async def get_capabilities() -> Capabilities:
        return resolved_capability_provider()

    _add_route(app, GET_LIVENESS, get_liveness)
    _add_route(app, GET_READINESS, get_readiness)
    _add_route(app, GET_CAPABILITIES, get_capabilities)
    # Prime FastAPI's route-version cache before replacing its derived schema.
    app.openapi()
    app.openapi_schema = deepcopy(OPENAPI_SCHEMA)

    return app


async def _api_readiness() -> ReadinessResponse:
    """Report only the API assembly check until a runtime supplies its own probe."""

    return ReadinessResponse(status=ReadinessStatus.READY, checks={"api": "ready"})


def _empty_capabilities() -> Capabilities:
    return Capabilities(source_types=[], artifact_families=[], search_modes=[], limits=[])


def _request_id(candidate: str | None) -> str:
    if candidate is not None and fullmatch(REQUEST_ID_PATTERN, candidate):
        return candidate
    return str(uuid4())


def _add_route(
    app: FastAPI,
    operation: Operation[_ResponseT],
    endpoint: Callable[[], Awaitable[_ResponseT | Response]],
) -> None:
    app.add_api_route(
        operation.path,
        endpoint,
        methods=[operation.method],
        operation_id=operation.operation_id,
        response_model=operation.response_type,
        responses=operation.responses,
        summary=operation.summary,
        tags=list(operation.tags),
    )
