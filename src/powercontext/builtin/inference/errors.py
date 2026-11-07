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

"""Stable failures exposed by PowerContext model capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from powercontext.errors import PowerContextError

if TYPE_CHECKING:
    from powercontext.builtin.inference.models import InferenceUsage


class InferenceError(PowerContextError):
    """Base exception for stable PowerContext inference failures."""

    usage: InferenceUsage | None = None


class InferencePrivacyError(InferenceError):
    """A required check prevented sending or releasing model content.

    This is intentionally neither an unavailable-provider error nor a validation
    error: advisory fallbacks and smaller-window retries cannot consume it.
    """

    def __init__(
        self,
        phase: Literal["before_send", "before_release"],
        decision: Literal["block", "withhold"],
        reason_codes: tuple[str, ...],
        *,
        usage: InferenceUsage | None = None,
    ) -> None:
        self.phase = phase
        self.decision = decision
        self.reason_codes = reason_codes
        self.usage = usage
        super().__init__(f"inference privacy {decision} at {phase}")


def find_privacy_error(error: BaseException) -> InferencePrivacyError | None:
    """Recover a protected-transport rejection wrapped by an upstream SDK."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, InferencePrivacyError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


class InferenceConfigurationError(InferenceError, RuntimeError):
    """Raised when a configured inference provider rejects a request."""


class InferenceUnavailableError(InferenceError, RuntimeError):
    """Raised when a transient provider failure prevents inference."""

    def __init__(self, operation: str, detail: str | None = None) -> None:
        self.operation = operation
        self.detail = detail
        message = f"inference is temporarily unavailable for {operation}"
        if detail is not None:
            message = f"{message}: {detail}"
        super().__init__(message)


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
