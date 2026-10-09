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
