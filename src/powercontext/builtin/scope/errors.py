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

"""Stable failures for Scope organization and binding."""

from __future__ import annotations

from powercontext.errors import PowerContextError


class ScopeError(PowerContextError):
    """Base failure for Scope operations."""


class ScopeNotFoundError(ScopeError, LookupError):
    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        super().__init__("scope was not found")


class ScopeVersionConflictError(ScopeError, RuntimeError):
    def __init__(self, scope_id: str, expected: int, actual: int) -> None:
        self.scope_id = scope_id
        self.expected = expected
        self.actual = actual
        super().__init__("scope metadata changed since it was read")


class ScopeIdempotencyConflictError(ScopeError, RuntimeError):
    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__("scope creation key was reused with different parameters")


class ScopeRelationshipError(ScopeError, ValueError):
    def __init__(self, relationship: str, issue: str) -> None:
        self.relationship = relationship
        self.issue = issue
        super().__init__(f"invalid Scope {relationship}: {issue}")


class ScopeBindingNotFoundError(ScopeError, LookupError):
    """Raised when no explicit, durable, or default binding can be resolved."""

    def __init__(self) -> None:
        super().__init__("no Scope binding is available")
