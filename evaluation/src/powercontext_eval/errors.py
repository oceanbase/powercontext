# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
