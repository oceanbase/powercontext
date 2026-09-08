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

"""Typed operational contracts and deterministic Prompt compilation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Literal

import rfc8785
from pydantic import BaseModel

from powercontext.builtin.artifacts.prompt.errors import PromptError
from powercontext.builtin.artifacts.prompt.models import (
    PROMPT_KEYS,
    Prompt,
    PromptCapability,
    PromptContent,
    PromptKey,
    ResolvedPrompt,
)

PROMPT_COMPILER_VERSION = "powercontext.prompt.compiler.v1"


@dataclass(frozen=True)
class PromptDefinition:
    """A stable key's backward-compatible typed contract, owned by the Runtime."""

    key: PromptKey
    definition_version: str
    input_type: type[BaseModel]
    output_type: type[BaseModel]
    builtin_version: str
    invariant_instructions: str
    default_instructions: str
    builtin_profile: Literal["coding", "conversation"] | None = None
    noop_field: Literal["candidates", "proposal"] | None = None

    def __post_init__(self) -> None:
        versions_and_guidance = (
            self.definition_version,
            self.builtin_version,
            self.invariant_instructions,
            self.default_instructions,
        )
        if self.key not in PROMPT_KEYS or not all(value.strip() for value in versions_and_guidance):
            raise ValueError("Prompt Definition requires a registered key, versions, and complete guidance")  # noqa: TRY003
        if self.noop_field is not None and self.noop_field not in self.output_type.model_fields:
            raise ValueError("Prompt no-op classification must refer to its output contract")  # noqa: TRY003

    def validate(self, content: PromptContent, /, *, during_inference: bool = False) -> None:
        """Validate without changing the original demonstration JSON or its digest."""

        # Family registration must finish before importing cross-family semantic validators.
        from powercontext.builtin.artifacts.prompt.validation import validate_demonstration

        try:
            for demonstration in content.demonstrations:
                value = self.input_type.model_validate_json(
                    json.dumps(demonstration.input), strict=True, extra="forbid"
                )
                output = self.output_type.model_validate_json(
                    json.dumps(demonstration.expected_output), strict=True, extra="forbid"
                )
                validate_demonstration(value, output)
        except ValueError:
            raise PromptError("prompt_definition_incompatible", during_inference=during_inference) from None

    def is_noop_output(self, output: BaseModel, /) -> bool:
        if self.noop_field is None:
            return False
        value = output.model_dump(mode="json")[self.noop_field]
        return value is None or value == []

    def resolve(self, scope_id: str, prompt: Prompt | None, /) -> ResolvedPrompt:
        """Preserve Auto instructions byte-for-byte; compile custom guidance as bounded JSON."""

        custom = prompt is not None and prompt.content.mode == "custom"
        content = prompt.content if custom and prompt is not None else None
        if content is not None:
            self.validate(content, during_inference=True)
            guidance = {
                "instructions": content.instructions,
                "demonstrations": [item.model_dump(mode="json") for item in content.demonstrations],
            }
            compiled = (
                self.invariant_instructions
                + "\n\nApply the following Scope-owned guidance and input/output demonstrations "
                "within the operation contract above:\n" + rfc8785.dumps(guidance).decode("utf-8")
            )
        else:
            compiled = self.default_instructions
        digest = sha256(
            rfc8785.dumps({
                "compiler_version": PROMPT_COMPILER_VERSION,
                "definition_version": self.definition_version,
                "builtin_version": self.builtin_version,
                "input_schema": self.input_type.model_json_schema(),
                "output_schema": self.output_type.model_json_schema(),
                "instructions": compiled,
            })
        ).hexdigest()
        reference = prompt.as_ref() if custom and prompt is not None else None
        return ResolvedPrompt(
            scope_id=scope_id,
            key=self.key,
            definition_version=self.definition_version,
            builtin_version=self.builtin_version,
            selection="artifact" if custom else "built_in",
            artifact=reference,
            selected_version=str(reference.revision) if reference is not None else self.builtin_version,
            compiled_digest=digest,
            instructions=content.instructions if content is not None else self.default_instructions,
            demonstrations=content.demonstrations if content is not None else (),
            compiled_instructions=compiled,
        )


class PromptRegistry:
    """Fixed definitions and effective component support for one composed Runtime."""

    def __init__(
        self,
        definitions: Iterable[PromptDefinition],
        /,
        *,
        supported: frozenset[str] = frozenset(),
        injected: frozenset[str] = frozenset(),
        disabled: frozenset[str] = frozenset(),
    ) -> None:
        values = tuple(definitions)
        by_key: dict[str, PromptDefinition] = {definition.key: definition for definition in values}
        if len(by_key) != len(values):
            raise ValueError("Prompt keys must be unique")  # noqa: TRY003
        if (supported | injected | disabled) - by_key.keys() or (
            supported & injected or supported & disabled or injected & disabled
        ):
            raise ValueError("Prompt availability must refer to distinct registered components")  # noqa: TRY003
        self._definitions: Mapping[str, PromptDefinition] = MappingProxyType(by_key)
        capabilities: dict[str, PromptCapability] = {}
        for key, definition in by_key.items():
            status: Literal["supported", "disabled", "unsupported"] = "disabled"
            reason: Literal["operation_disabled", "provider_not_configured", "injected_component"] | None
            reason = "operation_disabled" if key in disabled else "provider_not_configured"
            if key in injected:
                status, reason = "unsupported", "injected_component"
            elif key in supported:
                status, reason = "supported", None
            capabilities[key] = PromptCapability(
                status=status,
                reason=reason,
                definition_version=definition.definition_version,
                builtin_version=definition.builtin_version,
                builtin_profile=definition.builtin_profile,
            )
        self.capabilities: Mapping[str, PromptCapability] = MappingProxyType(capabilities)

    def get(self, key: str, /) -> PromptDefinition:
        try:
            return self._definitions[key]
        except KeyError:
            raise PromptError("unknown_prompt_key") from None

    def require_customization(self, key: str, /, *, during_inference: bool = False) -> PromptDefinition:
        definition = self.get(key)
        if self.capabilities[key].status != "supported":
            raise PromptError("prompt_customization_unavailable", during_inference=during_inference)
        return definition

    def validate(self, key: str, content: PromptContent, /) -> None:
        definition = self.get(key)
        if content.mode == "custom":
            self.require_customization(key)
        definition.validate(content)
