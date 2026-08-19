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

from powercontext.errors import PowerContextError


class InferenceError(PowerContextError):
    """Base exception for stable PowerContext inference failures."""


class InferenceConfigurationError(InferenceError, RuntimeError):
    """Raised when a configured inference provider rejects a request."""


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
