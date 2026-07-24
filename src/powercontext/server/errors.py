"""Stable failures owned by the Server application boundary."""

from powercontext.errors import PowerContextError


class InvalidServerRequestError(PowerContextError, ValueError):
    """Raised when a transport value violates an application-level input rule."""


__all__ = ["InvalidServerRequestError"]
