"""Stable failures owned by the local Runtime application boundary."""

from powercontext.errors import PowerContextError


class InvalidRuntimeRequestError(PowerContextError, ValueError):
    """Raised when a scoped Runtime request is not valid for the current state."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "since-revision": "since_revision is newer than the current Memory Revision",
        }
        super().__init__(messages.get(code, f"invalid Runtime request: {code}"))


__all__ = ["InvalidRuntimeRequestError"]
