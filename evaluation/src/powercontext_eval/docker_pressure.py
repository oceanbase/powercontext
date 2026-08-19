"""Process-wide admission control for Docker control-plane operations."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

DOCKER_HEAVY_OPERATION_MAX_CONCURRENCY = 4
_DOCKER_HEAVY_OPERATION_SEMAPHORE = threading.BoundedSemaphore(DOCKER_HEAVY_OPERATION_MAX_CONCURRENCY)
_DOCKER_HEAVY_OPERATION_LOCAL = threading.local()


@contextmanager
def heavy_operation() -> Iterator[None]:
    """Bound operations that hold Docker daemon streams or extract images."""

    depth = getattr(_DOCKER_HEAVY_OPERATION_LOCAL, "depth", 0)
    if depth:
        _DOCKER_HEAVY_OPERATION_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _DOCKER_HEAVY_OPERATION_LOCAL.depth = depth
        return
    with _DOCKER_HEAVY_OPERATION_SEMAPHORE:
        _DOCKER_HEAVY_OPERATION_LOCAL.depth = 1
        try:
            yield
        finally:
            _DOCKER_HEAVY_OPERATION_LOCAL.depth = 0
