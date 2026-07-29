"""Failures owned by the built-in Runtime application boundary."""

from powercontext.errors import PowerContextError


class InvalidRuntimeRequestError(PowerContextError, ValueError):
    """Raised when a scoped Runtime request is not valid for the current state."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "since-revision": "since_revision is newer than the current Memory Revision",
        }
        super().__init__(messages.get(code, f"invalid Runtime request: {code}"))


class PreparedContextInvariantError(PowerContextError, RuntimeError):
    """Raised when an internal source violates prepared-context construction."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Prepared Context invariant failed: {code}")


__all__ = ["InvalidRuntimeRequestError", "PreparedContextInvariantError"]
