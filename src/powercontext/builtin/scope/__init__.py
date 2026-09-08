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

"""Durable Scope organization and binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from powercontext.builtin.scope.errors import (
    ScopeBindingNotFoundError,
    ScopeError,
    ScopeIdempotencyConflictError,
    ScopeNotFoundError,
    ScopeRelationshipError,
    ScopeVersionConflictError,
)
from powercontext.builtin.scope.models import (
    ScopeBinding,
    ScopeBindingKey,
    ScopeDescriptor,
    ScopeDescriptorPage,
    ScopeDiscovery,
    ScopeDraft,
    ScopeExternalReference,
    ScopeMutation,
    ScopeQueryField,
    ScopeSelection,
)

if TYPE_CHECKING:
    from powercontext.builtin.scope.application import ScopeApplication


def __getattr__(name: str) -> Any:
    if name in {"ScopeApplication", "generate_scope_id"}:
        from powercontext.builtin.scope import application

        return getattr(application, name)
    raise AttributeError(name)


__all__ = [
    "ScopeApplication",
    "ScopeBinding",
    "ScopeBindingKey",
    "ScopeBindingNotFoundError",
    "ScopeDescriptor",
    "ScopeDescriptorPage",
    "ScopeDiscovery",
    "ScopeDraft",
    "ScopeError",
    "ScopeExternalReference",
    "ScopeIdempotencyConflictError",
    "ScopeMutation",
    "ScopeNotFoundError",
    "ScopeQueryField",
    "ScopeRelationshipError",
    "ScopeSelection",
    "ScopeVersionConflictError",
    "generate_scope_id",
]
