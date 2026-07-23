"""Stable failures exposed by PowerContext model capabilities."""

from __future__ import annotations

from powercontext.errors import PowerContextError


class InferenceError(PowerContextError):
    """Base exception for stable PowerContext inference failures."""


class InferenceUnavailableError(InferenceError, RuntimeError):
    """Raised when a transient provider failure prevents inference."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"inference is temporarily unavailable for {operation}")


class InferenceTimeoutError(InferenceError, TimeoutError):
    """Raised when an inference operation exceeds its configured deadline."""

    def __init__(self, operation: str, timeout_seconds: float) -> None:
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        super().__init__(f"inference timed out for {operation} after {timeout_seconds:g} seconds")


class InvalidInferenceOutputError(InferenceError, ValueError):
    """Raised when model output violates a PowerContext capability contract."""

    def __init__(self, operation: str, detail: str) -> None:
        self.operation = operation
        self.detail = detail
        super().__init__(f"invalid inference output for {operation}: {detail}")
