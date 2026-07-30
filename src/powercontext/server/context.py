"""Request-local context shared by Server transport adapters."""

from __future__ import annotations

from contextvars import ContextVar, Token

_internal_bridge: ContextVar[bool] = ContextVar("powercontext_internal_bridge", default=False)
_request_id: ContextVar[str | None] = ContextVar("powercontext_request_id", default=None)


def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def bind_internal_bridge() -> Token[bool]:
    return _internal_bridge.set(True)


def reset_internal_bridge(token: Token[bool]) -> None:
    _internal_bridge.reset(token)


def is_internal_bridge() -> bool:
    return _internal_bridge.get()


__all__ = [
    "bind_internal_bridge",
    "bind_request_id",
    "current_request_id",
    "is_internal_bridge",
    "reset_internal_bridge",
    "reset_request_id",
]
