"""Evaluation runner errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from powercontext_eval.process import CommandResult


class PowerContextEvalError(Exception):
    """Base error for evaluation runner failures."""


class CommandError(PowerContextEvalError):
    """Base error for a failed child process."""

    def __init__(self, message: str, result: CommandResult) -> None:
        super().__init__(message)
        self.result = result


class CommandNotFound(CommandError):
    """The requested executable could not be started."""


class CommandTimedOut(CommandError):
    """The child process exceeded its deadline."""


class CommandCancelled(CommandError):
    """The child process was cancelled by its owner."""


class CommandFailed(CommandError):
    """The child process exited unsuccessfully."""


class GitSourceError(PowerContextEvalError):
    """A Git source could not be normalized, resolved, or materialized."""
