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
an opaque handle and the prefix's token counts in the turn's Source metadata. A turn too long for one Source is
captured as consecutive parts, and the handle is on the last. For a window holding exactly one turn, Core asks the
gateway to append its extraction instruction to that prefix, so the provider reads the conversation from its prompt
cache instead of Core sending the transcript again. Cached input is still billed at a reduced rate, so the path is used
only when the cached prefix plus Core's instruction is estimated to cost less than normal extraction of the same
Sources. The gateway runs Core's normal context checks on that request. Anything this path cannot complete raises
:class:`PrefixedExtractionUnavailable`, and the caller extracts the normal way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeGuard

import httpx
from pydantic import ValidationError

from powercontext.builtin.artifacts.prompt.service import current_prompt
from powercontext.builtin.inference import InferenceError, InvalidInferenceOutputError, TokenEstimator
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.atomic_memory.extraction import (
        AtomicMemoryEvidence,
        AtomicMemoryExtractionInput,
        AtomicMemoryExtractionOutput,
    )

PREFIX_HANDLE_KEY = "prefix_handle"
# Token counts of the terminal response that ends the prefix: its input is read from the cache, its output is new
# input to the extraction request.
PREFIX_INPUT_TOKENS_KEY = "prefix_input_tokens"
PREFIX_OUTPUT_TOKENS_KEY = "prefix_output_tokens"
_GATEWAY_METADATA_KEYS = {PREFIX_HANDLE_KEY, PREFIX_INPUT_TOKENS_KEY, PREFIX_OUTPUT_TOKENS_KEY}
# Part of a turn captured as several Sources: ``{"source_id": <turn>, "part": i, "parts": n}``.
TURN_KEY = "turn"
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


def _metadata(evidence: AtomicMemoryEvidence) -> dict[str, Any]:
    source = evidence.source_metadata
    metadata = source.get("metadata") if isinstance(source, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _one_turn(evidence: tuple[AtomicMemoryEvidence, ...]) -> bool:
    """Whether the evidence is one whole gateway turn: a single Source, or all parts of one turn in order."""

    if len(evidence) == 1:
        return TURN_KEY not in _metadata(evidence[0])
    turns = [turn for item in evidence if isinstance(turn := _metadata(item).get(TURN_KEY), dict)]
    if len(turns) != len(evidence):
        return False
    first = turns[0]
    return all(
        turn.get("source_id") == first.get("source_id")
        and turn.get("parts") == len(evidence)
        and turn.get("part") == index
        for index, turn in enumerate(turns, start=1)
    )


def prefix_handle(value: AtomicMemoryExtractionInput) -> str | None:
    """The gateway prefix handle of a window that holds exactly one gateway turn, which its last Source carries."""

    if not value.evidence or not _one_turn(value.evidence):
        return None
    handle = _metadata(value.evidence[-1]).get(PREFIX_HANDLE_KEY)
    return handle if isinstance(handle, str) and handle else None


def _prefix_tokens(evidence: AtomicMemoryEvidence) -> tuple[int, int] | None:
    metadata = _metadata(evidence)
    cached, uncached = metadata.get(PREFIX_INPUT_TOKENS_KEY), metadata.get(PREFIX_OUTPUT_TOKENS_KEY)
    if _count(cached) and _count(uncached):
        return cached, uncached
    return None


def _count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class GatewayPrefixedExtractor:
    """Generate extraction output through the gateway's prefixed extraction route."""

    client: httpx.AsyncClient
    instructions: str
    estimator: TokenEstimator
    # Price of a cached input token relative to an uncached one.
    cached_input_ratio: float = 0.1
    max_output_tokens: int | None = None

    async def generate(self, value: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput

        handle = prefix_handle(value)
        if handle is None:
            raise PrefixedExtractionUnavailable("no_prefix")
        selection = current_prompt("atomic_memory.extract")
        instructions = self.instructions if selection is None else selection.compiled_instructions
        instruction = prefixed_instruction(instructions, value.evidence, AtomicMemoryExtractionOutput)
        tokens = _prefix_tokens(value.evidence[-1])
        if tokens is None:
            raise PrefixedExtractionUnavailable("prefix_size_unknown")
        cached, uncached = tokens
        prefixed_cost = self.estimator.estimate(instruction) + uncached + self.cached_input_ratio * cached
        # Normal extraction sends the instructions and the Source evidence uncached, estimated as its budget is.
        if prefixed_cost >= self.estimator.estimate(f"{instructions}\n{value.model_dump_json()}"):
            raise PrefixedExtractionUnavailable("not_cheaper")
        body: dict[str, Any] = {"handle": handle, "instruction": instruction}
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
    instructions: str, evidence: tuple[AtomicMemoryEvidence, ...], output_type: type[AtomicMemoryExtractionOutput]
) -> str:
    """The user message appended to the prefix.

    The output format is stated in the message because a response-format field would change the cached request.
    The Source records keep the provenance and timing the normal extraction input carries, without the transcript.
    """

    records = []
    for item in evidence:
        metadata = item.source_metadata
        if isinstance(metadata, dict) and isinstance(metadata.get("metadata"), dict):
            inner = {key: value for key, value in metadata["metadata"].items() if key not in _GATEWAY_METADATA_KEYS}
            metadata = {**metadata, "metadata": inner}
        records.append({
            "evidence_id": item.evidence_id,
            "journal_position": item.journal_position,
            "source_metadata": metadata,
        })
    return "\n\n".join((
        instructions,
        "The conversation above is the Source evidence for this extraction. Only its most recent user turn is "
        "evidence: the last user message before this one and everything after it. Earlier turns are context only; "
        "do not extract a fact that only they support. Content between a BEGIN_POWERCONTEXT_<NAME> marker and its "
        "END_POWERCONTEXT_<NAME> marker was supplied by PowerContext from existing memory or context: it is not "
        "evidence for this turn, and a fact that only it supports must not be extracted. This message is not "
        "evidence. Cite the evidence_id of the Source records below; a turn captured in several parts is listed in "
        "order, and a fact may cite every part that supports it.",
        "Source records:\n" + json.dumps(records, ensure_ascii=False, sort_keys=True),
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
    error = payload.get("error")
    if isinstance(error, str) and error in _GATEWAY_REASONS:
        return _GATEWAY_REASONS[error]
    # The gateway passes a released provider error through unchanged, such as an OpenAI rate-limit object.
    if isinstance(error, dict):
        return "provider_error"
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
