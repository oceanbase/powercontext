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
subagent — including the built-in ``general-purpose`` subagent that Deep Agents would otherwise create
without recall — and keeps ``auto_capture`` on the main agent so a delegated turn is stored once.

Both the main agent and its subagents resolve the same ``PowerContextScope`` because Deep Agents forwards the
parent runtime context to the ``task`` tool's subagent graphs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from powercontext_langchain import PowerContextMiddleware, PowerContextScope

_COMPILED_KEYS = ("runnable", "graph_id")
_GENERAL_PURPOSE = "general-purpose"


def _has_powercontext_middleware(middleware: Iterable[Any]) -> bool:
    return any(isinstance(item, PowerContextMiddleware) for item in middleware)


def _recall_only_copy(middleware: PowerContextMiddleware) -> PowerContextMiddleware:
    """Return an equivalent recall-only middleware; the caller's instance is never mutated."""
    return PowerContextMiddleware(auto_capture=False, trace_content=middleware.trace_content)


def _recall_only_subagent(spec: Any) -> Any:
    """Return ``spec`` with a recall-only middleware, leaving compiled subagents alone."""
    if not isinstance(spec, Mapping):
        return spec
    if any(key in spec for key in _COMPILED_KEYS):
        return spec
    existing = list(spec.get("middleware") or ())
    augmented = dict(spec)
    if _has_powercontext_middleware(existing):
        # A caller-supplied capture middleware would store the delegated turn too, and the different turn
        # texts produce different Source identities, so idempotency cannot remove it. Keep its position and
        # options but force the recall-only contract on a copy.
        augmented["middleware"] = [
            _recall_only_copy(item) if isinstance(item, PowerContextMiddleware) else item for item in existing
        ]
        return augmented
    augmented["middleware"] = [*existing, PowerContextMiddleware(auto_capture=False)]
    return augmented


def _general_purpose_subagent() -> dict[str, Any]:
    """Return Deep Agents' built-in general-purpose spec with recall-only middleware.

    The spec omits ``model``, ``tools`` and ``interrupt_on``, so Deep Agents inherits them from the parent
    exactly as it does for its own default subagent.
    """
    try:
        from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dependency
        raise ImportError(  # noqa: TRY003
            "Deep Agents integration requires the 'deepagents' package: pip install 'deepagents>=0.7,<0.8'"
        ) from exc
    return {**GENERAL_PURPOSE_SUBAGENT, "middleware": [PowerContextMiddleware(auto_capture=False)]}


def _spec_name(spec: Any) -> str | None:
    return spec.get("name") if isinstance(spec, Mapping) else None


def _effective_model(model: Any) -> Any:
    """Return the model Deep Agents will use, resolving its deprecated default when needed."""
    if model is not None:
        return model
    try:
        from deepagents.graph import _build_default_model
    except ImportError:  # pragma: no cover - exercised only with a different deepagents layout
        try:
            from deepagents.graph import get_default_model as _build_default_model
        except ImportError:
            return None
    try:
        return _build_default_model()
    except Exception:  # the builder can require provider configuration
        return None


def _default_general_purpose_enabled(model: Any) -> bool:
    """Whether the active harness profile allows Deep Agents' default subagent.

    A profile can disable the built-in ``general-purpose`` subagent, and Deep Agents honours that only when it
    auto-adds the default. An explicit spec bypasses the setting, so the adapter must not add one in that case.
    ``model=None`` is deprecated but still supported, and Deep Agents resolves it to its own default model, so the
    same effective model decides here. Profile resolution is best effort: any failure keeps the current behavior.
    """
    resolved = _effective_model(model)
    if resolved is None:
        return True
    try:
        from deepagents._models import resolve_model
        from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model
    except ImportError:  # pragma: no cover - exercised only with a different deepagents layout
        return True
    try:
        spec = model if isinstance(model, str) else None
        profile = _harness_profile_for_model(resolve_model(resolved) if spec is not None else resolved, spec)
    except Exception:  # resolution must never block agent creation
        return True
    general_purpose = profile.general_purpose_subagent
    return general_purpose is None or general_purpose.enabled is not False


def powercontext_deep_agent_middleware(middleware: Sequence[Any] = (), *, auto_capture: bool = False) -> list[Any]:
    """Return ``middleware`` with one capture-configured PowerContext entry appended when absent."""
    combined = list(middleware)
    if not _has_powercontext_middleware(combined):
        combined.append(PowerContextMiddleware(auto_capture=auto_capture))
    return combined


def powercontext_subagents(subagents: Iterable[Any] | None, *, model: Any = None) -> list[Any]:
    """Return subagent specs that all recall through PowerContext.

    Declarative specs receive a recall-only ``PowerContextMiddleware``; an existing ``PowerContextMiddleware``
    is replaced by an equivalent recall-only copy so a caller cannot accidentally capture a delegated turn.
    Compiled (:class:`deepagents.CompiledSubAgent`) and remote (:class:`deepagents.AsyncSubAgent`) subagents own
    their graph and are returned unchanged; add ``PowerContextMiddleware`` inside that graph explicitly.

    Deep Agents adds a built-in ``general-purpose`` subagent when no spec uses that name, and it would be
    created without recall. This function normally returns such a spec, carrying recall-only middleware, so
    default delegation keeps the same Scope-bound recall as the main agent. A caller-supplied
    ``general-purpose`` spec is normalized instead of duplicated, and a harness profile that disables the
    default subagent is respected by returning no extra spec.

    Args:
        subagents: Caller-supplied subagent specifications.
        model: The agent model, used only to resolve the active harness profile.
    """
    specs = [_recall_only_subagent(spec) for spec in subagents or ()]
    if not any(_spec_name(spec) == _GENERAL_PURPOSE for spec in specs) and _default_general_purpose_enabled(model):
        specs.insert(0, _general_purpose_subagent())
    return specs


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
            ``PowerContextMiddleware``, and an existing capture-enabled entry is normalized to recall-only.
            The built-in ``general-purpose`` subagent receives the same recall middleware when the caller
            does not pass a spec with that name and the active harness profile does not disable it; compiled
            and remote subagents are returned unchanged.
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
        subagents=powercontext_subagents(subagents, model=model),
        **kwargs,
    )


__all__ = [
    "create_powercontext_deep_agent",
    "powercontext_deep_agent_middleware",
    "powercontext_subagents",
]
