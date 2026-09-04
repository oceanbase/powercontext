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

"""Server-backed Scope resolution for Pydantic AI runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias, cast

from pydantic_ai import RunContext

from powercontext.client import PowerContextClient
from powercontext.http import ResolveScopeBindingRequest

ScopeId: TypeAlias = str | Callable[[RunContext[Any]], str] | None


async def resolve_scope_binding(
    client: PowerContextClient,
    ctx: RunContext[Any],
    constructor_scope_id: ScopeId,
    settings_scope_id: str | None,
) -> str:
    """Resolve an explicit Scope or the Server default for one run."""

    explicit_scope_id = _explicit_scope_id(ctx, constructor_scope_id, settings_scope_id)
    resolved = await client.resolve_scope_binding(ResolveScopeBindingRequest(explicit_scope_id=explicit_scope_id))
    return resolved.scope_id


def _explicit_scope_id(
    ctx: RunContext[Any],
    constructor_scope_id: ScopeId,
    settings_scope_id: str | None,
) -> str | None:
    if isinstance(constructor_scope_id, str):
        return _require_scope(constructor_scope_id)
    if constructor_scope_id is not None:
        resolver = cast(Callable[[RunContext[Any]], str], constructor_scope_id)
        return _require_scope(resolver(ctx))
    return settings_scope_id


def _require_scope(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("PowerContext scope callback must return a string")  # noqa: TRY003
    normalized = value.strip()
    if not normalized:
        raise ValueError("PowerContext scope_id must contain non-whitespace characters")  # noqa: TRY003
    return normalized
