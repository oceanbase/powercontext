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

"""Read the active :class:`PowerContextScope` from the LangGraph runtime."""

from __future__ import annotations

from langgraph.runtime import get_runtime

from .scope import PowerContextScope


def current_scope() -> PowerContextScope | None:
    """Return the run scope from the current graph runtime, or ``None`` outside a run.

    ``get_runtime`` raises :class:`RuntimeError` when called outside any runnable context, and returns ``None``
    when a runnable context exists without a graph runtime attached (for example when a tool is invoked directly
    rather than from a compiled graph). In both cases the scope falls back to the environment settings.
    """

    try:
        runtime = get_runtime(PowerContextScope)
    except RuntimeError:
        return None
    if runtime is None:
        return None
    context = runtime.context
    return context if isinstance(context, PowerContextScope) else None
