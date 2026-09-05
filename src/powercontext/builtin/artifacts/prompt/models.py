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

"""Scope-owned operational Prompt content and immutable runtime selections."""

from __future__ import annotations

import unicodedata
from typing import Annotated, ClassVar, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext.artifacts import Artifact, ArtifactRef

MAX_PROMPT_INSTRUCTIONS = 32_768
MAX_PROMPT_DEMONSTRATIONS = 50
MAX_DEMONSTRATION_BYTES = 64 * 1024
MAX_PROMPT_BYTES = 256 * 1024

PromptKey = Literal[
    "memory.extract",
    "memory.rerank",
    "experience.incubate",
    "experience.generate",
    "skill.generate",
    "handoff.generate",
]
PROMPT_KEYS: tuple[PromptKey, ...] = (
    "memory.extract",
    "memory.rerank",
    "experience.incubate",
    "experience.generate",
    "skill.generate",
    "handoff.generate",
)


class _PromptValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PromptDemonstration(_PromptValue):
    """One complete operation input and its desired, schema-valid output."""

    input: JsonValue
    expected_output: JsonValue

    @model_validator(mode="after")
    def require_bounded_json(self) -> PromptDemonstration:
        if len(rfc8785.dumps(self.model_dump(mode="json"))) > MAX_DEMONSTRATION_BYTES:
            raise ValueError("demonstration exceeds the canonical JSON size limit")  # noqa: TRY003
        return self


class PromptContent(_PromptValue):
    """Canonical content; typed demonstration compatibility belongs to its Definition."""

    schema_version: Literal["powercontext.prompt.v1"]
    mode: Literal["auto", "custom"]
    instructions: Annotated[str, Field(max_length=MAX_PROMPT_INSTRUCTIONS)]
    demonstrations: Annotated[tuple[PromptDemonstration, ...], Field(max_length=MAX_PROMPT_DEMONSTRATIONS)]

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str) -> str:
        return unicodedata.normalize("NFC", value).strip()

    @model_validator(mode="after")
    def require_valid_mode_and_size(self) -> PromptContent:
        if self.mode == "auto" and (self.instructions or self.demonstrations):
            raise ValueError("Auto requires empty instructions and demonstrations")  # noqa: TRY003
        if self.mode == "custom" and not self.instructions:
            raise ValueError("Custom requires non-blank instructions")  # noqa: TRY003
        if len(rfc8785.dumps(self.model_dump(mode="json"))) > MAX_PROMPT_BYTES:
            raise ValueError("Prompt exceeds the canonical JSON size limit")  # noqa: TRY003
        return self


class Prompt(Artifact[PromptContent]):
    """One immutable operational Prompt revision in an existing Scope."""

    family: ClassVar[str] = "prompt"
    model_config = ConfigDict(frozen=True)


class PromptCapability(_PromptValue):
    """Deployment metadata, independent of any Scope's persisted selection."""

    status: Literal["supported", "disabled", "unsupported"]
    reason: Literal["operation_disabled", "provider_not_configured", "injected_component"] | None
    definition_version: str
    builtin_version: str
    builtin_profile: Literal["coding", "conversation"] | None


class GeneratePromptDemonstrations(_PromptValue):
    """Generate suggestions without changing a Prompt head."""

    instructions: Annotated[str, Field(min_length=1, max_length=MAX_PROMPT_INSTRUCTIONS)]
    demonstration_count: Annotated[int, Field(ge=1, le=20)]

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value).strip()
        if not normalized:
            raise ValueError("instructions must not be blank")  # noqa: TRY003
        return normalized


class PromptDemonstrationResult(_PromptValue):
    prompt_key: PromptKey
    demonstrations: tuple[PromptDemonstration, ...]


class ResolvedPrompt(_PromptValue):
    """One selection frozen before inference, including every model retry."""

    scope_id: str
    key: PromptKey
    definition_version: str
    builtin_version: str
    selection: Literal["built_in", "artifact"]
    artifact: ArtifactRef | None
    selected_version: str
    compiled_digest: str
    instructions: str = Field(repr=False)
    demonstrations: tuple[PromptDemonstration, ...] = Field(repr=False)
    compiled_instructions: str = Field(repr=False)

    def trace_attributes(self) -> dict[str, str | int]:
        """Return bounded inference metadata without Scope identities or editable bodies."""
        attributes: dict[str, str | int] = {
            "powercontext.prompt.key": self.key,
            "powercontext.prompt.selection": self.selection,
            "powercontext.prompt.builtin_version": self.builtin_version,
            "powercontext.prompt.definition_version": self.definition_version,
            "powercontext.prompt.compiled_digest": self.compiled_digest,
            "powercontext.prompt.demonstration_count": len(self.demonstrations),
        }
        if self.artifact is not None:
            attributes.update({
                "powercontext.prompt.artifact.family": self.artifact.family,
                "powercontext.prompt.artifact.id": self.artifact.artifact_id,
                "powercontext.prompt.artifact.revision": self.artifact.revision,
            })
        return attributes
