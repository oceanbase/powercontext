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

"""Stable, secret-free failures at the code analysis boundary."""

from powercontext.errors import PowerContextError


class CodeError(PowerContextError):
    """Carry a bounded error code without engine output or repository paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class InvalidCodeRequestError(CodeError, ValueError):
    """Reject invalid paths, query targets, and budgets."""


class CodeUnavailableError(CodeError, RuntimeError):
    """Report a temporarily unavailable code index or engine."""


class CodeChangedError(CodeError, RuntimeError):
    """Require a fresh query after repository or index content changes."""


class UnsupportedCodeCapabilityError(CodeError, RuntimeError):
    """Report analysis that the configured engine cannot provide reliably."""
