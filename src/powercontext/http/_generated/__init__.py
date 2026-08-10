"""Generated HTTP structures. Regenerate them with ``make api-generate``."""

from powercontext.http._generated.models import (
    Capabilities,
    HealthResponse,
    ReadinessResponse,
    ReadinessStatus,
)
from powercontext.http._generated.operations import GET_CAPABILITIES, GET_LIVENESS, GET_READINESS, Operation

__all__ = [
    "GET_CAPABILITIES",
    "GET_LIVENESS",
    "GET_READINESS",
    "Capabilities",
    "HealthResponse",
    "Operation",
    "ReadinessResponse",
    "ReadinessStatus",
]
