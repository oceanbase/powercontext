"""Helpers for service readiness errors."""

from __future__ import annotations

from fastapi import Request


PUBLIC_STARTUP_ERROR_MESSAGE = "Storage backend initialization failed"
STORAGE_RECOMMENDATION_MESSAGE = (
    "For local basic storage, set DATABASE_PROVIDER=sqlite. "
    "For the full PowerMem capability stack, use embedded SeekDB on Linux "
    'with "powermem[seekdb]" or configure remote OceanBase with OCEANBASE_HOST.'
)


def public_startup_error_message() -> str:
    return PUBLIC_STARTUP_ERROR_MESSAGE


def public_startup_error_with_recommendation() -> str:
    return f"{PUBLIC_STARTUP_ERROR_MESSAGE}. {STORAGE_RECOMMENDATION_MESSAGE}"


def service_unavailable_message(_request: Request, service_name: str) -> str:
    return (
        f"{service_name} service unavailable: "
        f"{PUBLIC_STARTUP_ERROR_MESSAGE.lower()}. {STORAGE_RECOMMENDATION_MESSAGE}"
    )
