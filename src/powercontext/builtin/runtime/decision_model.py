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

"""Provider-neutral decision model contracts for runtime gates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext.builtin.inference import InferenceError, InferenceUsage, StructuredGenerator


class DecisionModelOption(BaseModel):
    """One selectable answer exposed to a deterministic runtime decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: Annotated[str, Field(min_length=1, max_length=128)]
    label: Annotated[str, Field(min_length=1, max_length=512)]
    description: Annotated[str | None, Field(min_length=1, max_length=4096)] = None

    @field_validator("option_id", "label", "description")
    @classmethod
    def validate_trimmed_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip():
            raise ValueError("DecisionModel text fields must be trimmed")  # noqa: TRY003
        return value


class DecisionModelRequest(BaseModel):
    """A bounded, explicit-choice decision made outside the main agent loop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Annotated[str, Field(min_length=1, max_length=128)]
    question: Annotated[str, Field(min_length=1, max_length=8192)]
    options: Annotated[tuple[DecisionModelOption, ...], Field(min_length=2, max_length=128)]
    context: Annotated[str | None, Field(min_length=1, max_length=32768)] = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("operation", "question", "context")
    @classmethod
    def validate_trimmed_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip():
            raise ValueError("DecisionModel text fields must be trimmed")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_option_ids(self) -> DecisionModelRequest:
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("DecisionModel option IDs must be unique")  # noqa: TRY003
        return self


class DecisionModelResult(BaseModel):
    """A selected option plus portable usage and fallback metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_option_id: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    confidence: Annotated[float | None, Field(ge=0.0, le=1.0)] = None
    scores: Mapping[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)
    rationale: Annotated[str | None, Field(min_length=1, max_length=4096)] = None
    used_fallback: bool = False
    fallback_reason: Annotated[str | None, Field(min_length=1, max_length=256)] = None
    usage: InferenceUsage = Field(default_factory=lambda: InferenceUsage(requests=0))

    @field_validator("selected_option_id", "rationale", "fallback_reason")
    @classmethod
    def validate_trimmed_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip():
            raise ValueError("DecisionModel text fields must be trimmed")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_fallback(self) -> DecisionModelResult:
        if self.used_fallback:
            if self.selected_option_id is not None:
                raise ValueError("DecisionModel fallback results must not select an option")  # noqa: TRY003
            if self.fallback_reason is None:
                raise ValueError("DecisionModel fallback results require fallback_reason")  # noqa: TRY003
        elif self.fallback_reason is not None:
            raise ValueError("DecisionModel fallback_reason requires used_fallback")  # noqa: TRY003
        return self


class DecisionModel(Protocol):
    """Evaluate explicit-choice runtime decisions without exposing provider SDKs."""

    policy_id: str

    async def evaluate(self, request: DecisionModelRequest, /) -> DecisionModelResult:
        """Return one provider-neutral decision result or an explicit fallback."""

        ...


class StructuredDecisionModel:
    """Adapt a structured generator into a fail-open DecisionModel."""

    def __init__(
        self,
        generator: StructuredGenerator[DecisionModelRequest, DecisionModelResult],
        *,
        policy_id: str,
    ) -> None:
        self._generator = generator
        self.policy_id = policy_id

    async def evaluate(self, request: DecisionModelRequest, /) -> DecisionModelResult:
        try:
            result = await self._generator.generate(request)
        except InferenceError as error:
            return DecisionModelResult(used_fallback=True, fallback_reason=type(error).__name__)
        output = result.output.model_copy(update={"usage": result.usage})
        option_ids = {option.option_id for option in request.options}
        if output.used_fallback:
            return output
        if output.selected_option_id not in option_ids:
            return DecisionModelResult(
                used_fallback=True,
                fallback_reason="invalid_output",
                usage=result.usage,
            )
        if any(option_id not in option_ids for option_id in output.scores):
            return DecisionModelResult(
                used_fallback=True,
                fallback_reason="invalid_output",
                usage=result.usage,
            )
        return output


__all__ = [
    "DecisionModel",
    "DecisionModelOption",
    "DecisionModelRequest",
    "DecisionModelResult",
    "StructuredDecisionModel",
]
