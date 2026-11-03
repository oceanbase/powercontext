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

"""Atomic Memory extraction over the agent's cached conversation prefix.

A gateway deployed with Core keeps the request and response of each terminal agent turn for a short time and records
an opaque handle in the turn's Source metadata. For a window holding exactly that one turn, Core asks the gateway to
append its extraction instruction to that prefix, so the provider reads the conversation from its prompt cache instead
of Core sending the transcript again. The gateway runs Core's normal context checks on that request. Anything this
path cannot complete raises :class:`PrefixedExtractionUnavailable`, and the caller extracts the normal way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError

from powercontext.builtin.artifacts.prompt.service import current_prompt
from powercontext.builtin.inference import InferenceError, InvalidInferenceOutputError
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.atomic_memory.extraction import (
        AtomicMemoryEvidence,
        AtomicMemoryExtractionInput,
        AtomicMemoryExtractionOutput,
    )

PREFIX_HANDLE_KEY = "prefix_handle"
EXTRACT_PATH = "/gateway/v1/extract"

# Gateway refusals that name their cause; other refusals are reported by kind.
_GATEWAY_REASONS = {
    "prefix_extraction_disabled": "disabled",
    "prefix_unavailable": "prefix_unavailable",
    "target_mismatch": "target_mismatch",
    "invalid_instruction": "invalid_instruction",
}


class PrefixedExtractionUnavailable(InferenceError):
    """The prefixed path cannot extract this window; ``reason`` is a bounded fallback code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"prefixed extraction unavailable: {reason}")


def prefix_handle(value: AtomicMemoryExtractionInput) -> str | None:
    """The gateway prefix handle of a window that holds exactly one gateway turn."""

    if len(value.evidence) != 1:
        return None
    source = value.evidence[0].source_metadata
    metadata = source.get("metadata") if isinstance(source, dict) else None
    handle = metadata.get(PREFIX_HANDLE_KEY) if isinstance(metadata, dict) else None
    return handle if isinstance(handle, str) and handle else None


@dataclass(frozen=True)
class GatewayPrefixedExtractor:
    """Generate extraction output through the gateway's prefixed extraction route."""

    client: httpx.AsyncClient
    instructions: str
    max_output_tokens: int | None = None

    async def generate(self, value: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput

        handle = prefix_handle(value)
        if handle is None:
            raise PrefixedExtractionUnavailable("no_prefix")
        selection = current_prompt("atomic_memory.extract")
        instructions = self.instructions if selection is None else selection.compiled_instructions
        body: dict[str, Any] = {
            "handle": handle,
            "instruction": prefixed_instruction(instructions, value.evidence[0], AtomicMemoryExtractionOutput),
        }
        if self.max_output_tokens is not None:
            body["max_output_tokens"] = self.max_output_tokens
        try:
            response = await self.client.post(EXTRACT_PATH, json=body)
        except httpx.HTTPError as error:
            raise PrefixedExtractionUnavailable("gateway_unavailable") from error
        payload = _json_object(response)
        if response.status_code != httpx.codes.OK:
            raise PrefixedExtractionUnavailable(_refusal_reason(payload))
        usage = _usage(payload.get("usage"), payload.get("model"))
        try:
            output = AtomicMemoryExtractionOutput.model_validate_json(_unfenced(payload["text"]))
        except (KeyError, TypeError, ValidationError) as error:
            failure = InvalidInferenceOutputError(
                "atomic-memory-extract", "prefixed output does not match the extraction schema"
            )
            failure.usage = usage
            raise failure from error
        return GenerationResult(output=output, usage=usage)


def prefixed_instruction(
    instructions: str, evidence: AtomicMemoryEvidence, output_type: type[AtomicMemoryExtractionOutput]
) -> str:
    """The user message appended to the prefix.

    The output format is stated in the message because a response-format field would change the cached request.
    The Source record keeps the provenance and timing the normal extraction input carries, without the transcript.
    """

    metadata = evidence.source_metadata
    if isinstance(metadata, dict) and isinstance(metadata.get("metadata"), dict):
        inner = {key: item for key, item in metadata["metadata"].items() if key != PREFIX_HANDLE_KEY}
        metadata = {**metadata, "metadata": inner}
    record = {
        "evidence_id": evidence.evidence_id,
        "journal_position": evidence.journal_position,
        "source_metadata": metadata,
    }
    return "\n\n".join((
        instructions,
        "The conversation above is the Source evidence for this extraction. Only its most recent user turn is "
        "evidence: the last user message before this one and everything after it. Earlier turns are context only; "
        "do not extract a fact that only they support. This message is not evidence. Cite the evidence_id of the "
        "Source record below.",
        "Source record:\n" + json.dumps(record, ensure_ascii=False, sort_keys=True),
        "Reply with only a JSON object that matches this JSON Schema, without code fences or other text:\n"
        + json.dumps(output_type.model_json_schema(), sort_keys=True),
    ))


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _refusal_reason(payload: dict[str, Any]) -> str:
    reason = _GATEWAY_REASONS.get(payload.get("error"))  # type: ignore[arg-type]
    if reason is not None:
        return reason
    if payload.get("decision") in {"block", "withhold"}:
        return "check_rejected"
    return "gateway_error"


def _unfenced(text: object) -> str:
    if not isinstance(text, str):
        raise TypeError("text")
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").removesuffix("```")
    return stripped


def _usage(value: object, model: object) -> InferenceUsage:
    """Portable usage from a Responses ``usage`` object."""

    usage = value if isinstance(value, dict) else {}

    def count(field: object, name: str) -> int | None:
        item = field.get(name) if isinstance(field, dict) else None
        return item if isinstance(item, int) and not isinstance(item, bool) else None

    return InferenceUsage(
        requests=1,
        input_tokens=count(usage, "input_tokens"),
        output_tokens=count(usage, "output_tokens"),
        cache_read_tokens=count(usage.get("input_tokens_details"), "cached_tokens"),
        reasoning_tokens=count(usage.get("output_tokens_details"), "reasoning_tokens"),
        target_model=model if isinstance(model, str) and model else None,
    )
