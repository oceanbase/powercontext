"""
Async-safe trace context propagation for the ``powermem`` logger tree.

Uses ``contextvars`` so each async request carries its own request_id /
user_id / agent_id without explicit parameter threading.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Tuple

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"

_request_id_var: ContextVar[str] = ContextVar("request_id", default=_ZERO_UUID)
_user_id_var: ContextVar[str] = ContextVar("user_id", default="")
_agent_id_var: ContextVar[str] = ContextVar("agent_id", default="")


def set_log_context(
    *,
    request_id: str = _ZERO_UUID,
    user_id: str = "",
    agent_id: str = "",
) -> Tuple[Token, Token, Token]:
    t1 = _request_id_var.set(request_id)
    t2 = _user_id_var.set(user_id)
    t3 = _agent_id_var.set(agent_id)
    return (t1, t2, t3)


def reset_log_context(tokens: Tuple[Token, Token, Token]) -> None:
    _request_id_var.reset(tokens[0])
    _user_id_var.reset(tokens[1])
    _agent_id_var.reset(tokens[2])


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()  # type: ignore[attr-defined]
        record.user_id = _user_id_var.get()  # type: ignore[attr-defined]
        record.agent_id = _agent_id_var.get()  # type: ignore[attr-defined]
        return True
