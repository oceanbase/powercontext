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

"""Resolve scoped Prompt heads once per operation and generate unsaved demonstrations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from types import CoroutineType
from typing import Any, Concatenate, ParamSpec, Protocol, TypeVar

from opentelemetry import trace

from powercontext.builtin.artifacts.prompt.definitions import PromptDefinition, PromptRegistry
from powercontext.builtin.artifacts.prompt.errors import PromptError
from powercontext.builtin.artifacts.prompt.models import (
    GeneratePromptDemonstrations,
    Prompt,
    PromptContent,
    PromptDemonstrationResult,
    ResolvedPrompt,
)

PromptHeadReader = Callable[[str, str], Awaitable[Prompt | None]]
DemonstrationGenerator = Callable[
    [PromptDefinition, GeneratePromptDemonstrations], Awaitable[PromptDemonstrationResult]
]
_SELECTIONS: ContextVar[tuple[ResolvedPrompt, ...]] = ContextVar("powercontext_prompt_selections", default=())


def current_prompt(key: str, /) -> ResolvedPrompt | None:
    """Read only an operation-bound selection, never a mutable global head."""

    return next((selection for selection in reversed(_SELECTIONS.get()) if selection.key == key), None)


@dataclass(frozen=True)
class ScopedPrompts:
    service: PromptService
    scope_id: str


class _PromptConsumer(Protocol):
    _prompt_context: ScopedPrompts | None


ConsumerT = TypeVar("ConsumerT", bound=_PromptConsumer)
Parameters = ParamSpec("Parameters")
ResultT = TypeVar("ResultT")


def prompt_operation(
    key: str,
) -> Callable[
    [Callable[Concatenate[ConsumerT, Parameters], CoroutineType[Any, Any, ResultT]]],
    Callable[Concatenate[ConsumerT, Parameters], CoroutineType[Any, Any, ResultT]],
]:
    """Freeze one scoped selection around the entire operation, including retries."""

    def decorate(
        operation: Callable[Concatenate[ConsumerT, Parameters], CoroutineType[Any, Any, ResultT]],
    ) -> Callable[Concatenate[ConsumerT, Parameters], CoroutineType[Any, Any, ResultT]]:
        @wraps(operation)
        async def run(self: ConsumerT, /, *args: Parameters.args, **kwargs: Parameters.kwargs) -> ResultT:
            context = self._prompt_context
            if context is None:
                return await operation(self, *args, **kwargs)
            async with context.service.bind(context.scope_id, key):
                return await operation(self, *args, **kwargs)

        return run

    return decorate


class PromptService:
    """Share immutable Definitions, without sharing scoped configuration or model state."""

    def __init__(
        self,
        registry: PromptRegistry,
        head_reader: PromptHeadReader,
        generators: dict[str, DemonstrationGenerator] | None = None,
    ) -> None:
        self.registry = registry
        self._head_reader = head_reader
        self._generators = dict(generators or {})

    async def resolve(self, scope_id: str, key: str, /) -> ResolvedPrompt | None:
        definition = self.registry.get(key)
        head = await self._head_reader(scope_id, key)
        if head is not None and head.content.mode == "custom":
            self.registry.require_customization(key, during_inference=True)
        elif self.registry.capabilities[key].status != "supported":
            # External components keep their own Auto behavior; do not attribute a built-in Prompt to them.
            return None
        return definition.resolve(scope_id, head)

    @asynccontextmanager
    async def bind(self, scope_id: str, key: str, /) -> AsyncIterator[ResolvedPrompt | None]:
        existing = current_prompt(key)
        if existing is not None and existing.scope_id == scope_id:
            yield existing
            return
        selection = await self.resolve(scope_id, key)
        if selection is not None:
            prefix = f"powercontext.prompt.{key}"
            trace.get_current_span().set_attributes({
                f"{prefix}.selection": selection.selection,
                f"{prefix}.version": selection.selected_version,
                f"{prefix}.definition_version": selection.definition_version,
                f"{prefix}.builtin_version": selection.builtin_version,
                f"{prefix}.compiled_digest": selection.compiled_digest,
                f"{prefix}.demonstration_count": len(selection.demonstrations),
            })
        # Clear same-key outer bindings even for an unsupported component in a different Scope.
        selections = tuple(item for item in _SELECTIONS.get() if item.key != key)
        token = _SELECTIONS.set(selections if selection is None else (*selections, selection))
        try:
            yield selection
        finally:
            _SELECTIONS.reset(token)

    async def generate_demonstrations(
        self, key: str, request: GeneratePromptDemonstrations, /
    ) -> PromptDemonstrationResult:
        definition = self.registry.require_customization(key)
        generator = self._generators.get(key)
        if generator is None:
            raise PromptError("prompt_customization_unavailable")
        result = await generator(definition, request)
        if result.prompt_key != key or len(result.demonstrations) != request.demonstration_count:
            raise PromptError("invalid_prompt_demonstrations")
        try:
            content = PromptContent(
                schema_version="powercontext.prompt.v1",
                mode="custom",
                instructions=request.instructions,
                demonstrations=result.demonstrations,
            )
            definition.validate(content)
        except ValueError:
            raise PromptError("invalid_prompt_demonstrations") from None
        return result
