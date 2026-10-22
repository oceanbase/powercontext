"""Observe final public retrieval results, without retaining queries or result content."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextlib import suppress
from time import perf_counter
from typing import Any

from starlette.responses import JSONResponse

from powercontext._logging import current_log_context, log_safely

_OPERATIONS = {
    "search_memory": "atomic-memory",
    "search_atomic_memory": "atomic-memory",
    "search_topic_memory": "topic-memory",
    "search_artifacts": "other",
    "prepare_context": "context",
    "list_managed_skills": "skill",
}
_FAMILIES = frozenset({"atomic-memory", "topic-memory", "experience", "skill", "profile", "handoff", "prompt"})


def observe_retrieval(
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
    """Count one dispatched operation, including MCP's single application dispatch.

    Transport rejection is outside this boundary. Empty results are successful
    execution, not evidence that a relevant answer exists or was used by a host.
    """
    if operation not in _OPERATIONS:
        return
    request = kwargs.get("request")
    if operation == "list_managed_skills" and not getattr(request, "query", None):
        return
    with suppress(Exception):
        family = _OPERATIONS[operation]
        if operation == "search_artifacts":
            selected = kwargs.get("family")
            family = selected if selected in _FAMILIES else "other"
        count = None
        if outcome == "success":
            if operation == "prepare_context":
                outcome = "nonempty" if result.status == "ready" and result.content_bytes > 0 else "empty"
            else:
                if operation == "search_artifacts":
                    if not isinstance(result, JSONResponse):
                        return
                    count = len(json.loads(result.body)["results"])
                else:
                    count = len(result.skills if operation == "list_managed_skills" else result.hits)
                outcome = "nonempty" if count else "empty"
        duration = max(0, perf_counter() - started_at)
        if metrics is not None:
            with suppress(Exception):
                metrics.observe_retrieval(operation, family, outcome, duration)
        fields = {
            "scope": current_log_context().get("scope"),
            "event": "retrieval.completed",
            "operation": operation,
            "unit": "application",
            "outcome": "success" if outcome in {"nonempty", "empty"} else outcome,
            "retrieval_outcome": outcome,
            "retrieval_family": family,
            "duration_ms": duration * 1000,
            "error_code": error_code,
            "status_code": status_code,
        }
        if count is not None:
            fields["result_count"] = count
        log_safely(logging.getLogger(__name__), logging.INFO, "Retrieval completed", extra=fields)
