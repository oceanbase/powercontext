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

"""Deep Agents adapter for the PowerContext LangChain middleware.

``create_deep_agent`` composes its own middleware stack and compiles every declarative subagent as a
separate graph, so a middleware passed to ``create_deep_agent`` only wraps the main agent's model calls.
``create_powercontext_deep_agent`` installs a recall-only ``PowerContextMiddleware`` on each declarative
subagent as well, and keeps ``auto_capture`` on the main agent so a delegated turn is stored once.

Both the main agent and its subagents resolve the same ``PowerContextScope`` because Deep Agents forwards the
parent runtime context to the ``task`` tool's subagent graphs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from powercontext_langchain import PowerContextMiddleware, PowerContextScope

_COMPILED_KEYS = ("runnable", "graph_id")


def _has_powercontext_middleware(middleware: Iterable[Any]) -> bool:
    return any(isinstance(item, PowerContextMiddleware) for item in middleware)


def _recall_only_subagent(spec: Any) -> Any:
    """Return ``spec`` with a recall-only middleware, leaving compiled subagents alone."""
    if not isinstance(spec, Mapping):
        return spec
    if any(key in spec for key in _COMPILED_KEYS):
        return spec
    existing = spec.get("middleware") or ()
    if _has_powercontext_middleware(existing):
        return spec
    augmented = dict(spec)
    augmented["middleware"] = [*existing, PowerContextMiddleware(auto_capture=False)]
    return augmented


def powercontext_deep_agent_middleware(middleware: Sequence[Any] = (), *, auto_capture: bool = False) -> list[Any]:
    """Return ``middleware`` with one capture-configured PowerContext entry appended when absent."""
    combined = list(middleware)
    if not _has_powercontext_middleware(combined):
        combined.append(PowerContextMiddleware(auto_capture=auto_capture))
    return combined


def powercontext_subagents(subagents: Iterable[Any] | None) -> list[Any] | None:
    """Return declarative subagent specs with recall-only PowerContext middleware.

    Compiled (:class:`deepagents.CompiledSubAgent`) and remote (:class:`deepagents.AsyncSubAgent`) subagents
    own their graph and are returned unchanged; add ``PowerContextMiddleware`` inside that graph explicitly.
    """
    if subagents is None:
        return None
    return [_recall_only_subagent(spec) for spec in subagents]


def create_powercontext_deep_agent(
    model: Any = None,
    tools: Sequence[Any] | None = None,
    *,
    subagents: Iterable[Any] | None = None,
    middleware: Sequence[Any] = (),
    auto_capture: bool = False,
    **kwargs: Any,
) -> Any:
    """Create a Deep Agent with bounded PowerContext recall for the agent and its subagents.

    Args:
        model: Chat model or ``provider:model`` string, forwarded to ``create_deep_agent``.
        tools: Additional tools, forwarded to ``create_deep_agent``.
        subagents: Declarative subagent specs. Each one receives a recall-only
            ``PowerContextMiddleware``; compiled and remote subagents are returned unchanged.
        middleware: Additional middleware for the main agent. An existing
            ``PowerContextMiddleware`` entry is reused instead of duplicated.
        auto_capture: Capture successful completed turns on the main agent. Subagents never capture, so a
            delegated turn is stored once.
        **kwargs: Any other ``create_deep_agent`` keyword argument. ``context_schema`` defaults to
            :class:`PowerContextScope`.

    Returns:
        The compiled agent returned by ``create_deep_agent``.
    """
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dependency
        raise ImportError(  # noqa: TRY003
            "Deep Agents integration requires the 'deepagents' package: pip install 'deepagents>=0.7,<0.8'"
        ) from exc

    if kwargs.get("context_schema") is None:
        kwargs["context_schema"] = PowerContextScope

    return create_deep_agent(
        model,
        tools,
        middleware=powercontext_deep_agent_middleware(middleware, auto_capture=auto_capture),
        subagents=powercontext_subagents(subagents),
        **kwargs,
    )


__all__ = [
    "create_powercontext_deep_agent",
    "powercontext_deep_agent_middleware",
    "powercontext_subagents",
]
