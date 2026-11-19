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
    BuiltinPromptInstructions,
    GeneratePromptDemonstrations,
    Prompt,
    PromptConfiguration,
    PromptContent,
    PromptDemonstrationResult,
    PromptInstructions,
    ResolvedPrompt,
)

PromptHeadsReader = Callable[[str, tuple[str, ...]], Awaitable[dict[str, Prompt]]]
DemonstrationGenerator = Callable[
    [PromptDefinition, GeneratePromptDemonstrations], Awaitable[PromptDemonstrationResult]
]
_SELECTIONS: ContextVar[tuple[ResolvedPrompt, ...]] = ContextVar("powercontext_prompt_selections", default=())
_HEADS: ContextVar[tuple[PromptService, str, dict[str, Prompt | None]] | None] = ContextVar(
    "powercontext_prompt_heads", default=None
)


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
        heads_reader: PromptHeadsReader,
        generators: dict[str, DemonstrationGenerator] | None = None,
    ) -> None:
        self.registry = registry
        self._heads_reader = heads_reader
        self._generators = dict(generators or {})

    async def read_configuration(self, scope_id: str, key: str, /) -> PromptConfiguration:
        """Read defaults and one saved head even when inference is disabled."""

        return (await self.read_configurations(scope_id, (key,)))[0]

    async def read_configurations(self, scope_id: str, keys: tuple[str, ...], /) -> tuple[PromptConfiguration, ...]:
        definitions = tuple(self.registry.get(key) for key in keys)
        heads = await self._read_heads(scope_id, keys)
        return tuple(self._configuration(scope_id, definition, heads.get(definition.key)) for definition in definitions)

    @asynccontextmanager
    async def snapshot(self, scope_id: str, keys: tuple[str, ...], /) -> AsyncIterator[None]:
        """Read one operation's heads together, including explicit absent heads."""
        heads = await self._read_heads(scope_id, keys)
        token = _HEADS.set((self, scope_id, {key: heads.get(key) for key in keys}))
        try:
            yield
        finally:
            _HEADS.reset(token)

    async def _read_heads(self, scope_id: str, keys: tuple[str, ...]) -> dict[str, Prompt]:
        snapshot = _HEADS.get()
        if snapshot is None or snapshot[0] is not self or snapshot[1] != scope_id:
            return await self._heads_reader(scope_id, keys)
        known = snapshot[2]
        missing = tuple(key for key in keys if key not in known)
        heads = await self._heads_reader(scope_id, missing) if missing else {}
        return {key: head for key in keys if (head := known.get(key, heads.get(key))) is not None}

    def _configuration(self, scope_id: str, definition: PromptDefinition, head: Prompt | None) -> PromptConfiguration:
        capability = self.registry.capabilities[definition.key]
        mode = "auto" if head is None else head.content.mode
        builtin = None
        effective = None
        if capability.reason != "injected_component":
            builtin = BuiltinPromptInstructions(
                version=definition.builtin_version,
                profile=definition.builtin_profile,
                instructions=definition.default_instructions,
            )
            custom = head.content if head is not None and mode == "custom" else None
            effective = PromptInstructions(
                instructions=custom.instructions if custom is not None else definition.default_instructions,
                demonstrations=custom.demonstrations if custom is not None else (),
            )
        return PromptConfiguration(
            scope_id=scope_id,
            prompt_key=definition.key,
            status=capability.status,
            reason=capability.reason,
            mode=mode,
            artifact=None if head is None else head.as_ref(),
            effective=effective,
            builtin=builtin,
        )

    async def resolve(self, scope_id: str, key: str, /) -> ResolvedPrompt | None:
        definition = self.registry.get(key)
        head = (await self._read_heads(scope_id, (key,))).get(key)
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
