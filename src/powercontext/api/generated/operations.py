# generated from openapi/powercontext.yaml; do not edit.

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import JsonValue

from powercontext.api.generated.models import Capabilities, HealthResponse, ReadinessResponse

OPENAPI_VERSION = "3.0.3"
API_TITLE = "PowerContext API"
API_DESCRIPTION = "Remote PowerContext transport. Runtime behavior is reported by /v1/capabilities."
API_VERSION = "0.0.1"

ResponseT = TypeVar("ResponseT")


@dataclass(frozen=True, slots=True, kw_only=True)
class Operation(Generic[ResponseT]):
    method: str
    path: str
    operation_id: str
    response_type: type[ResponseT]
    summary: str
    tags: tuple[str, ...]
    responses: dict[int | str, dict[str, JsonValue]]


GET_LIVENESS = Operation[HealthResponse](
    method="GET",
    path="/health/live",
    operation_id="get_liveness",
    response_type=HealthResponse,
    summary="Get process liveness",
    tags=("health",),
    responses={
        200: {
            "description": "The API process is alive.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        }
    },
)

GET_READINESS = Operation[ReadinessResponse](
    method="GET",
    path="/health/ready",
    operation_id="get_readiness",
    response_type=ReadinessResponse,
    summary="Get deployment readiness",
    tags=("health",),
    responses={
        200: {
            "description": "Required Server bindings are ready.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        503: {
            "description": "Required Server bindings are not ready.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
    },
)

GET_CAPABILITIES = Operation[Capabilities](
    method="GET",
    path="/v1/capabilities",
    operation_id="get_capabilities",
    response_type=Capabilities,
    summary="Get runtime capabilities",
    tags=("capabilities",),
    responses={
        200: {
            "description": "Behavior enabled by the assembled runtime.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        }
    },
)
