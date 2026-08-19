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

"""Stable failures owned by the Handoff Artifact Family."""

from __future__ import annotations

from powercontext.errors import PowerContextError


class HandoffError(PowerContextError):
    """Base exception for Handoff operations."""


class HandoffEvidenceUnavailableError(HandoffError, LookupError):
    """Raised when cited evidence cannot be resolved in the Handoff scope."""

    def __init__(self, citation: object) -> None:
        self.citation = citation
        super().__init__("Handoff evidence is unavailable")


class HandoffGenerationUnavailableError(HandoffError, RuntimeError):
    """Raised when no Handoff generation pipeline is configured."""

    def __init__(self) -> None:
        super().__init__("Handoff generation is not configured")


class InvalidHandoffGenerationError(HandoffError, ValueError):
    """Raised when a generation pipeline violates the Handoff contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "output": "Handoff generation returned an invalid output type",
            "objective": "generated Handoff changed the caller-owned objective",
            "evidence": "generated Handoff cited evidence outside the preparation action",
            "budget": "generated Handoff exceeds the preparation byte budget",
        }
        super().__init__(messages.get(code, f"invalid Handoff generation: {code}"))


class HandoffScopeMismatchError(HandoffError, ValueError):
    """Raised when a Prepared Handoff is used outside its originating scope."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Prepared Handoff belongs to scope {actual!r}, expected {expected!r}")


class InvalidHandoffReferenceError(HandoffError, ValueError):
    """Raised when a reference cannot address this scope's Handoff lifecycle."""

    def __init__(self, reference: object) -> None:
        self.reference = reference
        super().__init__("reference does not address the current Handoff lifecycle")


__all__ = [
    "HandoffError",
    "HandoffEvidenceUnavailableError",
    "HandoffGenerationUnavailableError",
    "HandoffScopeMismatchError",
    "InvalidHandoffGenerationError",
    "InvalidHandoffReferenceError",
]
