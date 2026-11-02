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

"""Observe public Artifact reads without counting exact lookup as relevance retrieval."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import suppress
from time import perf_counter
from typing import Any

from starlette.responses import Response

from powercontext._logging import current_log_context, log_safely

_OPERATIONS = frozenset({"get_artifact", "get_artifact_revision"})
_FAMILIES = frozenset({
    "memory",
    "atomic-memory",
    "experience",
    "skill",
    "handoff",
    "profile",
    "prompt",
    "topic-memory",
})


def observe_artifact_read(
    metrics: Any,
    operation: str,
    kwargs: Mapping[str, Any],
    *,
    started_at: float,
    outcome: str,
    result: Any = None,
    error_code: str | None = None,
    status_code: int | None = None,
) -> None:
    """Observe one dispatch, including its conditional-read decision and assembly.

    Entry authentication and validation stay transport observations. A 304 is a
    successful cache decision, not another response containing Artifact content.
    Exact reads do not establish relevance or host use. No body or identity is read.
    """
    if operation not in _OPERATIONS:
        return
    with suppress(Exception):
        selected = kwargs.get("family")
        family = getattr(selected, "root", selected)
        family = family if family in _FAMILIES else "other"
        read_outcome = (
            "not_modified"
            if outcome == "success" and isinstance(result, Response) and result.status_code == 304
            else outcome
        )
        duration = max(0, perf_counter() - started_at)
        if metrics is not None:
            with suppress(Exception):
                metrics.observe_artifact_read(operation, family, read_outcome, duration)
        log_safely(
            logging.getLogger(__name__),
            logging.INFO,
            "Artifact read completed",
            extra={
                "event": "artifact.read.completed",
                "operation": operation,
                "unit": "application",
                "outcome": outcome,
                "scope": current_log_context().get("scope"),
                "family": family,
                "read_outcome": read_outcome,
                "duration_ms": duration * 1000,
                "error_code": error_code,
                "status_code": status_code,
            },
        )
