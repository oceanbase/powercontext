"""Helpers for service readiness errors."""

from __future__ import annotations

from fastapi import Request


def service_unavailable_message(request: Request, service_name: str) -> str:
    message = f"{service_name} service unavailable: storage backend initialization failed"
    startup_error = getattr(request.app.state, "service_startup_error", None)
    if startup_error:
        message += f" ({str(startup_error)[:200]})"
    message += (
        ". For local basic storage, set DATABASE_PROVIDER=sqlite. "
        "For the full PowerMem capability stack, use embedded SeekDB on Linux "
        'with "powermem[seekdb]" or configure remote OceanBase with OCEANBASE_HOST.'
    )
    return message
