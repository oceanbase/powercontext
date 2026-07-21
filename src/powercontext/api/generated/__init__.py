"""Generated API structures. Regenerate them with ``make api-generate``."""

from powercontext.api.generated.models import (
    Capabilities,
    CapabilityLimit,
    HealthResponse,
    ReadinessResponse,
    ReadinessStatus,
)
from powercontext.api.generated.operations import GET_CAPABILITIES, GET_LIVENESS, GET_READINESS, Operation

__all__ = [
    "GET_CAPABILITIES",
    "GET_LIVENESS",
    "GET_READINESS",
    "Capabilities",
    "CapabilityLimit",
    "HealthResponse",
    "Operation",
    "ReadinessResponse",
    "ReadinessStatus",
]
