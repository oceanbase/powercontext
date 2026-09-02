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

"""Run-scoped connection configuration for one LangGraph run."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PowerContextScope:
    """PowerContext connection overrides for one graph run.

    LangGraph 1.x passes run-scoped configuration through ``context_schema``, which nodes receive as
    ``Runtime[ContextT]``. Supplying this dataclass makes the scope an explicit invocation argument::

        await graph.ainvoke(state, context=PowerContextScope())

    Separate invocations of one compiled graph can carry separate explicit scopes. When ``scope_id`` is omitted,
    the Server default Scope is used. Every connection field is optional; unset fields fall back to environment
    settings.
    """

    scope_id: str | None = None
    base_url: str | None = None
    # A bearer credential passed through the graph ``context``; hidden from the dataclass repr so it never surfaces
    # in a traceback or trace of the run context. Kept a plain ``str`` for ergonomic ``PowerContextScope(token=...)``.
    token: str | None = field(default=None, repr=False)
    timeout: float | None = None


__all__ = ["PowerContextScope"]
